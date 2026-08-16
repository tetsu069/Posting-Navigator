import networkx as nx
from shapely.geometry import box
from posting_navigator.fixture import make_offline_fixture
from posting_navigator.routing import build_graph, eulerize_weighted, generate_route

def test_route_is_eulerian_after_augmentation():
    roads = make_offline_fixture(box(139.69, 35.69, 139.70, 35.70))
    graph = eulerize_weighted(build_graph(roads))
    assert nx.is_eulerian(graph)
    result = generate_route(roads)
    assert result["route_length_m"] >= result["source_length_m"]


def test_requested_start_is_snapped_to_graph():
    from posting_navigator.routing import generate_route
    from shapely.geometry import LineString
    roads = [
        {"geometry": LineString([(139.0, 35.0), (139.001, 35.0)]), "highway": "residential", "name": "A"},
        {"geometry": LineString([(139.001, 35.0), (139.001, 35.001)]), "highway": "residential", "name": "B"},
    ]
    result = generate_route(roads, start_point=(139.0011, 35.0011))
    assert abs(result["start_lon"] - 139.001) < 1e-6
    assert abs(result["start_lat"] - 35.001) < 1e-6
    assert result["dead_end_count"] == 2


def test_split_route_balances_distance_and_preserves_continuity():
    from posting_navigator.routing import split_route
    roads = make_offline_fixture(box(139.69, 35.69, 139.70, 35.70))
    route = generate_route(roads)
    assignments = split_route(route, 4)
    assert len(assignments) == 4
    lengths = [a["length_m"] for a in assignments]
    assert max(lengths) - min(lengths) < 2.0
    assert abs(sum(lengths) - route["route_length_m"]) < 3.0
    for previous, current in zip(assignments, assignments[1:]):
        assert previous["end_point"].distance(current["start_point"]) < 1e-12


def test_route_keeps_disconnected_components_and_ordered_steps():
    from shapely.geometry import LineString
    from posting_navigator.routing import generate_route
    roads = [
        {"id": 1, "highway": "residential", "name": "A", "geometry": LineString([(139.0,35.0),(139.001,35.0)])},
        {"id": 2, "highway": "residential", "name": "B", "geometry": LineString([(139.01,35.0),(139.011,35.0)])},
    ]
    route = generate_route(roads, start_point=(139.0,35.0))
    assert route["component_count"] == 2
    assert route["source_edges"] == 2
    assert any(step["transfer"] for step in route["route_steps"])
    assert {step["osm_id"] for step in route["route_steps"] if not step["transfer"]} == {1,2}
    assert [s["seq"] for s in route["route_steps"]] == list(range(1, len(route["route_steps"])+1))
