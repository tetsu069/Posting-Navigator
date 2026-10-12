import io
import pytest
pytest.importorskip("flask")
from posting_navigator.webapp import app


def test_actual_kmz_diagnostic_does_not_persist():
    client = app.test_client()
    response = client.post('/api/diagnostics/upload?inspect=1', data={'kmz': (io.BytesIO(b'PK\x03\x04example'), 'test.kmz')})
    assert response.status_code == 200
    data = response.get_json()
    assert data['received_file_bytes'] == 11
    assert data['is_zip'] is True
    assert data['persisted'] is False


def test_actual_kmz_diagnostic_requires_file():
    response = app.test_client().post('/api/diagnostics/upload?inspect=1')
    assert response.status_code == 400
