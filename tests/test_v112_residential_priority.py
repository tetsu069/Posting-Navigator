from shapely.geometry import Polygon, LineString
from posting_navigator.osm import osm_json_to_lines
from posting_navigator.routing import generate_route, build_graph


def _way(i, highway, coords, **tags):
    return {"type":"way","id":i,"tags":{"highway":highway,**tags},"geometry":[{"lon":x,"lat":y} for x,y in coords]}


def _poly_way(i, coords, **tags):
    return {"type":"way","id":i,"tags":tags,"geometry":[{"lon":x,"lat":y} for x,y in coords]}


def test_park_footway_without_houses_is_connector_only():
    boundary=Polygon([(139.0,35.0),(139.002,35.0),(139.002,35.002),(139.0,35.002),(139.0,35.0)])
    park=[(139.0008,35.0004),(139.0018,35.0004),(139.0018,35.0016),(139.0008,35.0016),(139.0008,35.0004)]
    building=[(139.00005,35.00005),(139.00025,35.00005),(139.00025,35.00025),(139.00005,35.00025),(139.00005,35.00005)]
    data={"elements":[
        _way(1,"residential",[(139.0,35.0002),(139.0006,35.0002)]),
        _way(2,"footway",[(139.0010,35.0007),(139.0016,35.0013)]),
        _poly_way(10,building,building="yes"),
        _poly_way(11,park,leisure="park"),
    ]}
    roads=osm_json_to_lines(data,boundary)
    parkroad=next(r for r in roads if r["id"]==2)
    assert parkroad["posting_target"] is False
    street=next(r for r in roads if r["id"]==1)
    assert street["posting_target"] is True


def test_connector_only_road_not_required_by_route():
    roads=[
        {"id":1,"highway":"residential","name":"A","posting_target":True,"residential_score":1.0,"geometry":LineString([(139.0,35.0),(139.001,35.0)])},
        {"id":2,"highway":"footway","name":"Park","posting_target":False,"residential_score":0.0,"nonresidential_overlap":1.0,"geometry":LineString([(139.001,35.0),(139.002,35.0)])},
    ]
    r=generate_route(roads,start_point=(139.0,35.0))
    assert r["source_edges"] == 1
    assert all(s.get("posting_target", True) for s in r["route_steps"] if not s.get("transfer"))


def test_connector_only_has_high_route_cost():
    roads=[
        {"id":1,"highway":"residential","name":"A","posting_target":True,"residential_score":1.0,"geometry":LineString([(139.0,35.0),(139.001,35.0)])},
        {"id":2,"highway":"footway","name":"P","posting_target":False,"residential_score":0.0,"nonresidential_overlap":1.0,"geometry":LineString([(139.001,35.0),(139.002,35.0)])},
    ]
    g=build_graph(roads,simplify=False)
    costs=[]
    for _,_,d in g.edges(data=True):
        costs.append((d['posting_target'], d['route_cost']/d['length']))
    target=min(x[1] for x in costs if x[0])
    connector=min(x[1] for x in costs if not x[0])
    assert connector > target * 3
