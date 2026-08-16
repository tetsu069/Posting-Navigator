from pathlib import Path
import zipfile
from posting_navigator.kmz import list_area_info_from_kmz
from posting_navigator.osm import osm_json_to_lines
from shapely.geometry import Polygon


def test_households_from_kmz_description(tmp_path: Path):
    kml='''<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>テスト町</name><description>区画名：テスト町&lt;br&gt;世帯数：1234</description><Polygon><outerBoundaryIs><LinearRing><coordinates>139,35 139.01,35 139.01,35.01 139,35.01 139,35</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>'''
    kmz=tmp_path/'a.kmz'
    with zipfile.ZipFile(kmz,'w') as z:z.writestr('doc.kml',kml)
    assert list_area_info_from_kmz(kmz)['テスト町']['households']==1234


def test_boundary_near_road_is_included(monkeypatch):
    monkeypatch.setenv('BOUNDARY_ROAD_BUFFER_M','8')
    boundary=Polygon([(139,35),(139.01,35),(139.01,35.01),(139,35.01)])
    # south edgeから約4m外側の道路中心線
    data={'elements':[{'type':'way','id':1,'tags':{'highway':'residential'},'geometry':[{'lon':139.001,'lat':34.999964},{'lon':139.009,'lat':34.999964}]}]}
    roads=osm_json_to_lines(data,boundary)
    assert roads
    assert roads[0]['boundary_near'] is True
