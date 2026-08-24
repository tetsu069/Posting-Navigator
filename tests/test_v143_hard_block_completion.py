import networkx as nx
from posting_navigator.routing import _edge_coverage_walk

def test_does_not_leave_current_block_with_required_edges_remaining():
    # Two adjacent blocks.  Starting in A, every A coverage edge must be consumed
    # before the first B coverage edge appears in the output.
    req=nx.MultiGraph(); full=nx.MultiGraph()
    A=(0.0,0.0); B=(0.001,0.0); C=(0.001,0.001); D=(0.0,0.001); E=(0.002,0.0); F=(0.002,0.001)
    edges=[(A,B,'A'),(B,C,'A'),(C,D,'A'),(D,A,'A'),(B,E,'B'),(E,F,'B'),(F,C,'B')]
    for i,(u,v,bid) in enumerate(edges):
        d={'length':100.0,'route_cost':100.0,'required':True,'sweep_block':bid,'sweep_rank':0 if bid=='A' else 1,'highway':'residential','geometry':None,'id':i}
        req.add_edge(u,v,key=i,**d); full.add_edge(u,v,key=i,**d)
    steps,_=_edge_coverage_walk(req,full,A)
    cov=[s.get('sweep_block') for s in steps if not s.get('transfer')]
    first_b=cov.index('B')
    assert 'A' not in cov[first_b:]
