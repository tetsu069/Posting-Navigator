import tracemalloc
import networkx as nx
from shapely.geometry import LineString
from posting_navigator.routing import _conditional_snap_components, generate_route


def road(i, coords, target=True, connector=False):
    return {"id": i, "highway": "residential", "name": str(i),
            "posting_target": target, "connector_only": connector,
            "geometry": LineString(coords)}


def test_1_7m_gap_still_connects_with_one_pass_snap():
    gap = 0.000018
    left = road(1, [(139.0, 35.0), (139.001, 35.0)])
    right = road(2, [(139.002, 35.0), (139.003, 35.0)])
    conn = road(3, [(139.001 + gap, 35.0), (139.0015, 35.0002), (139.002 - gap, 35.0)], False, True)
    r = generate_route([left, right], connector_roads=[left, conn, right])
    assert r["road_only_route"] is True
    assert r["transfer_length_m"] > 0


def test_parallel_roads_not_collapsed():
    g = nx.MultiGraph()
    dy = 0.000020
    a0, a1 = (139.0,35.0),(139.001,35.0)
    b0, b1 = (139.0,35.0+dy),(139.001,35.0+dy)
    for u,v in [(a0,a1),(b0,b1)]:
        geom=LineString([u,v])
        g.add_edge(u,v,geometry=geom,length=100,route_cost=100,highway='residential')
    h = _conditional_snap_components(g, 3.0)
    assert nx.number_connected_components(h) == 2


def test_many_components_snap_pass_has_bounded_peak_memory():
    # 800 independent short roads, spaced far enough that no rescue snap occurs.
    # This catches the old mutate/rebuild loop regression without needing huge RAM.
    g = nx.MultiGraph()
    for i in range(800):
        x = 139.0 + (i % 40) * 0.0001
        y = 35.0 + (i // 40) * 0.0001
        a=(x,y); b=(x+0.00002,y)
        geom=LineString([a,b])
        g.add_edge(a,b,geometry=geom,length=2.0,route_cost=2.0,highway='residential')
    tracemalloc.start()
    h = _conditional_snap_components(g, 3.0)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert h.number_of_edges() == g.number_of_edges()
    # Python-level allocations should stay comfortably below Render's 512MB.
    assert peak < 80 * 1024 * 1024


def test_5_9m_collinear_dangling_gap_connects_in_strict_second_pass():
    # ~5.9m longitude gap around Tokyo latitude.
    dx = 0.000065
    g = nx.MultiGraph()
    a0,a1=(139.0,35.7),(139.001,35.7)
    b0,b1=(139.001+dx,35.7),(139.002,35.7)
    for u,v in [(a0,a1),(b0,b1)]:
        geom=LineString([u,v])
        g.add_edge(u,v,geometry=geom,length=100,route_cost=100,highway='residential')
    h = _conditional_snap_components(g, 8.0, min_gap_m=3.0, strict_long_gap=True)
    assert nx.number_connected_components(h) == 1


def test_6m_parallel_gap_is_not_connected_in_strict_second_pass():
    dy=0.000054
    g=nx.MultiGraph()
    for u,v in [((139.0,35.7),(139.001,35.7)),((139.0,35.7+dy),(139.001,35.7+dy))]:
        geom=LineString([u,v])
        g.add_edge(u,v,geometry=geom,length=100,route_cost=100,highway='residential')
    h = _conditional_snap_components(g, 8.0, min_gap_m=3.0, strict_long_gap=True)
    assert nx.number_connected_components(h) == 2
