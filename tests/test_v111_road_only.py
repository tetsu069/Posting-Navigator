from shapely.geometry import LineString, box
from posting_navigator.routing import generate_route, generate_worker_routes


def r(coords, name='road'):
    return {'geometry': LineString(coords), 'highway': 'residential', 'name': name, 'id': 1}


def test_transfer_between_target_components_uses_connector_road_geometry():
    # target は左右2本だけで非連結。connector には中央の道路を含める。
    left = r([(139.0,35.0),(139.001,35.0)], 'left')
    right = r([(139.002,35.0),(139.003,35.0)], 'right')
    bridge = r([(139.001,35.0),(139.001,35.0005),(139.002,35.0005),(139.002,35.0)], 'bridge')
    route = generate_route([left,right], start_point=(139.0,35.0), connector_roads=[left,bridge,right])
    transfers = [s for s in route['route_steps'] if s['transfer']]
    assert transfers
    # 直線なら中間緯度は35.0だけになる。実connectorを通るので35.0005を含む。
    ys = [y for s in transfers for x,y in s['geometry'].coords]
    assert max(ys) > 35.0004
    assert route['road_only_route'] is True


def test_disconnected_targets_without_real_connector_fail_instead_of_straight_line():
    left = r([(139.0,35.0),(139.001,35.0)])
    right = r([(139.01,35.0),(139.011,35.0)])
    try:
        generate_route([left,right], connector_roads=[left,right])
    except ValueError as e:
        assert '実道路' in str(e) or '接続' in str(e)
    else:
        raise AssertionError('disconnected roads must not be joined by a fake straight line')


def test_worker_routes_use_full_town_roads_as_connectors():
    boundary = box(139.0,35.0,139.004,35.002)
    roads = [
        r([(139.0,35.0005),(139.004,35.0005)], 'main'),
        r([(139.001,35.0),(139.001,35.002)], 'cross1'),
        r([(139.003,35.0),(139.003,35.002)], 'cross2'),
    ]
    assignments = generate_worker_routes(boundary, roads, 2, households=100)
    assert len(assignments) == 2
    assert all(a['route_steps'] for a in assignments)
    assert all(all(len(list(s['geometry'].coords)) >= 2 for s in a['route_steps']) for a in assignments)
