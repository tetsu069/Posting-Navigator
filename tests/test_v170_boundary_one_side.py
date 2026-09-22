from shapely.geometry import LineString
from posting_navigator.routing import generate_route


def road(a,b,*,boundary=False,inside_left=True):
    return {"id":1,"highway":"residential","name":"","required":True,
            "boundary_near":boundary,"boundary_inside_left":inside_left,
            "geometry":LineString([a,b])}


def test_boundary_road_is_service_task_only_once():
    # A single boundary segment is the simplest proof: v1.6 required an
    # immediate out-and-back; v1.7 must service only the inside-facing side.
    r=generate_route([road((139.70,35.70),(139.701,35.70),boundary=True)])
    coverage=[s for s in r["route_steps"] if not s.get("transfer")]
    assert len(coverage)==1
    assert coverage[0].get("coverage_direction") == "boundary-inside-left"
    assert r["boundary_one_side_length_m"] > 0
    assert r["expected_two_side_ratio"] == 1.0


def test_interior_road_still_requires_both_sides():
    r=generate_route([road((139.70,35.70),(139.701,35.70),boundary=False)])
    coverage=[s for s in r["route_steps"] if not s.get("transfer")]
    assert len(coverage)==2
    assert r["expected_two_side_ratio"] == 2.0


def test_boundary_direction_keeps_area_side_on_left():
    a=(139.70,35.70); b=(139.701,35.70)
    r=generate_route([road(a,b,boundary=True,inside_left=False)])
    coverage=[s for s in r["route_steps"] if not s.get("transfer")]
    assert len(coverage)==1
    assert coverage[0]["from"] == b
    assert coverage[0]["to"] == a
