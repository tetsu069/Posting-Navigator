import json
from pathlib import Path
import pytest

pytest.importorskip("flask")
pytest.importorskip("flask_cors")


def test_health_and_config(tmp_path, monkeypatch):
    monkeypatch.setenv('POSTING_NAV_DB', str(tmp_path / 'test.db'))
    from posting_navigator.webapp import app
    c = app.test_client()
    assert c.get('/api/health').get_json()['version'] == '1.0.21'
    cfg = c.get('/api/config').get_json()
    assert cfg['gps_threshold_m'] > 0


def test_project_progress_roundtrip(tmp_path, monkeypatch):
    # webapp uses the DB initialized at import; isolate rows using a unique project id via direct DB.
    import posting_navigator.webapp as w
    import uuid, time
    pid = uuid.uuid4().hex
    code = uuid.uuid4().hex[:6].upper()
    geo = {'type':'FeatureCollection','features':[]}
    summary = {'worker_count': 1}
    with w._db() as con:
        con.execute('INSERT INTO projects(id,share_code,area,worker_count,geojson,summary,created_at) VALUES(?,?,?,?,?,?,?)',
                    (pid, code, 'テスト町', 1, json.dumps(geo), json.dumps(summary), int(time.time())))
        con.execute('INSERT INTO progress(project_id,worker_id,total_distance_m,updated_at) VALUES(?,?,?,?)',
                    (pid, 1, 1000, int(time.time())))
    c = w.app.test_client()
    r = c.post(f'/api/projects/{pid}/progress/1', json={'completed_segments':[0,1,2], 'completed_distance_m':250})
    assert r.status_code == 200
    j = c.get(f'/api/projects/{pid}/progress').get_json()
    assert j['workers'][0]['percent'] == 25.0
    assert j['workers'][0]['completed_segments'] == [0,1,2]


def test_areas_returns_boundary_geojson():
    import io
    import posting_navigator.webapp as w
    kmz = Path(__file__).parents[1] / 'data' / 'input' / 'shinjuku_posting_map.kmz'
    c = w.app.test_client()
    with kmz.open('rb') as fh:
        r = c.post('/api/areas', data={'kmz': (io.BytesIO(fh.read()), 'areas.kmz')}, content_type='multipart/form-data')
    assert r.status_code == 200
    j = r.get_json()
    assert j['areas']
    assert j['area_geojson']['type'] == 'FeatureCollection'
    assert len(j['area_geojson']['features']) == len(j['areas'])


def test_build_accepts_kmz_again_in_multipart(tmp_path, monkeypatch):
    import io
    import json
    import posting_navigator.webapp as w

    def fake_run_build(*, kmz, area, output, workers, start_lat, start_lon, cache, offline_fallback):
        assert Path(kmz).exists()
        assert area == '富久町'
        assert workers == 2
        assert offline_fallback is False
        output.mkdir(parents=True, exist_ok=True)
        geo = {'type':'FeatureCollection','features':[]}
        (output / 'posting_navigator.geojson').write_text(json.dumps(geo), encoding='utf-8')
        (output / 'summary.json').write_text(json.dumps({'worker_count':2,'data_mode':'test'}), encoding='utf-8')
        return {'worker_count':2,'data_mode':'test'}

    monkeypatch.setattr(w, 'run_build', fake_run_build)
    kmz = Path(__file__).parents[1] / 'data' / 'input' / 'shinjuku_posting_map.kmz'
    c = w.app.test_client()
    with kmz.open('rb') as fh:
        r = c.post('/api/build', data={
            'kmz': (io.BytesIO(fh.read()), 'areas.kmz'),
            'area': '富久町',
            'workers': '2',
            'start_lat': '',
            'start_lon': '',
            'offline_fallback': '0',
        }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()['summary']['data_mode'] == 'test'
