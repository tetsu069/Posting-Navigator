from shapely.geometry import box
from posting_navigator.osm import osm_json_to_lines

def _way(i, coords):
    return {'type':'way','id':i,'tags':{'highway':'residential'},'geometry':[{'lon':x,'lat':y} for x,y in coords]}

def test_outside_parallel_removed_and_crossing_branch_clipped():
    boundary=box(139.0000,35.0000,139.0010,35.0010)
    parallel=[(139.0001,35.00104),(139.0009,35.00104)]
    outward=[(139.0005,35.0007),(139.0005,35.00108)]
    roads=osm_json_to_lines({'elements':[_way(1,parallel),_way(2,outward)]},boundary)
    ids={r['id'] for r in roads}
    assert 1 not in ids
    assert 2 in ids
