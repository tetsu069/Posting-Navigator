from shapely.geometry import box
from posting_navigator.osm import osm_json_to_lines


def _way(i, coords, highway='residential', **tags):
    t={'highway': highway}; t.update(tags)
    return {'type':'way','id':i,'tags':t,'geometry':[{'lon':x,'lat':y} for x,y in coords]}


def test_private_service_inside_area_is_kept_for_complete_road_coverage():
    boundary=box(139.0,35.0,139.001,35.001)
    data={'elements':[_way(501,[(139.0002,35.0003),(139.0008,35.0003)],highway='service',access='private')]}
    roads=[r for r in osm_json_to_lines(data,boundary) if r['id']==501]
    assert roads and any(r['required'] for r in roads)


def test_track_inside_area_is_kept():
    boundary=box(139.0,35.0,139.001,35.001)
    data={'elements':[_way(502,[(139.0002,35.0004),(139.0008,35.0004)],highway='track')]}
    roads=[r for r in osm_json_to_lines(data,boundary) if r['id']==502]
    assert roads and any(r['required'] for r in roads)


def test_parallel_boundary_road_about_9m_outside_is_rescued():
    boundary=box(139.0,35.0,139.001,35.001)
    y=35.001080
    data={'elements':[_way(503,[(139.0001,y),(139.0009,y)],highway='residential')]}
    roads=[r for r in osm_json_to_lines(data,boundary) if r['id']==503]
    assert roads and any(r['required'] and r['boundary_near'] for r in roads)
