import networkx as nx
from shapely.geometry import LineString
from posting_navigator.routing import _service_mode, _side_task_block_circuit

def edge(g,a,b,**kw):
    d=dict(length=kw.pop('length',60.0), highway=kw.pop('highway','residential'), geometry=LineString([a,b]), route_cost=60.0, required=True)
    d.update(kw); g.add_edge(a,b,**d)

def test_boundary_is_one_side_only():
    g=nx.MultiGraph(); a=(0,0);b=(.001,0)
    edge(g,a,b,boundary_single_side=True,boundary_service_from=a,boundary_service_to=b)
    steps,_=_side_task_block_circuit(g,a,component=1)
    svc=[s for s in steps if s.get('side_service_task')]
    assert len(svc)==1 and svc[0]['service_mode']=='boundary-one-side'

def test_narrow_alley_both_sides_one_pass():
    g=nx.MultiGraph();a=(0,0);b=(.0003,0)
    edge(g,a,b,highway='living_street',length=30)
    steps,_=_side_task_block_circuit(g,a,component=1)
    svc=[s for s in steps if s.get('side_service_task')]
    assert len(svc)==1 and svc[0].get('both_sides_single_pass')

def test_dead_end_keeps_two_side_tasks():
    g=nx.MultiGraph();a=(0,0);b=(.0005,0)
    edge(g,a,b,length=55)
    steps,_=_side_task_block_circuit(g,a,component=1)
    svc=[s for s in steps if s.get('side_service_task')]
    assert len(svc)==2
    assert all(s['service_mode']=='dead-end' for s in svc)

def test_long_normal_has_two_deferred_side_tasks():
    g=nx.MultiGraph();a=(0,0);b=(.001,0);c=(.001,.001);d=(0,.001)
    for x,y in [(a,b),(b,c),(c,d),(d,a)]:edge(g,x,y,length=100)
    steps,_=_side_task_block_circuit(g,a,component=1)
    svc=[s for s in steps if s.get('side_service_task')]
    assert len(svc)==8
    assert all(s['service_mode']=='long-defer-opposite' for s in svc)
