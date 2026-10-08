"""Stage 1 - Discover: layers, tables, counts, CRS, UN structure, reference model."""

from __future__ import annotations

from pathlib import Path

from ..geo import crs_info
from ..paths import display_path
from ..registry import check
from ..targets import match_target


@check("discover", "Inventory layers & tables", order=10)
def inventory(ctx):
    """List every layer/table with row counts, geometry type and CRS."""
    ds = ctx.dataset
    ctx.log(f"Opening {ds.kind.upper()} {display_path(ds.path)}")
    if ds.patched_tables:
        n = sum(len(v) for v in ds.patched_tables.values())
        ctx.finding(
            "info",
            f"{len(ds.patched_tables)} tables use a field-header layout GDAL cannot parse "
            f"(defaults on non-editable fields); read through a patched cached copy ({n} flag bytes). Source untouched.",
            count=len(ds.patched_tables),
            detail=", ".join(sorted(ds.patched_tables)),
        )
    layers = ds.layers()
    ctx.log(f"Found {len(layers)} layers/tables ({sum(1 for lyr in layers if lyr.system)} system/config tables)")
    rows = []
    for i, lyr in enumerate(layers):
        ci = crs_info(lyr.crs_wkt) if lyr.spatial else {}
        rows.append(
            {
                "name": lyr.name,
                "geometry": lyr.geometry_type or "table",
                "count": lyr.count,
                "fields": len(lyr.fields),
                "crs": ci.get("horizontal"),
                "vertical": ci.get("vertical"),
                "system": lyr.system,
                "readable": lyr.readable,
            }
        )
        if not lyr.system:
            ctx.log(
                f"  {lyr.name}: {lyr.count:,} {'features' if lyr.spatial else 'rows'}"
                f"{' | ' + str(ci.get('horizontal')) if lyr.spatial else ''}"
            )
        ctx.progress((i + 1) / len(layers), lyr.name)
    ctx.tables["layers"] = rows
    bad = [lyr for lyr in layers if not lyr.readable]
    for lyr in bad:
        if lyr.name.lower().startswith("view_"):
            ctx.finding(
                "info",
                "Database view could not be read through GDAL (views are skipped)",
                layer=lyr.name,
                detail=str(lyr.error or ""),
            )
        else:
            ctx.finding("error", f"Layer could not be read with open-source GDAL: {lyr.error}", layer=lyr.name)
    bad = [lyr for lyr in bad if not lyr.name.lower().startswith("view_")]
    user = [lyr for lyr in layers if not lyr.system]
    ctx.state["summary"] = {
        "layers": len(user),
        "feature_classes": sum(1 for lyr in user if lyr.spatial),
        "tables": sum(1 for lyr in user if not lyr.spatial),
        "system_tables": len(layers) - len(user),
        "features": sum(lyr.count for lyr in user if lyr.spatial),
        "rows": sum(lyr.count for lyr in user if not lyr.spatial),
        "unreadable": len(bad),
    }
    anno = [lyr.name for lyr in user if lyr.annotation]
    if anno:
        ctx.log(
            f"{len(anno)} annotation/dimension classes ({', '.join(anno)}) are inventoried but excluded from asset quality checks"
        )
    if not user:
        ctx.finding("warning", "The geodatabase contains no feature classes or tables; nothing to score")
    empty = [lyr.name for lyr in user if lyr.spatial and lyr.readable and lyr.count == 0]
    if empty:
        ctx.finding("info", f"{len(empty)} feature classes are empty", count=len(empty), detail=", ".join(empty))


@check("discover", "Read geodatabase catalog", order=20)
def catalog(ctx):
    """Parse GDB_Items: field definitions, domains, subtypes, metadata documents."""
    cat = ctx.dataset.catalog()
    nd = len(cat["domains"])
    ncls = len(cat["classes"])
    ndoc = sum(1 for it in cat["items"] if it.get("documentation"))
    ctx.log(f"Catalog: {ncls} class definitions, {nd} domains, {ndoc} items carry metadata XML")
    if cat.get("utility_networks"):
        ctx.log(f"Utility network controller dataset present: {', '.join(cat['utility_networks'])}")
    ctx.state["summary"].update(
        {
            "domains": nd,
            "utility_networks": cat.get("utility_networks", []),
            "feature_datasets": cat.get("feature_datasets", []),
        }
    )
    un_tables = [lyr.name for lyr in ctx.dataset.layers() if lyr.name.upper().startswith("UN_")]
    ap_tables = [lyr.name for lyr in ctx.dataset.layers() if lyr.name.startswith(("B_", "C_", "A_"))]
    if cat.get("utility_networks"):
        kind = "Built utility network"
    elif ap_tables:
        kind = "UN asset package (schema + sample data; network built in ArcGIS Pro)"
    elif (
        any(lyr.name.lower().startswith(("w", "e_", "ss")) for lyr in ctx.dataset.user_layers())
        and "migration" in str(ctx.dataset.path).lower()
    ):
        kind = "Pre-UN source data (migration input)"
    else:
        kind = "Plain geodatabase (no utility network)"
    # Tables registered in the system catalog that GDAL cannot list (missing / half-written files)
    if ctx.dataset.kind == "fgdb":
        sc = ctx.dataset.read_system_table("GDB_SystemCatalog")
        listed = {lyr.name.lower() for lyr in ctx.dataset.layers()}
        if sc is not None and "Name" in sc.columns:
            for oid, name in zip(sc.index, sc["Name"]):
                if not name or str(name).lower() in listed or str(name).upper().startswith("GDB_"):
                    continue
                stem = f"a{int(oid):08x}"
                base = Path(ctx.dataset.path)
                files = sorted(p.name for p in base.glob(stem + "*"))
                sev, hint = (
                    "error",
                    "Copy a fresh export from the data owner, or repair/compact the geodatabase in ArcGIS Pro.",
                )
                if str(name).lower().startswith("view_"):
                    sev, why, hint = "info", "it is a database view (views are not readable through GDAL)", ""
                elif any(f.endswith(".cdf") for f in files):
                    sev, why = (
                        "info",
                        "it is stored in Esri Compressed Data Format (CDF), which open-source GDAL cannot read",
                    )
                    hint = "Validator limitation, not a data defect. Uncompress the geodatabase in ArcGIS Pro to include it."
                elif not (base / f"{stem}.gdbtable").exists():
                    why = f"its data file {stem}.gdbtable is missing"
                    if any("_temp" in f for f in files):
                        why += f" (only {', '.join(f for f in files if '_temp' in f)} present - an interrupted write/compact)"
                else:
                    why = "the table file exists but could not be opened"
                ctx.log(f"  {name} is registered in GDB_SystemCatalog but {why}", "warn" if sev == "error" else "info")
                ctx.finding(
                    sev,
                    f"{name} is registered in the geodatabase but cannot be opened: {why}",
                    layer=str(name),
                    detail=hint,
                )
                if sev == "error":
                    ctx.state.setdefault("catalog_missing", []).append(str(name))
    ctx.state["summary"]["kind"] = kind
    ctx.state["summary"]["un_system_tables"] = len(un_tables)
    ctx.log(f"Dataset type: {kind}")


@check("discover", "Match reference UN data model", order=30)
def match_reference(ctx):
    """Find the Esri UN Foundation asset package that best matches this dataset."""
    cat = ctx.dataset.catalog()
    ctx.log("Comparing class and field names against the Esri UN Foundation asset packages...")
    target, ranking = match_target(ctx.dataset, cat["classes"])
    ctx.state["target"] = target
    ctx.state["target_ranking"] = ranking
    for r in ranking[:3]:
        ctx.log(
            f"  {r['name']}: score {r['score']} (class overlap {r['class_overlap']:.0%}, field overlap {r['field_jaccard']:.0%})"
        )
    if target:
        ctx.log(f"Reference model selected: {target['name']}")
        ctx.state["summary"]["reference_model"] = target["name"]
    else:
        ctx.log("No sufficiently similar Foundation model; schema conformance limited to internal checks", "warn")
        ctx.finding(
            "info", "No UN Foundation asset package resembles this dataset closely enough for a schema comparison"
        )
        ctx.state["summary"]["reference_model"] = None
