from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import string
import time
import uuid
import zipfile
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

from .kmz import list_areas_from_kmz, list_area_geojson_from_kmz
from .service import run_build

BASE = Path.cwd()
RUNTIME = BASE / "web_runtime"
UPLOADS = RUNTIME / "uploads"
JOBS = RUNTIME / "jobs"
DB_PATH = Path(os.getenv("POSTING_NAV_DB", str(RUNTIME / "posting_navigator.db")))
DOCS = BASE / "docs"
for d in (UPLOADS, JOBS, DB_PATH.parent):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024
allowed_origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "*").split(",") if x.strip()]
CORS(app, resources={r"/api/*": {"origins": allowed_origins}, r"/download/*": {"origins": allowed_origins}})


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _init_db() -> None:
    with _db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
              id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT,
              picture TEXT, created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions(
              token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at INTEGER NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS projects(
              id TEXT PRIMARY KEY, share_code TEXT UNIQUE NOT NULL, owner_user_id TEXT,
              area TEXT NOT NULL, worker_count INTEGER NOT NULL, geojson TEXT NOT NULL,
              summary TEXT NOT NULL, created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS progress(
              project_id TEXT NOT NULL, worker_id INTEGER NOT NULL,
              completed_segments TEXT NOT NULL DEFAULT '[]',
              completed_distance_m REAL NOT NULL DEFAULT 0,
              total_distance_m REAL NOT NULL DEFAULT 0,
              lat REAL, lon REAL, updated_at INTEGER NOT NULL,
              PRIMARY KEY(project_id, worker_id)
            );
            """
        )


_init_db()


def _job_dir(job_id: str) -> Path:
    if not job_id or any(c not in "0123456789abcdef" for c in job_id.lower()):
        raise ValueError("不正なジョブIDです")
    return JOBS / job_id


def _bearer_user() -> dict | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    now = int(time.time())
    with _db() as con:
        row = con.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires_at>?",
            (token, now),
        ).fetchone()
    return dict(row) if row else None


def _new_share_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        with _db() as con:
            if not con.execute("SELECT 1 FROM projects WHERE share_code=?", (code,)).fetchone():
                return code


@app.get("/")
def index():
    return send_from_directory(DOCS, "index.html")


@app.get("/<path:filename>")
def public_file(filename: str):
    # GitHub Pages とローカルFlaskで同一フロントエンドを利用する。
    if filename.startswith("api/") or filename.startswith("download/"):
        return jsonify(error="not found"), 404
    return send_from_directory(DOCS, filename)


@app.get("/api/health")
def api_health():
    return jsonify(status="ok", service="posting-navigator-api", version="1.0.7")


@app.get("/api/config")
def api_config():
    return jsonify(
        version="1.0.7",
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
        gps_threshold_m=float(os.getenv("GPS_THRESHOLD_M", "18")),
        sync_interval_ms=int(os.getenv("SYNC_INTERVAL_MS", "5000")),
    )


@app.post("/api/auth/google")
def auth_google():
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        return jsonify(error="Googleログインは未設定です。RenderのGOOGLE_CLIENT_IDを設定してください。"), 503
    credential = str((request.get_json(silent=True) or {}).get("credential", ""))
    if not credential:
        return jsonify(error="Google credential がありません"), 400
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        info = id_token.verify_oauth2_token(credential, google_requests.Request(), client_id)
        if not info.get("email_verified"):
            raise ValueError("email is not verified")
    except Exception as exc:
        return jsonify(error=f"Googleログインを確認できません: {exc}"), 401

    user_id = str(info["sub"])
    now = int(time.time())
    with _db() as con:
        con.execute(
            "INSERT INTO users(id,email,name,picture,created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET email=excluded.email,name=excluded.name,picture=excluded.picture",
            (user_id, info["email"], info.get("name", ""), info.get("picture", ""), now),
        )
        token = secrets.token_urlsafe(32)
        con.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES(?,?,?)", (token, user_id, now + 30 * 86400))
    return jsonify(token=token, user={"id": user_id, "email": info["email"], "name": info.get("name", ""), "picture": info.get("picture", "")})


@app.get("/api/auth/me")
def auth_me():
    user = _bearer_user()
    return jsonify(user=user) if user else (jsonify(error="not signed in"), 401)


@app.post("/api/auth/logout")
def auth_logout():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        with _db() as con:
            con.execute("DELETE FROM sessions WHERE token=?", (auth[7:].strip(),))
    return jsonify(ok=True)


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
        area_geojson = list_area_geojson_from_kmz(path)
    except Exception as exc:
        path.unlink(missing_ok=True)
        return jsonify(error=f"KMZを解析できません: {exc}"), 400
    return jsonify(upload_id=upload_id, areas=areas, area_geojson=area_geojson)


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
            offline_fallback=bool(payload.get("offline_fallback", False)),
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


@app.post("/api/projects")
def create_project():
    payload = request.get_json(force=True)
    job_id = str(payload.get("job_id", ""))
    try:
        out = _job_dir(job_id)
    except ValueError:
        return jsonify(error="不正なジョブIDです"), 400
    geo_path, summary_path = out / "posting_navigator.geojson", out / "summary.json"
    if not geo_path.exists() or not summary_path.exists():
        return jsonify(error="先に巡回ルートを生成してください"), 400
    geojson = json.loads(geo_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    area_feature = next((f for f in geojson["features"] if f.get("properties", {}).get("kind") == "area"), None)
    area = (area_feature or {}).get("properties", {}).get("name", "")
    workers = int(summary.get("worker_count", 1))
    project_id, code, now = uuid.uuid4().hex, _new_share_code(), int(time.time())
    user = _bearer_user()
    with _db() as con:
        con.execute(
            "INSERT INTO projects(id,share_code,owner_user_id,area,worker_count,geojson,summary,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (project_id, code, user["id"] if user else None, area, workers, json.dumps(geojson, ensure_ascii=False), json.dumps(summary, ensure_ascii=False), now),
        )
        for worker_id in range(1, workers + 1):
            feat = next((f for f in geojson["features"] if f.get("properties", {}).get("kind") == "worker_route" and int(f["properties"].get("worker_id", 0)) == worker_id), None)
            total = float((feat or {}).get("properties", {}).get("length_m", 0))
            con.execute(
                "INSERT INTO progress(project_id,worker_id,total_distance_m,updated_at) VALUES(?,?,?,?)",
                (project_id, worker_id, total, now),
            )
    return jsonify(project_id=project_id, share_code=code, area=area, worker_count=workers)


@app.get("/api/projects/join/<share_code>")
def join_project(share_code: str):
    code = share_code.strip().upper()
    with _db() as con:
        row = con.execute("SELECT * FROM projects WHERE share_code=?", (code,)).fetchone()
    if not row:
        return jsonify(error="共有コードが見つかりません"), 404
    return jsonify(project_id=row["id"], share_code=row["share_code"], area=row["area"], worker_count=row["worker_count"], geojson=json.loads(row["geojson"]), summary=json.loads(row["summary"]))


@app.get("/api/projects/<project_id>/progress")
def get_progress(project_id: str):
    with _db() as con:
        project = con.execute("SELECT id,share_code,area,worker_count FROM projects WHERE id=?", (project_id,)).fetchone()
        rows = con.execute("SELECT * FROM progress WHERE project_id=? ORDER BY worker_id", (project_id,)).fetchall()
    if not project:
        return jsonify(error="プロジェクトが見つかりません"), 404
    result = []
    for row in rows:
        d = dict(row)
        d["completed_segments"] = json.loads(d["completed_segments"] or "[]")
        d["percent"] = round((d["completed_distance_m"] / d["total_distance_m"] * 100), 1) if d["total_distance_m"] else 0
        result.append(d)
    return jsonify(project=dict(project), workers=result)


@app.post("/api/projects/<project_id>/progress/<int:worker_id>")
def update_progress(project_id: str, worker_id: int):
    payload = request.get_json(force=True)
    completed = payload.get("completed_segments", [])
    if not isinstance(completed, list) or len(completed) > 50000:
        return jsonify(error="完了区間データが不正です"), 400
    completed = sorted({int(x) for x in completed if int(x) >= 0})
    distance = max(0.0, float(payload.get("completed_distance_m", 0)))
    lat = payload.get("lat")
    lon = payload.get("lon")
    now = int(time.time())
    with _db() as con:
        row = con.execute("SELECT 1 FROM progress WHERE project_id=? AND worker_id=?", (project_id, worker_id)).fetchone()
        if not row:
            return jsonify(error="担当者が見つかりません"), 404
        con.execute(
            "UPDATE progress SET completed_segments=?,completed_distance_m=?,lat=?,lon=?,updated_at=? WHERE project_id=? AND worker_id=?",
            (json.dumps(completed), distance, lat, lon, now, project_id, worker_id),
        )
    return jsonify(ok=True, updated_at=now)


@app.get("/api/projects")
def my_projects():
    user = _bearer_user()
    if not user:
        return jsonify(error="Googleログインが必要です"), 401
    with _db() as con:
        rows = con.execute("SELECT id,share_code,area,worker_count,created_at FROM projects WHERE owner_user_id=? ORDER BY created_at DESC LIMIT 50", (user["id"],)).fetchall()
    return jsonify(projects=[dict(r) for r in rows])


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
