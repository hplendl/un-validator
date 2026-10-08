"""FastAPI server: dataset picker API, job runner, live SSE event stream, reports.

Security model: the server binds to 127.0.0.1 by default and has no authentication. Paths
from the browser are restricted to the configured data roots (see engine/paths.py), and
every page is served with a Content-Security-Policy that forbids inline scripts.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .engine.dataset import discover_datasets
from .engine.jobs import Job, JobManager
from .engine.models import STAGES
from .engine.paths import PathNotAllowed, display_path, resolve_user_path, resolved_roots
from .engine.registry import load_checks, registered_checks
from .log import get_logger, setup_logging

log = get_logger("server")
STATIC = Path(__file__).parent / "static"
SSE_POLL_S = 0.2
SSE_KEEPALIVE_S = 15.0

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging(config.LOG_LEVEL)
    load_checks()
    # parse the reference asset packages in the background so the first run is quick
    from .engine.targets import load_targets

    threading.Thread(target=load_targets, daemon=True, name="unv-warm-targets").start()
    yield


app = FastAPI(title=config.APP_TITLE, version=config.APP_VERSION, lifespan=lifespan)
JOBS = JobManager()
_ds_cache: dict | None = None
_ds_lock = threading.Lock()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def api_config():
    return {
        "title": config.APP_TITLE,
        "version": config.APP_VERSION,
        "footer_html": config.FOOTER_HTML,
        "data_root": display_path(config.DATA_ROOT),
        "data_roots": [display_path(r) for r in config.DATA_ROOTS],
        "allow_any_path": config.ALLOW_ANY_PATH,
        "stages": [{"id": s, "name": n} for s, n in STAGES],
        "checks": {
            s: [
                {
                    "name": c.name,
                    "description": c.description,
                    "requires": list(c.requires),
                    "plugin": c.source != "builtin",
                }
                for c in registered_checks(s)
            ]
            for s, _ in STAGES
        },
    }


def _size(p: Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.iterdir() if f.is_file()) if p.is_dir() else p.stat().st_size
    except OSError:
        return 0


def scan_root(root: Path) -> tuple[list[dict], dict[str, dict]]:
    """Datasets under one data root, plus every folder (below the root) that holds 2+ of them."""
    root = Path(root)
    rroot = root.resolve()
    items: list[dict] = []
    folders: dict[str, dict] = {}
    for p in discover_datasets(root):
        try:
            rel = p.resolve().relative_to(rroot)
        except ValueError:
            continue  # a symlink leading outside the root
        parts = rel.parts
        group = parts[0] if len(parts) > 1 else "(root)"
        items.append(
            {
                "path": display_path(p),
                "name": p.name,
                "rel": str(rel),
                "group": group,
                "kind": p.suffix.lower().lstrip("."),
                "size_mb": round(_size(p) / 1e6, 1),
            }
        )
        for k in range(1, len(parts)):
            f = root.joinpath(*parts[:k])
            if f.suffix.lower() == ".gdb":
                continue
            key = display_path(f)
            folders.setdefault(
                key, {"path": key, "rel": str(Path(*parts[:k])), "count": 0, "_parent": display_path(f.parent)}
            )
            folders[key]["count"] += 1
    return items, folders


def _scan_all() -> dict:
    items: list[dict] = []
    folders: dict[str, dict] = {}
    for root in config.DATA_ROOTS:
        it, fo = scan_root(Path(root))
        items += it
        folders.update(fo)

    def redundant(f: dict) -> bool:  # drop folders that only repeat their parent's contents
        parent = folders.get(f["_parent"])
        return parent is not None and parent["count"] == f["count"]

    fl = sorted(
        (
            {k: v for k, v in f.items() if k != "_parent"}
            for f in folders.values()
            if f["count"] >= 2 and not redundant(f)
        ),
        key=lambda f: f["rel"],
    )
    if len(items) >= 2 and len(config.DATA_ROOTS) == 1:
        fl.insert(0, {"path": display_path(config.DATA_ROOT), "rel": "(all sample data)", "count": len(items)})
    return {
        "root": ", ".join(display_path(r) for r in config.DATA_ROOTS),
        "datasets": sorted(items, key=lambda d: (d["group"], d["rel"].lower())),
        "folders": fl,
    }


@app.get("/api/datasets")
def api_datasets(refresh: bool = False):
    global _ds_cache
    with _ds_lock:
        if _ds_cache is None or refresh:
            _ds_cache = _scan_all()
        return _ds_cache


class RunRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=4096)
    pace_ms: int = 0
    rules_path: str = Field("", max_length=4096)
    dispositions_path: str = Field("", max_length=4096)
    profile_only: bool = False


@app.post("/api/run")
def api_run(req: RunRequest):
    try:
        p = resolve_user_path(req.path)
    except PathNotAllowed as e:
        raise HTTPException(403, str(e)) from None
    if not p.exists():
        raise HTTPException(404, f"Path not found: {display_path(p)}")
    targets = [str(x) for x in discover_datasets(p)]
    if not config.ALLOW_ANY_PATH:  # symlinks inside a root must not lead outside it either
        roots = resolved_roots()
        targets = [t for t in targets if any(Path(t).resolve().is_relative_to(r) for r in roots)]
    if not targets:
        raise HTTPException(400, "No file geodatabase (.gdb) or GeoPackage (.gpkg) found at that path")
    pace = max(0, min(config.MAX_PACE_MS, int(req.pace_ms or 0)))
    options: dict = {"pace_ms": pace, "profile_only": bool(req.profile_only)}
    from .engine.rules import RulesError

    try:
        rules = _load_optional(req.rules_path, "UNV_RULES", "rules")
        disp = _load_optional(req.dispositions_path, "UNV_DISPOSITIONS", "dispositions")
    except PathNotAllowed as e:
        raise HTTPException(403, str(e)) from None
    except RulesError as e:
        raise HTTPException(400, " ".join(e.errors)) from None
    if rules is not None:
        options["rules"] = rules
    if disp is not None:
        options["dispositions"] = disp
    job = JOBS.submit(targets, options)
    return {"job_id": job.id, "targets": [display_path(t) for t in targets], "status": job.status}


def _load_optional(raw: str, env_name: str, kind: str):
    """Load a rules or dispositions file. A browser path must sit inside the data roots.
    The environment variable is read on the server and is not a browser-supplied path."""
    from .engine.rules import load_dispositions, load_rules_file

    text = (raw or "").strip()
    if text:
        path = resolve_user_path(text)
        return load_rules_file(path) if kind == "rules" else load_dispositions(path)
    env = os.environ.get(env_name, "").strip()
    if not env:
        return None
    path = Path(env).expanduser()
    return load_rules_file(path) if kind == "rules" else load_dispositions(path)


def _job(job_id: str) -> Job:
    job = JOBS.get(job_id) if job_id.isalnum() else None
    if not job:
        raise HTTPException(404, "Unknown job")
    return job


def _sse(ev: dict) -> str:
    return f"id: {ev['seq']}\nevent: {ev['kind']}\ndata: {json.dumps(ev)}\n\n"


@app.get("/api/jobs/{job_id}/events")
async def api_events(job_id: str, request: Request, since: int = 0):
    """Server-sent events. Resumes after ``Last-Event-ID`` on reconnect; ends with an ``eof`` event."""
    job = _job(job_id)
    last_id = request.headers.get("last-event-id")
    start = since
    if last_id is not None and last_id.strip().isdigit():
        start = max(start, int(last_id) + 1)

    async def gen():
        pos = start
        idle = 0.0
        while True:
            evs = job.events_since(pos)
            if evs:
                idle = 0.0
                for ev in evs:
                    yield _sse(ev)
                pos += len(evs)
                continue
            if job.is_finished:
                yield "event: eof\ndata: {}\n\n"
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(SSE_POLL_S)
            idle += SSE_POLL_S
            if idle >= SSE_KEEPALIVE_S:
                idle = 0.0
                yield ": keep-alive\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel(job_id: str):
    job = _job(job_id)
    job.request_cancel()
    return {"ok": True, "status": job.status}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    return _job(job_id).summary()


@app.get("/api/jobs/{job_id}/result")
def api_result(job_id: str):
    j = _job(job_id)
    if j.result is None:
        raise HTTPException(409, f"Job is {j.status}")
    return JSONResponse(j.result)


def _report_file(job_id: str, kind: str) -> Path:
    j = _job(job_id)
    files = (j.result or {}).get("files") or {}
    if not files.get(kind):
        raise HTTPException(409, "No report yet")
    p = Path(files[kind]).resolve()
    if not p.is_relative_to(Path(config.REPORT_DIR).resolve()) or not p.is_file():
        raise HTTPException(404, "Report not found")
    return p


@app.get("/api/jobs/{job_id}/report.html")
def api_report_html(job_id: str, download: bool = False):
    return FileResponse(
        _report_file(job_id, "html"),
        media_type="text/html",
        filename=f"un_validator_{job_id}.html" if download else None,
        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:"},
    )


@app.get("/api/jobs/{job_id}/findings.csv")
def api_report_csv(job_id: str):
    return FileResponse(
        _report_file(job_id, "csv"), media_type="text/csv", filename=f"un_validator_{job_id}_findings.csv"
    )


@app.get("/api/jobs/{job_id}/questions.csv")
def api_questions_csv(job_id: str):
    return FileResponse(
        _report_file(job_id, "questions_csv"), media_type="text/csv", filename=f"un_validator_{job_id}_questions.csv"
    )


@app.get("/api/jobs/{job_id}/questions.xlsx")
def api_questions_xlsx(job_id: str):
    return FileResponse(
        _report_file(job_id, "questions_xlsx"),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"un_validator_{job_id}_questions.xlsx",
    )


@app.get("/api/jobs/{job_id}/manifest.json")
def api_manifest(job_id: str):
    return FileResponse(
        _report_file(job_id, "manifest"), media_type="application/json", filename=f"un_validator_{job_id}_manifest.json"
    )


@app.get("/api/jobs/{job_id}/asset_mapping.csv")
def api_asset_mapping(job_id: str):
    return FileResponse(
        _report_file(job_id, "asset_mapping"),
        media_type="text/csv",
        filename=f"un_validator_{job_id}_asset_mapping.csv",
    )


app.mount("/static", StaticFiles(directory=STATIC), name="static")
