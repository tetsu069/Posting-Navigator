from shapely.geometry import box
from posting_navigator.osm import osm_json_to_lines


def _way(i, coords, highway="residential"):
    return {"type":"way","id":i,"tags":{"highway":highway},"geometry":[{"lon":x,"lat":y} for x,y in coords]}


def test_outward_crossing_is_clipped_to_exact_boundary():
    boundary = box(139.0, 35.0, 139.001, 35.001)
    outward = [(139.0005,34.9999),(139.0005,35.0005),(139.0005,35.0011)]
    roads = osm_json_to_lines({"elements":[_way(1,outward)]}, boundary)
    assert roads
    # every retained point must be inside/on the polygon, never outside it
    for road in roads:
        assert boundary.buffer(2e-9).covers(road["geometry"])


def test_parallel_road_outside_boundary_is_not_added():
    boundary = box(139.0, 35.0, 139.001, 35.001)
    # roughly 1m outside the north edge: old rescue logic could add this
    outside = [(139.0001,35.001009),(139.0009,35.001009)]
    roads = osm_json_to_lines({"elements":[_way(2,outside)]}, boundary)
    assert all(r["id"] != 2 for r in roads)
