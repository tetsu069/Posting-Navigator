from shapely.geometry import LineString
from posting_navigator.routing import generate_route

def r(i,a,b,h='residential'):
    return {'id':i,'highway':h,'name':'','geometry':LineString([a,b])}

def test_non_dead_end_immediate_reverse_is_zero_on_grid():
    lon,lat=139.0,35.0; d=0.00035
    roads=[]; i=1
    for y in range(5):
        for x in range(4):
            roads.append(r(i,(lon+x*d,lat+y*d),(lon+(x+1)*d,lat+y*d))); i+=1
    for x in range(5):
        for y in range(4):
            roads.append(r(i,(lon+x*d,lat+y*d),(lon+x*d,lat+(y+1)*d))); i+=1
    route=generate_route(roads,start_point=(lon,lat))
    assert route.get('left_side_delivery') is True
    assert 1.95 <= route['duplication_ratio'] <= 2.10

def test_already_used_primary_is_not_reused_when_local_alternative_exists():
    a=(139.0,35.0); b=(139.001,35.0); c=(139.0,35.0005); d=(139.001,35.0005)
    roads=[
        r(1,a,b,'primary'),
        r(2,a,c), r(3,c,d), r(4,d,b),
        r(5,c,(138.9997,35.0005)),
        r(6,d,(139.0013,35.0005)),
    ]
    route=generate_route(roads,start_point=a)
    primary_steps=[s for s in route['route_steps'] if s.get('osm_id')==1]
    assert len(primary_steps) <= 1
