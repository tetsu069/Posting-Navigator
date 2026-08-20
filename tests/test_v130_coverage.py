
from shapely.geometry import Polygon
from posting_navigator.osm import osm_json_to_lines

def _way(i, highway, coords, **tags):
    t={"highway":highway}; t.update(tags)
    return {"type":"way","id":i,"tags":t,
            "geometry":[{"lat":y,"lon":x} for x,y in coords]}

def test_major_roads_inside_area_are_required():
    b=Polygon([(139.0,35.0),(139.01,35.0),(139.01,35.01),(139.0,35.01)])
    data={"elements":[
        _way(1,"primary",[(139.001,35.003),(139.009,35.003)]),
        _way(2,"secondary",[(139.001,35.005),(139.009,35.005)]),
        _way(3,"tertiary",[(139.001,35.007),(139.009,35.007)]),
    ]}
    roads=osm_json_to_lines(data,b)
    assert roads
    assert all(r["required"] for r in roads)

def test_cycleway_stays_optional():
    b=Polygon([(139.0,35.0),(139.01,35.0),(139.01,35.01),(139.0,35.01)])
    data={"elements":[_way(4,"cycleway",[(139.001,35.004),(139.009,35.004)])]}
    roads=osm_json_to_lines(data,b)
    assert roads and not any(r["required"] for r in roads)
