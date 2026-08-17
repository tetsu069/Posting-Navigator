import networkx as nx

from posting_navigator.routing import eulerize_weighted, _eulerize_open, EXACT_MATCHING_MAX_NODES


def _large_star(leaves=60):
    g = nx.MultiGraph()
    center = (0.0, 0.0)
    for i in range(leaves):
        leaf = (float(i + 1), 1.0)
        g.add_edge(center, leaf, length=1.0, route_cost=1.0, highway="residential", required=True)
    return g


def test_large_odd_set_avoids_blossom_matching(monkeypatch):
    assert 60 > EXACT_MATCHING_MAX_NODES
    g = _large_star(60)

    def forbidden(*args, **kwargs):
        raise AssertionError("large odd sets must not call min_weight_matching")

    monkeypatch.setattr(nx.algorithms.matching, "min_weight_matching", forbidden)
    out = eulerize_weighted(g)
    assert nx.is_eulerian(out)


def test_large_open_route_avoids_blossom_matching(monkeypatch):
    g = _large_star(60)
    leaves = [n for n in g.nodes if n != (0.0, 0.0)]
    start, end = leaves[0], leaves[1]

    def forbidden(*args, **kwargs):
        raise AssertionError("large open matching must not call min_weight_matching")

    monkeypatch.setattr(nx.algorithms.matching, "min_weight_matching", forbidden)
    out = _eulerize_open(g, start, end)
    odd = {n for n, degree in out.degree() if degree % 2 == 1}
    assert odd == {start, end}
