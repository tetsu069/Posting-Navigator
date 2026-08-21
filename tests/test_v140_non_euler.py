import networkx as nx
from shapely.geometry import LineString
from posting_navigator.routing import _edge_coverage_walk

def add(g,a,b,highway='residential'):
    g.add_edge(a,b,length=10,route_cost=10,duplicate_cost=10,geometry=LineString([a,b]),highway=highway,required=True)

def test_tree_only_repeats_when_needed_to_reach_remaining_edges():
    a=(0,0); b=(.001,0); c=(.002,0); d=(.001,.001)
    g=nx.MultiGraph(); add(g,a,b,'primary'); add(g,b,c); add(g,b,d)
    steps,end=_edge_coverage_walk(g,g,a)
    covered={(min(s['from'],s['to']),max(s['from'],s['to'])) for s in steps if not s['transfer']}
    assert len(covered)==3
    assert sum(s['length_m'] for s in steps if s.get('duplicated') and s.get('highway')=='primary') == 0

def test_cycle_needs_no_duplicate_edges():
    a=(0,0); b=(.001,0); c=(.001,.001); d=(0,.001)
    g=nx.MultiGraph()
    for x,y in [(a,b),(b,c),(c,d),(d,a)]: add(g,x,y)
    steps,end=_edge_coverage_walk(g,g,a)
    assert sum(1 for s in steps if s.get('duplicated'))==0
    assert sum(1 for s in steps if not s.get('transfer'))==4
