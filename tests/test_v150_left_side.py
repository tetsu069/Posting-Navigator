import networkx as nx
from shapely.geometry import LineString
from posting_navigator.routing import _edge_coverage_walk, _assign_local_sweep_blocks


def _g():
    g=nx.MultiGraph()
    # 2x2-ish connected residential block
    edges=[((0.0,0.0),(0.001,0.0)),((0.001,0.0),(0.001,0.001)),((0.001,0.001),(0.0,0.001)),((0.0,0.001),(0.0,0.0)),((0.0,0.0),(0.001,0.001))]
    for i,(u,v) in enumerate(edges):
        geom=LineString([u,v])
        g.add_edge(u,v,key=i,length=100.0,route_cost=100.0,duplicate_cost=100.0,highway='residential',required=True,geometry=geom,name='',osm_id=i,boundary_near=False)
    return g


def test_each_required_edge_is_traversed_once_each_direction():
    g=_g()
    _assign_local_sweep_blocks(g,(0.0,0.0),cell_m=500.0)
    steps,end=_edge_coverage_walk(g,g,(0.0,0.0))
    cov=[s for s in steps if not s.get('transfer')]
    # 5 physical edges x 2 directions
    assert len(cov)==10
    pairs={}
    for s in cov:
        key=frozenset((s['from'],s['to']))
        pairs.setdefault(key,[]).append((s['from'],s['to']))
        assert s.get('left_side_pass') is True
    assert len(pairs)==5
    for dirs in pairs.values():
        assert len(dirs)==2
        assert dirs[0][0]==dirs[1][1] and dirs[0][1]==dirs[1][0]


def test_left_side_block_coverage_has_theoretical_minimum_inside_block():
    g=_g()
    _assign_local_sweep_blocks(g,(0.0,0.0),cell_m=500.0)
    steps,_=_edge_coverage_walk(g,g,(0.0,0.0))
    assert len([s for s in steps if not s.get('transfer')]) == 10
