import networkx as nx
from shapely.geometry import LineString

from posting_navigator.routing import _eulerize_open, _local_completion_euler_trail


def add(g, a, b, length=10, required=True):
    g.add_edge(a, b, geometry=LineString([a,b]), length=length, route_cost=length,
               highway='residential', required=required)


def test_connector_graph_avoids_immediate_reverse_at_nonphysical_deadend():
    # required roads alone make B look like a leaf, but the physical street network
    # has B-C-D-A, so coming A->B should be able to leave by real roads instead of B->A.
    A=(0.0,0.0); B=(0.001,0.0); C=(0.001,0.001); D=(0.0,0.001); E=(-0.001,0.0)
    req=nx.MultiGraph()
    add(req,E,A); add(req,A,B)
    full=req.copy()
    add(full,B,C, required=False); add(full,C,D, required=False); add(full,D,A, required=False)

    routed=_eulerize_open(req, E, B, connector_graph=full)
    physical_degrees={n:full.degree(n) for n in routed.nodes}
    trail=_local_completion_euler_trail(routed, E, B, base_degrees=physical_degrees)
    pairs=[((u,v),(u2,v2)) for (u,v,_,_),(u2,v2,_,_) in zip(trail,trail[1:])]
    assert not any(u==v2 and v==u2 and full.degree(v)>1 for (u,v),(u2,v2) in pairs)
