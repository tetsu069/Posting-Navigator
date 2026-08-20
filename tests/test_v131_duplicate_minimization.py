import networkx as nx
from shapely.geometry import LineString
from posting_navigator.routing import _minimum_pairing_paths


def _add(g,u,v,length,highway,dup):
    g.add_edge(u,v,length=length,route_cost=length,duplicate_cost=length*dup,
               highway=highway,required=True,boundary_near=False,
               geometry=LineString([u,v]))


def test_pairing_avoids_major_road_when_residential_detour_is_reasonable():
    g=nx.MultiGraph()
    a=(0.0,0.0); b=(0.001,0.0); c=(0.0,0.001); d=(0.001,0.001)
    # direct major route is physically shorter but should be very expensive to duplicate
    _add(g,a,b,100,'primary',35)
    # residential detour 3x longer should still win for duplicate traversal
    _add(g,a,c,120,'residential',1)
    _add(g,c,d,80,'residential',1)
    _add(g,d,b,100,'residential',1)
    pairs=_minimum_pairing_paths(g,[a,b])
    path=pairs[0][2]
    assert path in ([a,c,d,b], [b,d,c,a])


def test_boundary_duplicate_cost_can_be_higher_without_affecting_first_pass_length():
    g=nx.MultiGraph()
    a=(0.0,0.0); b=(0.001,0.0)
    g.add_edge(a,b,length=100,route_cost=100,duplicate_cost=200,
               highway='residential',required=True,boundary_near=True,
               geometry=LineString([a,b]))
    data=list(g.edges(data=True))[0][2]
    assert data['route_cost'] == 100
    assert data['duplicate_cost'] == 200
