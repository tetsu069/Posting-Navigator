import time

import networkx as nx

from posting_navigator.routing import _component_order, _dist_m


def test_local_distance_is_reasonable_for_tokyo_scale():
    # Roughly 100 m north/south and east/west around Tokyo.
    p = (139.713, 35.696)
    north = (139.713, 35.6969)
    east = (139.7141, 35.696)
    assert 95 < _dist_m(p, north) < 105
    assert 95 < _dist_m(p, east) < 105


def test_component_order_handles_many_components_quickly():
    g = nx.MultiGraph()
    # 240 disconnected components with 24 nodes each. This mirrors the
    # fragmentation that previously made the all-component/all-node scan costly.
    for ci in range(240):
        base_lon = 139.68 + (ci % 20) * 0.001
        base_lat = 35.68 + (ci // 20) * 0.001
        prev = None
        for ni in range(24):
            node = (base_lon + ni * 0.000003, base_lat)
            g.add_node(node)
            if prev is not None:
                g.add_edge(prev, node, length=0.3, route_cost=0.3)
            prev = node
    started = time.perf_counter()
    order = _component_order(g, (139.68, 35.68))
    elapsed = time.perf_counter() - started
    assert len(order) == 240
    assert elapsed < 3.0
