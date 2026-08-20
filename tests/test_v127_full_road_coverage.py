from shapely.geometry import box
from posting_navigator.osm import osm_json_to_lines


def _way(i, coords, highway='residential', **tags):
    t = {'highway': highway}
    t.update(tags)
    return {
        'type': 'way', 'id': i, 'tags': t,
        'geometry': [{'lon': x, 'lat': y} for x, y in coords],
    }


def test_parking_aisle_inside_area_is_not_silently_dropped_anymore():
    boundary = box(139.0, 35.0, 139.001, 35.001)
    data = {'elements': [_way(
        401,
        [(139.0002, 35.0002), (139.0008, 35.0002)],
        highway='service', service='parking_aisle',
    )]}
    roads = [r for r in osm_json_to_lines(data, boundary) if r['id'] == 401]
    assert roads and any(r['required'] for r in roads)


def test_generic_highway_road_inside_area_is_required():
    boundary = box(139.0, 35.0, 139.001, 35.001)
    data = {'elements': [_way(
        402,
        [(139.0002, 35.0003), (139.0008, 35.0003)],
        highway='road',
    )]}
    roads = [r for r in osm_json_to_lines(data, boundary) if r['id'] == 402]
    assert roads and any(r['required'] for r in roads)


def test_curving_boundary_road_is_rescued_in_local_runs():
    boundary = box(139.0, 35.0, 139.001, 35.001)
    # north boundary の約7.8m外側を緩く曲がって沿う道路。
    y = 35.001070
    data = {'elements': [_way(403, [
        (139.00005, y),
        (139.00030, y + 0.000004),
        (139.00055, y - 0.000003),
        (139.00080, y + 0.000005),
        (139.00095, y),
    ])]}
    roads = [r for r in osm_json_to_lines(data, boundary) if r['id'] == 403]
    assert roads
    assert any(r['boundary_near'] and r['required'] for r in roads)


def test_outward_branch_still_not_rescued_with_wider_boundary_band():
    boundary = box(139.0, 35.0, 139.001, 35.001)
    data = {'elements': [_way(
        404,
        [(139.0005, 35.00101), (139.0005, 35.00108)],
    )]}
    roads = [r for r in osm_json_to_lines(data, boundary) if r['id'] == 404]
    assert not roads
