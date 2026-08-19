from shapely.geometry import LineString
from posting_navigator.routing import _prune_redundant_duplicate_backtracks, _redundant_oscillation_count


def row(a,b,duplicated=False):
    return (a,b,0,{"geometry":LineString([a,b]),"length":10,"route_cost":10,"highway":"residential","required":True,"duplicated":duplicated})


def test_prunes_closed_duplicate_backtrack_pair_but_keeps_required_edge():
    A=(0,0); B=(1,0); C=(2,0)
    trail=[
        row(A,B,False),
        row(B,A,True),
        row(A,B,True),
        row(B,C,False),
    ]
    cleaned=_prune_redundant_duplicate_backtracks(trail)
    assert [(u,v) for u,v,_,_ in cleaned] == [(A,B),(B,C)]
    assert _redundant_oscillation_count(cleaned) == 0


def test_true_deadend_required_out_and_duplicate_return_is_not_deleted():
    A=(0,0); B=(1,0); C=(0,1)
    trail=[row(C,A,False),row(A,B,False),row(B,A,True)]
    cleaned=_prune_redundant_duplicate_backtracks(trail)
    assert [(u,v) for u,v,_,_ in cleaned] == [(C,A),(A,B),(B,A)]


def test_two_duplicate_edge_loop_is_deleted():
    A=(0,0); B=(1,0)
    trail=[row(A,B,True),row(B,A,True)]
    assert _prune_redundant_duplicate_backtracks(trail) == []
