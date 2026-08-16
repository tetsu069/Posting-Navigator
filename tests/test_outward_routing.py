from shapely.geometry import LineString
from posting_navigator.routing import generate_route


def test_outward_strategy_marks_strategy_and_uses_real_road_connectors():
    # 3x3 grid. START is lower-left; every transfer must remain on a source road edge.
    roads = []
    rid = 1
    lon0, lat0 = 139.0, 35.0
    d = 0.001
    for y in range(3):
        for x in range(2):
            roads.append({"id": rid, "highway": "residential", "name": f"H{y}",
                          "geometry": LineString([(lon0+x*d, lat0+y*d), (lon0+(x+1)*d, lat0+y*d)])})
            rid += 1
    for x in range(3):
        for y in range(2):
            roads.append({"id": rid, "highway": "residential", "name": f"V{x}",
                          "geometry": LineString([(lon0+x*d, lat0+y*d), (lon0+x*d, lat0+(y+1)*d)])})
            rid += 1
    route = generate_route(roads, start_point=(lon0, lat0))
    assert route["routing_strategy"] == "local-clusters-outward"
    assert route["cluster_count"] >= 1
    assert route["route_steps"]
    # Transfer geometry is always a source-road geometry, never a coordinate chord.
    source_ids = {r["id"] for r in roads}
    for step in route["route_steps"]:
        if step["transfer"]:
            assert step["osm_id"] in source_ids


def test_route_starts_in_nearest_local_area():
    roads = [
        {"id": 1, "highway": "residential", "name": "near", "geometry": LineString([(139.0,35.0),(139.001,35.0)])},
        {"id": 2, "highway": "residential", "name": "next", "geometry": LineString([(139.001,35.0),(139.002,35.0)])},
        {"id": 3, "highway": "residential", "name": "far", "geometry": LineString([(139.002,35.0),(139.003,35.0)])},
    ]
    route = generate_route(roads, start_point=(139.0,35.0))
    first = route["route_steps"][0]
    assert abs(first["from"][0] - 139.0) < 1e-6
    assert abs(first["from"][1] - 35.0) < 1e-6
