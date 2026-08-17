import json
from pathlib import Path
from unittest.mock import Mock

from shapely.geometry import Polygon

from posting_navigator import osm


def sample_data():
    return {"version": 0.6, "elements": [{"type": "way", "id": 1, "tags": {"highway": "residential"}, "geometry": [{"lat":35.0,"lon":139.0},{"lat":35.0001,"lon":139.0001}]}]}


def test_existing_cache_skips_network(tmp_path, monkeypatch):
    cache = tmp_path / 'a.json'
    cache.write_text(json.dumps(sample_data()), encoding='utf-8')
    called = False
    def boom(*a, **k):
        nonlocal called
        called = True
        raise AssertionError('network should not be called')
    monkeypatch.setattr(osm, '_post_query', boom)
    data, source = osm.fetch_osm_roads(Polygon([(139,35),(139.01,35),(139.01,35.01),(139,35.01)]), cache, return_source=True)
    assert source == 'cache'
    assert called is False


def test_refresh_failure_falls_back_to_cache(tmp_path, monkeypatch):
    cache = tmp_path / 'a.json'
    cache.write_text(json.dumps(sample_data()), encoding='utf-8')
    monkeypatch.setattr(osm, 'DEFAULT_ENDPOINTS', ('https://example.invalid',))
    monkeypatch.setattr(osm, '_post_query', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('down')))
    data, source = osm.fetch_osm_roads(Polygon([(139,35),(139.01,35),(139.01,35.01),(139,35.01)]), cache, force_refresh=True, return_source=True)
    assert source == 'stale-cache'


def test_fresh_fetch_writes_cache(tmp_path, monkeypatch):
    cache = tmp_path / 'a.json'
    monkeypatch.setattr(osm, 'DEFAULT_ENDPOINTS', ('https://example.test',))
    monkeypatch.setattr(osm, '_post_query', lambda *a, **k: sample_data())
    data, source = osm.fetch_osm_roads(Polygon([(139,35),(139.01,35),(139.01,35.01),(139,35.01)]), cache, return_source=True)
    assert source == 'fresh'
    assert cache.exists()
    saved = json.loads(cache.read_text(encoding='utf-8'))
    assert isinstance(saved['elements'], list)
