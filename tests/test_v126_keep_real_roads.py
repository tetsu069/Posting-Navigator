from shapely.geometry import box
from posting_navigator.osm import osm_json_to_lines


def _way(i, coords, highway='residential', **tags):
    t={'highway': highway}; t.update(tags)
    return {'type':'way','id':i,'tags':t,'geometry':[{'lon':x,'lat':y} for x,y in coords]}


def test_boundary_clipped_real_street_over_12m_stays_required():
    boundary=box(139.0,35.0,139.001,35.001)
    data={'elements':[_way(301,[(139.0005,35.00082),(139.0005,35.00100),(139.0005,35.00115)])]}
    roads=[r for r in osm_json_to_lines(data,boundary) if r['id']==301]
    assert roads
    assert any(r['required'] for r in roads)
    assert all(not r.get('boundary_clip_tail') for r in roads if r['geometry'].length)


def test_very_tiny_outward_boundary_stub_can_still_be_optional():
    boundary=box(139.0,35.0,139.001,35.001)
    data={'elements':[_way(302,[(139.0005,35.00094),(139.0005,35.00100),(139.0005,35.00115)])]}
    roads=[r for r in osm_json_to_lines(data,boundary) if r['id']==302]
    assert roads
    assert any(r.get('boundary_clip_tail') and not r['required'] for r in roads)


def test_driveway_is_required_not_silently_dropped():
    boundary=box(139.0,35.0,139.001,35.001)
    data={'elements':[_way(303,[(139.0002,35.0002),(139.0002,35.0006)], highway='service', service='driveway')]}
    roads=[r for r in osm_json_to_lines(data,boundary) if r['id']==303]
    assert roads and all(r['required'] for r in roads)
