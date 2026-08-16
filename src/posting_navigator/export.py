from __future__ import annotations

import csv
import html
import json
import zipfile
from pathlib import Path
from shapely.geometry import Polygon, mapping


def _coords_text(coords) -> str:
    return " ".join(f"{x:.7f},{y:.7f},0" for x, y in coords)


def _route_properties(route: dict) -> dict:
    excluded = {"geometry", "start_point", "requested_start", "assignments", "route_steps", "navigation_legs"}
    return {k: v for k, v in route.items() if k not in excluded}


def _assignment_properties(assignment: dict) -> dict:
    return {k: v for k, v in assignment.items() if k not in {"geometry", "worker_area", "start_point", "end_point", "route_steps", "navigation_legs"}}


def write_kml(area_name: str, boundary: Polygon, roads: list[dict], route: dict, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    road_placemarks = []
    for road in roads:
        road_placemarks.append(f'''<Placemark><name>{html.escape(road.get("name") or road.get("highway") or "道路")}</name>
<styleUrl>#road</styleUrl><ExtendedData><Data name="highway"><value>{html.escape(road.get("highway", ""))}</value></Data></ExtendedData><LineString><tessellate>1</tessellate><coordinates>{_coords_text(road["geometry"].coords)}</coordinates></LineString></Placemark>''')
    desc = html.escape(json.dumps(_route_properties(route), ensure_ascii=False))
    start = route["start_point"]
    worker_styles = "".join(
        f'<Style id="worker{i}"><LineStyle><color>{color}</color><width>6</width></LineStyle></Style>'
        for i, color in enumerate(["ff0000ff", "ff00a5ff", "ff00cc00", "ffff0000", "ff00ffff", "ffff00ff", "ff0088ff", "ff888800"], start=1)
    )
    assignment_folders = []
    for assignment in route.get("assignments", []):
        props = html.escape(json.dumps(_assignment_properties(assignment), ensure_ascii=False))
        style_id = ((assignment["worker_id"] - 1) % 8) + 1
        s, e = assignment["start_point"], assignment["end_point"]
        area_pm = ""
        if assignment.get("worker_area") is not None and assignment["worker_area"].geom_type == "Polygon":
            area_pm = f'<Placemark><name>{html.escape(assignment["name"])} 担当エリア</name><styleUrl>#area</styleUrl><Polygon><outerBoundaryIs><LinearRing><coordinates>{_coords_text(assignment["worker_area"].exterior.coords)}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>'
        assignment_folders.append(f'''<Folder><name>{html.escape(assignment["name"])}</name>
{area_pm}
<Placemark><name>{html.escape(assignment["name"])} 巡回区間</name><description>{props}</description><styleUrl>#worker{style_id}</styleUrl><LineString><tessellate>1</tessellate><coordinates>{_coords_text(assignment["geometry"].coords)}</coordinates></LineString></Placemark>
<Placemark><name>{html.escape(assignment["name"])} 開始</name><styleUrl>#start</styleUrl><Point><coordinates>{s.x:.7f},{s.y:.7f},0</coordinates></Point></Placemark>
<Placemark><name>{html.escape(assignment["name"])} 終了</name><Point><coordinates>{e.x:.7f},{e.y:.7f},0</coordinates></Point></Placemark></Folder>''')
    assignment_section = f'<Folder><name>05 担当別ルート</name>{"".join(assignment_folders)}</Folder>' if assignment_folders else ""
    kml = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<name>Posting Navigator - {html.escape(area_name)}</name>
<Style id="area"><LineStyle><color>ff0000ff</color><width>3</width></LineStyle><PolyStyle><color>330000ff</color></PolyStyle></Style>
<Style id="road"><LineStyle><color>88999999</color><width>2</width></LineStyle></Style>
<Style id="route"><LineStyle><color>ff00a5ff</color><width>5</width></LineStyle></Style>
<Style id="start"><IconStyle><scale>1.2</scale><Icon><href>http://maps.google.com/mapfiles/kml/paddle/grn-circle.png</href></Icon></IconStyle></Style>
{worker_styles}
<Folder><name>01 区画</name><Placemark><name>{html.escape(area_name)}</name><styleUrl>#area</styleUrl><Polygon><outerBoundaryIs><LinearRing><coordinates>{_coords_text(boundary.exterior.coords)}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Folder>
<Folder><name>02 対象道路</name>{''.join(road_placemarks)}</Folder>
<Folder><name>03 巡回ルート</name><Placemark><name>{html.escape(area_name)} 巡回ルート</name><description>{desc}</description><styleUrl>#route</styleUrl><LineString><tessellate>1</tessellate><coordinates>{_coords_text(route["geometry"].coords)}</coordinates></LineString></Placemark></Folder>
<Folder><name>04 開始地点</name><Placemark><name>開始地点</name><styleUrl>#start</styleUrl><Point><coordinates>{start.x:.7f},{start.y:.7f},0</coordinates></Point></Placemark></Folder>
{assignment_section}
</Document></kml>'''
    output_path.write_text(kml, encoding="utf-8")
    return output_path


def write_kmz(kml_path: str | Path, kmz_path: str | Path) -> Path:
    kml_path, kmz_path = Path(kml_path), Path(kmz_path)
    with zipfile.ZipFile(kmz_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(kml_path, "doc.kml")
    return kmz_path


def write_geojson(area_name: str, boundary: Polygon, roads: list[dict], route: dict, output_path: str | Path) -> Path:
    features = [{"type": "Feature", "properties": {"kind": "area", "name": area_name}, "geometry": mapping(boundary)}]
    features += [{"type": "Feature", "properties": {"kind": "road", "highway": r.get("highway"), "name": r.get("name")}, "geometry": mapping(r["geometry"])} for r in roads]
    features.append({"type": "Feature", "properties": {"kind": "route", **_route_properties(route)}, "geometry": mapping(route["geometry"])})
    for step in route.get("route_steps", []):
        props = {k: v for k, v in step.items() if k not in {"geometry", "from", "to"}}
        features.append({"type": "Feature", "properties": {"kind": "route_step", **props}, "geometry": mapping(step["geometry"])})
    for leg in route.get("navigation_legs", []):
        props = {k: v for k, v in leg.items() if k != "geometry"}
        features.append({"type": "Feature", "properties": {"kind": "navigation_leg", **props}, "geometry": mapping(leg["geometry"])})
    features.append({"type": "Feature", "properties": {"kind": "start", "name": "開始地点"}, "geometry": mapping(route["start_point"])})
    for assignment in route.get("assignments", []):
        props = _assignment_properties(assignment)
        if assignment.get("worker_area") is not None:
            features.append({"type": "Feature", "properties": {"kind": "worker_area", **props}, "geometry": mapping(assignment["worker_area"])})
        features.append({"type": "Feature", "properties": {"kind": "worker_route", **props}, "geometry": mapping(assignment["geometry"])})
        for step in assignment.get("route_steps", []):
            sp = {k:v for k,v in step.items() if k not in {"geometry", "from", "to"}}
            features.append({"type":"Feature","properties":{"kind":"worker_route_step","worker_id":assignment["worker_id"],**sp},"geometry":mapping(step["geometry"])})
        for leg in assignment.get("navigation_legs", []):
            lp = {k:v for k,v in leg.items() if k != "geometry"}
            features.append({"type":"Feature","properties":{"kind":"worker_navigation_leg","worker_id":assignment["worker_id"],**lp},"geometry":mapping(leg["geometry"])})
    Path(output_path).write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=2), encoding="utf-8")
    return Path(output_path)


def write_assignments_csv(assignments: list[dict], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    fields = ["worker_id", "name", "estimated_households", "length_m", "estimated_minutes", "difference_from_average_m", "start_lon", "start_lat", "end_lon", "end_lat"]
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for assignment in assignments:
            writer.writerow({field: assignment[field] for field in fields})
    return output_path
