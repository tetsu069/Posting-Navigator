from __future__ import annotations

import math
import networkx as nx
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

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
    """Fast local distance in metres for town-scale routing.

    v1.0.17 deliberately avoids pyproj.Geod.inv() here.  This helper is called
    tens of thousands of times while ordering disconnected road components and
    while building navigation legs.  For distances of a few kilometres the
    equirectangular approximation is far more than accurate enough for routing
    heuristics and removes a major CPU/memory pressure point on Render Free.
    """
    lon1, lat1 = a
    lon2, lat2 = b
    latm = math.radians((lat1 + lat2) * 0.5)
    dx = math.radians(lon2 - lon1) * math.cos(latm) * 6371008.8
    dy = math.radians(lat2 - lat1) * 6371008.8
    return math.hypot(dx, dy)


def _bbox_lower_bound_m(bbox: tuple[float, float, float, float], point: tuple[float, float]) -> float:
    """Lower-bound distance from a point to a lon/lat bbox, using _dist_m."""
    minx, miny, maxx, maxy = bbox
    x = min(max(point[0], minx), maxx)
    y = min(max(point[1], miny), maxy)
    return _dist_m(point, (x, y))


def _component_meta(nodes: set[tuple[float, float]]) -> dict:
    xs = [n[0] for n in nodes]
    ys = [n[1] for n in nodes]
    return {
        "nodes": nodes,
        "bbox": (min(xs), min(ys), max(xs), max(ys)),
        "center": ((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5),
    }


def _nearest_node_fast(nodes, point: tuple[float, float]) -> tuple[float, float]:
    """Nearest node with the inexpensive town-scale distance metric."""
    return min(nodes, key=lambda n: _dist_m(n, point))


def _snap(p: tuple[float, float], precision: int = 6) -> tuple[float, float]:
    return (round(p[0], precision), round(p[1], precision))


class _RoadSourceIndex:
    """Spatial index used while reconstructing graph edges after noding.

    v1.0.17 searched *every* road with geometry.distance(midpoint) for every
    tiny segment emitted by unary_union().  Dense blocks can create thousands
    of segments, so that O(segments x roads) scan becomes both CPU- and
    allocation-heavy.  STRtree.query_nearest() returns the nearest geometry
    index directly in Shapely 2.x, reducing each lookup to a spatial-index
    query.
    """

    def __init__(self, roads: list[dict]):
        if not roads:
            raise ValueError("道路データが空です")
        self.roads = roads
        self.geometries = [r["geometry"] for r in roads]
        self.tree = STRtree(self.geometries)

    def source_for_segment(self, segment: LineString) -> dict:
        midpoint = segment.interpolate(0.5, normalized=True)
        idx = self.tree.query_nearest(midpoint)
        # Shapely 2.x commonly returns a one-element ndarray for scalar input.
        # Pull the scalar out explicitly so NumPy does not allocate/warn on an
        # implicit array-to-scalar conversion.
        if hasattr(idx, "shape") and getattr(idx, "size", 0):
            index = int(idx.flat[0])
        elif hasattr(idx, "__len__"):
            if not len(idx):
                raise ValueError("道路空間インデックスから最近傍道路を取得できませんでした")
            index = int(idx[0])
        else:
            index = int(idx)
        return self.roads[index]


def _dedupe_roads_by_geometry(roads: list[dict]) -> list[dict]:
    """Remove exact duplicate road geometries before expensive noding.

    Worker-area target roads are sometimes also present in connector_roads.
    Feeding both copies into unary_union/STRtree wastes memory while adding no
    connectivity.  Keep one copy per geometry WKB and prefer posting_target
    metadata when duplicate records differ.
    """
    by_wkb: dict[bytes, dict] = {}
    for road in roads:
        geom = road.get("geometry")
        if geom is None or geom.is_empty:
            continue
        key = bytes(geom.wkb)
        prev = by_wkb.get(key)
        if prev is None or (not prev.get("posting_target", True) and road.get("posting_target", True)):
            by_wkb[key] = road
    return list(by_wkb.values())


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
                "posting_target": bool(d1.get("posting_target", True) and d2.get("posting_target", True)),
                "connector_only": bool(d1.get("connector_only") or d2.get("connector_only")),
                "residential_score": max(float(d1.get("residential_score", 0.5)), float(d2.get("residential_score", 0.5))),
                "nonresidential_overlap": max(float(d1.get("nonresidential_overlap", 0.0)), float(d2.get("nonresidential_overlap", 0.0))),
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




def _edge_axis_angle_deg(graph: nx.MultiGraph, node: tuple[float, float]) -> list[float]:
    """Return local road-axis bearings (0-180 deg) for edges incident to node."""
    out: list[float] = []
    for _, other, data in graph.edges(node, data=True):
        geom = data.get("geometry")
        if geom is not None and not geom.is_empty:
            coords = _orient_coords(geom.coords, node, other)
            # Use a point a few metres into the edge when possible so tiny
            # coordinate noise at the endpoint does not dominate the bearing.
            target = coords[-1]
            for c in coords[1:]:
                if _dist_m(node, c) >= 3.0:
                    target = c
                    break
        else:
            target = other
        dx = (target[0] - node[0]) * math.cos(math.radians((target[1] + node[1]) * 0.5))
        dy = target[1] - node[1]
        if abs(dx) + abs(dy) < 1e-15:
            continue
        bearing = math.degrees(math.atan2(dx, dy)) % 180.0
        out.append(bearing)
    return out


def _axis_diff_deg(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _safe_snap_pair(graph: nx.MultiGraph, a: tuple[float, float], b: tuple[float, float], max_gap_m: float, strict_long_gap: bool = False) -> bool:
    """Decide whether a 1.25-3m gap is a likely broken junction, not parallel roads."""
    gap = _dist_m(a, b)
    if gap <= 1.25 or gap > max_gap_m:
        return gap <= 1.25
    # Rescue snapping is intentionally limited to dangling/near-dangling nodes.
    # This prevents nearby mid-block vertices on parallel streets from merging.
    if graph.degree(a) > 2 or graph.degree(b) > 2:
        return False
    aa = _edge_axis_angle_deg(graph, a)
    bb = _edge_axis_angle_deg(graph, b)
    if not aa or not bb:
        return False
    dx = (b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) * 0.5))
    dy = b[1] - a[1]
    gap_axis = math.degrees(math.atan2(dx, dy)) % 180.0
    a_align = min(_axis_diff_deg(x, gap_axis) for x in aa)
    b_align = min(_axis_diff_deg(x, gap_axis) for x in bb)
    cross = min(_axis_diff_deg(x, y) for x in aa for y in bb)
    # v1.0.21: 3mを超える救済は「同一道路の切れ目」に限定する。
    # 両端が行き止まりで、両側の道路軸がギャップ方向を向き、かつ互いに
    # ほぼ同一直線の場合だけ許可する。近接並行道路・道路を横切る短絡は拒否。
    if strict_long_gap or gap > 3.0:
        if graph.degree(a) != 1 or graph.degree(b) != 1:
            return False
        return a_align <= 22.0 and b_align <= 22.0 and cross <= 25.0
    # Normal broken continuation: at least one road points toward the gap.
    # Perpendicular junctions can also be rescued when the two road axes are
    # clearly different.  Two side-by-side parallel roads are rejected.
    if min(a_align, b_align) <= 40.0:
        return True
    if cross >= 35.0 and min(a_align, b_align) <= 70.0:
        return True
    return False


def _rebuild_graph_with_mapping(graph: nx.MultiGraph, mapping: dict[tuple[float, float], tuple[float, float]]) -> nx.MultiGraph:
    """Rebuild a road graph once after deciding all endpoint snaps.

    v1.0.20 deliberately separates *decision* from *mutation*.  Older rescue
    snapping repeatedly rebuilt the whole MultiGraph after each accepted pair,
    which caused very high temporary memory use on Render Free.  Here every
    accepted endpoint pair is collected first and the graph is reconstructed a
    single time.
    """
    if not mapping or all(mapping.get(n, n) == n for n in graph.nodes):
        return graph
    out = nx.MultiGraph()
    for u, v, data in graph.edges(data=True):
        nu = mapping.get(u, u)
        nv = mapping.get(v, v)
        if nu == nv:
            continue
        d = dict(data)
        geom = d.get("geometry")
        if geom is not None:
            try:
                coords = _orient_coords(geom.coords, u, v)
            except Exception:
                coords = [u, v]
            if len(coords) >= 2:
                coords[0] = nu
                coords[-1] = nv
                d["geometry"] = LineString(coords)
                new_len = sum(_dist_m(a, b) for a, b in zip(coords, coords[1:]))
                old_len = float(data.get("length", 0) or 0)
                old_cost = float(data.get("route_cost", new_len) or new_len)
                factor = old_cost / old_len if old_len > 0 else 1.0
                d["length"] = new_len
                d["route_cost"] = new_len * factor
        out.add_edge(nu, nv, **d)
    return out


def _conditional_snap_components(graph: nx.MultiGraph, max_gap_m: float = 3.0, min_gap_m: float = 1.25, strict_long_gap: bool = False) -> nx.MultiGraph:
    """Low-memory one-pass rescue snapping for small false OSM junction gaps.

    Strict sub-1.25m snapping has already happened in ``build_graph``.  This
    second pass considers only dangling / near-dangling nodes from *different*
    original connected components and allows 1.25--3m rescue snaps when road
    directions indicate the same junction.  Candidate pairs are chosen first,
    then the graph is rebuilt once.  No straight connector edge is invented.
    """
    if max_gap_m <= min_gap_m or graph.number_of_nodes() < 2:
        return graph

    comps = [set(c) for c in nx.connected_components(graph)]
    if len(comps) <= 1:
        return graph
    node_comp = {n: i for i, c in enumerate(comps) for n in c}
    candidates = [n for n in graph.nodes if graph.degree(n) <= 2]
    if len(candidates) < 2:
        return graph

    mean_lat = sum(n[1] for n in candidates) / len(candidates)
    cell_lat = max_gap_m / 111_320.0
    cell_lon = max_gap_m / max(1.0, 111_320.0 * math.cos(math.radians(mean_lat)))

    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    possible: list[tuple[float, tuple[float, float], tuple[float, float]]] = []
    for a in candidates:
        ix = int(math.floor(a[0] / cell_lon))
        iy = int(math.floor(a[1] / cell_lat))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for b in buckets.get((ix + dx, iy + dy), ()):
                    if node_comp[a] == node_comp[b]:
                        continue
                    gap = _dist_m(a, b)
                    if min_gap_m < gap <= max_gap_m and _safe_snap_pair(graph, a, b, max_gap_m, strict_long_gap=strict_long_gap):
                        possible.append((gap, a, b))
        buckets.setdefault((ix, iy), []).append(a)

    if not possible:
        return graph

    # Greedy shortest-first matching.  Each endpoint participates in at most one
    # rescue snap, preventing a dense cluster of parallel/nearby endpoints from
    # collapsing into a single artificial junction.
    possible.sort(key=lambda x: x[0])
    used: set[tuple[float, float]] = set()
    mapping: dict[tuple[float, float], tuple[float, float]] = {}
    accepted = 0
    for _, a, b in possible:
        if a in used or b in used:
            continue
        keep, drop = (a, b) if a <= b else (b, a)
        mapping[drop] = keep
        used.add(a)
        used.add(b)
        accepted += 1

    if not accepted:
        return graph
    return _rebuild_graph_with_mapping(graph, mapping)

def _snap_graph_nodes(graph: nx.MultiGraph, tolerance_m: float = 1.25) -> nx.MultiGraph:
    """Merge near-identical road endpoints without inventing transfer lines.

    OSM ways, administrative clipping and floating-point transforms can leave
    endpoints that represent the same junction a few decimetres apart.  Older
    versions required exact coordinate equality, splitting one real road
    network into false components.  We cluster only very close nodes and move
    the *endpoint coordinate* of the existing road geometry to the cluster
    representative; no standalone straight connector edge is created.
    """
    if tolerance_m <= 0 or graph.number_of_nodes() < 2:
        return graph
    # ~metre grid in lon/lat. Neighbouring cells are checked with _dist_m.
    mean_lat = sum(n[1] for n in graph.nodes) / max(1, graph.number_of_nodes())
    cell_lat = tolerance_m / 111_320.0
    cell_lon = tolerance_m / max(1.0, 111_320.0 * math.cos(math.radians(mean_lat)))
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    mapping: dict[tuple[float, float], tuple[float, float]] = {}
    for node in sorted(graph.nodes, key=lambda n: (n[1], n[0])):
        ix = int(math.floor(node[0] / cell_lon)); iy = int(math.floor(node[1] / cell_lat))
        candidates = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                candidates.extend(buckets.get((ix+dx, iy+dy), []))
        near = [r for r in candidates if _dist_m(node, r) <= tolerance_m]
        rep = min(near, key=lambda r: _dist_m(node, r)) if near else node
        mapping[node] = rep
        if rep == node:
            buckets.setdefault((ix, iy), []).append(rep)
    if all(k == v for k, v in mapping.items()):
        return graph
    out = nx.MultiGraph()
    for u, v, data in graph.edges(data=True):
        nu, nv = mapping[u], mapping[v]
        if nu == nv:
            continue
        d = dict(data)
        geom = d.get('geometry')
        if geom is not None and not geom.is_empty:
            coords = _orient_coords(geom.coords, u, v)
            coords[0] = nu; coords[-1] = nv
            d['geometry'] = LineString(coords)
            d['length'] = sum(_dist_m(a,b) for a,b in zip(coords, coords[1:]))
            # Preserve penalty ratio when possible.
            old_len = float(data.get('length', 0) or 0)
            old_cost = float(data.get('route_cost', d['length']) or d['length'])
            factor = old_cost / old_len if old_len > 0 else 1.0
            d['route_cost'] = d['length'] * factor
        out.add_edge(nu, nv, **d)
    return out



def _mark_posting_targets(shared_graph: nx.MultiGraph, target_roads: list[dict], tolerance_m: float = 0.9) -> None:
    """Classify shared-network edges against the actual distribution geometries.

    Connector roads can carry stale/default posting_target metadata, and worker
    polygons often clip a target road while the connector layer retains the
    full original way.  Therefore target membership must be derived spatially
    *after* the common graph is noded, not copied from connector metadata.
    """
    geoms = [r['geometry'] for r in target_roads if r.get('geometry') is not None and not r['geometry'].is_empty]
    if not geoms:
        return
    tree = STRtree(geoms)
    # convert metres to a conservative degree tolerance for the point-distance
    # check; actual _dist_m is used on nearest points only if needed.
    deg_tol = tolerance_m / 100_000.0
    for u, v, data in shared_graph.edges(data=True):
        geom = data.get('geometry')
        if geom is None or geom.is_empty:
            data['posting_target'] = False
            continue
        mid = geom.interpolate(0.5, normalized=True)
        idx = tree.query_nearest(mid)
        if hasattr(idx, 'shape') and getattr(idx, 'size', 0):
            j = int(idx.flat[0])
        elif hasattr(idx, '__len__'):
            j = int(idx[0]) if len(idx) else -1
        else:
            j = int(idx)
        if j < 0:
            data['posting_target'] = False
            continue
        nearest = geoms[j]
        # Shapely works in degrees here.  At Tokyo this threshold is under 1 m
        # and only classifies an edge as target when it lies on the target way.
        data['posting_target'] = bool(nearest.distance(mid) <= deg_tol)
        if data['posting_target']:
            data['connector_only'] = False

def _posting_subgraph(shared_graph: nx.MultiGraph) -> nx.MultiGraph:
    """Extract distribution edges from the already-noded shared road graph."""
    g = nx.MultiGraph()
    for u, v, data in shared_graph.edges(data=True):
        if not data.get('posting_target', True):
            continue
        g.add_edge(u, v, **dict(data))
    if g.number_of_edges() == 0:
        raise ValueError('住宅・配布対象道路がありません')
    return _simplify_degree_two(g)

def build_graph(roads: list[dict], *, simplify: bool = True, snap_tolerance_m: float = 0.0) -> nx.MultiGraph:
    """道路を実交差点でnode化し、全連結成分を保持したMultiGraphへ変換する。"""
    if not roads:
        raise ValueError("道路データが空です")
    roads = _dedupe_roads_by_geometry(roads)
    graph = nx.MultiGraph()
    source_index = _RoadSourceIndex(roads)
    merged = unary_union([road["geometry"] for road in roads])
    lines = [merged] if merged.geom_type == "LineString" else [g for g in getattr(merged, "geoms", []) if g.geom_type == "LineString"]
    for line in lines:
        coords = list(line.coords)
        for a, b in zip(coords, coords[1:]):
            u, v = _snap(a), _snap(b)
            if u == v:
                continue
            segment = LineString([u, v])
            source = source_index.source_for_segment(segment)
            length = _dist_m(u, v)
            if length < 0.25:
                continue
            highway = source.get("highway", "")
            penalty = HIGHWAY_PENALTY.get(highway, 1.5)
            residential_score = float(source.get("residential_score", 0.5))
            nonres_overlap = float(source.get("nonresidential_overlap", 0.0))
            posting_target = bool(source.get("posting_target", True))
            connector_only = bool(source.get("connector_only", False))
            # 余分な往復/成分間移動では住宅沿いを優先し、公園等connector-only道路を強く避ける。
            density_factor = 1.30 - 0.50 * max(0.0, min(1.0, residential_score))
            if nonres_overlap >= 0.45:
                density_factor *= 2.5
            if not posting_target:
                density_factor *= 4.0
            if connector_only:
                # Boundary-outside stubs exist only to make a real walking connection.
                # Strongly discourage them unless they are genuinely needed.
                density_factor *= 2.0
            graph.add_edge(
                u, v,
                length=length,
                route_cost=length * penalty * density_factor,
                highway=highway,
                name=source.get("name", ""),
                osm_id=source.get("id"),
                boundary_near=bool(source.get("boundary_near", False)),
                posting_target=posting_target,
                connector_only=connector_only,
                residential_score=residential_score,
                nonresidential_overlap=nonres_overlap,
                geometry=segment,
            )
    if graph.number_of_edges() == 0:
        raise ValueError("道路グラフが空です")
    if snap_tolerance_m > 0:
        graph = _snap_graph_nodes(graph, snap_tolerance_m)
    return _simplify_degree_two(graph) if simplify else graph

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
    """Order disconnected road components without all-node/all-component scans.

    v1.0.14 repeatedly evaluated every node of every remaining component with
    pyproj.Geod.inv().  On dense Tokyo blocks this became the dominant hot path
    and could terminate the Render Free worker.  v1.0.17 caches each component
    bbox and only performs node-level nearest checks for the few components whose
    bboxes are already closest to the current position.
    """
    components = [set(c) for c in nx.connected_components(graph)]
    if len(components) <= 1:
        return components

    metas = [_component_meta(c) for c in components]

    if start_point is None:
        first_meta = max(metas, key=lambda m: graph.subgraph(m["nodes"]).number_of_edges())
        # Deterministic representative for the first component.
        current = min(first_meta["nodes"], key=lambda n: (n[1], n[0]))
    else:
        shortlist = sorted(metas, key=lambda m: _bbox_lower_bound_m(m["bbox"], start_point))[:4]
        first_meta = min(
            shortlist,
            key=lambda m: _dist_m(_nearest_node_fast(m["nodes"], start_point), start_point),
        )
        current = _nearest_node_fast(first_meta["nodes"], start_point)

    order = [first_meta["nodes"]]
    remaining = [m for m in metas if m is not first_meta]

    while remaining:
        # Bbox lower bounds cheaply eliminate almost all components.  Four is a
        # small enough shortlist to keep memory/CPU bounded while still choosing
        # the genuinely nearest component in normal street networks.
        shortlist = sorted(remaining, key=lambda m: _bbox_lower_bound_m(m["bbox"], current))[:4]
        nearest_pairs = [(_nearest_node_fast(m["nodes"], current), m) for m in shortlist]
        entry, nxt_meta = min(nearest_pairs, key=lambda pair: _dist_m(pair[0], current))
        order.append(nxt_meta["nodes"])
        current = entry
        remaining.remove(nxt_meta)

    return order


def _oriented_edge_geometry(u, v, data: dict) -> LineString:
    geom = data.get("geometry") if data else None
    if geom is None:
        return LineString([u, v])
    coords = _orient_coords(geom.coords, u, v)
    return LineString(coords)


def _step(u, v, data: dict, seq: int, *, transfer: bool = False, component: int = 1) -> dict:
    # v1.0.11: transfer も必ず実道路 geometry を使う。data が無い場合だけ
    # 旧互換として直線になるが、本番の generate_route からは data 無し transfer を作らない。
    geom = _oriented_edge_geometry(u, v, data) if data else LineString([u, v])
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
        "posting_target": bool(data.get("posting_target", True)) if data else True,
        "connector_only": bool(data.get("connector_only", False)) if data else False,
        "residential_score": float(data.get("residential_score", 0.5)) if data else 0.5,
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

def _connector_path_steps(connector_graph: nx.MultiGraph, start, goal, *, seq_start: int, component: int) -> list[dict]:
    """2つの巡回成分を、実在する道路エッジだけで接続する。

    直線ショートカットは一切作らない。接続できない場合は生成を失敗させる。
    """
    if start == goal:
        return []
    if start not in connector_graph or goal not in connector_graph:
        raise ValueError("巡回区間を実道路上で接続できません（接続ノードが道路網にありません）")
    try:
        path = nx.shortest_path(connector_graph, start, goal, weight="route_cost")
    except (nx.NetworkXNoPath, nx.NodeNotFound) as exc:
        try:
            ca = nx.node_connected_component(connector_graph, start) if start in connector_graph else set()
            cb = nx.node_connected_component(connector_graph, goal) if goal in connector_graph else set()
            gap = min((_dist_m(a, b) for a in ca for b in cb), default=math.inf)
            detail = f"（接続成分間の最短ギャップ約{gap:.1f}m）" if math.isfinite(gap) else ""
        except Exception:
            detail = ""
        raise ValueError("巡回区間同士を実道路上で接続できません" + detail + "。OSM道路の接続状態を確認してください") from exc
    out: list[dict] = []
    seq = seq_start
    for a, b in zip(path, path[1:]):
        candidates = connector_graph.get_edge_data(a, b)
        if not candidates:
            raise ValueError("道路接続経路の復元に失敗しました")
        data = dict(min(candidates.values(), key=lambda d: d.get("route_cost", math.inf)))
        # transfer は配布対象ではない移動区間だが、形状は必ずOSM道路そのもの。
        data["duplicated"] = False
        out.append(_step(a, b, data, seq, transfer=True, component=component))
        seq += 1
    return out


def generate_route(roads: list[dict], start_point: tuple[float, float] | None = None,
                   connector_roads: list[dict] | None = None) -> dict:
    """全対象道路を巡回する順序付きルートを生成する。

    v1.0.11では非連結な担当道路成分間の移動も、直線ではなく
    connector_roads（通常は町丁目全体のOSM道路）上の最短経路を使う。
    実道路で接続できない場合は偽ルートを描かずエラーにする。
    """
    target_roads = [r for r in roads if r.get("posting_target", True)]
    if not target_roads:
        raise ValueError("住宅・配布対象道路がありません")

    # v1.0.18: 配布網と移動網を別々にnode化しない。
    # 同じ道路集合を一度だけnode化し、その上でposting_target edgeを抽出することで、
    # クリップ端点や交差点の座標差による「偽の非連結」を防ぐ。
    connector_source = _dedupe_roads_by_geometry(list(connector_roads or roads) + list(target_roads))
    connector_graph = build_graph(connector_source, simplify=False, snap_tolerance_m=1.25)
    connector_graph = _conditional_snap_components(connector_graph, max_gap_m=3.0)
    # v1.0.21: 3〜8mのOSM/クリップ由来ギャップを第二段階で救済。
    # 長距離側は非常に厳しい「同一直線のdangling endpoint同士」判定のみ。
    # 新しいconnector edgeは作らず、既存道路端点を共通nodeへ揃える。
    if nx.number_connected_components(connector_graph) > 1:
        connector_graph = _conditional_snap_components(
            connector_graph, max_gap_m=8.0, min_gap_m=3.0, strict_long_gap=True
        )
    _mark_posting_targets(connector_graph, target_roads)
    source_graph = _posting_subgraph(connector_graph)
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
        if steps:
            prev = steps[-1]["to"]
            if prev != comp_start:
                transfer_steps = _connector_path_steps(
                    connector_graph, prev, comp_start, seq_start=len(steps) + 1, component=component_index
                )
                steps.extend(transfer_steps)
                transfer_length += sum(float(x["length_m"]) for x in transfer_steps)
        circuit = list(nx.eulerian_circuit(comp, source=comp_start, keys=True))
        for u, v, k in circuit:
            data = comp[u][v][k]
            st = _step(u, v, data, len(steps) + 1, component=component_index)
            steps.append(st)
            if st["duplicated"]:
                duplicated_length += st["length_m"]
                if st["highway"] in {"primary", "primary_link", "secondary", "secondary_link"}:
                    major_duplicated += st["length_m"]
        current_point = comp_start

    if not steps or first_start is None:
        raise ValueError("巡回ルートを生成できませんでした")

    # すべての隣接stepが実座標で連続していることを検証する。
    # ここで不連続ならLineString化してはいけない。
    for a, b in zip(steps, steps[1:]):
        if _dist_m(a["to"], b["from"]) > 0.75:
            raise ValueError("巡回ルートに道路外の不連続区間が検出されました。生成を中止します")

    coords: list[tuple[float, float]] = []
    for st in steps:
        c = list(st["geometry"].coords)
        if not coords:
            coords.extend(c)
        else:
            if _dist_m(coords[-1], c[0]) > 0.75:
                raise ValueError("道路geometry同士が接続していないため、偽の直線を作らず生成を中止しました")
            coords.extend(c[1:])
    route = LineString(coords)
    route_length = sum(st["length_m"] for st in steps)
    covered_steps = [st for st in steps if not st["transfer"]]
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
        "road_only_route": True,
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

# --- v1.0.10: geographic worker-area partitioning ---
from shapely.geometry import box as _box


def _road_length_m(road: dict) -> float:
    coords = list(road["geometry"].coords)
    return sum(_dist_m(a, b) for a, b in zip(coords, coords[1:]))


def _clip_roads_to_polygon(roads: list[dict], polygon, *, min_length_m: float = 1.0) -> list[dict]:
    """Clip road geometries to a worker polygon, avoiding shared out-of-area branches."""
    out: list[dict] = []
    for road in roads:
        inter = road["geometry"].intersection(polygon)
        geoms = []
        if inter.is_empty:
            continue
        if inter.geom_type == "LineString":
            geoms = [inter]
        elif inter.geom_type == "MultiLineString":
            geoms = list(inter.geoms)
        elif inter.geom_type == "GeometryCollection":
            geoms = [g for g in inter.geoms if g.geom_type == "LineString"]
        for geom in geoms:
            r = dict(road)
            r["geometry"] = geom
            if _road_length_m(r) >= min_length_m:
                out.append(r)
    return out


def _weighted_split_threshold(roads: list[dict], axis: int, target_fraction: float) -> float:
    items = []
    for road in roads:
        p = road["geometry"].interpolate(0.5, normalized=True)
        coord = p.x if axis == 0 else p.y
        items.append((coord, max(_road_length_m(road), 0.1)))
    items.sort(key=lambda x: x[0])
    total = sum(w for _, w in items) or 1.0
    target = total * target_fraction
    acc = 0.0
    for coord, w in items:
        acc += w
        if acc >= target:
            return coord
    return items[-1][0]


def _split_partition(polygon, roads: list[dict], left_count: int, total_count: int):
    minx, miny, maxx, maxy = polygon.bounds
    xs = [r["geometry"].interpolate(0.5, normalized=True).x for r in roads]
    ys = [r["geometry"].interpolate(0.5, normalized=True).y for r in roads]
    xspread = (max(xs) - min(xs)) if xs else (maxx - minx)
    yspread = (max(ys) - min(ys)) if ys else (maxy - miny)
    axis = 0 if xspread >= yspread else 1
    frac = left_count / total_count
    threshold = _weighted_split_threshold(roads, axis, frac)
    pad = max(maxx - minx, maxy - miny, 1e-6) * 2 + 1e-5
    if axis == 0:
        left_shape = polygon.intersection(_box(minx-pad, miny-pad, threshold, maxy+pad))
        right_shape = polygon.intersection(_box(threshold, miny-pad, maxx+pad, maxy+pad))
    else:
        left_shape = polygon.intersection(_box(minx-pad, miny-pad, maxx+pad, threshold))
        right_shape = polygon.intersection(_box(minx-pad, threshold, maxx+pad, maxy+pad))
    left_roads = _clip_roads_to_polygon(roads, left_shape)
    right_roads = _clip_roads_to_polygon(roads, right_shape)
    # Defensive fallback for pathological split values.
    if not left_roads or not right_roads:
        threshold = ((minx + maxx) / 2) if axis == 0 else ((miny + maxy) / 2)
        if axis == 0:
            left_shape = polygon.intersection(_box(minx-pad, miny-pad, threshold, maxy+pad))
            right_shape = polygon.intersection(_box(threshold, miny-pad, maxx+pad, maxy+pad))
        else:
            left_shape = polygon.intersection(_box(minx-pad, miny-pad, maxx+pad, threshold))
            right_shape = polygon.intersection(_box(minx-pad, threshold, maxx+pad, maxy+pad))
        left_roads = _clip_roads_to_polygon(roads, left_shape)
        right_roads = _clip_roads_to_polygon(roads, right_shape)
    return left_shape, left_roads, right_shape, right_roads


def partition_worker_areas(boundary, roads: list[dict], workers: int) -> list[dict]:
    """Split one town polygon into geographically contiguous worker areas.

    The split target is candidate-road workload, not a simple slice of the finished route.
    This keeps each worker in a compact, contiguous part of the town.
    """
    if workers < 1:
        raise ValueError("担当者数は1以上で指定してください")
    if workers == 1:
        return [{"worker_id": 1, "polygon": boundary, "roads": roads}]

    def rec(poly, rs, count):
        if count <= 1:
            return [(poly, rs)]
        a = count // 2
        b = count - a
        lp, lr, rp, rr = _split_partition(poly, rs, a, count)
        return rec(lp, lr, a) + rec(rp, rr, b)

    raw = rec(boundary, roads, workers)
    out = []
    for i, (poly, rs) in enumerate(raw, start=1):
        if poly.is_empty or not rs:
            raise ValueError(f"担当{i:02d}のエリア分割に失敗しました")
        out.append({"worker_id": i, "polygon": poly, "roads": rs})
    return out


def generate_worker_routes(boundary, roads: list[dict], workers: int,
                           start_point: tuple[float, float] | None = None,
                           households: int | None = None,
                           connector_roads: list[dict] | None = None) -> list[dict]:
    """Generate an independent Chinese-Postman route for each geographic worker area."""
    target_roads = [r for r in roads if r.get("posting_target", True)]
    parts = partition_worker_areas(boundary, target_roads, workers)
    assignments = []
    total_source = 0.0
    generated = []
    for part in parts:
        wr = generate_route(
            part["roads"], start_point=start_point,
            connector_roads=(connector_roads if connector_roads is not None else roads),
        )
        total_source += float(wr.get("source_length_m", 0))
        generated.append((part, wr))
    for part, wr in generated:
        share = (float(wr.get("source_length_m", 0)) / total_source) if total_source else 1 / workers
        est_households = round(households * share) if households else None
        a = {
            "worker_id": part["worker_id"],
            "name": f"担当{part['worker_id']:02d}",
            "geometry": wr["geometry"],
            "worker_area": part["polygon"],
            "start_point": wr["start_point"],
            "end_point": Point(list(wr["geometry"].coords)[-1]),
            "start_lon": wr["start_point"].x,
            "start_lat": wr["start_point"].y,
            "end_lon": list(wr["geometry"].coords)[-1][0],
            "end_lat": list(wr["geometry"].coords)[-1][1],
            "length_m": float(wr["route_length_m"]),
            "source_length_m": float(wr["source_length_m"]),
            "duplication_ratio": wr.get("duplication_ratio"),
            "component_count": wr.get("component_count", 1),
            "estimated_households": est_households,
            "route_steps": wr.get("route_steps", []),
            "navigation_legs": wr.get("navigation_legs", []),
        }
        generated_len = float(wr["route_length_m"])
        a["estimated_minutes"] = round(generated_len / 75.0)  # brisk posting walk proxy
        assignments.append(a)
    avg = sum(a["length_m"] for a in assignments) / len(assignments) if assignments else 0.0
    for a in assignments:
        a["difference_from_average_m"] = round(a["length_m"] - avg, 1)
    # Keep household totals exact after rounding by correcting the last worker.
    if households and assignments:
        delta = int(households) - sum(int(a.get("estimated_households") or 0) for a in assignments)
        assignments[-1]["estimated_households"] = int(assignments[-1].get("estimated_households") or 0) + delta
    return assignments
