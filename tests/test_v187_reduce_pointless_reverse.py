import networkx as nx
from shapely.geometry import LineString
from posting_navigator.routing import _side_task_block_circuit

def edge(g,a,b,length=50.0):
    g.add_edge(a,b,geometry=LineString([a,b]),length=length,route_cost=length,highway="residential",required=True)

def test_cycle_does_not_ping_pong_at_multiple_corners():
    a=(139.0,35.0); b=(139.0005,35.0); c=(139.0005,35.0004); d=(139.0,35.0004)
    g=nx.MultiGraph(); edge(g,a,b); edge(g,b,c); edge(g,c,d); edge(g,d,a)
    steps,_=_side_task_block_circuit(g,a,component=1,movement_graph=g)
    rev=sum(1 for x,y in zip(steps,steps[1:]) if x["from"]==y["to"] and x["to"]==y["from"])
    assert rev <= 1
