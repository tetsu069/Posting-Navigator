import pytest
from shapely.geometry import LineString
from posting_navigator.routing import generate_route

def road(i,a,b):
    return {"id":i,"highway":"residential","name":"","geometry":LineString([a,b])}

def test_dead_end_is_taken_when_first_reaching_its_junction():
    # trunk A-B-C-D with a tooth B-E. Once B is reached, E must be cleared before C.
    A=(139.0,35.0); B=(139.0003,35.0); C=(139.0006,35.0); D=(139.0009,35.0); E=(139.0003,35.00025)
    r=generate_route([road(1,A,B),road(2,B,C),road(3,C,D),road(4,B,E)], start_point=A)
    st=r['route_steps']
    first_B=next(i for i,x in enumerate(st) if x['to']==B)
    # the next departure from B must enter the tooth, not pass it and come back later
    assert st[first_B+1]['from']==B and st[first_B+1]['to']==E

def test_partial_disconnected_route_is_never_reported_complete():
    A=(139.0,35.0); B=(139.0003,35.0); C=(139.01,35.0); D=(139.0103,35.0)
    with pytest.raises(ValueError, match='最後まで巡回できません'):
        generate_route([road(1,A,B), road(2,C,D)], start_point=A)
