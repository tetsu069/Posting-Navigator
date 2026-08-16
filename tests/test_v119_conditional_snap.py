from shapely.geometry import LineString
from posting_navigator.routing import generate_route


def road(i, coords, target=True, connector=False):
    return {'id': i, 'highway': 'residential', 'name': str(i), 'posting_target': target,
            'connector_only': connector, 'geometry': LineString(coords)}


def test_1_7m_broken_continuation_is_conditionally_snapped():
    # About 1.65m at Tokyo latitude: above the old 1.25m hard cap.
    gap = 0.000018
    left = road(1, [(139.0, 35.0), (139.001, 35.0)])
    right = road(2, [(139.002, 35.0), (139.003, 35.0)])
    conn = road(3, [(139.001 + gap, 35.0), (139.0015, 35.0002), (139.002 - gap, 35.0)], False, True)
    r = generate_route([left, right], connector_roads=[left, conn, right])
    assert r['road_only_route'] is True
    assert r['transfer_length_m'] > 0


def test_parallel_side_by_side_roads_are_not_rescue_snapped():
    # Two east-west dangling roads separated north/south by ~2.2m.  Their axes
    # are parallel while the gap is perpendicular, so they must stay separate.
    dy = 0.000020
    a = road(1, [(139.0, 35.0), (139.001, 35.0)])
    b = road(2, [(139.0, 35.0 + dy), (139.001, 35.0 + dy)])
    try:
        generate_route([a, b], connector_roads=[a, b])
    except ValueError as e:
        assert '接続' in str(e)
    else:
        raise AssertionError('parallel nearby roads must not be auto-snapped')
