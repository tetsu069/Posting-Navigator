from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import requests
from shapely.geometry import LineString, Polygon
from shapely.ops import linemerge, transform, unary_union
from shapely.strtree import STRtree
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
    """道路に加え、住宅密度と非住宅敷地の判定に必要なOSM要素も取得する。

    v1.0.13では「道路だから全部巡回」ではなく、住宅がある道路を配布対象にし、
    公園・学校・緑地の園路は必要な移動時だけconnectorとして残す。
    """
    minx, miny, maxx, maxy = poly.bounds
    # Include a small halo outside the town polygon.  Some in-area streets are
    # connected only by a junction whose centreline sits a few metres over the
    # administrative boundary.  v1.0.19 keeps that halo as transfer-only roads.
    query_buffer_m = float(os.getenv("OVERPASS_CONTEXT_BUFFER_M", "35"))
    lat0 = (miny + maxy) * 0.5
    dlat = query_buffer_m / 111_320.0
    dlon = query_buffer_m / max(1.0, 111_320.0 * math.cos(math.radians(lat0)))
    minx -= dlon; maxx += dlon; miny -= dlat; maxy += dlat
    bbox = f"{miny:.7f},{minx:.7f},{maxy:.7f},{maxx:.7f}"
    return f'''[out:json][timeout:75];
(
  way["highway"]({bbox});
  way["building"]({bbox});
  way["leisure"~"^(park|garden|playground|pitch|sports_centre)$"]({bbox});
  relation["leisure"~"^(park|garden|playground|pitch|sports_centre)$"]({bbox});
  way["landuse"~"^(grass|recreation_ground|forest|cemetery|allotments)$"]({bbox});
  relation["landuse"~"^(grass|recreation_ground|forest|cemetery|allotments)$"]({bbox});
  way["amenity"~"^(school|kindergarten|university|college)$"]({bbox});
  relation["amenity"~"^(school|kindergarten|university|college)$"]({bbox});
);
out tags geom;'''


def _headers() -> dict[str, str]:
    # overpass-api.de はアプリを一意に識別できる User-Agent / Referer を推奨している。
    # 環境変数で本番URLや連絡先入り UA に差し替え可能。
    user_agent = os.getenv(
        "OVERPASS_USER_AGENT",
        "Posting-Navigator/1.0.19 (+https://tetsu069.github.io/Posting-Navigator/)",
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
                # v1.0.13から建物・公園等のcontext要素が必要。旧道路-onlyキャッシュは使わない。
                if cached.get("_pn_cache_schema") != 3 or "data" not in cached:
                    raise ValueError("old cache schema")
                return _validate_overpass_json(cached["data"], "cache")
            except Exception:
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
                tmp.write_text(json.dumps({"_pn_cache_schema": 3, "data": data}, ensure_ascii=False), encoding="utf-8")
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



def _polygon_from_coords(coords):
    if len(coords) < 4 or coords[0] != coords[-1]:
        return None
    try:
        poly = Polygon(coords)
        if not poly.is_valid:
            poly = poly.buffer(0)
        return poly if not poly.is_empty else None
    except Exception:
        return None


def _element_polygons(element: dict) -> list[Polygon]:
    """way / multipolygon relation の外周を簡易Polygon化する。"""
    if element.get("type") == "way":
        geom = element.get("geometry") or []
        coords = [(p["lon"], p["lat"]) for p in geom if "lon" in p and "lat" in p]
        p = _polygon_from_coords(coords)
        return [p] if p is not None else []
    if element.get("type") == "relation":
        polys = []
        for member in element.get("members", []):
            if member.get("role") not in {"", "outer"}:
                continue
            geom = member.get("geometry") or []
            coords = [(p["lon"], p["lat"]) for p in geom if "lon" in p and "lat" in p]
            p = _polygon_from_coords(coords)
            if p is not None:
                polys.append(p)
        return polys
    return []


class _ContextIndex:
    """住宅/非住宅ポリゴンの軽量空間インデックス。

    v1.0.14: 全建物を unary_union して各道路との distance/intersection を総当たり
    する方式を廃止。STRtree で道路近傍だけを候補化してから厳密計算する。
    Render Free (512MB) でも大規模町丁目を処理できることを優先する。
    """

    __slots__ = ("buildings", "nonres", "building_tree", "nonres_tree")

    def __init__(self, buildings: list, nonres: list):
        self.buildings = buildings
        self.nonres = nonres
        self.building_tree = STRtree(buildings) if buildings else None
        self.nonres_tree = STRtree(nonres) if nonres else None

    @staticmethod
    def _indices(tree, query_geom) -> list[int]:
        if tree is None or query_geom.is_empty:
            return []
        # Shapely 2.x の STRtree.query は ndarray[int] を返す。
        return [int(i) for i in tree.query(query_geom)]

    def nearby_buildings(self, geom, radius_m: float = 50.0) -> list:
        if self.building_tree is None:
            return []
        # 住宅判定で必要なのは最大45mまで。50m envelope で候補だけ絞る。
        search = geom.buffer(radius_m, cap_style=2).envelope
        return [self.buildings[i] for i in self._indices(self.building_tree, search)]

    def intersecting_nonres(self, geom) -> list:
        if self.nonres_tree is None:
            return []
        return [self.nonres[i] for i in self._indices(self.nonres_tree, geom.envelope)]


def _context_index(data: dict, fwd) -> _ContextIndex:
    buildings: list = []
    nonres: list = []
    for element in data.get("elements", []):
        tags = element.get("tags", {})
        is_building = bool(tags.get("building") and tags.get("building") != "no")
        is_nonres = (
            tags.get("leisure") in {"park", "garden", "playground", "pitch", "sports_centre"}
            or tags.get("landuse") in {"grass", "recreation_ground", "forest", "cemetery", "allotments"}
            or tags.get("amenity") in {"school", "kindergarten", "university", "college"}
        )
        if not is_building and not is_nonres:
            continue
        for p in _element_polygons(element):
            try:
                pm = transform(fwd, p)
            except Exception:
                continue
            if pm.is_empty:
                continue
            if is_building:
                buildings.append(pm)
            if is_nonres:
                nonres.append(pm)
    return _ContextIndex(buildings, nonres)


def _road_context_score(geom_m: LineString, highway: str, service: str, context: _ContextIndex) -> tuple[bool, float, float, float]:
    """(posting_target, residential_score, nonres_overlap, nearest_building_m) を返す。

    v1.0.14 は STRtree で近傍候補だけを調べる。全建物 union と全道路×全建物の
    distance/intersection は行わない。
    """
    buildings = context.nearby_buildings(geom_m, 50.0)
    if not buildings:
        nearest = 9999.0
    else:
        nearest = min(float(b.distance(geom_m)) for b in buildings)

    corridor = geom_m.buffer(25.0, cap_style=2)
    if not buildings or corridor.is_empty or corridor.area <= 0:
        cover = 0.0
    else:
        # 建物同士は通常重ならないため、unionを作らず交差面積を合計する。
        # 万一重複しても住宅スコアは最終的に1へclipされる。
        inter_area = 0.0
        for b in buildings:
            if not b.intersects(corridor):
                continue
            try:
                inter_area += float(b.intersection(corridor).area)
            except Exception:
                pass
        cover = min(1.0, inter_area / corridor.area)

    nonres_candidates = context.intersecting_nonres(geom_m)
    if not nonres_candidates or geom_m.length <= 0:
        nonres_overlap = 0.0
    else:
        overlap_len = 0.0
        for p in nonres_candidates:
            if not p.intersects(geom_m):
                continue
            try:
                overlap_len += float(p.intersection(geom_m).length)
            except Exception:
                pass
        nonres_overlap = min(1.0, overlap_len / geom_m.length)

    if nearest <= 8:
        proximity = 1.0
    elif nearest <= 18:
        proximity = 0.8
    elif nearest <= 30:
        proximity = 0.55
    elif nearest <= 45:
        proximity = 0.25
    else:
        proximity = 0.05
    residential_score = max(0.0, min(1.0, 0.65 * proximity + min(0.35, cover * 5.0)))

    low_value_types = {"footway", "path", "pedestrian", "steps", "cycleway"}
    posting_target = True
    if highway in low_value_types and nonres_overlap >= 0.45 and nearest > 28.0:
        posting_target = False
    if highway in {"footway", "path", "steps", "cycleway"} and nearest > 45.0 and residential_score < 0.18:
        posting_target = False
    if highway == "service" and service in {"parking_aisle", "parking"} and nearest > 18.0:
        posting_target = False
    return posting_target, residential_score, nonres_overlap, nearest

def osm_json_to_lines(data: dict, boundary: Polygon) -> list[dict]:
    """Overpass道路を町丁目内の巡回可能な形状へ切り出す。

    v1.0.14:
    - 境界道路を『探す』8m帯と、実際に『歩いてよい』1.5m帯を分離。
    - 建物密度と公園・学校・緑地ポリゴンを使い、配布先のない園路をconnector-only化。
    - 通常道路は町丁目＋1.5mでクリップ。
    - 1.5〜8mの外側帯は境界と平行な道路だけ例外採用。
    - 境界から外向きに伸びる交差点枝は巡回グラフへ入れない。
    """
    roads: list[dict] = []
    candidate_buffer_m = float(os.getenv("BOUNDARY_ROAD_BUFFER_M", "8"))
    walk_tolerance_m = float(os.getenv("BOUNDARY_WALK_TOLERANCE_M", "1.5"))
    connector_buffer_m = float(os.getenv("CONNECTOR_ROAD_BUFFER_M", "20"))
    candidate_buffer_m = max(walk_tolerance_m, candidate_buffer_m)
    connector_buffer_m = max(candidate_buffer_m, connector_buffer_m)
    walk_tolerance_m = max(0.0, walk_tolerance_m)

    fwd, inv = _metric_transformers(boundary)
    boundary_m = transform(fwd, boundary)
    boundary_line_m = boundary_m.boundary
    context_index = _context_index(data, fwd)
    # posting candidate band is 8 m by default; mobility can use a slightly
    # wider 20 m halo, but those extra pieces are connector-only.
    candidate_region = boundary_m.buffer(connector_buffer_m)
    posting_candidate_region = boundary_m.buffer(candidate_buffer_m)
    safe_region = boundary_m.buffer(walk_tolerance_m)

    for element in data.get("elements", []):
        if element.get("type") != "way" or "geometry" not in element:
            continue
        tags = element.get("tags", {})
        highway = tags.get("highway", "")
        if highway in EXCLUDED_HIGHWAYS:
            continue
        access = tags.get("access", "")
        if access in {"no"} and highway not in {"pedestrian", "footway"}:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in element["geometry"]]
        if len(coords) < 2:
            continue

        line_m = transform(fwd, LineString(coords))
        # Strict distribution layer: only the safe in-area geometry plus truly
        # boundary-parallel roads are returned here.  Transfer-only roads are
        # produced separately by osm_json_to_mobility_lines().
        candidate = line_m.intersection(posting_candidate_region)
        accepted: list[LineString] = []
        for cand in _as_lines(candidate):
            inside = cand.intersection(safe_region)
            accepted.extend(g for g in _as_lines(inside) if g.length >= 0.5)

            fringe = cand.difference(safe_region)
            accepted.extend(
                g for g in _as_lines(fringe)
                if g.length >= 0.5 and _is_boundary_parallel(g, boundary_line_m, candidate_buffer_m)
            )

        if not accepted:
            continue
        united = unary_union(accepted)
        merged = united if united.geom_type == "LineString" else linemerge(united)
        for geom_m in _as_lines(merged):
            if geom_m.length < 0.5:
                continue
            posting_target, residential_score, nonres_overlap, nearest_building_m = _road_context_score(
                geom_m, highway, tags.get("service", ""), context_index
            )
            geom = transform(inv, geom_m)
            roads.append({
                "id": element.get("id"), "highway": highway, "name": tags.get("name", ""),
                "access": tags.get("access", ""), "service": tags.get("service", ""),
                "foot": tags.get("foot", ""), "boundary_near": not geom.within(boundary),
                "posting_target": posting_target,
                "connector_only": False,
                "residential_score": round(residential_score, 3),
                "nonresidential_overlap": round(nonres_overlap, 3),
                "nearest_building_m": round(nearest_building_m, 1),
                "geometry": geom,
            })
    return roads


def osm_json_to_mobility_lines(data: dict, boundary: Polygon) -> list[dict]:
    """Return the walking-connector road layer for routing transfers.

    v1.0.19 separates *where we distribute* from *where we are allowed to walk*.
    This layer keeps traversable OSM highways inside the town plus a small
    boundary halo (20 m by default), including parks/footways and short outward
    boundary stubs.  Every returned edge is connector-only; the strict posting
    layer from osm_json_to_lines() remains unchanged.
    """
    connector_buffer_m = max(0.0, float(os.getenv("CONNECTOR_ROAD_BUFFER_M", "20")))
    fwd, inv = _metric_transformers(boundary)
    boundary_m = transform(fwd, boundary)
    mobility_region = boundary_m.buffer(connector_buffer_m)
    roads: list[dict] = []
    for element in data.get("elements", []):
        if element.get("type") != "way" or "geometry" not in element:
            continue
        tags = element.get("tags", {})
        highway = tags.get("highway", "")
        if not highway or highway in EXCLUDED_HIGHWAYS:
            continue
        access = tags.get("access", "")
        if access in {"no", "private"} and highway not in {"pedestrian", "footway", "path", "steps"}:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in element["geometry"] if "lon" in pt and "lat" in pt]
        if len(coords) < 2:
            continue
        try:
            clipped = transform(fwd, LineString(coords)).intersection(mobility_region)
        except Exception:
            continue
        for geom_m in _as_lines(clipped):
            if geom_m.length < 0.5:
                continue
            geom = transform(inv, geom_m)
            roads.append({
                "id": element.get("id"), "highway": highway, "name": tags.get("name", ""),
                "access": access, "service": tags.get("service", ""), "foot": tags.get("foot", ""),
                "boundary_near": not geom.within(boundary),
                "posting_target": False, "connector_only": True,
                # Mobility edges are not distribution targets. The default score is
                # deliberately neutral; build_graph adds a connector-only penalty.
                "residential_score": 0.35, "nonresidential_overlap": 0.0,
                "geometry": geom,
            })
    return roads
