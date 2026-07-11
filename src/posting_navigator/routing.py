from __future__ import annotations

import math
import networkx as nx
from pyproj import Geod
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

GEOD = Geod(ellps="WGS84")

# 幹線道路は配布対象からは除外しないが、重複通行（奇数頂点解消）には強い罰則を付ける。
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
}


def _dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    return abs(GEOD.inv(a[0], a[1], b[0], b[1])[2])


def _snap(p: tuple[float, float], precision: int = 6) -> tuple[float, float]:
    return (round(p[0], precision), round(p[1], precision))


def _source_for_segment(segment: LineString, roads: list[dict]) -> dict:
    """noding後の線分に、最も近い元道路の属性を戻す。"""
    midpoint = segment.interpolate(0.5, normalized=True)
    return min(roads, key=lambda road: road["geometry"].distance(midpoint))


def build_graph(roads: list[dict]) -> nx.MultiGraph:
    """道路線を交差点でnode化し、道路属性付きMultiGraphへ変換する。"""
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
            highway = source.get("highway", "")
            penalty = HIGHWAY_PENALTY.get(highway, 1.5)
            graph.add_edge(
                u,
                v,
                length=length,
                route_cost=length * penalty,
                highway=highway,
                name=source.get("name", ""),
                osm_id=source.get("id"),
            )
    if graph.number_of_edges() == 0:
        raise ValueError("道路グラフが空です")
    return graph


def _largest_component(g: nx.MultiGraph) -> nx.MultiGraph:
    nodes = max(nx.connected_components(g), key=len)
    return g.subgraph(nodes).copy()


def _nearest_node(g: nx.MultiGraph, point: tuple[float, float] | None) -> tuple[float, float]:
    if point is None:
        return min(g.nodes, key=lambda p: (p[1], p[0]))
    return min(g.nodes, key=lambda p: _dist_m(p, point))


def eulerize_weighted(g: nx.MultiGraph) -> nx.MultiGraph:
    """奇数次数頂点を最小コストでペアリング。幹線道路の重複通行を避ける。"""
    g = _largest_component(g)
    odd = [n for n, degree in g.degree() if degree % 2 == 1]
    if not odd:
        return g
    complete = nx.Graph()
    paths: dict[tuple, list] = {}
    for i, u in enumerate(odd):
        lengths, all_paths = nx.single_source_dijkstra(g, u, weight="route_cost")
        for v in odd[i + 1 :]:
            if v in lengths:
                complete.add_edge(u, v, weight=lengths[v])
                paths[(u, v)] = all_paths[v]
                paths[(v, u)] = list(reversed(all_paths[v]))
    matching = nx.algorithms.matching.min_weight_matching(complete, weight="weight")
    for u, v in matching:
        path = paths[(u, v)]
        for a, b in zip(path, path[1:]):
            edge_data = min(g.get_edge_data(a, b).values(), key=lambda d: d.get("route_cost", math.inf))
            g.add_edge(a, b, **edge_data, duplicated=True)
    return g


def generate_route(roads: list[dict], start_point: tuple[float, float] | None = None) -> dict:
    source_graph = _largest_component(build_graph(roads))
    graph = eulerize_weighted(source_graph)
    start = _nearest_node(graph, start_point)
    circuit = list(nx.eulerian_circuit(graph, source=start, keys=True))
    coords = [circuit[0][0]] + [v for _, v, _ in circuit]
    route = LineString(coords)
    source_length = sum(d["length"] for _, _, d in source_graph.edges(data=True))
    route_length = sum(graph[u][v][k]["length"] for u, v, k in circuit)
    dead_ends = [n for n, degree in source_graph.degree() if degree == 1]
    duplicated_length = sum(d["length"] for _, _, d in graph.edges(data=True) if d.get("duplicated"))
    major_duplicated = sum(
        d["length"] for _, _, d in graph.edges(data=True)
        if d.get("duplicated") and d.get("highway") in {"primary", "primary_link", "secondary", "secondary_link"}
    )
    return {
        "geometry": route,
        "start_point": Point(start),
        "requested_start": Point(start_point) if start_point else None,
        "source_edges": source_graph.number_of_edges(),
        "route_edges": len(circuit),
        "source_length_m": round(source_length, 1),
        "route_length_m": round(route_length, 1),
        "duplicated_length_m": round(duplicated_length, 1),
        "major_road_duplicated_m": round(major_duplicated, 1),
        "duplication_ratio": round(route_length / source_length, 3) if source_length else None,
        "connected_nodes": graph.number_of_nodes(),
        "dead_end_count": len(dead_ends),
        "start_lon": start[0],
        "start_lat": start[1],
    }


def split_route(route: dict, workers: int) -> list[dict]:
    """巡回ルートを連続した担当区間へ距離均等分割する。"""
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
            "start_lon": piece[0][0],
            "start_lat": piece[0][1],
            "end_lon": piece[-1][0],
            "end_lat": piece[-1][1],
        })
    average = total / workers
    for assignment in assignments:
        assignment["difference_from_average_m"] = round(assignment["length_m"] - average, 1)
    return assignments
