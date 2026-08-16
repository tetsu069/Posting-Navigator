from posting_navigator.kmz import load_area_from_kmz
from posting_navigator.fixture import make_offline_fixture
from posting_navigator.routing import generate_worker_routes, partition_worker_areas

KMZ='data/input/shinjuku_posting_map.kmz'

def test_worker_areas_are_geographic_and_cover_multiple_workers():
    boundary=load_area_from_kmz(KMZ,'北新宿一丁目')
    roads=make_offline_fixture(boundary)
    parts=partition_worker_areas(boundary,roads,4)
    assert len(parts)==4
    assert all(not p['polygon'].is_empty and p['roads'] for p in parts)
    # Every assigned polygon remains inside the original town boundary (with numeric tolerance).
    assert all(boundary.buffer(1e-9).covers(p['polygon']) for p in parts)

def test_worker_routes_are_independent_and_households_sum_exactly():
    boundary=load_area_from_kmz(KMZ,'北新宿一丁目')
    roads=make_offline_fixture(boundary)
    a=generate_worker_routes(boundary,roads,3,households=1234)
    assert len(a)==3
    assert sum(x['estimated_households'] for x in a)==1234
    assert all(x['length_m']>0 and x['navigation_legs'] for x in a)
    assert all(x['worker_area'] is not None for x in a)
