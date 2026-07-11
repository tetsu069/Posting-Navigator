#!/usr/bin/env bash
set -euo pipefail
python -m posting_navigator.cli build \
  --kmz data/input/shinjuku_posting_map.kmz \
  --area 北新宿一丁目 \
  --output output/kita-shinjuku-1 \
  --offline-fallback
