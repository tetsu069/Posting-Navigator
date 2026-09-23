from pathlib import Path


def test_large_block_candidate_search_is_bounded():
    src = Path('src/posting_navigator/routing.py').read_text()
    assert 'extra_orders=6' in src
    assert 'min(256,max(64,len(base)*6))' not in src


def test_balance_dijkstra_is_cached():
    src = Path('src/posting_navigator/routing.py').read_text()
    assert 'distance_cache={}' in src
    assert 'if a not in distance_cache' in src
