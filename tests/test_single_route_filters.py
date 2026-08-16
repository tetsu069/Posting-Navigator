from shapely.geometry import box
from posting_navigator.osm import osm_json_to_lines

def way(i,tags,coords):
    return {"type":"way","id":i,"tags":tags,"geometry":[{"lon":x,"lat":y} for x,y in coords]}

def test_park_footway_removed_but_residential_kept():
    b=box(139,35,139.01,35.01)
    park=[(139.002,35.002),(139.008,35.002),(139.008,35.008),(139.002,35.008),(139.002,35.002)]
    foot=[(139.003,35.005),(139.007,35.005)]
    street=[(139.003,35.006),(139.007,35.006)]
    data={"elements":[way(10,{"leisure":"park"},park),way(11,{"highway":"footway"},foot),way(12,{"highway":"residential"},street)]}
    ids={r["id"] for r in osm_json_to_lines(data,b)}
    assert 11 not in ids
    assert 12 in ids
