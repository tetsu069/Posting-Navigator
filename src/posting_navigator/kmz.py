from __future__ import annotations

import json
import zipfile
from pathlib import Path
from lxml import etree
from shapely.geometry import Polygon, mapping
from shapely.validation import make_valid

KML_NS = {"k": "http://www.opengis.net/kml/2.2"}


def _parse_coordinates(text: str) -> list[tuple[float, float]]:
    coords: list[tuple[float, float]] = []
    for token in text.split():
        parts = token.split(",")
        if len(parts) >= 2:
            coords.append((float(parts[0]), float(parts[1])))
    return coords



def list_areas_from_kmz(kmz_path: str | Path) -> list[str]:
    """KMZ内のPolygonを持つPlacemark名を重複なしで返す。"""
    kmz_path = Path(kmz_path)
    with zipfile.ZipFile(kmz_path) as zf:
        kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            raise ValueError("KMZ内にKMLがありません")
        root = etree.fromstring(zf.read(kml_names[0]))
    names: list[str] = []
    seen: set[str] = set()
    for pm in root.xpath("//k:Placemark", namespaces=KML_NS):
        name = (pm.findtext("{http://www.opengis.net/kml/2.2}name") or "").strip()
        has_polygon = bool(pm.xpath(".//k:Polygon", namespaces=KML_NS))
        if name and has_polygon and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def list_area_geojson_from_kmz(kmz_path: str | Path) -> dict:
    """KMZ内の全町丁目PolygonをGeoJSON FeatureCollectionとして返す。"""
    kmz_path = Path(kmz_path)
    with zipfile.ZipFile(kmz_path) as zf:
        kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            raise ValueError("KMZ内にKMLがありません")
        root = etree.fromstring(zf.read(kml_names[0]))

    features = []
    seen: set[str] = set()
    for pm in root.xpath("//k:Placemark", namespaces=KML_NS):
        name = (pm.findtext("{http://www.opengis.net/kml/2.2}name") or "").strip()
        if not name or name in seen:
            continue
        outer = pm.xpath(
            "string(.//k:Polygon[1]/k:outerBoundaryIs/k:LinearRing/k:coordinates)",
            namespaces=KML_NS,
        )
        if not outer.strip():
            continue
        poly = make_valid(Polygon(_parse_coordinates(outer)))
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
        if poly.is_empty:
            continue
        seen.add(name)
        features.append({
            "type": "Feature",
            "properties": {"name": name},
            "geometry": mapping(poly),
        })
    return {"type": "FeatureCollection", "features": features}

def load_area_from_kmz(kmz_path: str | Path, area_name: str) -> Polygon:
    """KMZ内から完全一致するPlacemarkの最初のPolygonを返す。"""
    kmz_path = Path(kmz_path)
    with zipfile.ZipFile(kmz_path) as zf:
        kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            raise ValueError("KMZ内にKMLがありません")
        root = etree.fromstring(zf.read(kml_names[0]))

    for pm in root.xpath("//k:Placemark", namespaces=KML_NS):
        name = pm.findtext("{http://www.opengis.net/kml/2.2}name") or ""
        if name.strip() != area_name.strip():
            continue
        outer = pm.xpath("string(.//k:Polygon[1]/k:outerBoundaryIs/k:LinearRing/k:coordinates)", namespaces=KML_NS)
        if not outer.strip():
            continue
        poly = make_valid(Polygon(_parse_coordinates(outer)))
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
        if poly.is_empty:
            raise ValueError(f"{area_name} のポリゴンが空です")
        return poly
    raise KeyError(f"KMZ内に町丁目 '{area_name}' が見つかりません")



def _placemark_properties(pm) -> dict:
    """Placemark の description から「項目：値」を抽出する。"""
    import re
    raw = (pm.findtext("{http://www.opengis.net/kml/2.2}description") or "").strip()
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    props: dict[str, object] = {}
    for line in text.splitlines():
        if "：" not in line:
            continue
        key, value = [x.strip() for x in line.split("：", 1)]
        props[key] = value
    if "世帯数" in props:
        try:
            props["households"] = int(str(props["世帯数"]).replace(",", ""))
        except ValueError:
            props["households"] = None
    return props


def list_area_info_from_kmz(kmz_path: str | Path) -> dict[str, dict]:
    """町丁目名 -> KMZ description由来メタデータ。"""
    kmz_path = Path(kmz_path)
    with zipfile.ZipFile(kmz_path) as zf:
        kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            raise ValueError("KMZ内にKMLがありません")
        root = etree.fromstring(zf.read(kml_names[0]))
    out: dict[str, dict] = {}
    for pm in root.xpath("//k:Placemark", namespaces=KML_NS):
        name = (pm.findtext("{http://www.opengis.net/kml/2.2}name") or "").strip()
        if name and pm.xpath(".//k:Polygon", namespaces=KML_NS):
            out[name] = _placemark_properties(pm)
    return out

def write_boundary_geojson(poly: Polygon, area_name: str, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    obj = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"name": area_name}, "geometry": mapping(poly)}],
    }
    output_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
