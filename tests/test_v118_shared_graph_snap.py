from shapely.geometry import LineString
from posting_navigator.routing import generate_route


def road(i, coords, target=True, connector=False):
    return {'id':i,'highway':'residential','name':str(i),'posting_target':target,
            'connector_only':connector,'geometry':LineString(coords)}


def test_shared_graph_uses_same_nodes_for_target_and_connector():
    left=road(1,[(139.0,35.0),(139.001,35.0)])
    right=road(2,[(139.002,35.0),(139.003,35.0)])
    conn=road(3,[(139.001,35.0),(139.001,35.0002),(139.002,35.0002),(139.002,35.0)],False,True)
    r=generate_route([left,right], connector_roads=[left,conn,right])
    assert r['component_count']==2
    assert any(s['transfer'] for s in r['route_steps'])


def test_sub_metre_endpoint_gap_is_snapped_without_fake_transfer_line():
    # ~0.55m longitude gap at Tokyo latitude.
    gap=0.000006
    left=road(1,[(139.0,35.0),(139.001,35.0)])
    right=road(2,[(139.002,35.0),(139.003,35.0)])
    conn=road(3,[(139.001+gap,35.0),(139.0015,35.0002),(139.002-gap,35.0)],False,True)
    r=generate_route([left,right], connector_roads=[left,conn,right])
    assert r['road_only_route'] is True
    assert r['transfer_length_m'] > 0


def test_large_gap_is_not_silently_snapped():
    left=road(1,[(139.0,35.0),(139.001,35.0)])
    right=road(2,[(139.002,35.0),(139.003,35.0)])
    # ~11m gaps: must remain disconnected and error, not invent a line.
    conn=road(3,[(139.00112,35.0),(139.0015,35.0002),(139.00188,35.0)],False,True)
    try:
        generate_route([left,right], connector_roads=[left,conn,right])
    except ValueError as e:
        assert '接続' in str(e)
    else:
        raise AssertionError('large gap must not be auto-connected')
