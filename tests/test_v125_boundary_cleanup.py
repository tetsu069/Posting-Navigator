from shapely.geometry import box
from posting_navigator.osm import osm_json_to_lines


def _way(i, coords, highway='residential'):
    return {
        'type': 'way', 'id': i, 'tags': {'highway': highway},
        'geometry': [{'lon': x, 'lat': y} for x, y in coords],
    }


def test_33m_cross_boundary_street_stays_required_in_v126():
    boundary = box(139.0, 35.0, 139.001, 35.001)
    # North boundaryを直交して外へ続くway。内側に約33mだけ残る短い切れ端。
    data = {'elements': [_way(201, [
        (139.0005, 35.00070),
        (139.0005, 35.00100),
        (139.0005, 35.00120),
    ])]}
    roads = [r for r in osm_json_to_lines(data, boundary) if r['id'] == 201]
    assert roads
    # v1.2.6: 33mもある実道路をstub扱いして落とさない。
    assert all(not r.get('boundary_clip_tail') for r in roads)
    assert any(r['required'] for r in roads)


def test_long_cross_boundary_street_stays_required():
    boundary = box(139.0, 35.0, 139.001, 35.001)
    # 町丁目内を十分長く通る道路は、境界で切れていても配布対象のまま。
    data = {'elements': [_way(202, [
        (139.0005, 35.00010),
        (139.0005, 35.00100),
        (139.0005, 35.00120),
    ])]}
    roads = [r for r in osm_json_to_lines(data, boundary) if r['id'] == 202]
    assert roads
    assert any(r['required'] for r in roads)


def test_parallel_boundary_road_seven_metres_outside_is_rescued():
    boundary = box(139.0, 35.0, 139.001, 35.001)
    # north boundaryの約7m外側。旧5m救済では抜けていた。
    y = 35.001063
    data = {'elements': [_way(203, [(139.00008, y), (139.00092, y)])]}
    roads = [r for r in osm_json_to_lines(data, boundary) if r['id'] == 203]
    assert roads
    assert any(r['boundary_near'] and r['required'] for r in roads)


def test_boundary_way_can_turn_outward_but_only_parallel_run_is_rescued():
    boundary = box(139.0, 35.0, 139.001, 35.001)
    y = 35.001045  # 約5m外
    # 前半は境界に平行、後半は外向きに曲がる。同じOSM wayでも前半だけ拾う。
    data = {'elements': [_way(204, [
        (139.00005, y), (139.00045, y), (139.00080, y),
        (139.00080, 35.00113),
    ])]}
    roads = [r for r in osm_json_to_lines(data, boundary) if r['id'] == 204 and r['boundary_near']]
    assert roads
    # 救済された線は境界から外へ曲がる最後の縦区間まで含まない。
    max_y = max(pt[1] for r in roads for pt in r['geometry'].coords)
    assert max_y < 35.00108
