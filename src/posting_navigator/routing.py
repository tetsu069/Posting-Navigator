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


def _orient_coords(coords, start, end):
    coords = list(coords)
    if not coords:
        return [start, end]
    if _dist_m(coords[0], start) <= _dist_m(coords[-1], start):
        return coords
    return list(reversed(coords))


def _simplify_degree_two(graph: nx.MultiGraph) -> nx.MultiGraph:
    """OSM way の単なる形状点(degree=2)を交差点扱いしないよう圧縮する。

    これにより巡回ステップは「数mごとの線分」ではなく、概ね交差点〜交差点の道路区間になる。
    """
    g = graph.copy()
    changed = True
    while changed:
        changed = False
        for n in list(g.nodes):
            if n not in g or g.degree(n) != 2:
                continue
            incident = list(g.edges(n, keys=True, data=True))
            if len(incident) != 2:
                continue
            e1, e2 = incident
            _, a, k1, d1 = e1
            _, b, k2, d2 = e2
            if a == b or a == n or b == n:
                continue
            # 並行辺などで曖昧になる場合は圧縮しない。
            if g.number_of_edges(n, a) != 1 or g.number_of_edges(n, b) != 1:
                continue
            c1 = _orient_coords(d1.get("geometry", [n, a]).coords if hasattr(d1.get("geometry"), "coords") else [n, a], a, n)
            c2 = _orient_coords(d2.get("geometry", [n, b]).coords if hasattr(d2.get("geometry"), "coords") else [n, b], n, b)
            coords = c1 + c2[1:]
            highway = d1.get("highway") if d1.get("highway") == d2.get("highway") else (d1.get("highway") or d2.get("highway") or "")
            name = d1.get("name") if d1.get("name") == d2.get("name") else (d1.get("name") or d2.get("name") or "")
            osm_id = d1.get("osm_id") if d1.get("osm_id") == d2.get("osm_id") else None
            data = {
                "length": float(d1.get("length", 0)) + float(d2.get("length", 0)),
                "route_cost": float(d1.get("route_cost", 0)) + float(d2.get("route_cost", 0)),
                "highway": highway, "name": name, "osm_id": osm_id,
                "geometry": LineString(coords),
            }
            g.remove_edge(n, a, k1)
            g.remove_edge(n, b, k2)
            if n in g and g.degree(n) == 0:
                g.remove_node(n)
            g.add_edge(a, b, **data)
            changed = True
            break
    return g


def build_graph(roads: list[dict]) -> nx.MultiGraph:
    """道路を実交差点でnode化し、全連結成分を保持したMultiGraphへ変換する。"""
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
                geometry=segment,
            )
    if graph.number_of_edges() == 0:
        raise ValueError("道路グラフが空です")
    return _simplify_degree_two(graph)

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


def _oriented_edge_geometry(u, v, data: dict) -> LineString:
    geom = data.get("geometry") if data else None
    if geom is None:
        return LineString([u, v])
    coords = _orient_coords(geom.coords, u, v)
    return LineString(coords)


def _step(u, v, data: dict, seq: int, *, transfer: bool = False, component: int = 1) -> dict:
    geom = LineString([u, v]) if transfer else _oriented_edge_geometry(u, v, data)
    return {
        "seq": seq,
        "geometry": geom,
        "from": u,
        "to": v,
        "length_m": round(sum(_dist_m(a, b) for a, b in zip(list(geom.coords), list(geom.coords)[1:])), 1),
        "highway": data.get("highway", "") if data else "",
        "name": data.get("name", "") if data else "",
        "osm_id": data.get("osm_id") if data else None,
        "duplicated": bool(data.get("duplicated")) if data else False,
        "transfer": transfer,
        "component": component,
    }


def _bearing(a, b) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    y = math.sin(lon2-lon1) * math.cos(lat2)
    x = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(lon2-lon1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _turn_label(prev_bearing: float | None, new_bearing: float) -> str:
    if prev_bearing is None:
        return "開始"
    diff = ((new_bearing - prev_bearing + 540) % 360) - 180
    if abs(diff) >= 150:
        return "折り返し"
    if diff >= 35:
        return "右折"
    if diff <= -35:
        return "左折"
    return "直進"


def build_navigation_legs(steps: list[dict]) -> list[dict]:
    """細かい巡回edgeを、地図で理解できるナビ区間へまとめる。"""
    legs = []
    current = None
    for step in steps:
        coords = list(step["geometry"].coords)
        if len(coords) < 2:
            continue
        step_bearing = _bearing(coords[0], coords[-1])
        if current is None:
            current = {
                "start_seq": step["seq"], "end_seq": step["seq"], "coords": coords[:],
                "length_m": step["length_m"], "name": step.get("name", ""),
                "transfer": step.get("transfer", False), "duplicated": step.get("duplicated", False),
                "bearing_start": step_bearing, "bearing_end": step_bearing,
            }
            continue
        diff = abs(((step_bearing - current["bearing_end"] + 540) % 360) - 180)
        same_kind = step.get("transfer", False) == current["transfer"] and step.get("duplicated", False) == current["duplicated"]
        same_name = bool(step.get("name")) and bool(current.get("name")) and step.get("name") == current.get("name")
        # 同一道路、またはほぼ直進なら一つの案内区間としてまとめる。
        can_merge = same_kind and (same_name and diff < 55 or diff < 22) and current["length_m"] < 260
        if can_merge:
            if current["coords"][-1] == coords[0]:
                current["coords"].extend(coords[1:])
            else:
                current["coords"].extend(coords)
            current["end_seq"] = step["seq"]
            current["length_m"] += step["length_m"]
            current["bearing_end"] = step_bearing
            if not current.get("name"):
                current["name"] = step.get("name", "")
        else:
            legs.append(current)
            current = {
                "start_seq": step["seq"], "end_seq": step["seq"], "coords": coords[:],
                "length_m": step["length_m"], "name": step.get("name", ""),
                "transfer": step.get("transfer", False), "duplicated": step.get("duplicated", False),
                "bearing_start": step_bearing, "bearing_end": step_bearing,
            }
    if current is not None:
        legs.append(current)
    prev = None
    result = []
    for i, leg in enumerate(legs, start=1):
        turn = _turn_label(prev, leg["bearing_start"])
        road = leg.get("name") or ("次の道路群へ移動" if leg["transfer"] else "この道路")
        instruction = f"{turn}：{road}を約{round(leg['length_m'])}m"
        result.append({
            "leg": i, "start_seq": leg["start_seq"], "end_seq": leg["end_seq"],
            "geometry": LineString(leg["coords"]), "length_m": round(leg["length_m"], 1),
            "name": leg.get("name", ""), "turn": turn, "instruction": instruction,
            "bearing": round(leg["bearing_start"], 1), "transfer": leg["transfer"],
            "duplicated": leg["duplicated"],
        })
        prev = leg["bearing_end"]
    return result

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

    # 実際のOSM道路形状を順番どおり連結した一本の巡回線を作る。
    coords = []
    for s in steps:
        c = list(s["geometry"].coords)
        if not coords:
            coords.extend(c)
        elif coords[-1] == c[0]:
            coords.extend(c[1:])
        else:
            coords.extend(c)
    route = LineString(coords)
    route_length = sum(s["length_m"] for s in steps)
    covered_steps = [s for s in steps if not s["transfer"]]
    navigation_legs = build_navigation_legs(steps)
    return {
        "geometry": route,
        "route_steps": steps,
        "navigation_legs": navigation_legs,
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
