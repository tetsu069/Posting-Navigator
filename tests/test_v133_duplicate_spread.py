import networkx as nx
from shapely.geometry import LineString
from posting_navigator.routing import _spread_pairing_paths


def add(g,a,b,cost=1.0):
    g.add_edge(a,b,length=cost,route_cost=cost,duplicate_cost=cost,
               highway='residential',required=True,geometry=LineString([a,b]))


def test_pairing_paths_avoid_reusing_same_corridor_when_small_detour_exists():
    # Two matched pairs can both use the central corridor, but the second has a
    # modest alternate route.  v1.3.3 should spread parity duplication instead
    # of piling both pairs onto the same physical road.
    g=nx.MultiGraph()
    A=(0,0); B=(1,0); C=(2,0); D=(3,0)
    U=(1,1); V=(2,1)
    add(g,A,B); add(g,B,C); add(g,C,D)
    add(g,B,U,0.4); add(g,U,V,0.4); add(g,V,C,0.4)
    pairings=[(A,C,[A,B,C]),(B,D,[B,C,D])]
    out=_spread_pairing_paths(g,pairings)
    paths=[p for _,_,p in out]
    # The shared B-C edge must not be duplicated by both pairing paths.
    uses=sum(any({x,y}=={B,C} for x,y in zip(p,p[1:])) for p in paths)
    assert uses <= 1
