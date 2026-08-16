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
                "boundary_near": bool(d1.get("boundary_near") or d2.get("boundary_near")),
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
                boundary_near=bool(source.get("boundary_near", False)),
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


def _node_radial_distances(g: nx.MultiGraph, start_node) -> dict:
    """開始地点からの道路ネットワーク距離。クラスタの内→外順序に使う。"""
    return nx.single_source_dijkstra_path_length(g, start_node, weight="length")


def _edge_ref_key(u, v, k):
    return (u, v, k)


def _edge_midpoint(data: dict, u, v) -> tuple[float, float]:
    geom = data.get("geometry")
    if geom is not None and not geom.is_empty:
        p = geom.interpolate(0.5, normalized=True)
        return (p.x, p.y)
    return ((u[0] + v[0]) / 2, (u[1] + v[1]) / 2)


def _cluster_component_outward(
    g: nx.MultiGraph,
    start_node,
    *,
    target_length_m: float | None = None,
) -> list[dict]:
    """道路を局所的な連結クラスタへ分け、開始地点から外向きに並べる。

    純粋な Chinese Postman を町丁目全体へ一度に適用すると、開始地点近くを
    残したまま遠方へ行き、後で戻る巡回が起きやすい。ここではまず近接道路を
    300〜600m程度の連結した塊へまとめ、その塊を開始地点からの道路距離が
    概ね増える順に処理する。
    """
    edges = list(g.edges(keys=True, data=True))
    if not edges:
        return []
    radial = _node_radial_distances(g, start_node)
    total = sum(float(d.get("length", 0.0)) for _, _, _, d in edges)
    if target_length_m is None:
        # 小区域は細かく割りすぎず、大区域でも1クラスタが巨大化しない。
        target_length_m = min(600.0, max(320.0, total / max(4, min(12, round(total / 480.0) or 1))))

    refs = {(u, v, k) for u, v, k, _ in edges}
    data_by_ref = {(u, v, k): d for u, v, k, d in edges}
    incident: dict[tuple[float, float], set] = {}
    for u, v, k, _ in edges:
        incident.setdefault(u, set()).add((u, v, k))
        incident.setdefault(v, set()).add((u, v, k))

    def ref_nodes(ref):
        return ref[0], ref[1]

    def ref_radial(ref):
        u, v = ref_nodes(ref)
        return min(radial.get(u, math.inf), radial.get(v, math.inf))

    unassigned = set(refs)
    clusters: list[dict] = []
    while unassigned:
        seed = min(unassigned, key=lambda r: (ref_radial(r), data_by_ref[r].get("length", math.inf)))
        su, sv = ref_nodes(seed)
        seed_mid = _edge_midpoint(data_by_ref[seed], su, sv)
        chosen: set = set()
        cluster_nodes: set = set()
        length = 0.0
        frontier: set = {seed}

        while frontier and (length < target_length_m or not chosen):
            # 「外向き」だけで細長く伸びすぎないよう、seedからの空間距離も加味。
            ref = min(
                frontier,
                key=lambda r: (
                    ref_radial(r),
                    _dist_m(_edge_midpoint(data_by_ref[r], *ref_nodes(r)), seed_mid) * 0.45,
                ),
            )
            frontier.remove(ref)
            if ref not in unassigned:
                continue
            unassigned.remove(ref)
            chosen.add(ref)
            u, v = ref_nodes(ref)
            cluster_nodes.update((u, v))
            length += float(data_by_ref[ref].get("length", 0.0))
            for n in (u, v):
                frontier.update(r for r in incident.get(n, ()) if r in unassigned)

        # 行き止まり等でfrontierが尽きた小クラスタもそのまま独立塊として保持。
        min_r = min((ref_radial(r) for r in chosen), default=0.0)
        max_r = max((max(radial.get(r[0], 0.0), radial.get(r[1], 0.0)) for r in chosen), default=min_r)
        clusters.append({"edges": chosen, "nodes": cluster_nodes, "length_m": length, "min_radial_m": min_r, "max_radial_m": max_r})

    # 基本は内→外。近い帯の中では前クラスタに接する塊を優先する。
    ordered: list[dict] = []
    remaining = clusters[:]
    current_nodes = {start_node}
    last_radius = 0.0
    while remaining:
        def score(c):
            regression = max(0.0, last_radius - c["min_radial_m"])
            adjacency = 0 if current_nodes.intersection(c["nodes"]) else 1
            return (regression * 4.0 + c["min_radial_m"], adjacency, c["min_radial_m"])
        nxt = min(remaining, key=score)
        ordered.append(nxt)
        remaining.remove(nxt)
        current_nodes = nxt["nodes"]
        last_radius = max(last_radius, nxt["min_radial_m"])
    return ordered


def _graph_from_edge_refs(g: nx.MultiGraph, refs: set) -> nx.MultiGraph:
    out = nx.MultiGraph()
    for u, v, k in refs:
        data = dict(g[u][v][k])
        out.add_edge(u, v, **data)
    return out


def _eulerize_open(g: nx.MultiGraph, start, end) -> nx.MultiGraph:
    """start→endの開いたRoute Inspection trail用に辺を最小追加する。"""
    if start == end:
        return eulerize_weighted(g)
    out = g.copy()
    odd = {n for n, degree in out.degree() if degree % 2 == 1}
    toggle = odd.symmetric_difference({start, end})
    if not toggle:
        return out
    toggle = list(toggle)
    complete = nx.Graph()
    paths: dict[tuple, list] = {}
    for i, u in enumerate(toggle):
        lengths, all_paths = nx.single_source_dijkstra(out, u, weight="route_cost")
        for v in toggle[i + 1:]:
            if v in lengths:
                complete.add_edge(u, v, weight=lengths[v])
                paths[(u, v)] = all_paths[v]
                paths[(v, u)] = list(reversed(all_paths[v]))
    matching = nx.algorithms.matching.min_weight_matching(complete, weight="weight")
    for u, v in matching:
        for a, b in zip(paths[(u, v)], paths[(u, v)][1:]):
            edge_data = dict(min(out.get_edge_data(a, b).values(), key=lambda d: d.get("route_cost", math.inf)))
            edge_data["duplicated"] = True
            out.add_edge(a, b, **edge_data)
    return out


def _append_shortest_path_steps(full_graph: nx.MultiGraph, start, end, steps: list[dict], component: int) -> float:
    """クラスタ間移動は直線ではなく、必ず既存道路グラフ上を通る。"""
    if start == end:
        return 0.0
    path = nx.shortest_path(full_graph, start, end, weight="route_cost")
    total = 0.0
    for a, b in zip(path, path[1:]):
        data = min(full_graph.get_edge_data(a, b).values(), key=lambda d: d.get("route_cost", math.inf))
        s = _step(a, b, data, len(steps) + 1, transfer=True, component=component)
        steps.append(s)
        total += s["length_m"]
    return total

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
        "boundary_near": bool(data.get("boundary_near")) if data else False,
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
    if abs(diff) >= 155:
        return "折り返し"
    if diff >= 42:
        return "右折"
    if diff <= -42:
        return "左折"
    return "直進"


def _bearing_over_distance(coords: list[tuple[float, float]], *, from_start: bool, sample_m: float = 10.0) -> float:
    """交差点直近の1m級ノードに引っ張られないよう、道路に沿って数m先との方位を使う。"""
    if len(coords) < 2:
        return 0.0
    seq = coords if from_start else list(reversed(coords))
    origin = seq[0]
    acc = 0.0
    prev = origin
    for pt in seq[1:]:
        seg = _dist_m(prev, pt)
        acc += seg
        if acc >= sample_m:
            b = _bearing(origin, pt)
            return b if from_start else (b + 180) % 360
        prev = pt
    b = _bearing(seq[0], seq[-1])
    return b if from_start else (b + 180) % 360


def _raw_navigation_legs(steps: list[dict]) -> list[dict]:
    legs: list[dict] = []
    current = None
    for step in steps:
        coords = list(step["geometry"].coords)
        if len(coords) < 2:
            continue
        b0 = _bearing_over_distance(coords, from_start=True)
        b1 = _bearing_over_distance(coords, from_start=False)
        if current is None:
            current = {
                "start_seq": step["seq"], "end_seq": step["seq"], "coords": coords[:],
                "length_m": step["length_m"], "name": step.get("name", ""),
                "transfer": step.get("transfer", False), "duplicated": step.get("duplicated", False),
                "bearing_start": b0, "bearing_end": b1,
            }
            continue
        diff = abs(((b0 - current["bearing_end"] + 540) % 360) - 180)
        same_kind = step.get("transfer", False) == current["transfer"] and step.get("duplicated", False) == current["duplicated"]
        same_name = bool(step.get("name")) and bool(current.get("name")) and step.get("name") == current.get("name")
        contiguous = _dist_m(current["coords"][-1], coords[0]) <= 1.5
        can_merge = contiguous and same_kind and ((same_name and diff < 60) or diff < 25) and current["length_m"] < 280
        if can_merge:
            current["coords"].extend(coords[1:])
            current["end_seq"] = step["seq"]
            current["length_m"] += step["length_m"]
            current["bearing_end"] = _bearing_over_distance(current["coords"], from_start=False)
            if not current.get("name"):
                current["name"] = step.get("name", "")
        else:
            legs.append(current)
            current = {
                "start_seq": step["seq"], "end_seq": step["seq"], "coords": coords[:],
                "length_m": step["length_m"], "name": step.get("name", ""),
                "transfer": step.get("transfer", False), "duplicated": step.get("duplicated", False),
                "bearing_start": b0, "bearing_end": b1,
            }
    if current is not None:
        legs.append(current)
    return legs


def _coalesce_micro_legs(legs: list[dict], threshold_m: float = 7.0) -> list[dict]:
    """1〜数mの交差点ノイズを単独ナビにしない。実際の行き止まりtransferは保持する。"""
    out: list[dict] = []
    i = 0
    while i < len(legs):
        leg = legs[i]
        if leg["length_m"] < threshold_m and not leg["transfer"]:
            # 次区間へ吸収するのを優先。開始直後の「左折1m→折返し1m」を消す。
            if i + 1 < len(legs) and not legs[i+1]["transfer"] and _dist_m(leg["coords"][-1], legs[i+1]["coords"][0]) <= 1.5:
                nxt = dict(legs[i+1])
                nxt["coords"] = leg["coords"] + nxt["coords"][1:]
                nxt["start_seq"] = leg["start_seq"]
                nxt["length_m"] += leg["length_m"]
                nxt["bearing_start"] = _bearing_over_distance(nxt["coords"], from_start=True)
                legs = legs[:i] + [nxt] + legs[i+2:]
                continue
            if out and not out[-1]["transfer"] and _dist_m(out[-1]["coords"][-1], leg["coords"][0]) <= 1.5:
                out[-1]["coords"].extend(leg["coords"][1:])
                out[-1]["end_seq"] = leg["end_seq"]
                out[-1]["length_m"] += leg["length_m"]
                out[-1]["bearing_end"] = _bearing_over_distance(out[-1]["coords"], from_start=False)
                i += 1
                continue
        out.append(leg)
        i += 1
    return out


def build_navigation_legs(steps: list[dict]) -> list[dict]:
    """巡回edgeを、人が読める「交差点〜交差点」単位の案内へ変換する。"""
    legs = _coalesce_micro_legs(_raw_navigation_legs(steps))
    prev = None
    result = []
    for i, leg in enumerate(legs, start=1):
        leg["bearing_start"] = _bearing_over_distance(leg["coords"], from_start=True)
        leg["bearing_end"] = _bearing_over_distance(leg["coords"], from_start=False)
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
    """開始地点の近隣を局所完結させながら外側へ広がる1町丁目1ルートを生成する。

    町丁目全体を一度にEuler化せず、道路を連結した小クラスタへ分ける。
    各クラスタは開始地点からの道路距離が概ね増える順に処理し、クラスタ内では
    open Chinese Postman trailを使って「入口→外側の出口」へ抜けることで、
    近所を残して遠方へ行き後から戻る動きを抑える。
    """
    source_graph = build_graph(roads)
    source_length = sum(d["length"] for _, _, d in source_graph.edges(data=True))
    dead_ends = [n for n, degree in source_graph.degree() if degree == 1]
    components = [set(c) for c in nx.connected_components(source_graph)]
    if not components:
        raise ValueError("巡回ルートを生成できませんでした")

    # 開始地点を含む/最も近い成分を先頭にする。別成分間を道路外直線で結ばない。
    if start_point is None:
        first_comp = max(components, key=lambda c: source_graph.subgraph(c).number_of_edges())
    else:
        first_comp = min(components, key=lambda c: min(_dist_m(n, start_point) for n in c))
    components.remove(first_comp)
    component_sets = [first_comp] + sorted(
        components,
        key=lambda c: min(_dist_m(n, start_point or _nearest_node(source_graph.subgraph(first_comp), None)) for n in c),
    )

    steps: list[dict] = []
    duplicated_length = 0.0
    major_duplicated = 0.0
    transfer_length = 0.0
    first_start = None
    current_node = None
    skipped_disconnected_length = 0.0
    cluster_count = 0

    for component_index, nodes in enumerate(component_sets, start=1):
        comp_graph = source_graph.subgraph(nodes).copy()
        if current_node is None:
            comp_start = _nearest_node(comp_graph, start_point)
        elif current_node in comp_graph:
            comp_start = current_node
        else:
            # 実道路でつながっていない別成分は偽の直線で接続しない。
            # 小さな孤立成分は診断値として残し、ルート本体からは除外する。
            skipped_disconnected_length += sum(d.get("length", 0.0) for _, _, d in comp_graph.edges(data=True))
            continue
        if first_start is None:
            first_start = comp_start

        radial = _node_radial_distances(comp_graph, comp_start)
        clusters = _cluster_component_outward(comp_graph, comp_start)
        cluster_count += len(clusters)
        current_node = comp_start

        for cluster_index, cluster in enumerate(clusters, start=1):
            cluster_graph = _graph_from_edge_refs(comp_graph, cluster["edges"])
            if cluster_graph.number_of_edges() == 0:
                continue
            entry = _nearest_node(cluster_graph, current_node)
            if current_node != entry:
                transfer_length += _append_shortest_path_steps(comp_graph, current_node, entry, steps, component_index)
            # 入口から見て外側のnodeを出口にし、クラスタを抜けながら処理する。
            exit_node = max(cluster_graph.nodes, key=lambda n: radial.get(n, 0.0))
            if exit_node == entry and cluster_graph.number_of_nodes() > 1:
                exit_node = max(cluster_graph.nodes, key=lambda n: _dist_m(n, entry))
            routed = _eulerize_open(cluster_graph, entry, exit_node)
            try:
                trail = list(nx.eulerian_path(routed, source=entry, keys=True)) if entry != exit_node else list(nx.eulerian_circuit(routed, source=entry, keys=True))
            except nx.NetworkXError:
                # parityが特殊な小クラスタでは閉路へフォールバック。
                routed = eulerize_weighted(cluster_graph)
                trail = list(nx.eulerian_circuit(routed, source=entry, keys=True))
                exit_node = entry
            for u, v, k in trail:
                data = routed[u][v][k]
                s = _step(u, v, data, len(steps) + 1, component=component_index)
                steps.append(s)
                if s["duplicated"]:
                    duplicated_length += s["length_m"]
                    if s["highway"] in {"primary", "primary_link", "secondary", "secondary_link"}:
                        major_duplicated += s["length_m"]
            current_node = exit_node

    if not steps or first_start is None:
        raise ValueError("巡回ルートを生成できませんでした")

    # route_stepsは道路Geometryのみ。非連続箇所を直線で描画しないため、route本体は
    # 連続している最初の系列だけLineString化し、表示はroute_stepsを正とする。
    coords = []
    for s in steps:
        c = list(s["geometry"].coords)
        if not coords:
            coords.extend(c)
        elif _dist_m(coords[-1], c[0]) <= 1.5:
            coords.extend(c[1:])
        else:
            break
    route = LineString(coords) if len(coords) >= 2 else steps[0]["geometry"]
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
        "duplication_ratio": round((route_length - transfer_length) / max(source_length - skipped_disconnected_length, 1.0), 3),
        "connected_nodes": source_graph.number_of_nodes(),
        "component_count": len(component_sets),
        "cluster_count": cluster_count,
        "skipped_disconnected_length_m": round(skipped_disconnected_length, 1),
        "dead_end_count": len(dead_ends),
        "routing_strategy": "local-clusters-outward",
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
