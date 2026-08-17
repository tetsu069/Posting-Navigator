from __future__ import annotations

import math
import heapq
import itertools
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

EXACT_MATCHING_MAX_NODES = 24

def _nearest_target_path_low_memory(g: nx.MultiGraph, source, targets: set) -> tuple[object, list, float]:
    """Dijkstra until the first target is settled; keeps only local predecessor state."""
    if not targets:
        raise ValueError("ペアリング候補がありません")
    counter = itertools.count()
    heap = [(0.0, next(counter), source)]
    dist = {source: 0.0}
    prev = {}
    settled = set()
    while heap:
        d, _, u = heapq.heappop(heap)
        if u in settled:
            continue
        settled.add(u)
        if u in targets:
            path = [u]
            cur = u
            while cur != source:
                cur = prev[cur]
                path.append(cur)
            path.reverse()
            return u, path, d
        for v, keyed in g.adj[u].items():
            if v in settled:
                continue
            edge_cost = min(float(data.get("route_cost", data.get("length", 1.0))) for data in keyed.values())
            nd = d + edge_cost
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, next(counter), v))
    raise ValueError("奇数頂点を実道路上でペアリングできません")

def _minimum_pairing_paths(g: nx.MultiGraph, nodes: list) -> list[tuple[object, object, list]]:
    """Return node pairs and shortest paths without blowing memory on large odd sets.

    For small sets we keep NetworkX's exact minimum-weight perfect matching.  For
    larger sets we deliberately avoid constructing the O(n^2) complete graph and
    the Blossom matching state; instead we repeatedly pair a node with its nearest
    remaining odd node using one Dijkstra result at a time.  This trades a little
    optimality for bounded memory, which is essential on Render Free (512 MB).
    """
    nodes = list(nodes)
    if len(nodes) % 2:
        raise ValueError("奇数頂点数が偶数ではありません")
    if not nodes:
        return []

    if len(nodes) <= EXACT_MATCHING_MAX_NODES:
        complete = nx.Graph()
        paths: dict[tuple, list] = {}
        for i, u in enumerate(nodes):
            lengths, all_paths = nx.single_source_dijkstra(g, u, weight="route_cost")
            for v in nodes[i + 1:]:
                if v in lengths:
                    complete.add_edge(u, v, weight=lengths[v])
                    paths[(u, v)] = all_paths[v]
                    paths[(v, u)] = list(reversed(all_paths[v]))
        matching = nx.algorithms.matching.min_weight_matching(complete, weight="weight")
        if len(matching) * 2 != len(nodes):
            raise ValueError("奇数頂点を完全にペアリングできません")
        return [(u, v, paths[(u, v)]) for u, v in matching]

    # Low-memory greedy pairing.  Keep only one Dijkstra table alive at a time.
    # Choosing the lexicographically smallest node makes results deterministic.
    remaining = set(nodes)
    pairs: list[tuple[object, object, list]] = []
    while remaining:
        u = min(remaining, key=repr)
        remaining.remove(u)
        v, path, _ = _nearest_target_path_low_memory(g, u, remaining)
        remaining.remove(v)
        pairs.append((u, v, path))
    return pairs



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
            # required/optional の境界は交差点として保持し、配布道路と移動道路を混ぜない。
            if bool(d1.get("required", True)) != bool(d2.get("required", True)):
                continue
            highway = d1.get("highway") if d1.get("highway") == d2.get("highway") else (d1.get("highway") or d2.get("highway") or "")
            name = d1.get("name") if d1.get("name") == d2.get("name") else (d1.get("name") or d2.get("name") or "")
            osm_id = d1.get("osm_id") if d1.get("osm_id") == d2.get("osm_id") else None
            data = {
                "length": float(d1.get("length", 0)) + float(d2.get("length", 0)),
                "route_cost": float(d1.get("route_cost", 0)) + float(d2.get("route_cost", 0)),
                "highway": highway, "name": name, "osm_id": osm_id,
                "boundary_near": bool(d1.get("boundary_near") or d2.get("boundary_near")),
                "required": bool(d1.get("required", True)),
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
                required=bool(source.get("required", True)),
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
    for u, v, path in _minimum_pairing_paths(g, odd):
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
        target_length_m = min(950.0, max(520.0, total / max(3, min(9, round(total / 760.0) or 1))))

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


def _local_xy_m(point: tuple[float, float], origin: tuple[float, float]) -> tuple[float, float]:
    """Small-area lon/lat -> local east/north metres."""
    lon, lat = point
    lon0, lat0 = origin
    north = (lat - lat0) * 111_320.0
    east = (lon - lon0) * 111_320.0 * math.cos(math.radians((lat + lat0) * 0.5))
    return east, north


def _assign_local_sweep_blocks(g: nx.MultiGraph, start, cell_m: float = 220.0) -> dict:
    """Assign coarse, street-axis-aligned local blocks to required edges.

    Blocks are anchored at the area's minimum projected coordinates rather than at
    START.  This avoids splitting one obvious street block simply because START
    happens to lie on a grid boundary.
    """
    if g.number_of_edges() == 0:
        return {}
    axis = _dominant_street_axis(g)
    theta = math.radians(axis)
    projected = []
    for u, v, k, data in g.edges(keys=True, data=True):
        mid = _edge_midpoint(data, u, v)
        e, n = _local_xy_m(mid, start)
        along = e * math.sin(theta) + n * math.cos(theta)
        across = e * math.cos(theta) - n * math.sin(theta)
        projected.append((u, v, k, data, along, across))
    min_along = min(x[4] for x in projected)
    min_across = min(x[5] for x in projected)
    blocks = {}
    for u, v, k, data, along, across in projected:
        bid = (
            math.floor((across - min_across) / (cell_m * 1.15)),
            math.floor((along - min_along) / cell_m),
        )
        data["sweep_block"] = bid
        blocks.setdefault(bid, {"edges": 0, "along": [], "across": []})
        blocks[bid]["edges"] += 1
        blocks[bid]["along"].append(along)
        blocks[bid]["across"].append(across)

    rows = {}
    for bid, info in blocks.items():
        a = sum(info["along"]) / len(info["along"])
        c = sum(info["across"]) / len(info["across"])
        info["centroid"] = (a, c)
        rows.setdefault(bid[0], []).append((bid, a, c))
    # Begin with the block containing the edge nearest START, then snake outward.
    start_bid = min(
        blocks,
        key=lambda bid: _dist_m(
            start,
            (
                start[0] + (blocks[bid]["centroid"][1] * math.cos(theta) + blocks[bid]["centroid"][0] * math.sin(theta)) / (111_320.0 * max(0.2, math.cos(math.radians(start[1])))),
                start[1] + (-blocks[bid]["centroid"][1] * math.sin(theta) + blocks[bid]["centroid"][0] * math.cos(theta)) / 111_320.0,
            ),
        ),
    )
    row_order = sorted(rows, key=lambda r: (abs(r - start_bid[0]), r))
    rank = {}
    rr = 0
    for row_i, row in enumerate(row_order):
        vals = rows[row]
        vals.sort(key=lambda x: x[1], reverse=bool(row_i % 2))
        # In the first row, start from the cell nearest START rather than a fixed side.
        if row_i == 0 and vals:
            vals.sort(key=lambda x: abs(x[0][1] - start_bid[1]))
        for bid, _, _ in vals:
            rank[bid] = rr
            rr += 1
    for _, _, _, data, _, _ in projected:
        data["sweep_rank"] = rank.get(data.get("sweep_block"), 0)
    return rank

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
    for u, v, path in _minimum_pairing_paths(out, toggle):
        for a, b in zip(path, path[1:]):
            edge_data = dict(min(out.get_edge_data(a, b).values(), key=lambda d: d.get("route_cost", math.inf)))
            edge_data["duplicated"] = True
            out.add_edge(a, b, **edge_data)
    return out



def _edge_departure_bearing(node, other, data: dict) -> float:
    """nodeからotherへ出るときの道路接線方向。"""
    geom = data.get("geometry") if data else None
    if geom is not None and not geom.is_empty:
        coords = _orient_coords(geom.coords, node, other)
        return _bearing_over_distance(list(coords), from_start=True, sample_m=8.0)
    return _bearing(node, other)


def _trail_quality(trail: list[tuple], radial: dict | None = None) -> tuple:
    """Euler候補の現場向け品質。

    1) 来た道への即時折返し、2) 180度Uターン、3) 内側を残したまま遠方へ行く挙動、
    4) 外側から内側への戻り量、5) 総旋回量、の順で小さい候補を採用する。
    """
    immediate_reverse = 0
    hard_uturn = 0
    turn_sum = 0.0
    inward_m = 0.0
    edge_r = []
    for i, (u, v, k, data) in enumerate(trail):
        if radial is not None:
            ru, rv = radial.get(u, 0.0), radial.get(v, 0.0)
            edge_r.append(min(ru, rv))
            inward_m += max(0.0, ru - rv)
        if i + 1 >= len(trail):
            continue
        a, b, _, next_data = trail[i + 1]
        if u == b and v == a:
            immediate_reverse += 1
        incoming = _edge_departure_bearing(u, v, data)
        outgoing = _edge_departure_bearing(a, b, next_data)
        diff = abs(((outgoing - incoming + 540) % 360) - 180)
        turn_sum += diff
        if diff >= 155:
            hard_uturn += 1
    premature = 0.0
    if radial is not None and edge_r:
        suffix_min = [0.0] * len(edge_r)
        m = math.inf
        for i in range(len(edge_r) - 1, -1, -1):
            m = min(m, edge_r[i])
            suffix_min[i] = m
        # 150m以上内側の未処理道路を残して外側へ行く動きを減点。
        for i, r in enumerate(edge_r[:-1]):
            gap = r - suffix_min[i + 1] - 150.0
            if gap > 0:
                premature += gap
    return (immediate_reverse, hard_uturn, round(premature, 2), round(inward_m, 2), round(turn_sum, 2))


def _turn_aware_euler_trail(g: nx.MultiGraph, start, end=None, samples: int = 96, radial: dict | None = None):
    """距離最適性を保ったまま、現場で歩きやすいEuler順序を探索する。"""
    import random
    edge_rows = [(u, v, dict(data)) for u, v, _, data in g.edges(keys=True, data=True)]
    if not edge_rows:
        return []
    best = None
    best_score = None
    # 同一入力なら同じルートになる固定seed。道路数が少ないときは探索数を抑える。
    sample_count = min(samples, max(16, len(edge_rows) // 3))
    for seed in range(sample_count):
        rows = edge_rows[:]
        if seed:
            random.Random(seed * 104729 + len(rows)).shuffle(rows)
        h = nx.MultiGraph()
        for u, v, data in rows:
            h.add_edge(u, v, **data)
        try:
            raw = (list(nx.eulerian_circuit(h, source=start, keys=True))
                   if end is None or start == end
                   else list(nx.eulerian_path(h, source=start, keys=True)))
        except nx.NetworkXError:
            continue
        trail = [(u, v, k, dict(h[u][v][k])) for u, v, k in raw]
        if end is not None and trail and trail[-1][1] != end:
            continue
        score = _trail_quality(trail, radial=radial)
        if best_score is None or score < best_score:
            best_score, best = score, trail
            if score[0] == 0 and score[1] == 0 and score[2] == 0:
                break
    if best is None:
        raise nx.NetworkXError("Euler路候補を生成できません")
    return best



def _undirected_angle_diff(a: float, b: float) -> float:
    """0..90deg. bearingは向きを無視して比較する。"""
    d = abs(((a - b + 90.0) % 180.0) - 90.0)
    return d


def _dominant_street_axis(g: nx.MultiGraph) -> float:
    """格子状街区の主軸を道路延長の重み付きヒストグラムから求める。"""
    bins = [0.0] * 12  # 15度刻み、0..180
    for u, v, data in g.edges(data=True):
        b = _edge_departure_bearing(u, v, data) % 180.0
        idx = int((b + 7.5) // 15.0) % 12
        bins[idx] += max(1.0, float(data.get("length", 0.0)))
    if not any(bins):
        return 0.0
    i = max(range(len(bins)), key=bins.__getitem__)
    return (i * 15.0) % 180.0


def _is_bridge_edge(g: nx.MultiGraph, u, v, k) -> bool:
    """残グラフで今この辺を使うと未処理部を分断するか。並行辺はbridgeではない。"""
    if g.number_of_edges(u, v) > 1:
        return False
    if g.degree(u) <= 1 or g.degree(v) <= 1:
        return False
    try:
        before = nx.number_connected_components(g)
        data = dict(g[u][v][k])
        g.remove_edge(u, v, k)
        after = nx.number_connected_components(g)
        g.add_edge(u, v, key=k, **data)
        return after > before
    except Exception:
        return False


def _pendant_size_after_node(g: nx.MultiGraph, current, neighbor, end=None, limit: int = 40) -> int | None:
    """currentを外すとneighbor側が小さな袋小路群になる場合、その大きさを返す。

    幹道路を進みながら左右の行止まりをその場で処理するための判定。
    """
    if current == neighbor or neighbor not in g:
        return None
    # currentを実際にcopy/removeすると重いので、currentを通らないBFSを上限付きで行う。
    seen = {neighbor}
    q = [neighbor]
    while q and len(seen) <= limit:
        n = q.pop()
        if end is not None and n == end:
            return None
        for m in g.neighbors(n):
            if m == current or m in seen:
                continue
            seen.add(m)
            q.append(m)
    if len(seen) > limit:
        return None
    # current以外の外界へつながっていない小成分なら pendant。
    return len(seen)


def _local_completion_euler_trail(g: nx.MultiGraph, start, end=None, radial: dict | None = None, base_degrees: dict | None = None):
    """現場向けFleury/Hierholzerハイブリッド。

    優先順位:
      1. 今いる幹道路にぶら下がる小さな袋小路群をその場で完了
      2. 未処理部を分断するbridgeを最後まで温存
      3. 格子状街区では同じ主軸をなるべく直進して端まで処理
      4. 即時Uターンを避ける
      5. START近傍を残して遠くへ飛ばない

    Euler化済みgraphなので、全辺を必ず1回ずつ使う順序だけを人向けに並べ替える。
    """
    h = g.copy()
    if base_degrees is None:
        base_degrees = dict(g.degree())
    current = start
    prev = None
    trail = []
    axis = _dominant_street_axis(h)
    block_remaining = {}
    for _, _, _, d in h.edges(keys=True, data=True):
        bid = d.get("sweep_block")
        if bid is not None:
            block_remaining[bid] = block_remaining.get(bid, 0) + 1
    current_block = None
    completed_blocks = set()
    max_steps = h.number_of_edges() + 5

    while h.number_of_edges() and len(trail) < max_steps:
        rows = list(h.edges(current, keys=True, data=True))
        if not rows:
            raise nx.NetworkXError("Euler巡回中に未処理辺から切断されました")
        forced = len(rows) == 1

        scored = []
        for _, v, k, data in rows:
            depart = _edge_departure_bearing(current, v, data)
            axis_diff = min(_undirected_angle_diff(depart, axis), _undirected_angle_diff(depart, (axis + 90.0) % 180.0))
            # 主軸/副軸どちらかに揃う格子道路を優先。斜めの接続道路は後回し。
            grid_penalty = axis_diff * 0.8

            reverse = 0.0
            turn_penalty = 0.0
            if prev is not None:
                pu, pv, _, pdata = prev
                incoming = _edge_departure_bearing(pu, pv, pdata)
                diff = abs(((depart - incoming + 540.0) % 360.0) - 180.0)
                turn_penalty = diff * 0.35
                if v == pu:
                    # 行き止まり以外で「来た道をその場で戻る」は現場では非常に分かりにくい。
                    # 他に未処理辺がある限り事実上禁止し、真の袋小路だけ許容する。
                    reverse = 250.0 if base_degrees.get(current, 0) <= 1 else (1_000_000.0 if len(rows) > 1 else 12_000.0)

            pendant = _pendant_size_after_node(h, current, v, end=end, limit=32)
            # 小さな枝は「後回し」ではなく、その幹道路を通った今クリアする。
            pendant_bonus = -900.0 + (pendant or 0) * 8.0 if pendant is not None else 0.0

            bridge_penalty = 0.0
            if not forced and _is_bridge_edge(h, current, v, k):
                bridge_penalty = 600.0

            outward_penalty = 0.0
            if radial is not None:
                rc = radial.get(current, 0.0)
                rv = radial.get(v, rc)
                # 大きく内側へ戻る動きを抑える。小さな枝往復はpendant_bonusが勝つ。
                outward_penalty = max(0.0, rc - rv - 25.0) * 2.0

            # Strong locality rule: finish the current nearby block before leaving it.
            # This is intentionally stronger than pure distance optimality because field
            # usability matters more than saving a few metres.
            bid = data.get("sweep_block")
            block_penalty = 0.0
            if bid is not None:
                if current_block is not None and bid != current_block and block_remaining.get(current_block, 0) > 0:
                    block_penalty += 4200.0
                if bid == current_block:
                    block_penalty -= 450.0
                if bid in completed_blocks:
                    block_penalty += 6500.0
                # Prefer the serpentine block order, but never at the expense of a local
                # pendant branch that should be cleared immediately.
                rank = float(data.get("sweep_rank", 0.0))
                block_penalty += rank * 5.0

            duplicated_penalty = 55.0 if data.get("duplicated") else 0.0
            score = pendant_bonus + bridge_penalty + reverse + turn_penalty + grid_penalty + outward_penalty + block_penalty + duplicated_penalty
            scored.append((score, float(data.get("length", 0.0)), v, k, data))

        _, _, v, k, data = min(scored, key=lambda x: (x[0], x[1]))
        bid = data.get("sweep_block")
        if current_block is None and bid is not None:
            current_block = bid
        row = (current, v, k, dict(data))
        trail.append(row)
        h.remove_edge(current, v, k)
        if bid is not None:
            block_remaining[bid] = max(0, block_remaining.get(bid, 1) - 1)
            if block_remaining[bid] == 0:
                completed_blocks.add(bid)
                if bid == current_block:
                    current_block = None
            elif current_block is None:
                current_block = bid
        prev = row
        current = v

    if h.number_of_edges():
        raise nx.NetworkXError("Euler巡回を完了できませんでした")
    if end is not None and current != end:
        raise nx.NetworkXError("指定GOALに到達しないEuler巡回になりました")
    return trail


def _local_completion_quality(trail: list[tuple], radial: dict | None = None, base_degrees: dict | None = None) -> tuple:
    """既存qualityに『同じ交差点/局所へ後から戻る』ペナルティを追加。"""
    base = _trail_quality(trail, radial=radial)
    bad_reverse = 0
    if base_degrees is not None:
        for a, b in zip(trail, trail[1:]):
            u, v, _, _ = a
            a2, b2, _, _ = b
            if u == b2 and v == a2 and base_degrees.get(v, 0) > 1:
                bad_reverse += 1
    last_visit = {}
    revisits = 0
    long_revisits = 0
    for i, (u, v, k, data) in enumerate(trail):
        for n in (u, v):
            if n in last_visit:
                gap = i - last_visit[n]
                if gap >= 6:
                    revisits += 1
                if gap >= 18:
                    long_revisits += 1
            last_visit[n] = i
    return (bad_reverse, long_revisits, revisits) + base

def _cluster_exit_node(comp_graph: nx.MultiGraph, cluster_graph: nx.MultiGraph, entry, radial: dict, next_cluster: dict | None):
    """クラスタ途中で引き返さず、次の塊へ抜けやすい交差点を出口にする。"""
    nodes = list(cluster_graph.nodes)
    if len(nodes) <= 1:
        return entry
    # クラスタ外の辺に接続するnodeのみを出口候補にする。なければ全node。
    cluster_edges = {frozenset((u, v)) for u, v in cluster_graph.edges()}
    boundary = []
    for n in nodes:
        if any(frozenset((a, b)) not in cluster_edges for a, b in comp_graph.edges(n)):
            boundary.append(n)
    candidates = boundary or nodes
    if next_cluster:
        target_nodes = set(next_cluster.get("nodes", ()))
        shared = [n for n in candidates if n in target_nodes]
        if shared:
            # 同じ交差点で次クラスタへ入れるならそこが最優先。
            return max(shared, key=lambda n: radial.get(n, 0.0))
        # 次クラスタからの道路距離を一度だけ計算し、最も自然につながる出口を選ぶ。
        try:
            dist_to_next = nx.multi_source_dijkstra_path_length(comp_graph, target_nodes, weight="route_cost")
        except Exception:
            dist_to_next = {}
        entry_r = radial.get(entry, 0.0)
        def score(n):
            regression = max(0.0, entry_r - radial.get(n, 0.0))
            return (dist_to_next.get(n, math.inf) + regression * 2.5, -radial.get(n, 0.0))
        return min(candidates, key=score)
    # 最終クラスタだけは外側へ抜けて終える。
    return max(candidates, key=lambda n: radial.get(n, 0.0))


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
    # transfer も必ず実道路geometryに沿う。道路外の直線は作らない。
    geom = _oriented_edge_geometry(u, v, data)
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
        "sweep_block": data.get("sweep_block") if data else None,
        "sweep_rank": data.get("sweep_rank") if data else None,
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

def _required_subgraph(full_graph: nx.MultiGraph) -> nx.MultiGraph:
    g = nx.MultiGraph()
    for u, v, k, data in full_graph.edges(keys=True, data=True):
        if data.get("required", True):
            g.add_edge(u, v, key=k, **dict(data))
    return g


def _nearest_component_entry(full_graph: nx.MultiGraph, current, nodes: set) -> tuple[tuple, float]:
    """現在地から実道路上で最も近く入れる required component の入口。"""
    if current in nodes:
        return current, 0.0
    lengths = nx.single_source_dijkstra_path_length(full_graph, current, weight="route_cost")
    candidates = [(lengths[n], n) for n in nodes if n in lengths]
    if not candidates:
        raise nx.NetworkXNoPath("配布対象道路へ実道路上で接続できません")
    dist, node = min(candidates)
    return node, dist


def generate_route(roads: list[dict], start_point: tuple[float, float] | None = None) -> dict:
    """v1.1.3 現場優先ルート。

    - 住宅街の道路だけを「配布必須」として網羅する。
    - 太い幹線道路、公園内園路、駐車場通路は必須にせず、必要時だけ移動に使う。
    - 一つの局所成分を終わらせてから次へ進み、行き止まりはその場で処理する。
    - 道路途中の即時折返しは、真の袋小路以外では強く禁止する。
    """
    full_graph = build_graph(roads)
    required_graph = _required_subgraph(full_graph)
    if required_graph.number_of_edges() == 0:
        raise ValueError("配布対象道路がありません")

    source_length = sum(d["length"] for _, _, d in required_graph.edges(data=True))
    dead_ends = [n for n, degree in required_graph.degree() if degree == 1]
    remaining = [set(c) for c in nx.connected_components(required_graph)]
    total_required_components = len(remaining)
    skipped_disconnected_length = 0.0

    # STARTは配布必須道路へスナップする。
    all_required_nodes = set(required_graph.nodes)
    if start_point is None:
        first_start = _nearest_node(required_graph, None)
    else:
        first_start = min(all_required_nodes, key=lambda n: _dist_m(n, start_point))
    current_node = first_start

    steps: list[dict] = []
    duplicated_length = 0.0
    major_duplicated = 0.0
    transfer_length = 0.0
    optimized_components = 0

    while remaining:
        # 今いる場所から「実道路上で」最短の未処理塊を選ぶ。近所を残して遠くへ飛ばない。
        reachable = []
        for idx, nodes in enumerate(remaining):
            try:
                entry, dist = _nearest_component_entry(full_graph, current_node, nodes)
                reachable.append((dist, idx, entry, nodes))
            except nx.NetworkXNoPath:
                continue
        if not reachable:
            # 実道路上で接続できない孤立成分は偽の直線で結ばず、診断値として残す。
            for nodes in remaining:
                sg = required_graph.subgraph(nodes)
                skipped_disconnected_length += sum(d.get("length", 0.0) for _, _, d in sg.edges(data=True))
            remaining.clear()
            break
        _, idx, comp_start, nodes = min(reachable, key=lambda x: x[0])
        remaining.pop(idx)

        # 次の塊へ行くための移動も実道路上のみ。公園/幹線はここで必要な時だけ使われる。
        if current_node != comp_start:
            transfer_length += _append_shortest_path_steps(full_graph, current_node, comp_start, steps, optimized_components + 1)

        comp_graph = required_graph.subgraph(nodes).copy()
        # v1.1.7: align coarse local blocks to the dominant street axis.  The graph is
        # still globally eulerized once, so this changes traversal order rather than
        # introducing new duplicated roads.
        _assign_local_sweep_blocks(comp_graph, comp_start, cell_m=220.0)
        radial = _node_radial_distances(comp_graph, comp_start)
        base_degrees = dict(comp_graph.degree())
        raw_end_candidates = [n for n in comp_graph.nodes if comp_graph.degree(n) != 2 and n != comp_start] or [n for n in comp_graph.nodes if n != comp_start]
        # 「最遠点固定」だと途中Uターンを強制する場合がある。外側の候補を複数試し、
        # Uターンなし→局所完結→戻りの少なさ、の順で最良GOALを選ぶ。
        end_candidates = sorted(raw_end_candidates, key=lambda n: radial.get(n, 0.0), reverse=True)[:10]
        if not end_candidates:
            end_candidates = [comp_start]

        route_options = []
        for candidate_end in end_candidates:
            try:
                routed = _eulerize_open(comp_graph, comp_start, candidate_end)
            except Exception:
                continue
            candidate_trails = []
            try:
                candidate_trails.append(_local_completion_euler_trail(routed, comp_start, candidate_end, radial=radial, base_degrees=base_degrees))
            except nx.NetworkXError:
                pass
            try:
                candidate_trails.append(_turn_aware_euler_trail(routed, comp_start, candidate_end, samples=96, radial=radial))
            except nx.NetworkXError:
                pass
            for t in candidate_trails:
                quality = _local_completion_quality(t, radial=radial, base_degrees=base_degrees)
                # GOALは外側を優先。ただし途中Uターンをなくせるならそちらが最優先。
                outward_bonus = -radial.get(candidate_end, 0.0) / 5000.0
                route_options.append((quality + (outward_bonus,), candidate_end, t))
        if not route_options:
            raise ValueError("巡回順を生成できませんでした")
        _, comp_end, trail = min(route_options, key=lambda x: x[0])

        for u, v, k, data in trail:
            st = _step(u, v, data, len(steps) + 1, component=optimized_components + 1)
            steps.append(st)
            if st["duplicated"]:
                duplicated_length += st["length_m"]
                if st["highway"] in {"primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link"}:
                    major_duplicated += st["length_m"]
        current_node = comp_end
        optimized_components += 1

    if not steps:
        raise ValueError("巡回ルートを生成できませんでした")

    coords = []
    for st in steps:
        c = list(st["geometry"].coords)
        if not coords:
            coords.extend(c)
        elif _dist_m(coords[-1], c[0]) <= 1.5:
            coords.extend(c[1:])
        else:
            # 実道路上のtransferを入れているため通常ここには来ない。
            break
    route = LineString(coords) if len(coords) >= 2 else steps[0]["geometry"]
    route_length = sum(st["length_m"] for st in steps)
    navigation_legs = build_navigation_legs(steps)

    # 診断: 真の袋小路以外で即時折返しが何回あるか。
    midroad_uturns = 0
    req_deg = dict(required_graph.degree())
    for a, b in zip(steps, steps[1:]):
        if a["from"] == b["to"] and a["to"] == b["from"] and req_deg.get(a["to"], 0) > 1:
            midroad_uturns += 1

    return {
        "geometry": route,
        "route_steps": steps,
        "navigation_legs": navigation_legs,
        "start_point": Point(first_start),
        "requested_start": Point(start_point) if start_point else None,
        "source_edges": required_graph.number_of_edges(),
        "route_edges": len(steps),
        "source_length_m": round(source_length, 1),
        "route_length_m": round(route_length, 1),
        "transfer_length_m": round(transfer_length, 1),
        "duplicated_length_m": round(duplicated_length, 1),
        "major_road_duplicated_m": round(major_duplicated, 1),
        "duplication_ratio": round((route_length - transfer_length) / max(source_length, 1.0), 3),
        "connected_nodes": required_graph.number_of_nodes(),
        "component_count": total_required_components,
        "cluster_count": optimized_components,
        "skipped_disconnected_length_m": round(skipped_disconnected_length, 1),
        "dead_end_count": len(dead_ends),
        "midroad_uturn_count": midroad_uturns,
        "routing_strategy": "block-completion-comb-grid-sweep",
        "routing_strategy_version": "1.1.7",
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
