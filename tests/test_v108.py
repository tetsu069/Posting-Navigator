from shapely.geometry import Polygon
from posting_navigator.osm import osm_json_to_lines

def test_boundary_near_road_is_excluded(monkeypatch):
    boundary=Polygon([(139,35),(139.01,35),(139.01,35.01),(139,35.01)])
    data={'elements':[{'type':'way','id':1,'tags':{'highway':'residential'},'geometry':[{'lon':139.001,'lat':34.999964},{'lon':139.009,'lat':34.999964}]}]}
    assert not osm_json_to_lines(data,boundary)
