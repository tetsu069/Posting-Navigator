import networkx as nx
from shapely.geometry import LineString
from posting_navigator.routing import _side_task_block_circuit

def edge(g,a,b,length=50.0):
    g.add_edge(a,b,geometry=LineString([a,b]),length=length,route_cost=length,highway='residential',required=True)

def test_straight_osm_split_is_not_selected_as_foldback_point():
    a=(139.0,35.0); b=(139.0003,35.0); c=(139.0006,35.0); d=(139.0006,35.0004); e=(139.0,35.0004)
    g=nx.MultiGraph(); edge(g,a,b,30); edge(g,b,c,30); edge(g,c,d,45); edge(g,d,e,60); edge(g,e,a,45)
    steps,_=_side_task_block_circuit(g,a,component=1)
    reversals=[]
    for x,y in zip(steps,steps[1:]):
        if x['from']==y['to'] and x['to']==y['from']:reversals.append(x['to'])
    assert b not in reversals
