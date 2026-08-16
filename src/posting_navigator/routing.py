from __future__ import annotations

import math
import networkx as nx
from pyproj import Geod
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

GEOD = Geod(ellps="WGS84")

# 幹線道路は対象には残すが、奇数頂点解消のための余分な往復では強い罰則を付ける。
HIGHWAY_PENALTY = {
    "primary": 8.0,
    "primary_link": 8.0,
    "secondary": 5.0,
    "secondary_link": 5.0,
    "tertiary": 2.5,
    "tertiary_link": 2.5,
    "unclassified": 1.4,
    "residential": 1.0,
    "living_street": 1.0,
    "service": 1.1,
    "pedestrian": 1.0,
    "footway": 1.2,
    "path": 1.3,
    "steps": 1.5,
    "cycleway": 1.3,
}


def _dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    return abs(GEOD.inv(a[0], a[1], b[0], b[1])[2])


def _snap(p: tuple[float, float], precision: int = 6) -> tuple[float, float]:
    return (round(p[0], precision), round(p[1], precision))


def _source_for_segment(segment: LineString, roads: list[dict]) -> dict:
    midpoint = segment.interpolate(0.5, normalized=True)
    return min(roads, key=lambda road: road["geometry"].distance(midpoint))


def build_graph(roads: list[dict]) -> nx.MultiGraph:
    """道路を交差点でnode化し、全連結成分を保持したMultiGraphへ変換する。"""
    if not roads:
        raise ValueError("道路データが空です")
    graph = nx.MultiGraph()
    merged = unary_union([road["geometry"] for road in roads])
    lines = [merged] if merged.geom_type == "LineString" else [g for g in getattr(merged, "geoms", []) if g.geom_type == "LineString"]
    for line in lines:
        coords = list(line.coords)
        for a, b in zip(coords, coords[1:]):
            u, v = _snap(a), _snap(b)
            if u == v:
                continue
            segment = LineString([u, v])
            source = _source_for_segment(segment, roads)
            length = _dist_m(u, v)
            if length < 0.25:
                continue
            highway = source.get("highway", "")
            penalty = HIGHWAY_PENALTY.get(highway, 1.5)
            graph.add_edge(
                u, v,
                length=length,
                route_cost=length * penalty,
                highway=highway,
                name=source.get("name", ""),
                osm_id=source.get("id"),
            )
    if graph.number_of_edges() == 0:
        raise ValueError("道路グラフが空です")
    return graph


def _nearest_node(g: nx.MultiGraph, point: tuple[float, float] | None) -> tuple[float, float]:
    if point is None:
        return min(g.nodes, key=lambda p: (p[1], p[0]))
    return min(g.nodes, key=lambda p: _dist_m(p, point))


def eulerize_weighted(g: nx.MultiGraph) -> nx.MultiGraph:
    """単一連結成分をChinese Postman用にEuler化する。"""
    g = g.copy()
    if not nx.is_connected(g):
        raise ValueError("eulerize_weighted は単一連結成分で呼び出してください")
    odd = [n for n, degree in g.degree() if degree % 2 == 1]
    if not odd:
        return g
    complete = nx.Graph()
    paths: dict[tuple, list] = {}
    for i, u in enumerate(odd):
        lengths, all_paths = nx.single_source_dijkstra(g, u, weight="route_cost")
        for v in odd[i + 1:]:
            if v in lengths:
                complete.add_edge(u, v, weight=lengths[v])
                paths[(u, v)] = all_paths[v]
                paths[(v, u)] = list(reversed(all_paths[v]))
    matching = nx.algorithms.matching.min_weight_matching(complete, weight="weight")
    for u, v in matching:
        path = paths[(u, v)]
        for a, b in zip(path, path[1:]):
            edge_data = dict(min(g.get_edge_data(a, b).values(), key=lambda d: d.get("route_cost", math.inf)))
            edge_data["duplicated"] = True
            g.add_edge(a, b, **edge_data)
    return g


def _component_order(graph: nx.MultiGraph, start_point: tuple[float, float] | None) -> list[set]:
    """全連結成分を巡回する順。最初は開始地点に近い成分、以後は直前位置から近い成分。"""
    components = [set(c) for c in nx.connected_components(graph)]
    if len(components) <= 1:
        return components
    remaining = components[:]
    if start_point is None:
        first = max(remaining, key=lambda c: graph.subgraph(c).number_of_edges())
    else:
        first = min(remaining, key=lambda c: min(_dist_m(n, start_point) for n in c))
    order = [first]
    remaining.remove(first)
    current = _nearest_node(graph.subgraph(first), start_point)
    while remaining:
        nxt = min(remaining, key=lambda c: min(_dist_m(n, current) for n in c))
        order.append(nxt)
        current = min(nxt, key=lambda n: _dist_m(n, current))
        remaining.remove(nxt)
    return order


def _step(u, v, data: dict, seq: int, *, transfer: bool = False, component: int = 1) -> dict:
    return {
        "seq": seq,
        "geometry": LineString([u, v]),
        "from": u,
        "to": v,
        "length_m": round(_dist_m(u, v), 1),
        "highway": data.get("highway", "") if data else "",
        "name": data.get("name", "") if data else "",
        "osm_id": data.get("osm_id") if data else None,
        "duplicated": bool(data.get("duplicated")) if data else False,
        "transfer": transfer,
        "component": component,
    }


def generate_route(roads: list[dict], start_point: tuple[float, float] | None = None) -> dict:
    """全道路成分を対象に、順序付きの巡回ステップ列を生成する。

    小さな非連結成分も捨てない。成分間は transfer ステップとして明示し、
    配布対象道路と区別できるようにする。
    """
    source_graph = build_graph(roads)
    component_sets = _component_order(source_graph, start_point)
    source_length = sum(d["length"] for _, _, d in source_graph.edges(data=True))
    steps: list[dict] = []
    duplicated_length = 0.0
    major_duplicated = 0.0
    transfer_length = 0.0
    dead_ends = [n for n, degree in source_graph.degree() if degree == 1]
    current_point = start_point
    first_start = None

    for component_index, nodes in enumerate(component_sets, start=1):
        comp_source = source_graph.subgraph(nodes).copy()
        comp = eulerize_weighted(comp_source)
        comp_start = _nearest_node(comp, current_point)
        if first_start is None:
            first_start = comp_start
        if current_point is not None and steps:
            prev = steps[-1]["to"]
            if prev != comp_start:
                s = _step(prev, comp_start, {}, len(steps) + 1, transfer=True, component=component_index)
                steps.append(s)
                transfer_length += s["length_m"]
        circuit = list(nx.eulerian_circuit(comp, source=comp_start, keys=True))
        for u, v, k in circuit:
            data = comp[u][v][k]
            s = _step(u, v, data, len(steps) + 1, component=component_index)
            steps.append(s)
            if s["duplicated"]:
                duplicated_length += s["length_m"]
                if s["highway"] in {"primary", "primary_link", "secondary", "secondary_link"}:
                    major_duplicated += s["length_m"]
        current_point = comp_start

    if not steps or first_start is None:
        raise ValueError("巡回ルートを生成できませんでした")

    # 互換用の一本線。transferも含むため、UIでは route_step を優先表示する。
    coords = [steps[0]["from"]] + [s["to"] for s in steps]
    route = LineString(coords)
    route_length = sum(s["length_m"] for s in steps)
    covered_steps = [s for s in steps if not s["transfer"]]
    return {
        "geometry": route,
        "route_steps": steps,
        "start_point": Point(first_start),
        "requested_start": Point(start_point) if start_point else None,
        "source_edges": source_graph.number_of_edges(),
        "route_edges": len(covered_steps),
        "source_length_m": round(source_length, 1),
        "route_length_m": round(route_length, 1),
        "transfer_length_m": round(transfer_length, 1),
        "duplicated_length_m": round(duplicated_length, 1),
        "major_road_duplicated_m": round(major_duplicated, 1),
        "duplication_ratio": round((route_length - transfer_length) / source_length, 3) if source_length else None,
        "connected_nodes": source_graph.number_of_nodes(),
        "component_count": len(component_sets),
        "dead_end_count": len(dead_ends),
        "start_lon": first_start[0],
        "start_lat": first_start[1],
    }


def split_route(route: dict, workers: int) -> list[dict]:
    """巡回順序を保ったまま担当区間を距離均等分割する。"""
    if workers < 1:
        raise ValueError("担当者数は1以上で指定してください")
    coords = list(route["geometry"].coords)
    if len(coords) < 2:
        raise ValueError("分割対象ルートが空です")
    edge_lengths = [_dist_m(a, b) for a, b in zip(coords, coords[1:])]
    total = sum(edge_lengths)
    targets = [total * i / workers for i in range(1, workers)]
    pieces: list[list[tuple[float, float]]] = [[coords[0]]]
    cumulative = 0.0
    target_index = 0
    for a, b, edge_length in zip(coords, coords[1:], edge_lengths):
        edge_start = cumulative
        while target_index < len(targets) and targets[target_index] <= edge_start + edge_length + 1e-9:
            fraction = 0.0 if edge_length == 0 else (targets[target_index] - edge_start) / edge_length
            fraction = max(0.0, min(1.0, fraction))
            split_point = (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)
            if pieces[-1][-1] != split_point:
                pieces[-1].append(split_point)
            pieces.append([split_point])
            target_index += 1
        if pieces[-1][-1] != b:
            pieces[-1].append(b)
        cumulative += edge_length
    while len(pieces) < workers:
        pieces.append([coords[-1], coords[-1]])
    assignments = []
    for index, piece in enumerate(pieces[:workers], start=1):
        if len(piece) == 1:
            piece.append(piece[0])
        geometry = LineString(piece)
        length_m = sum(_dist_m(a, b) for a, b in zip(piece, piece[1:]))
        assignments.append({
            "worker_id": index,
            "name": f"担当{index:02d}",
            "geometry": geometry,
            "length_m": round(length_m, 1),
            "start_point": Point(piece[0]),
            "end_point": Point(piece[-1]),
            "start_lon": piece[0][0], "start_lat": piece[0][1],
            "end_lon": piece[-1][0], "end_lat": piece[-1][1],
        })
    average = total / workers
    for assignment in assignments:
        assignment["difference_from_average_m"] = round(assignment["length_m"] - average, 1)
    return assignments
