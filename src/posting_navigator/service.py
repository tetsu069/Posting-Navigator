from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from .cli import build


def run_build(*, kmz: str | Path, area: str, output: str | Path, workers: int = 1,
              start_lon: float | None = None, start_lat: float | None = None,
              cache: str | Path = "data/cache/osm_roads.json",
              offline_fallback: bool = True) -> dict:
    args = SimpleNamespace(
        kmz=str(kmz), area=area, output=str(output), workers=int(workers),
        start_lon=start_lon, start_lat=start_lat, cache=str(cache),
        offline_fallback=offline_fallback,
    )
    build(args)
    return json.loads((Path(output) / "summary.json").read_text(encoding="utf-8"))
