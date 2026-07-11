from __future__ import annotations

import argparse
import json
from pathlib import Path

from .kmz import load_area_from_kmz, write_boundary_geojson
from .osm import fetch_osm_roads, osm_json_to_lines
from .fixture import make_offline_fixture
from .routing import generate_route, split_route
from .export import write_assignments_csv, write_geojson, write_kml, write_kmz


def build(args: argparse.Namespace) -> int:
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    boundary = load_area_from_kmz(args.kmz, args.area)
    write_boundary_geojson(boundary, args.area, out / "boundary.geojson")

    mode = "osm"
    try:
        data = fetch_osm_roads(boundary, args.cache)
        roads = osm_json_to_lines(data, boundary)
        if not roads:
            raise RuntimeError("境界内の道路が0件です")
    except Exception as exc:
        if not args.offline_fallback:
            raise
        mode = "offline-fixture"
        print(f"[WARN] OSM取得失敗。オフラインフィクスチャで継続: {exc}")
        roads = make_offline_fixture(boundary)

    start_point = (args.start_lon, args.start_lat) if args.start_lon is not None and args.start_lat is not None else None
    route = generate_route(roads, start_point=start_point)
    route["data_mode"] = mode
    route["worker_count"] = args.workers
    route["assignments"] = split_route(route, args.workers)
    kml = write_kml(args.area, boundary, roads, route, out / "posting_navigator.kml")
    write_kmz(kml, out / "posting_navigator.kmz")
    write_geojson(args.area, boundary, roads, route, out / "posting_navigator.geojson")
    write_assignments_csv(route["assignments"], out / "assignments.csv")
    workers_dir = out / "workers"
    workers_dir.mkdir(exist_ok=True)
    for assignment in route["assignments"]:
        worker_route = dict(route)
        worker_route["geometry"] = assignment["geometry"]
        worker_route["start_point"] = assignment["start_point"]
        worker_route["route_length_m"] = assignment["length_m"]
        worker_route["assignments"] = [assignment]
        stem = f"worker_{assignment['worker_id']:02d}"
        worker_kml = write_kml(f"{args.area} {assignment['name']}", boundary, roads, worker_route, workers_dir / f"{stem}.kml")
        write_kmz(worker_kml, workers_dir / f"{stem}.kmz")
    (out / "summary.json").write_text(json.dumps({**{k:v for k,v in route.items() if k not in {"geometry", "start_point", "requested_start", "assignments"}}, "assignments": [{k:v for k,v in a.items() if k not in {"geometry", "start_point", "end_point"}} for a in route["assignments"]]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**{k:v for k,v in route.items() if k not in {"geometry", "start_point", "requested_start", "assignments"}}, "assignments": [{k:v for k,v in a.items() if k not in {"geometry", "start_point", "end_point"}} for a in route["assignments"]]}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="posting-navigator")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="町丁目境界から巡回ルートとKML/KMZを生成")
    p.add_argument("--kmz", required=True)
    p.add_argument("--area", required=True)
    p.add_argument("--output", default="output")
    p.add_argument("--cache", default="data/cache/osm_roads.json")
    p.add_argument("--offline-fallback", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--start-lon", type=float, help="開始地点の経度（最寄り道路ノードへ補正）")
    p.add_argument("--start-lat", type=float, help="開始地点の緯度（最寄り道路ノードへ補正）")
    p.add_argument("--workers", type=int, default=1, help="担当者数。巡回ルートを距離均等に連続分割（既定: 1）")
    p.set_defaults(func=build)
    args = parser.parse_args()
    return args.func(args)

if __name__ == "__main__":
    raise SystemExit(main())
