from shapely.geometry import LineString
from posting_navigator.routing import generate_route


def test_outward_strategy_marks_strategy_and_uses_real_road_connectors():
    # 3x3 grid. START is lower-left; every transfer must remain on a source road edge.
    roads = []
    rid = 1
    lon0, lat0 = 139.0, 35.0
    d = 0.001
    for y in range(3):
        for x in range(2):
            roads.append({"id": rid, "highway": "residential", "name": f"H{y}",
                          "geometry": LineString([(lon0+x*d, lat0+y*d), (lon0+(x+1)*d, lat0+y*d)])})
            rid += 1
    for x in range(3):
        for y in range(2):
            roads.append({"id": rid, "highway": "residential", "name": f"V{x}",
                          "geometry": LineString([(lon0+x*d, lat0+y*d), (lon0+x*d, lat0+(y+1)*d)])})
            rid += 1
    route = generate_route(roads, start_point=(lon0, lat0))
    assert route["routing_strategy"] == "global-open-postman-turn-aware-outward"
    assert route["cluster_count"] >= 1
    assert route["route_steps"]
    # Transfer geometry is always a source-road geometry, never a coordinate chord.
    source_ids = {r["id"] for r in roads}
    for step in route["route_steps"]:
        if step["transfer"]:
            assert step["osm_id"] in source_ids


def test_route_starts_in_nearest_local_area():
    roads = [
        {"id": 1, "highway": "residential", "name": "near", "geometry": LineString([(139.0,35.0),(139.001,35.0)])},
        {"id": 2, "highway": "residential", "name": "next", "geometry": LineString([(139.001,35.0),(139.002,35.0)])},
        {"id": 3, "highway": "residential", "name": "far", "geometry": LineString([(139.002,35.0),(139.003,35.0)])},
    ]
    route = generate_route(roads, start_point=(139.0,35.0))
    first = route["route_steps"][0]
    assert abs(first["from"][0] - 139.0) < 1e-6
    assert abs(first["from"][1] - 35.0) < 1e-6



def test_turn_aware_euler_avoids_immediate_reverse_when_alternative_exists():
    import networkx as nx
    from posting_navigator.routing import _turn_aware_euler_trail
    a=(139.0,35.0); b=(139.001,35.0); c=(139.001,35.001)
    g=nx.MultiGraph()
    def add(u,v,dup=False):
        g.add_edge(u,v,length=100,route_cost=100,geometry=LineString([u,v]),highway='residential',duplicated=dup)
    # Eulerian: AB x2, BC x2. At B, after arriving from A, BC is available, so AB reverse should not be immediate.
    add(a,b,False); add(a,b,True); add(b,c,False); add(b,c,True)
    trail=_turn_aware_euler_trail(g,a,a)
    nodes=[trail[0][0]]+[e[1] for e in trail]
    assert nodes[:3] == [a,b,c]


def test_outward_route_does_not_create_consecutive_uturn_navigation_on_grid():
    roads=[]; rid=1; lon0,lat0=139.0,35.0; d=0.001
    for y in range(4):
        for x in range(3):
            roads.append({'id':rid,'highway':'residential','name':f'H{y}','geometry':LineString([(lon0+x*d,lat0+y*d),(lon0+(x+1)*d,lat0+y*d)])}); rid+=1
    for x in range(4):
        for y in range(3):
            roads.append({'id':rid,'highway':'residential','name':f'V{x}','geometry':LineString([(lon0+x*d,lat0+y*d),(lon0+x*d,lat0+(y+1)*d)])}); rid+=1
    route=generate_route(roads,start_point=(lon0,lat0))
    turns=[leg['turn'] for leg in route['navigation_legs']]
    assert not any(a=='折り返し' and b=='折り返し' for a,b in zip(turns,turns[1:]))
    assert route['routing_strategy']=='global-open-postman-turn-aware-outward'



def test_global_postman_reduces_repeated_roads_on_dense_grid():
    roads=[]; rid=1; lon0,lat0=139.0,35.0; d=0.001
    for y in range(5):
        for x in range(4):
            roads.append({'id':rid,'highway':'residential','name':f'H{y}','geometry':LineString([(lon0+x*d,lat0+y*d),(lon0+(x+1)*d,lat0+y*d)])}); rid+=1
    for x in range(5):
        for y in range(4):
            roads.append({'id':rid,'highway':'residential','name':f'V{x}','geometry':LineString([(lon0+x*d,lat0+y*d),(lon0+x*d,lat0+(y+1)*d)])}); rid+=1
    route=generate_route(roads,start_point=(lon0,lat0))
    assert route['duplication_ratio'] < 1.30
    steps=route['route_steps']
    immediate=sum(1 for a,b in zip(steps,steps[1:]) if a['from']==b['to'] and a['to']==b['from'])
    assert immediate <= 2
