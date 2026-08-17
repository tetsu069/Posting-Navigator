from shapely.geometry import LineString, Polygon, box
from posting_navigator.osm import osm_json_to_lines
from posting_navigator.routing import generate_route


def way(i, tags, coords):
    return {"type":"way","id":i,"tags":tags,"geometry":[{"lon":x,"lat":y} for x,y in coords]}


def test_park_path_is_optional_and_not_traversed_when_unneeded():
    b=box(139,35,139.01,35.01)
    park=[(139.002,35.002),(139.008,35.002),(139.008,35.008),(139.002,35.008),(139.002,35.002)]
    foot=[(139.003,35.005),(139.007,35.005)]
    street=[(139.001,35.001),(139.009,35.001)]
    data={"elements":[way(10,{"leisure":"park"},park),way(11,{"highway":"footway"},foot),way(12,{"highway":"residential"},street)]}
    roads=osm_json_to_lines(data,b)
    footrows=[r for r in roads if r["id"]==11]
    assert footrows and all(r["required"] is False for r in footrows)
    route=generate_route(roads,start_point=(139.001,35.001))
    assert all(s.get("osm_id") != 11 for s in route["route_steps"])


def test_boundary_parallel_residential_is_required_but_outward_branch_not_added():
    boundary=Polygon([(139,35),(139.01,35),(139.01,35.01),(139,35.01)])
    along=way(1,{"highway":"residential"},[(139.001,34.99997),(139.009,34.99997)])
    outward=way(2,{"highway":"residential"},[(139.005,34.99997),(139.005,34.9994)])
    roads=osm_json_to_lines({"elements":[along,outward]},boundary)
    assert any(r["id"]==1 and r["required"] for r in roads)
    assert not any(r["id"]==2 for r in roads)


def test_major_road_is_connector_only_not_required():
    b=box(139,35,139.01,35.01)
    data={"elements":[way(1,{"highway":"primary"},[(139.001,35.005),(139.009,35.005)]),
                      way(2,{"highway":"residential"},[(139.002,35.004),(139.002,35.006)])]}
    roads=osm_json_to_lines(data,b)
    assert all(r["required"] is False for r in roads if r["id"]==1)
    assert any(r["required"] is True for r in roads if r["id"]==2)


def test_grid_has_no_midroad_immediate_uturns():
    roads=[]; rid=1; lon0,lat0=139.0,35.0; d=0.0004
    for y in range(4):
        for x in range(3):
            roads.append({"id":rid,"highway":"residential","geometry":LineString([(lon0+x*d,lat0+y*d),(lon0+(x+1)*d,lat0+y*d)])}); rid+=1
    for x in range(4):
        for y in range(3):
            roads.append({"id":rid,"highway":"residential","geometry":LineString([(lon0+x*d,lat0+y*d),(lon0+x*d,lat0+(y+1)*d)])}); rid+=1
    route=generate_route(roads,start_point=(lon0,lat0))
    assert route["midroad_uturn_count"] == 0
