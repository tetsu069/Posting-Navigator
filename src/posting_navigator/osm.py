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
        "Posting-Navigator/1.1.7 (+https://tetsu069.github.io/Posting-Navigator/)",
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


def _load_cache_file(cache_path: Path) -> dict | None:
    if not cache_path.exists():
        return None
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        return _validate_overpass_json(cached, "cache")
    except Exception:
        cache_path.unlink(missing_ok=True)
        return None


def fetch_osm_roads(
    poly: Polygon,
    cache_path: str | Path | None = None,
    timeout: int = 90,
    *,
    force_refresh: bool = False,
    return_source: bool = False,
):
    """Overpass APIから道路wayを取得する。

    v1.1.7 では「毎回Overpassへ行かない」を最優先にする。
    - 通常はローカルキャッシュを即利用する。
    - 明示的な force_refresh 時だけ最新OSMを取りに行く。
    - 更新取得に失敗しても既存キャッシュがあればそれを使って継続する。
    - ``return_source=True`` の場合は (data, source) を返す。
    """
    cache_file = Path(cache_path) if cache_path else None
    cached = _load_cache_file(cache_file) if cache_file else None
    if cached is not None and not force_refresh:
        return (cached, "cache") if return_source else cached

    endpoints_env = os.getenv("OVERPASS_ENDPOINTS", "").strip()
    endpoints = tuple(x.strip() for x in endpoints_env.split(",") if x.strip()) if endpoints_env else DEFAULT_ENDPOINTS
    if not endpoints:
        if cached is not None:
            return (cached, "stale-cache") if return_source else cached
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
            if cache_file:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache_file.with_suffix(cache_file.suffix + ".tmp")
                # 内部メタデータは保存してもOSM解析には影響しない。取得元確認にも使える。
                tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                tmp.replace(cache_file)
            return (data, "fresh") if return_source else data
        except Exception as exc:  # 次のミラーへフェイルオーバー
            errors.append(f"{endpoint}: {exc}")

    if cached is not None:
        return (cached, "stale-cache") if return_source else cached

    raise RuntimeError(
        "OSM道路取得に失敗しました。保存済みOSMキャッシュもありません。公開Overpassが復旧後に再試行してください。\n"
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
    """OSM道路を「配布必須」と「移動専用」に分けて町丁目へ取り込む。

    v1.1.3:
    - 住宅街の生活道路は配布必須(required=True)。
    - primary/secondary/tertiary 等の太い車道、公園内園路、駐車場内通路は
      配布必須にせず移動専用(required=False)として保持する。
    - 境界道路は、中心線が境界のすぐ外側にある場合でも、境界にほぼ平行で
      4m以内なら「境界沿い道路」として救済する。ただし外向き枝道は拾わない。
    - それ以外のエリア外道路は使わない。
    """
    roads: list[dict] = []
    fwd, inv = _metric_transformers(boundary)
    boundary_m = transform(fwd, boundary)
    boundary_line = boundary_m.boundary
    # v1.1.7: 巡回対象は町丁目ポリゴンの内側へ厳密にクリップする。
    # 境界外の中心線を「近いから」という理由だけで救済すると、縁から外向きの
    # 不要な往復が発生するため、外側バンドは巡回対象にしない。
    inside_region = boundary_m
    boundary_corridor = boundary_m.buffer(2.0)

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

    major = {"primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link"}
    residential_required = {"residential", "living_street", "unclassified"}
    walk_required = {"pedestrian", "footway", "path", "steps"}
    parking_services = {"parking_aisle", "driveway"}

    for element in data.get("elements", []):
        if element.get("type") != "way" or "geometry" not in element:
            continue
        tags = element.get("tags", {})
        highway = tags.get("highway", "")
        if not highway or highway in EXCLUDED_HIGHWAYS:
            continue
        access = tags.get("access", "")
        if access in {"no", "private"} and highway not in {"pedestrian", "footway"}:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in element["geometry"]]
        if len(coords) < 2:
            continue
        line_m = transform(fwd, LineString(coords))

        # まず町丁目内だけを採用。境界の中心線誤差だけ後段で救済する。
        chunks: list[tuple[LineString, bool]] = [(g, False) for g in _as_lines(line_m.intersection(inside_region)) if g.length >= 0.5]

        # v1.1.7: 境界外の道路中心線は巡回対象へ追加しない。
        # 境界道路の取りこぼしよりも「エリア外へ出るルートを作らない」ことを優先する。
        # 境界を横切る道路は上の exact intersection により、内側部分だけが残る。

        for geom_m, boundary_near in chunks:
            if geom_m.length < 0.5:
                continue
            service = tags.get("service", "")
            in_park_ratio = 0.0
            if parks_m is not None:
                try:
                    in_park_ratio = geom_m.intersection(parks_m).length / max(geom_m.length, 0.001)
                except Exception:
                    in_park_ratio = 0.0

            # 「通る必要がある場合だけ使える」道路は optional connector として残す。
            required = True
            if highway in major or highway == "cycleway":
                required = False
            elif highway == "service" and service in parking_services:
                required = False
            elif highway in walk_required and in_park_ratio >= 0.25:
                required = False
            elif highway == "service" and in_park_ratio >= 0.35:
                required = False
            elif highway not in residential_required | walk_required | {"service"}:
                required = False

            geom = transform(inv, geom_m)
            roads.append({
                "id": element.get("id"), "highway": highway, "name": tags.get("name", ""),
                "access": access, "service": service,
                "foot": tags.get("foot", ""), "boundary_near": boundary_near,
                "required": required,
                "geometry": geom,
            })
    return roads
