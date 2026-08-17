from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import requests
from shapely.geometry import LineString, Polygon
from shapely.ops import linemerge, transform, unary_union
from pyproj import CRS, Transformer

# OpenStreetMap Wiki の Public Overpass API instances (2026-08 確認) を基準にする。
# kumi.systems は private.coffee へ移行済み。
DEFAULT_ENDPOINTS = (
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)

EXCLUDED_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link", "raceway", "construction", "proposed"}

# 429 を返した公開インスタンスへ連打しないため、Render プロセス内で簡易クールダウンする。
_ENDPOINT_COOLDOWN_UNTIL: dict[str, float] = {}


def overpass_query(poly: Polygon) -> str:
    minx, miny, maxx, maxy = poly.bounds
    bbox = f"{miny:.7f},{minx:.7f},{maxy:.7f},{maxx:.7f}"
    return f'''[out:json][timeout:60];
(
  way["highway"]({bbox});
  way["leisure"~"^(park|garden|playground)$"]({bbox});
  way["landuse"="recreation_ground"]({bbox});
);
out tags geom;'''


def _headers() -> dict[str, str]:
    # overpass-api.de はアプリを一意に識別できる User-Agent / Referer を推奨している。
    # 環境変数で本番URLや連絡先入り UA に差し替え可能。
    user_agent = os.getenv(
        "OVERPASS_USER_AGENT",
        "Posting-Navigator/1.1.2 (+https://tetsu069.github.io/Posting-Navigator/)",
    ).strip()
    referer = os.getenv(
        "OVERPASS_REFERER",
        "https://tetsu069.github.io/Posting-Navigator/",
    ).strip()
    return {
        "User-Agent": user_agent,
        "Referer": referer,
        "Accept": "application/json",
        "Accept-Language": "ja,en;q=0.8",
    }


def _retry_after_seconds(response: requests.Response) -> int:
    value = (response.headers.get("Retry-After") or "").strip()
    try:
        return max(1, min(int(value), 120))
    except ValueError:
        # overpass-api.de の公開利用方針は 429 時に 30 秒待つよう案内している。
        return 30


def _validate_overpass_json(data: object, endpoint: str) -> dict:
    if not isinstance(data, dict):
        raise RuntimeError(f"{endpoint}: JSON応答の形式が不正です")
    elements = data.get("elements")
    if not isinstance(elements, list):
        remark = data.get("remark")
        suffix = f" ({remark})" if remark else ""
        raise RuntimeError(f"{endpoint}: OSM elements がありません{suffix}")
    return data


def _post_query(session: requests.Session, endpoint: str, query: str, timeout: int) -> dict:
    headers = _headers()

    # 標準的な form POST。公式 Wiki の例と同じ data=<query> 形式。
    response = session.post(endpoint, data={"data": query}, headers=headers, timeout=timeout)

    # 一部環境で 406 が発生する場合に備え、同じ query を text/plain の raw POST でも試す。
    # 406 はクエリ処理前の拒否なので、負荷を増やす通常リトライとは分ける。
    if response.status_code == 406:
        raw_headers = dict(headers)
        raw_headers["Content-Type"] = "text/plain; charset=utf-8"
        response = session.post(endpoint, data=query.encode("utf-8"), headers=raw_headers, timeout=timeout)

    if response.status_code == 429:
        wait = _retry_after_seconds(response)
        _ENDPOINT_COOLDOWN_UNTIL[endpoint] = time.monotonic() + wait
        raise RuntimeError(f"HTTP 429 Too Many Requests（この接続先を{wait}秒クールダウン）")

    if 500 <= response.status_code <= 599:
        raise RuntimeError(f"HTTP {response.status_code}（Overpassサーバー一時障害）")

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:240].replace("\n", " ").strip()
        raise RuntimeError(f"HTTP {response.status_code}: {detail or exc}") from exc

    content_type = (response.headers.get("Content-Type") or "").lower()
    if "json" not in content_type and response.text.lstrip().startswith("<"):
        raise RuntimeError(f"HTML応答が返りました（Content-Type: {content_type or 'unknown'}）")
    try:
        return _validate_overpass_json(response.json(), endpoint)
    except ValueError as exc:
        raise RuntimeError("JSONとして解析できない応答です") from exc


def fetch_osm_roads(poly: Polygon, cache_path: str | Path | None = None, timeout: int = 90) -> dict:
    """Overpass APIから道路wayを取得する。

    - 有効なローカルキャッシュがあれば優先利用
    - User-Agent / Referer を付与
    - 複数の公開Overpassインスタンスへ自動フェイルオーバー
    - 429 を返した接続先は一定時間クールダウン
    - 406 の場合のみ raw text/plain POST へ互換フォールバック
    """
    if cache_path:
        cache_path = Path(cache_path)
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                return _validate_overpass_json(cached, "cache")
            except Exception:
                # 壊れた/旧形式キャッシュは捨てて取り直す。
                cache_path.unlink(missing_ok=True)

    endpoints_env = os.getenv("OVERPASS_ENDPOINTS", "").strip()
    endpoints = tuple(x.strip() for x in endpoints_env.split(",") if x.strip()) if endpoints_env else DEFAULT_ENDPOINTS
    if not endpoints:
        raise RuntimeError("Overpass接続先が設定されていません")

    query = overpass_query(poly)
    errors: list[str] = []
    now = time.monotonic()
    session = requests.Session()

    for endpoint in endpoints:
        cooldown_until = _ENDPOINT_COOLDOWN_UNTIL.get(endpoint, 0.0)
        if cooldown_until > now:
            remaining = max(1, int(round(cooldown_until - now)))
            errors.append(f"{endpoint}: 429クールダウン中（残り約{remaining}秒）")
            continue
        try:
            data = _post_query(session, endpoint, query, timeout)
            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                tmp.replace(cache_path)
            return data
        except Exception as exc:  # 次のミラーへフェイルオーバー
            errors.append(f"{endpoint}: {exc}")

    raise RuntimeError(
        "OSM道路取得に失敗しました。公開Overpassが混雑している場合は30秒ほど待って再試行してください。\n"
        + "\n".join(errors)
    )


def _metric_transformers(boundary: Polygon):
    """町丁目の重心を中心にした局所AEQD座標系を作る。距離判定をメートルで行うため。"""
    c = boundary.centroid
    crs_geo = CRS.from_epsg(4326)
    crs_local = CRS.from_proj4(
        f"+proj=aeqd +lat_0={c.y:.10f} +lon_0={c.x:.10f} +datum=WGS84 +units=m +no_defs"
    )
    fwd_t = Transformer.from_crs(crs_geo, crs_local, always_xy=True)
    inv_t = Transformer.from_crs(crs_local, crs_geo, always_xy=True)
    return fwd_t.transform, inv_t.transform


def _as_lines(geom) -> list[LineString]:
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "LineString" and not g.is_empty]


def _angle_diff_deg(a: float, b: float) -> float:
    d = abs((a - b) % 180.0)
    return min(d, 180.0 - d)


def _line_angle(line: LineString) -> float:
    coords = list(line.coords)
    if len(coords) < 2:
        return 0.0
    a, b = coords[0], coords[-1]
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0


def _boundary_tangent_angle(boundary_line, point, sample_m: float = 5.0) -> float:
    d = boundary_line.project(point)
    length = boundary_line.length
    a = boundary_line.interpolate(max(0.0, d - sample_m))
    b = boundary_line.interpolate(min(length, d + sample_m))
    return math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 180.0


def _is_boundary_parallel(chunk: LineString, boundary_line, max_dist_m: float) -> bool:
    """境界外の許容帯にある道路が『境界に沿う道路』か判定する。"""
    if chunk.length < 1.0:
        return False
    pts = [chunk.interpolate(frac, normalized=True) for frac in (0.0, 0.25, 0.5, 0.75, 1.0)]
    distances = [boundary_line.distance(pt) for pt in pts]
    if max(distances) > max_dist_m + 0.25:
        return False
    # 外向き枝道は境界からの距離が急増する。平行道路はほぼ一定。
    if max(distances) - min(distances) > 2.75:
        return False
    road_angle = _line_angle(chunk)
    tangent_angle = _boundary_tangent_angle(boundary_line, pts[2])
    return _angle_diff_deg(road_angle, tangent_angle) <= 32.0


def osm_json_to_lines(data: dict, boundary: Polygon) -> list[dict]:
    """OSM道路を町丁目内に限定し、配布価値の低い公園内通路を除外する。

    single-route版:
    - 巡回対象は町丁目ポリゴン内（境界誤差0.5mだけ許容）に限定。
    - 境界から外へ伸びる道路・外側の近接道路は採用しない。
    - park/garden/playground/recreation_ground 内を主に通る footway/path/pedestrian/steps/service は除外。
      公園沿いの一般道路や、公園を横切る通常の生活道路は残す。
    """
    roads: list[dict] = []
    fwd, inv = _metric_transformers(boundary)
    boundary_m = transform(fwd, boundary)
    safe_region = boundary_m.buffer(0.5)

    park_polys = []
    for element in data.get("elements", []):
        if element.get("type") != "way" or "geometry" not in element:
            continue
        tags = element.get("tags", {})
        is_park = tags.get("leisure") in {"park", "garden", "playground"} or tags.get("landuse") == "recreation_ground"
        if not is_park:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in element["geometry"]]
        if len(coords) >= 4 and coords[0] == coords[-1]:
            try:
                poly = Polygon(coords)
                if poly.is_valid and not poly.is_empty:
                    park_polys.append(transform(fwd, poly))
            except Exception:
                pass
    parks_m = unary_union(park_polys) if park_polys else None

    park_walk_highways = {"footway", "path", "pedestrian", "steps", "service"}
    for element in data.get("elements", []):
        if element.get("type") != "way" or "geometry" not in element:
            continue
        tags = element.get("tags", {})
        highway = tags.get("highway", "")
        if not highway or highway in EXCLUDED_HIGHWAYS:
            continue
        access = tags.get("access", "")
        if access == "no" and highway not in {"pedestrian", "footway"}:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in element["geometry"]]
        if len(coords) < 2:
            continue
        line_m = transform(fwd, LineString(coords))
        clipped = line_m.intersection(safe_region)
        for geom_m in _as_lines(clipped):
            if geom_m.length < 0.5:
                continue
            if parks_m is not None and highway in park_walk_highways:
                in_park = geom_m.intersection(parks_m).length
                if in_park / max(geom_m.length, 0.001) >= 0.50:
                    continue
            geom = transform(inv, geom_m)
            roads.append({
                "id": element.get("id"), "highway": highway, "name": tags.get("name", ""),
                "access": access, "service": tags.get("service", ""),
                "foot": tags.get("foot", ""), "boundary_near": False,
                "geometry": geom,
            })
    return roads
