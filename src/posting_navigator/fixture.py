from __future__ import annotations

from shapely.geometry import LineString, Polygon


def make_offline_fixture(boundary: Polygon) -> list[dict]:
    """通信不能時のE2E確認用。境界内に決定論的な道路状ネットワークを作る。実OSMではない。"""
    minx, miny, maxx, maxy = boundary.bounds
    roads: list[dict] = []
    xs = [minx + (maxx-minx)*f for f in (0.10, 0.25, 0.40, 0.55, 0.70, 0.85)]
    ys = [miny + (maxy-miny)*f for f in (0.12, 0.28, 0.44, 0.60, 0.76, 0.90)]
    candidates = [LineString([(x, miny), (x, maxy)]) for x in xs]
    candidates += [LineString([(minx, y), (maxx, y)]) for y in ys]
    candidates += [LineString([(minx, miny), (maxx, maxy)]), LineString([(minx, maxy), (maxx, miny)])]
    idx = 1
    for line in candidates:
        clipped = line.intersection(boundary)
        geoms = [clipped] if clipped.geom_type == "LineString" else list(getattr(clipped, "geoms", []))
        for geom in geoms:
            if geom.geom_type == "LineString" and geom.length > 1e-7:
                roads.append({"id": f"fixture-{idx}", "highway": "residential", "name": "offline fixture", "geometry": geom})
                idx += 1
    return roads
