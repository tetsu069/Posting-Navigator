from shapely.geometry import LineString
from posting_navigator.routing import generate_route


def road(i,a,b):
    return {"id":i,"highway":"residential","name":"","geometry":LineString([a,b])}


def test_comb_teeth_are_cleared_locally_before_moving_far_away():
    lon,lat=139.0,35.0; dx=0.00035; dy=0.00028
    roads=[]; rid=1
    # two horizontal trunks; three short teeth on each.  Start left of upper trunk.
    for row in range(2):
        y=lat-row*0.0012
        xs=[lon+i*dx for i in range(5)]
        for a,b in zip(xs,xs[1:]): roads.append(road(rid,(a,y),(b,y))); rid+=1
        for x in xs[1:4]: roads.append(road(rid,(x,y),(x,y-dy))); rid+=1
    # connector between local groups
    roads.append(road(rid,(lon+4*dx,lat),(lon+4*dx,lat-0.0012)))
    route=generate_route(roads,start_point=(lon,lat))
    steps=route['route_steps']
    # The first local block should dominate the early route; do not jump to the lower group immediately.
    first_half=steps[:max(8,len(steps)//3)]
    lower=sum(1 for s in first_half if min(s['from'][1],s['to'][1]) < lat-0.0008)
    assert lower <= 1


def test_completed_sweep_blocks_do_not_ping_pong_excessively_on_grid():
    lon,lat=139.0,35.0; d=0.00032
    roads=[]; rid=1
    for y in range(6):
        for x in range(5):
            roads.append(road(rid,(lon+x*d,lat+y*d),(lon+(x+1)*d,lat+y*d))); rid+=1
    for x in range(6):
        for y in range(5):
            roads.append(road(rid,(lon+x*d,lat+y*d),(lon+x*d,lat+(y+1)*d))); rid+=1
    route=generate_route(roads,start_point=(lon,lat))
    blocks=[tuple(s['sweep_block']) if s.get('sweep_block') is not None else None for s in route['route_steps']]
    # Count returns to a block after at least one different block has been completed/visited.
    segments=[]
    for b in blocks:
        if b is None: continue
        if not segments or segments[-1] != b: segments.append(b)
    repeats=len(segments)-len(set(segments))
    assert repeats <= max(3, len(set(segments))//2)
    assert route['midroad_uturn_count'] <= 2
