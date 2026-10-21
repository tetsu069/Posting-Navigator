from unittest.mock import patch
import pytest
pytest.importorskip("flask")
from posting_navigator.webapp import app


def test_gps_roads_rejects_invalid_coordinates():
    client = app.test_client()
    for query in ('lat=nan&lon=139', 'lat=95&lon=139', 'lat=35&lon=oops'):
        assert client.get('/api/gps-roads?' + query).status_code == 400


def test_gps_roads_fetches_and_returns_geometry(tmp_path):
    data = {'elements': [{'type': 'way', 'id': 1, 'tags': {'highway': 'residential'},
                          'geometry': [{'lat': 35.0, 'lon': 139.0}, {'lat': 35.001, 'lon': 139.001}]}]}
    with patch('posting_navigator.webapp.OSM_CACHE', tmp_path), patch('posting_navigator.osm._post_query', return_value=data):
        response = app.test_client().get('/api/gps-roads?lat=35.1&lon=139.2')
        assert response.status_code == 200
        assert response.json == data
        response = app.test_client().get('/api/gps-roads?lat=35.1&lon=139.2')
        assert response.status_code == 200
