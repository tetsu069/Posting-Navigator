from shapely.geometry import box
from posting_navigator.osm import osm_json_to_lines


def _way(i, coords, highway="residential"):
    return {
        "type": "way", "id": i, "tags": {"highway": highway},
        "geometry": [{"lon": x, "lat": y} for x, y in coords],
    }


def test_parallel_boundary_road_just_outside_is_required():
    boundary = box(139.0, 35.0, 139.001, 35.001)
    # north boundaryの約2m外側を平行に走る生活道路
    y = 35.001018
    roads = osm_json_to_lines({"elements": [_way(101, [(139.0001, y), (139.0009, y)])]}, boundary)
    rescued = [r for r in roads if r["id"] == 101]
    assert rescued
    assert any(r["boundary_near"] for r in rescued)
    assert any(r["required"] for r in rescued)


def test_outward_branch_near_boundary_is_not_rescued():
    boundary = box(139.0, 35.0, 139.001, 35.001)
    # north boundaryから外向きに伸びる道路。5m帯に入っていても平行でないので救済しない。
    roads = osm_json_to_lines({"elements": [_way(102, [(139.0005, 35.001005), (139.0005, 35.00104)])]}, boundary)
    assert all(r["id"] != 102 for r in roads)


def test_far_parallel_outside_road_is_not_rescued():
    boundary = box(139.0, 35.0, 139.001, 35.001)
    # 約12m外。v1.2.7の10m救済でも遠すぎる。
    y = 35.001108
    roads = osm_json_to_lines({"elements": [_way(103, [(139.0001, y), (139.0009, y)])]}, boundary)
    assert all(r["id"] != 103 for r in roads)
