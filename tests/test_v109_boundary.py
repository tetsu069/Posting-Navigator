from shapely.geometry import box
from pyproj import CRS, Transformer
from shapely.ops import transform

from posting_navigator.osm import osm_json_to_lines


def _way(osm_id, coords, highway="residential"):
    return {
        "type": "way",
        "id": osm_id,
        "tags": {"highway": highway},
        "geometry": [{"lon": x, "lat": y} for x, y in coords],
    }


def _local(boundary):
    c = boundary.centroid
    local = CRS.from_proj4(
        f"+proj=aeqd +lat_0={c.y} +lon_0={c.x} +datum=WGS84 +units=m +no_defs"
    )
    return Transformer.from_crs(4326, local, always_xy=True).transform


def test_boundary_parallel_road_kept_but_outward_branch_trimmed(monkeypatch):
    monkeypatch.setenv("BOUNDARY_ROAD_BUFFER_M", "8")
    monkeypatch.setenv("BOUNDARY_WALK_TOLERANCE_M", "1.5")
    boundary = box(139.0000, 35.0000, 139.0010, 35.0010)
    # 北境界の約4.4m外側を平行に走る道路 -> 救済対象
    parallel = [(139.0001, 35.00104), (139.0009, 35.00104)]
    # 境界を垂直に横切って外へ伸びる枝 -> 1.5m許容を超える外側部分は除外
    outward = [(139.0005, 35.0007), (139.0005, 35.00108)]
    data = {"elements": [_way(1, parallel), _way(2, outward)]}
    roads = osm_json_to_lines(data, boundary)
    ids = {r["id"] for r in roads}
    assert 1 in ids and 2 in ids

    fwd = _local(boundary)
    b_m = transform(fwd, boundary)
    by_id = {}
    for r in roads:
        by_id.setdefault(r["id"], []).append(transform(fwd, r["geometry"]))

    # 平行道路は境界から約4m外でも残る。
    assert any(g.distance(b_m) >= 2.0 for g in by_id[1])
    # 垂直枝は安全許容幅付近までしか外へ残らない。
    assert max(g.difference(b_m).length for g in by_id[2]) < 2.5


def test_outside_perpendicular_stub_is_not_kept_as_separate_road(monkeypatch):
    monkeypatch.setenv("BOUNDARY_ROAD_BUFFER_M", "8")
    monkeypatch.setenv("BOUNDARY_WALK_TOLERANCE_M", "1.5")
    boundary = box(139.0000, 35.0000, 139.0010, 35.0010)
    outward_only = [(139.0003, 35.00101), (139.0003, 35.00107)]
    roads = osm_json_to_lines({"elements": [_way(3, outward_only)]}, boundary)
    # 境界から外向きにだけ延びる道路は『境界道路』扱いしない。
    # 1.5mの誤差許容に入るごく短い部分だけなら残り得るが、長い外向き枝は残らない。
    fwd = _local(boundary)
    b_m = transform(fwd, boundary)
    outside_lengths = [transform(fwd, r["geometry"]).difference(b_m).length for r in roads]
    assert not outside_lengths or max(outside_lengths) < 2.5
