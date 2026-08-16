import json
from pathlib import Path

import requests
from shapely.geometry import box

import posting_navigator.osm as osm


class FakeResponse:
    def __init__(self, status=200, payload=None, text=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})
        self.headers = headers or {"Content-Type": "application/json"}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_fetch_adds_identity_headers_and_writes_cache(monkeypatch, tmp_path):
    seen = {}

    def fake_post(self, url, data=None, headers=None, timeout=None):
        seen.update(url=url, data=data, headers=headers, timeout=timeout)
        return FakeResponse(200, {"elements": []})

    monkeypatch.setattr(requests.Session, "post", fake_post)
    monkeypatch.setenv("OVERPASS_ENDPOINTS", "https://example.invalid/api/interpreter")
    cache = tmp_path / "osm.json"
    result = osm.fetch_osm_roads(box(139.7, 35.6, 139.71, 35.61), cache, timeout=7)
    assert result == {"elements": []}
    assert seen["headers"]["User-Agent"].startswith("Posting-Navigator/")
    assert seen["headers"]["Referer"].startswith("https://")
    assert seen["headers"]["Accept"] == "application/json"
    assert seen["timeout"] == 7
    assert cache.exists()


def test_406_retries_as_text_plain(monkeypatch):
    calls = []

    def fake_post(self, url, data=None, headers=None, timeout=None):
        calls.append((data, dict(headers or {})))
        if len(calls) == 1:
            return FakeResponse(406, None, "Not Acceptable", {"Content-Type": "text/plain"})
        return FakeResponse(200, {"elements": []})

    monkeypatch.setattr(requests.Session, "post", fake_post)
    monkeypatch.setenv("OVERPASS_ENDPOINTS", "https://example.invalid/api/interpreter")
    osm.fetch_osm_roads(box(139.7, 35.6, 139.71, 35.61))
    assert len(calls) == 2
    assert isinstance(calls[0][0], dict)
    assert calls[1][1]["Content-Type"].startswith("text/plain")


def test_429_fails_over_to_next_endpoint(monkeypatch):
    osm._ENDPOINT_COOLDOWN_UNTIL.clear()
    called = []

    def fake_post(self, url, data=None, headers=None, timeout=None):
        called.append(url)
        if "one.invalid" in url:
            return FakeResponse(429, None, "rate limited", {"Retry-After": "30", "Content-Type": "text/plain"})
        return FakeResponse(200, {"elements": []})

    monkeypatch.setattr(requests.Session, "post", fake_post)
    monkeypatch.setenv(
        "OVERPASS_ENDPOINTS",
        "https://one.invalid/api/interpreter,https://two.invalid/api/interpreter",
    )
    result = osm.fetch_osm_roads(box(139.7, 35.6, 139.71, 35.61))
    assert result == {"elements": []}
    assert any("one.invalid" in x for x in called)
    assert any("two.invalid" in x for x in called)
    assert "https://one.invalid/api/interpreter" in osm._ENDPOINT_COOLDOWN_UNTIL
