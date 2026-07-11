from __future__ import annotations

import json
from pathlib import Path
import requests
from shapely.geometry import LineString, Polygon

DEFAULT_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

EXCLUDED_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link", "raceway", "construction", "proposed"}


def overpass_query(poly: Polygon) -> str:
    minx, miny, maxx, maxy = poly.bounds
    bbox = f"{miny:.7f},{minx:.7f},{maxy:.7f},{maxx:.7f}"
    return f'''[out:json][timeout:60];
(
  way["highway"]({bbox});
);
out tags geom;'''


def fetch_osm_roads(poly: Polygon, cache_path: str | Path | None = None, timeout: int = 90) -> dict:
    """Overpass APIから道路wayを取得。キャッシュがあれば優先利用。"""
    if cache_path:
        cache_path = Path(cache_path)
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for endpoint in DEFAULT_ENDPOINTS:
        try:
            response = requests.post(endpoint, data={"data": overpass_query(poly)}, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data
        except Exception as exc:  # retry mirror
            errors.append(f"{endpoint}: {exc}")
    raise RuntimeError("OSM道路取得に失敗しました\n" + "\n".join(errors))


def osm_json_to_lines(data: dict, boundary: Polygon) -> list[dict]:
    roads: list[dict] = []
    for element in data.get("elements", []):
        if element.get("type") != "way" or "geometry" not in element:
            continue
        tags = element.get("tags", {})
        highway = tags.get("highway", "")
        if highway in EXCLUDED_HIGHWAYS:
            continue
        coords = [(p["lon"], p["lat"]) for p in element["geometry"]]
        if len(coords) < 2:
            continue
        clipped = LineString(coords).intersection(boundary)
        geoms = [clipped] if clipped.geom_type == "LineString" else list(getattr(clipped, "geoms", []))
        for geom in geoms:
            if geom.geom_type == "LineString" and len(geom.coords) >= 2 and geom.length > 1e-7:
                roads.append({"id": element.get("id"), "highway": highway, "name": tags.get("name", ""), "geometry": geom})
    return roads
