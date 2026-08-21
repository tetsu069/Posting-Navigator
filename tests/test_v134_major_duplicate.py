import networkx as nx
from posting_navigator.routing import _congestion_aware_path


def _edge(g, a, b, length, highway):
    g.add_edge(a, b, length=length, route_cost=length,
               duplicate_cost=length, highway=highway)


def test_parity_reroute_avoids_major_even_when_local_detour_exceeds_old_145_cap():
    g = nx.MultiGraph()
    # Direct arterial = 100m. Local alternative = 180m (> old 1.45x cap).
    _edge(g, 'A', 'B', 100, 'primary')
    _edge(g, 'A', 'C', 60, 'residential')
    _edge(g, 'C', 'D', 60, 'residential')
    _edge(g, 'D', 'B', 60, 'residential')
    path = _congestion_aware_path(g, 'A', 'B', {})
    assert path == ['A', 'C', 'D', 'B']
