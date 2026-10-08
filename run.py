#!/usr/bin/env python
"""UN Validator entry point.

    python run.py                                  # start the web UI (http://127.0.0.1:8765)
    python run.py --data-root D:/gis serve --port 9000
    python run.py validate PATH [PATH ...]         # headless run: prints scores, writes HTML/CSV/JSON reports
    python run.py clear-cache                      # delete cached GDAL-compatible copies and parsed models

Global options (before the command) override the UNV_* environment variables; see README.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config
from app.log import setup_logging

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def cmd_validate(paths: list[str], pace_ms: int = 0) -> int:
    """Validate datasets from the command line (paths given here are trusted, so not restricted)."""
    from app.engine.dataset import discover_datasets
    from app.engine.jobs import Job
    from app.engine.runner import run_job

    targets: list[str] = []
    for p in paths:
        targets += [str(x) for x in discover_datasets(p)]
    if not targets:
        print("No .gdb / .gpkg datasets found")
        return 1
    job = Job(f"cli{int(time.time())}", targets, {"pace_ms": pace_ms})
    last = 0

    def printer() -> None:
        nonlocal last
        for ev in job.events_since(last):
            if ev["kind"] == "log":
                print(f"[{ev['stage']:>8}] {ev['msg']}")
            elif ev["kind"] == "stage_end":
                print(f"== stage {ev['stage']} score={ev['score']} counts={ev['counts']}")
            elif ev["kind"] == "job_error":
                print(ev["trace"])
            last = ev["seq"] + 1

    import threading

    t = threading.Thread(target=run_job, args=(job,), daemon=True)
    t.start()
    try:
        while t.is_alive():
            t.join(0.3)
            printer()
    except KeyboardInterrupt:
        job.request_cancel()
        t.join()
    printer()
    if job.result:
        for ds in job.result["datasets"]:
            print(
                f"\n{ds['dataset']}: overall {ds['overall']} ({ds['grade']}) "
                f"stages={ds['stages']} counts={ds['counts']}"
            )
        print("Reports:", job.result.get("files"))
    return 0 if job.status == "done" else 2


def cmd_clear_cache() -> int:
    import shutil

    from app.engine.fgdb_compat import clear_cache

    n = clear_cache(Path(config.CACHE_DIR) / "fgdb_compat")
    tp = Path(config.CACHE_DIR) / "targets.pickle"
    if tp.exists():
        tp.unlink()
    for tmp in Path(config.CACHE_DIR).glob("fgdb_compat/.build-*"):
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"Removed {n} cached geodatabase copies from {config.CACHE_DIR}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="run.py", description=config.APP_TITLE)
    ap.add_argument(
        "--data-root",
        action="append",
        metavar="DIR",
        help="folder the UI may browse and validate (repeatable; default ./data or UNV_DATA_ROOTS)",
    )
    ap.add_argument("--foundation-root", metavar="DIR", help="folder with the Esri UN Foundation asset packages")
    ap.add_argument(
        "--allow-any-path",
        action="store_true",
        default=None,
        help="let the UI validate paths outside the data roots (only on a trusted machine)",
    )
    ap.add_argument("--cache-dir", metavar="DIR", help="working folder for caches (default ./.cache)")
    ap.add_argument("--report-dir", metavar="DIR", help="folder for exported reports (default ./reports)")
    ap.add_argument("--log-level", default=config.LOG_LEVEL, help="DEBUG, INFO, WARNING (default INFO)")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="start the web UI (default)")
    s.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    s.add_argument("--port", type=int, default=None, help="port (default 8765)")
    v = sub.add_parser("validate", help="validate datasets without the UI")
    v.add_argument("paths", nargs="+")
    v.add_argument("--pace-ms", type=int, default=0)
    sub.add_parser("clear-cache", help="delete cached GDAL-compatible copies and parsed reference models")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    setup_logging(a.log_level)
    config.configure(
        data_roots=a.data_root,
        foundation_root=a.foundation_root,
        allow_any_path=a.allow_any_path,
        cache_dir=a.cache_dir,
        report_dir=a.report_dir,
        host=getattr(a, "host", None),
        port=getattr(a, "port", None),
    )
    if a.cmd == "validate":
        return cmd_validate(a.paths, a.pace_ms)
    if a.cmd == "clear-cache":
        return cmd_clear_cache()
    import uvicorn

    host, port = config.HOST, config.PORT
    if host not in LOOPBACK:
        print(
            f"WARNING: listening on {host}. The validator has no authentication; anyone who can reach "
            "this port can read datasets under the data roots.",
            file=sys.stderr,
        )
    roots = ", ".join(str(r) for r in config.DATA_ROOTS)
    print(f"{config.APP_TITLE} -> http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}   (data: {roots})")
    uvicorn.run("app.server:app", host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
