# UN Validator

A portable, open-source validator for **ArcGIS Utility Network** data: file geodatabases (`.gdb`) and GeoPackages (`.gpkg`).
It checks **metadata**, **data sources and lineage**, **schema and domain conformance** against the Esri UN Foundation
asset packages, and **data quality**. A browser UI shows each step live while it runs.

* No ArcPy or ArcGIS licence needed. It is built on GDAL (via pyogrio), shapely, pyproj, pandas and lxml.
* Runs locally with FastAPI and a single-page HTML/JS front end. Live progress is streamed with Server-Sent Events.
* ArcPy-only checks can be added later as plugins. They are skipped automatically when `arcpy` is not importable.
* Developed and tested only against public Esri sample data and municipal open data (see *Data sources* below).

The working title is set in one place: `APP_TITLE` in `app/config.py`. You can also set the `UNV_TITLE` environment variable.

---

## What it does: the 6-stage pipeline

| # | Stage | What is checked |
|---|-------|-----------------|
| 1 | **Discover** | Layers, tables, counts, geometry types and CRS. Parses the `GDB_Items` catalog (field definitions, domains, subtypes, metadata XML). Classifies the dataset (built UN, asset package, migration source or plain gdb). Picks the best-matching UN Foundation model. Flags tables that are registered in the catalog but whose files are missing. |
| 2 | **Metadata validation** | Scores the ArcGIS, FGDC or ISO metadata of every feature class, table and the workspace. Eight elements are checked: summary, description, tags, credits, use limits, contact, extent and lineage. Placeholder text and auto-generated geoprocessing history are detected. |
| 3 | **Data source & lineage** | Horizontal and vertical CRS consistency, mixed CRS, Z values without a vertical CRS, and datum differences against the reference model. Covers editor tracking and edit-date span. For migration sources, it measures **field-mapping coverage** using Esri data-loading `*MatchTable*.csv` and `DataMapping.xlsx` files. |
| 4 | **Schema & domain conformance** | Domain integrity, then coded and range values checked against their domains, subtype by subtype. Compares each class with its counterpart in the matched Foundation asset package: missing required or optional fields, extra fields, type mismatches and domain differences. |
| 5 | **Data quality** | Nulls in required fields. Duplicate GlobalIDs (within and across classes) and duplicate asset or facility IDs. Null, empty, invalid, zero-length and zero-area geometry, plus stacked points. Line dangles and near-miss endpoints (STRtree, tolerance-aware). Z/M consistency, date sanity, `ISCONNECTED` status. UN system-table sanity: associations (orphans), subnetworks (dirty), rules, dirty areas and error tables. Asset-package `C_Associations` / `C_SubnetworkControllers` are checked too. |
| 6 | **Score & report** | Weighted stage scores, an overall 0-100 score and grade, a map preview and the HTML/CSV export. |

The UI shows the pipeline cards lighting up, with a progress bar and the current check for each stage. Below that are a
live activity log and a live findings feed. When the run finishes you get a **dashboard** (overall and per-stage
scores with their components, top issues, and a comparison table for folder runs), a filterable **findings table**,
a **Leaflet map** of flagged features (reprojected to WGS84 and sampled when large), and **Layers & metadata** tables.
**Export HTML** and **Export CSV** download the report.

The *Demo pace* option slows each step down so the pipeline can be followed during a demo.

---

## Windows setup

You need Python 3.10–3.13 (python.org or Miniforge/Anaconda). The `pyogrio` wheel bundles GDAL, so nothing else has to be installed.

### Option A: pip and venv (simplest)

```bat
cd C:\path\to\un_validator
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Option B: conda / Miniforge

```bat
cd C:\path\to\un_validator
conda env create -f environment.yml
conda activate unvalidator
```

> Do not install these packages into ArcGIS Pro's own `arcgispro-py3` environment. If you want the optional ArcPy
> plugins, clone that environment first (`conda create --clone arcgispro-py3 -n unv-arcpy`), then run
> `pip install fastapi "uvicorn[standard]" pyogrio` in the clone. Everything else works without ArcPy.

### Point it at your data

By default the validator looks for data in `.\data` inside the project folder. To use other folders, pass them on
the command line or set environment variables (Command Prompt syntax):

```bat
python run.py --data-root C:\data --data-root D:\GIS\networks serve
set UNV_DATA_ROOTS=C:\data;D:\GIS\networks
set UNV_FOUNDATION_ROOT=C:\data\foundations
```

`UNV_FOUNDATION_ROOT` (or `--foundation-root`) is the folder that holds the Esri UN Foundation `*_AssetPackage.gdb`
files. It defaults to `foundations` inside the first data folder. They are used as the reference schemas in stages 1,
3 and 4. Without them, conformance is limited to internal checks.

The browser UI can only validate datasets inside the data folders: a typed path such as `C:\Windows` or
`..\..\` is refused. On a single-user machine you can lift this with `--allow-any-path` (or `UNV_ALLOW_ANY_PATH=1`).
The server listens on `127.0.0.1` only and has no login; `--host 0.0.0.0` prints a warning, because anyone who can
reach the port could then read the data folders.

| Setting | Command line | Environment | Default |
|---|---|---|---|
| Data folders | `--data-root DIR` (repeatable) | `UNV_DATA_ROOTS` / `UNV_DATA_ROOT` | `.\data` |
| Reference models | `--foundation-root DIR` | `UNV_FOUNDATION_ROOT` | `<first data folder>\foundations` |
| Paths outside the data folders | `--allow-any-path` | `UNV_ALLOW_ANY_PATH=1` | off |
| Cache / reports | `--cache-dir`, `--report-dir` | `UNV_CACHE_DIR`, `UNV_REPORT_DIR` | `.\.cache`, `.\reports` |
| Host / port | `serve --host`, `serve --port` | `UNV_HOST`, `UNV_PORT` | `127.0.0.1`, `8765` |
| Concurrent runs (others queue) | | `UNV_MAX_CONCURRENT_JOBS` | `1` |
| Read cache per dataset | | `UNV_READ_CACHE_MB` | `1500` |
| Log level | `--log-level` | `UNV_LOG_LEVEL` | `INFO` |

---

## Running

```bat
python run.py                      &REM web UI on http://127.0.0.1:8765
python run.py serve --port 9000    &REM another port   (or: set UNV_PORT=9000)
python run.py validate "D:\data\MyNetwork.gdb"           &REM headless run, prints scores
python run.py validate D:\data\folder_of_gdbs --pace-ms 0
python run.py clear-cache                                &REM delete cached compatibility copies
```

`start_windows.bat` starts the server and opens the browser.

In the UI, pick a **Dataset**, a **Folder** (every gdb/gpkg inside it runs in sequence), or type a **Path** inside a
data folder. Then click **Run validation**. **Stop** ends the run after the current step and still produces a partial
report. Runs started while another is in progress wait in a queue.

Reports are written to `reports\<job id>\report.html`, `findings.csv` and `result.json`.

The validator never modifies the source data. Some geodatabases written by recent ArcGIS Pro releases need a
compatibility view (see *GDAL note* below). That view is built in `.cache\`, and the cache can be deleted at any time.

---

## Adding a check

Drop a `.py` file into `plugins\`. It is imported at start-up and shown in the UI under *Registered checks*.

```python
# plugins/my_checks.py
from app.engine.core import check


@check("quality", "Valves must have an install date", order=150, description="WaterDevice valves without INSTALLDATE")
def valve_install_date(ctx):
    for layer in ctx.dataset.user_layers():
        if layer.name.lower() != "waterdevice" or not layer.count:
            continue
        df = ctx.dataset.read_columns(layer.name, ["INSTALLDATE"])  # index = ObjectID (cached per run)
        bad = df.index[df["INSTALLDATE"].isna()]
        ctx.log(f"Checking {len(df):,} {layer.name} features for INSTALLDATE")
        if len(bad):
            ctx.finding(
                "warning",
                f"{len(bad):,} devices have no install date",
                layer=layer.name,
                count=len(bad),
                sample_ids=list(bad[:25]),
            )
            ctx.flag_fids(layer.name, bad, "warning", "No install date")  # shows on the map
        ctx.score(100 * (1 - len(bad) / len(df)), weight=1, label="Install dates")
```

**Stages:** `discover`, `metadata`, `lineage`, `schema`, `quality`, `report`.

**The `ctx` API:**

* `ctx.dataset`:
  * `.layers()` and `.user_layers()` return `LayerInfo` objects (`name`, `count`, `fields`, `geometry_type`, `crs_wkt`).
  * `.read_columns(name, columns)` (attributes only), `.read_geometry(name)`, `.read_fids(name, ids)`, and
    `.iter_batches(name, columns, geometry)` for very large layers. All are indexed by ObjectID and cached per run
    within a memory budget. `.read(name, columns=None, geometry=True)` returns both at once.
  * `.catalog()` gives the parsed `GDB_Items`: classes, domains, subtypes and metadata XML.
  * `.class_def(name)` and `.domains()`.
* `ctx.log(msg, level="info"|"warn"|"error")` adds a line to the live log.
* `ctx.progress(fraction, label)` drives the stage progress bar.
* `ctx.finding(severity, message, layer=, count=, sample_ids=, detail=)` records a finding. Severity is `error`, `warning` or `info`.
* `ctx.score(value_0_100, weight, label)` adds a component to the stage score.
* `ctx.flag_features(layer, gdf, severity, label)` or `ctx.flag_fids(layer, ids, severity, label)` adds features to the map preview.
* `ctx.state` is a dict shared between checks. Examples: `state["target"]` (the matched reference model) and `state["main_crs"]`.
* `ctx.pace()` respects the demo pace; `ctx.check_cancel()` stops a long loop promptly when the user presses Stop.

A check that raises an exception does not stop the run: it is reported as a *warning* finding ("Check failed to run")
and the next check starts. A plugin file that fails to import is logged and skipped.

**ArcPy-only checks:** add `requires=("arcpy",)` to the decorator. See `plugins/arcpy_checks.py`. The check runs inside
an ArcGIS Pro Python environment and is shown as *skipped* everywhere else.

Built-in checks live in `app/engine/checks/` and use exactly the same decorator. `plugins/example_custom_check.py`
is a working example (short network lines).

### Scoring

Each check adds weighted 0-100 components, and a stage score is the weighted mean of its components. The overall score
weights the stages with `STAGE_WEIGHTS` in `app/config.py`. The defaults are metadata 20, lineage 15, schema 25 and
quality 40. Grades are A ≥ 90, B ≥ 80, C ≥ 70 and D ≥ 60. Tolerances (snap and near-miss distances), required fields,
asset-ID fields and the metadata element weights are also set in `app/config.py`.

---

## GDAL note: file geodatabases from recent ArcGIS Pro releases

The utility network feature classes in recent Pro-written geodatabases store a *default value* on fields that are not
flagged *editable*. GDAL's OpenFileGDB driver (3.10–3.13 at the time of writing) only skips default-value bytes when the
editable flag is set. As a result it misreads the field header ("Unhandled field type: 22") and treats the class as
unreadable.

`app/engine/fgdb_compat.py` detects this pattern by reading the table headers. It then builds a separate copy in
`.cache/fgdb_compat/` in which only the affected header flag bytes are changed, and reads that copy instead:

* The source geodatabase is only ever opened read-only; its files are never written to.
* All files are copied (set `UNV_FGDB_COMPAT_LINK_MODE=hardlink` to hard-link the unchanged ones and save disk space;
  patched tables are always real copies).
* The copy is built in a temporary folder and renamed into place when complete, so an interrupted build leaves no
  half-written copy. Only the 12 most recently used copies are kept, and cleanup only ever deletes folders carrying
  the module's marker file. `python run.py clear-cache` removes them all.

For example, NapervilleElectric needs 49 flag bytes patched across 12 tables. The run reports this as an *info*
finding. This GDAL issue is worth reporting upstream.

## Known limitations

* **No network topology engine.** Traces, subnetwork updates and topology validation need ArcGIS. The validator reads
  the stored outcomes instead (dirty areas, dirty subnetworks, error tables, `ISCONNECTED`), and you can add ArcPy
  plugins for the rest.
* **M values are dropped** by pyogrio/GDAL when reading, so M checks are limited to schema flags (`HasM`).
* **Mobile geodatabases** (`.geodatabase`, ST_Geometry) and enterprise geodatabases are not supported. Export them to a file gdb or GeoPackage first.
* Tables stored in Esri Compressed Data Format (`.cdf`), such as `_Version` in the Foundation asset packages, and
  database views (`view_UN_5_*`) cannot be read by GDAL. They are reported as *info* findings.
* Connectivity is endpoint-based (dangles and near-misses within a tolerance). It is not a full UN connectivity-rule evaluation.
* Attribute rules (Arcade) are inventoried by the UN rule tables but not executed.
* The map basemap uses OpenStreetMap tiles and needs internet access. All validation itself runs offline.

## Project layout

```
run.py                     CLI: serve | validate | clear-cache (+ --data-root, --allow-any-path, ...)
app/config.py              title, paths, tolerances, weights, footer (env / CLI overridable)
app/log.py                 logging setup
app/server.py              FastAPI: /api/datasets, /api/run, /api/jobs/{id}/events (SSE), result, exports
app/engine/registry.py     @check decorator, check registry, plugin loader
app/engine/models.py       stages, severities, Finding
app/engine/context.py      RunContext: what a check sees (log, progress, finding, score, map)
app/engine/jobs.py         Job event log + cancellation, JobManager (concurrency limit, queue)
app/engine/runner.py       runs the stages per dataset; isolates failing checks and datasets
app/engine/scoring.py      penalty curve, weighted means, grades
app/engine/results.py      result document (scores, findings, tables, map)
app/engine/paths.py        data-folder restriction for browser-supplied paths
app/engine/dataset.py      GDAL/pyogrio reader (column cache, batched reads)
app/engine/catalog.py      GDB_Items definitions, domains and metadata XML (safe XML parsing)
app/engine/fgdb_compat.py  GDAL compatibility copy for Pro-written FGDBs
app/engine/targets.py      Foundation asset-package reference models and matching
app/engine/checks/         built-in checks per stage (quality.py + network.py for stage 5)
app/engine/report.py       HTML / CSV / JSON report writer
app/engine/core.py         compatibility imports for plugins (check, Job, run_job, ...)
app/static/                single-page UI (vanilla JS, Leaflet 1.9.4 vendored with its licence)
plugins/                   your checks (ArcPy hook example included)
tests/                     pytest suite (synthetic data generated at test time)
tools/screenshots.py       headless UI run + screenshots (Playwright)
```

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check . && ruff format --check .
pytest --cov=app
```

The tests build small GeoPackages (and a File Geodatabase where GDAL can write one) with known defects at run time,
so no sample data is needed. `tests/test_sample_data.py` additionally re-validates four public sample datasets and
checks their scores; it is skipped unless the sample data is present in `./data` (or `UNV_SAMPLE_DATA`). GitHub
Actions runs ruff and the tests on Windows and Ubuntu with Python 3.10–3.12 (`.github/workflows/ci.yml`).

## Data sources and licences

The validator was developed and tested only with public sample data. Item URLs and licences are listed in
[`DATA_SOURCES.md`](DATA_SOURCES.md). No sample data is included in this repository.

* **Esri ArcGIS Solutions:** Utility Network Foundation asset packages and data-migration tutorials (Apache-2.0).
* **Esri ArcGIS Pro SDK community sample data**, including NapervilleElectric and NapervilleWater (Apache-2.0):
  https://github.com/Esri/arcgis-pro-sdk-community-samples
* **Esri tutorial project packages** (tutorial use).
* **City of SeaTac** Stormwater Infrastructure open data.
* **City of Naperville** open data.
* **City of Langley** Water Utility. *Contains information licensed under the Open Government License – City of Langley.*
  https://langleycity.ca/open-data-license
* **Basemap:** © OpenStreetMap contributors. **Leaflet** (BSD-2-Clause).

## Licence

MIT, see [`LICENSE`](LICENSE). Leaflet is BSD-2-Clause (`app/static/vendor/LICENSE-leaflet.txt`). The sample datasets
keep their own licences (see above).
