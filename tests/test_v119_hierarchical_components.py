from shapely.geometry import LineString
from posting_navigator.routing import generate_route


def road(i,a,b,required=True,name=''):
    return {'id':i,'highway':'residential' if required else 'footway','name':name,'required':required,'geometry':LineString([a,b])}


def test_optional_osm_connector_is_used_before_manual_break():
    a=(139.0,35.0); b=(139.0002,35.0); c=(139.0004,35.0); d=(139.0006,35.0)
    roads=[
        road(1,a,b,True,'A'),
        road(2,b,c,False,'connector'),
        road(3,c,d,True,'B'),
    ]
    r=generate_route(roads,start_point=a)
    assert r['component_count']==2
    assert r['manual_transfer_count']==0
    assert r['transfer_length_m']>0
    assert r['geometry'].geom_type=='LineString'
    assert any(s['transfer'] for s in r['route_steps'])


def test_three_disconnected_components_are_all_routed_in_nearby_order():
    a=(139.0,35.0); b=(139.0002,35.0)
    c=(139.002,35.0); d=(139.0022,35.0)
    e=(139.006,35.0); f=(139.0062,35.0)
    r=generate_route([road(1,a,b),road(2,c,d),road(3,e,f)],start_point=a)
    assert r['cluster_count']==3
    assert r['manual_transfer_count']==2
    assert r['geometry'].geom_type=='MultiLineString'
    order=r['component_order']
    assert abs(order[1]['start_lon']-c[0]) < abs(order[1]['start_lon']-e[0])
