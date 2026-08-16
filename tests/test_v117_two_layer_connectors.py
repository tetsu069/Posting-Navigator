from shapely.geometry import LineString, box

from posting_navigator.osm import osm_json_to_lines, osm_json_to_mobility_lines
from posting_navigator.routing import generate_route


def _way(i, coords, highway='residential'):
    return {
        'type': 'way', 'id': i,
        'tags': {'highway': highway},
        'geometry': [{'lon': x, 'lat': y} for x, y in coords],
    }


def test_outward_boundary_stub_is_only_in_mobility_layer(monkeypatch):
    monkeypatch.setenv('BOUNDARY_ROAD_BUFFER_M', '8')
    monkeypatch.setenv('BOUNDARY_WALK_TOLERANCE_M', '1.5')
    monkeypatch.setenv('CONNECTOR_ROAD_BUFFER_M', '20')
    boundary = box(139.0, 35.0, 139.001, 35.001)
    data = {'elements': [_way(1, [(139.0005, 35.0008), (139.0005, 35.00114)])]}
    strict = osm_json_to_lines(data, boundary)
    mobility = osm_json_to_mobility_lines(data, boundary)
    # Distribution layer retains only the in-area/safe portion.
    assert strict
    assert all(not r.get('connector_only') for r in strict)
    # Mobility layer may keep the outward real-road stub as transfer-only.
    assert mobility
    assert all(r.get('connector_only') and not r.get('posting_target') for r in mobility)
    strict_max_y = max(y for r in strict for x, y in r['geometry'].coords)
    mobility_max_y = max(y for r in mobility for x, y in r['geometry'].coords)
    assert mobility_max_y > strict_max_y


def test_connector_only_real_road_can_join_target_components():
    left = {
        'id': 1, 'highway': 'residential', 'name': 'left', 'posting_target': True,
        'geometry': LineString([(139.0, 35.0), (139.001, 35.0)])
    }
    right = {
        'id': 2, 'highway': 'residential', 'name': 'right', 'posting_target': True,
        'geometry': LineString([(139.002, 35.0), (139.003, 35.0)])
    }
    connector = {
        'id': 3, 'highway': 'residential', 'name': 'transfer', 'posting_target': False,
        'connector_only': True, 'residential_score': 0.0,
        'geometry': LineString([(139.001, 35.0), (139.001, 35.0002), (139.002, 35.0002), (139.002, 35.0)])
    }
    route = generate_route([left, right], connector_roads=[left, connector, right])
    transfers = [s for s in route['route_steps'] if s.get('transfer')]
    assert transfers
    assert any(s.get('connector_only') for s in transfers)
    assert all(s.get('posting_target') for s in route['route_steps'] if not s.get('transfer'))
