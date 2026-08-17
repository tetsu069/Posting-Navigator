from shapely.geometry import Polygon
from posting_navigator.osm import osm_json_to_lines

def test_boundary_parallel_road_outside_is_not_rescued(monkeypatch):
    boundary=Polygon([(139,35),(139.01,35),(139.01,35.01),(139,35.01)])
    data={'elements':[{'type':'way','id':1,'tags':{'highway':'residential'},'geometry':[{'lon':139.001,'lat':34.999964},{'lon':139.009,'lat':34.999964}]}]}
    roads=osm_json_to_lines(data,boundary)
    assert roads == []
