from __future__ import annotations

import json
import os
import shutil
import uuid
import zipfile
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename

from .kmz import list_areas_from_kmz
from .service import run_build

BASE = Path.cwd()
RUNTIME = BASE / "web_runtime"
UPLOADS = RUNTIME / "uploads"
JOBS = RUNTIME / "jobs"
for d in (UPLOADS, JOBS):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024

allowed_origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "*").split(",") if x.strip()]
CORS(app, resources={r"/api/*": {"origins": allowed_origins}, r"/download/*": {"origins": allowed_origins}})


def _job_dir(job_id: str) -> Path:
    if not job_id or any(c not in "0123456789abcdef" for c in job_id.lower()):
        raise ValueError("不正なジョブIDです")
    return JOBS / job_id


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def api_health():
    return jsonify(status="ok", service="posting-navigator-api", version="0.5.0")


@app.post("/api/areas")
def api_areas():
    file = request.files.get("kmz")
    if not file or not file.filename.lower().endswith(".kmz"):
        return jsonify(error="KMZファイルを選択してください"), 400
    upload_id = uuid.uuid4().hex
    path = UPLOADS / f"{upload_id}.kmz"
    file.save(path)
    try:
        areas = list_areas_from_kmz(path)
    except Exception as exc:
        path.unlink(missing_ok=True)
        return jsonify(error=f"KMZを解析できません: {exc}"), 400
    return jsonify(upload_id=upload_id, areas=areas)


@app.post("/api/build")
def api_build():
    payload = request.get_json(force=True)
    upload_id = str(payload.get("upload_id", ""))
    area = str(payload.get("area", "")).strip()
    workers = int(payload.get("workers", 1))
    if not 1 <= workers <= 30:
        return jsonify(error="担当人数は1〜30人で指定してください"), 400
    kmz = UPLOADS / f"{upload_id}.kmz"
    if not kmz.exists() or not area:
        return jsonify(error="KMZまたは町丁目が選択されていません"), 400

    def num(name: str):
        value = payload.get(name)
        return None if value in (None, "") else float(value)

    start_lat, start_lon = num("start_lat"), num("start_lon")
    if (start_lat is None) != (start_lon is None):
        return jsonify(error="開始地点は緯度・経度を両方指定してください"), 400

    job_id = uuid.uuid4().hex
    out = _job_dir(job_id)
    try:
        summary = run_build(
            kmz=kmz, area=area, output=out, workers=workers,
            start_lat=start_lat, start_lon=start_lon,
            cache=RUNTIME / "cache" / f"{area}.json",
            offline_fallback=bool(payload.get("offline_fallback", True)),
        )
        bundle = out / "posting_navigator_results.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in out.rglob("*"):
                if p.is_file() and p != bundle:
                    zf.write(p, p.relative_to(out))
        geojson = json.loads((out / "posting_navigator.geojson").read_text(encoding="utf-8"))
        return jsonify(job_id=job_id, summary=summary, geojson=geojson)
    except Exception as exc:
        shutil.rmtree(out, ignore_errors=True)
        return jsonify(error=f"ルート生成に失敗しました: {exc}"), 500


@app.get("/download/<job_id>/<path:filename>")
def download(job_id: str, filename: str):
    base = _job_dir(job_id).resolve()
    path = (base / secure_filename(filename)).resolve()
    if path.parent != base or not path.exists():
        return jsonify(error="ファイルが見つかりません"), 404
    return send_file(path, as_attachment=True)


@app.get("/download/<job_id>/worker/<path:filename>")
def download_worker(job_id: str, filename: str):
    base = (_job_dir(job_id) / "workers").resolve()
    path = (base / secure_filename(filename)).resolve()
    if path.parent != base or not path.exists():
        return jsonify(error="ファイルが見つかりません"), 404
    return send_file(path, as_attachment=True)


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8787"))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
