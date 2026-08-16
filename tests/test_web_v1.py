import json
from pathlib import Path
import pytest

pytest.importorskip("flask")
pytest.importorskip("flask_cors")


def test_health_and_config(tmp_path, monkeypatch):
    monkeypatch.setenv('POSTING_NAV_DB', str(tmp_path / 'test.db'))
    from posting_navigator.webapp import app
    c = app.test_client()
    assert c.get('/api/health').get_json()['version'] == '1.0.0'
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
