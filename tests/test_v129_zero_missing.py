from shapely.geometry import box
from posting_navigator.osm import osm_json_to_lines
from posting_navigator.routing import generate_route


def _way(i, coords, highway='residential', **tags):
    t={'highway': highway}; t.update(tags)
    return {'type':'way','id':i,'tags':t,'geometry':[{'lon':x,'lat':y} for x,y in coords]}


def test_nonpark_footway_inside_area_is_required_for_completeness():
    boundary=box(139.0,35.0,139.002,35.002)
    data={'elements':[_way(801,[(139.0002,35.001),(139.0018,35.001)],highway='footway')]}
    roads=osm_json_to_lines(data,boundary)
    assert roads and all(r['required'] for r in roads if r['id']==801)


def test_service_parking_aisle_inside_area_is_required():
    boundary=box(139.0,35.0,139.002,35.002)
    data={'elements':[_way(802,[(139.0002,35.0007),(139.0018,35.0007)],highway='service',service='parking_aisle')]}
    roads=osm_json_to_lines(data,boundary)
    assert roads and all(r['required'] for r in roads if r['id']==802)


def test_generated_route_covers_all_required_graph_edges_simple_grid():
    boundary=box(139.0,35.0,139.002,35.002)
    ways=[
      _way(810,[(139.0002,35.0004),(139.0018,35.0004)]),
      _way(811,[(139.0002,35.0010),(139.0018,35.0010)], highway='service'),
      _way(812,[(139.0002,35.0016),(139.0018,35.0016)], highway='footway'),
      _way(813,[(139.0005,35.0002),(139.0005,35.0018)]),
      _way(814,[(139.0014,35.0002),(139.0014,35.0018)], highway='service'),
    ]
    roads=osm_json_to_lines({'elements':ways},boundary)
    route=generate_route(roads,start_point=(139.0002,35.0004))
    assert route['source_edges'] > 0
    assert route['route_edges'] >= route['source_edges']
