"""Central configuration.

Every value can be edited here; the path and server settings can also be overridden with
environment variables (``UNV_*``) or the command line (see ``run.py --help``). Modules read
values as ``config.NAME`` at call time, so :func:`configure` takes effect immediately.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import __version__

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    return default if val is None else val.strip().lower() in ("1", "true", "yes", "on")


def _env_paths(name: str) -> list[Path]:
    val = os.environ.get(name, "")
    return [Path(p).expanduser() for p in val.split(os.pathsep) if p.strip()]


# ---- Branding -------------------------------------------------------------
APP_TITLE = os.environ.get("UNV_TITLE", "UN Validator")  # rename the tool here
APP_SUBTITLE = "Utility Network metadata, lineage, schema & data-quality validation"
APP_VERSION = __version__

# ---- Data locations -------------------------------------------------------
# Folders the UI may browse and validate. UNV_DATA_ROOTS takes several folders separated by
# os.pathsep (";" on Windows, ":" elsewhere); UNV_DATA_ROOT takes one. Default: ./data
DATA_ROOTS: list[Path] = _env_paths("UNV_DATA_ROOTS") or _env_paths("UNV_DATA_ROOT") or [PROJECT_ROOT / "data"]
DATA_ROOT: Path = DATA_ROOTS[0]
# Allow /api/run to validate paths outside DATA_ROOTS (only sensible on a single-user machine).
ALLOW_ANY_PATH = _env_bool("UNV_ALLOW_ANY_PATH")
# Folder holding the Esri UN Foundation asset packages (*_AssetPackage.gdb) used as reference schemas.
FOUNDATION_ROOT = Path(os.environ.get("UNV_FOUNDATION_ROOT", DATA_ROOT / "foundations")).expanduser()
CACHE_DIR = Path(os.environ.get("UNV_CACHE_DIR", PROJECT_ROOT / ".cache")).expanduser()
REPORT_DIR = Path(os.environ.get("UNV_REPORT_DIR", PROJECT_ROOT / "reports")).expanduser()
PLUGIN_DIR = Path(os.environ.get("UNV_PLUGIN_DIR", PROJECT_ROOT / "plugins")).expanduser()

# ---- Server ---------------------------------------------------------------
HOST = os.environ.get("UNV_HOST", "127.0.0.1")  # loopback only by default; there is no authentication
PORT = int(os.environ.get("UNV_PORT", "8765"))
MAX_CONCURRENT_JOBS = int(os.environ.get("UNV_MAX_CONCURRENT_JOBS", "1"))  # further runs wait in a queue
MAX_JOBS_KEPT = 20  # finished jobs kept in memory (older ones are dropped; their report files stay on disk)
LOG_LEVEL = os.environ.get("UNV_LOG_LEVEL", "INFO")

# ---- Reading --------------------------------------------------------------
READ_CACHE_MB = int(os.environ.get("UNV_READ_CACHE_MB", "1500"))  # per-dataset in-memory layer cache budget
CHUNK_ROWS = 250_000  # per-row checks stream layers larger than this in batches of this many rows
# GDAL compatibility view for Pro-written file geodatabases (see app/engine/fgdb_compat.py).
# "copy" duplicates unpatched files into the cache (safest); "hardlink" saves disk space but the cache
# then shares files with the source, so never open the cached copy for editing.
FGDB_COMPAT_LINK_MODE = os.environ.get("UNV_FGDB_COMPAT_LINK_MODE", "copy")
FGDB_COMPAT_MAX_ENTRIES = 12  # least recently used compatibility views beyond this are deleted

# ---- Check settings -------------------------------------------------------
REQUIRED_FIELDS = ["GLOBALID", "ASSETGROUP", "ASSETTYPE"]  # always treated as required when present
# Editor-tracking fields are maintained by ArcGIS; the names vary between models.
EDITOR_TRACKING_GROUPS = [
    {"created_user", "creator", "createdby", "created_by", "creationuser"},
    {"created_date", "creationdate", "createdate", "created_on", "datecreated"},
    {"last_edited_user", "updatedby", "editor", "lastuser", "last_edited_by", "lasteditor"},
    {"last_edited_date", "lastupdate", "editdate", "last_edited_on", "datemodified"},
]
EDITOR_TRACKING_FIELDS: set[str] = set().union(*EDITOR_TRACKING_GROUPS)

ASSET_ID_FIELDS = ["ASSETID", "FACILITYID"]  # checked for duplicates
SNAP_TOLERANCE_M = 0.001  # endpoint coincidence (used when the class has no XY tolerance)
NEAR_MISS_M = 0.5  # dangling endpoint within this distance of another feature => near-miss
MAX_MAP_FEATURES = 2000  # flagged features sent to the map (sampled above this)
MAX_CONTEXT_FEATURES = 2500  # grey context features on the map
MAX_SAMPLE_IDS = 25  # object IDs listed per finding
DEMO_PACE_MS = 0  # default extra delay between steps (UI can override)
MAX_PACE_MS = 2000

# Stage weights for the overall score (stages without any scored check are skipped).
STAGE_WEIGHTS = {"metadata": 20, "lineage": 15, "schema": 25, "quality": 40}

# Metadata elements and their weights in the per-item metadata score.
METADATA_ELEMENTS = {
    "summary": 1.0,
    "description": 1.5,
    "tags": 1.0,
    "credits": 0.75,
    "use_limits": 1.0,
    "contact": 1.0,
    "extent": 0.75,
    "lineage": 1.0,
}

# System / configuration tables that are not end-user data.
SYSTEM_TABLE_PATTERNS = [
    r"^GDB_",
    r"^UN_\d+_",
    r"^UN_Temp",
    r"^[ABC]_",
    r"^_Version$",
    r"__ATTACH$",
    r"^T_\d+_",
    r"^N_\d+_",
    r"^view_UN_",
    r"^gpkg_",
    r"^rtree_",
    r"^sqlite_",
]

# Trusted, static HTML (rendered as-is in the UI footer and the HTML report).
FOOTER_HTML = (
    "Sample data: Esri ArcGIS Solutions Utility Network Foundation asset packages &amp; data-migration tutorials "
    "(Apache-2.0); Esri ArcGIS Pro SDK community sample data (Apache-2.0); Esri tutorial project packages "
    "(tutorial use); City of SeaTac Stormwater Infrastructure (informational use, no warranty); City of Naperville "
    "open data (terms of use). <strong>Contains information licensed under the Open Government License &ndash; "
    "City of Langley.</strong> Basemap &copy; OpenStreetMap contributors. Map library: Leaflet (BSD-2-Clause)."
)


def configure(
    data_roots: list[str | Path] | None = None,
    foundation_root: str | Path | None = None,
    allow_any_path: bool | None = None,
    host: str | None = None,
    port: int | None = None,
    cache_dir: str | Path | None = None,
    report_dir: str | Path | None = None,
) -> None:
    """Override settings at run time (used by the CLI and the tests)."""
    global DATA_ROOTS, DATA_ROOT, FOUNDATION_ROOT, ALLOW_ANY_PATH, HOST, PORT, CACHE_DIR, REPORT_DIR
    if data_roots:
        DATA_ROOTS = [Path(p).expanduser() for p in data_roots]
        DATA_ROOT = DATA_ROOTS[0]
        if foundation_root is None and "UNV_FOUNDATION_ROOT" not in os.environ:
            FOUNDATION_ROOT = DATA_ROOT / "foundations"
    if foundation_root is not None:
        FOUNDATION_ROOT = Path(foundation_root).expanduser()
    if allow_any_path is not None:
        ALLOW_ANY_PATH = allow_any_path
    if host is not None:
        HOST = host
    if port is not None:
        PORT = port
    if cache_dir is not None:
        CACHE_DIR = Path(cache_dir).expanduser()
    if report_dir is not None:
        REPORT_DIR = Path(report_dir).expanduser()
