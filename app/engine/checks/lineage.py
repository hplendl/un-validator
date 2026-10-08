"""Stage 3 - Data source & lineage: CRS/vertical datum consistency, provenance, field-mapping coverage."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from ... import config
from ..geo import crs_info
from ..paths import resolved_roots
from ..registry import check

SYSTEM_FIELDS = {
    "objectid",
    "shape",
    "shape_length",
    "shape_area",
    "shape.len",
    "shape.area",
    "globalid",
    "fid",
    "st_length(shape)",
    "st_area(shape)",
    "shape_leng",
}
EDIT_FIELDS = {
    "created_user",
    "created_date",
    "last_edited_user",
    "last_edited_date",
    "creator",
    "creationdate",
    "editor",
    "editdate",
    "updatedby",
    "lastupdate",
}


@check("lineage", "CRS & vertical datum consistency", order=10)
def crs_consistency(ctx):
    """All spatial layers should share one horizontal CRS and a consistent vertical CRS."""
    spatial = [lyr for lyr in ctx.dataset.user_layers() if lyr.spatial and lyr.readable]
    if not spatial:
        ctx.log("No spatial layers")
        return
    ctx.log(f"Comparing coordinate systems of {len(spatial)} spatial layers")
    hz, vt, undefined = Counter(), Counter(), []
    per = {}
    for lyr in spatial:
        ci = crs_info(lyr.crs_wkt)
        per[lyr.name] = ci
        if not ci["defined"]:
            undefined.append(lyr.name)
            continue
        hz[ci["horizontal"]] += 1
        vt[ci["vertical"] or "(none)"] += 1
    for name, n in hz.most_common():
        ctx.log(f"  Horizontal: {name} on {n} layers")
    for name, n in vt.most_common():
        ctx.log(f"  Vertical:   {name} on {n} layers")
    sc = 100.0
    if undefined:
        ctx.finding(
            "error",
            f"{len(undefined)} spatial layers have no coordinate system",
            count=len(undefined),
            detail=", ".join(undefined),
        )
        sc -= 40
    if len(hz) > 1:
        main = hz.most_common(1)[0][0]
        odd = [n for n, ci in per.items() if ci["defined"] and ci["horizontal"] != main]
        ctx.finding(
            "error",
            f"Mixed horizontal CRS: {len(hz)} different systems ({', '.join(hz)})",
            count=len(odd),
            detail=", ".join(odd),
        )
        sc -= 40
    else:
        ctx.log("Horizontal CRS is consistent across all layers")
    if len(vt) > 1:
        ctx.finding("warning", f"Inconsistent vertical CRS across layers: {dict(vt)}", count=len(vt))
        sc -= 15
    zdecl = [lyr.name for lyr in spatial if lyr.geometry_type and "Z" in lyr.geometry_type.split()[-1]]
    if zdecl and vt.get("(none)", 0) == sum(vt.values()):
        ctx.finding(
            "warning",
            f"{len(zdecl)} layers store Z values but no vertical coordinate system is defined",
            count=len(zdecl),
            detail=", ".join(zdecl),
        )
        sc -= 10
    main = hz.most_common(1)[0][0] if hz else None
    if main:
        ci = next(c for c in per.values() if c.get("horizontal") == main)
        if ci.get("geographic"):
            ctx.finding(
                "warning", f"Data is stored in a geographic CRS ({main}); lengths and tolerances are in degrees"
            )
            sc -= 10
        ctx.state["main_crs"] = ci
    ctx.tables["crs"] = [
        {
            "layer": n,
            "horizontal": c.get("horizontal"),
            "epsg": c.get("epsg"),
            "vertical": c.get("vertical"),
            "units": c.get("units"),
        }
        for n, c in per.items()
    ]
    ctx.score(sc, weight=2, label="CRS consistency")


@check("lineage", "CRS vs reference model", order=20)
def crs_vs_reference(ctx):
    """Compare the dataset CRS with the matched UN Foundation asset package."""
    target, main = ctx.state.get("target"), ctx.state.get("main_crs")
    if not target or not main or not target.get("crs_wkt"):
        ctx.log("Skipped: no reference model or CRS to compare")
        return
    tc = crs_info(target["crs_wkt"])
    ctx.log(f"Dataset: {main['horizontal']} / {main.get('vertical') or 'no vertical CRS'}")
    ctx.log(f"Reference ({target['name']}): {tc['horizontal']} / {tc.get('vertical') or 'no vertical CRS'}")
    sc = 100.0
    if main["horizontal"] != tc["horizontal"]:
        if main.get("datum") != tc.get("datum"):
            ctx.finding(
                "warning",
                f"Horizontal datum differs from the reference model ({main.get('datum')} vs {tc.get('datum')}); "
                "loading requires a datum transformation",
                detail=f"{main['horizontal']} -> {tc['horizontal']}",
            )
            sc -= 40
        else:
            ctx.finding(
                "info",
                f"Projection differs from the reference model ({main['horizontal']} vs {tc['horizontal']}); same datum",
            )
            sc -= 15
    if (main.get("vertical") or None) != (tc.get("vertical") or None):
        ctx.finding(
            "info",
            f"Vertical CRS differs from the reference model ({main.get('vertical') or 'none'} vs {tc.get('vertical') or 'none'})",
        )
        sc -= 10
    ctx.score(sc, weight=1, label="CRS vs reference")


@check("lineage", "Provenance & edit tracking", order=30)
def provenance(ctx):
    """Editor-tracking fields and edit date range per feature class."""
    fcs = [lyr for lyr in ctx.dataset.user_layers() if lyr.spatial and lyr.readable and not lyr.annotation]
    if not fcs:
        return
    tracked, untracked, last_dates = [], [], []
    for lyr in fcs:
        names = {n.lower() for n, _ in lyr.fields}
        if names & EDIT_FIELDS:
            tracked.append(lyr.name)
            date_cols = [
                n
                for n, _ in lyr.fields
                if n.lower() in ("last_edited_date", "lastupdate", "editdate", "created_date", "creationdate")
            ]
            if date_cols and lyr.count:
                try:
                    df = ctx.dataset.read(lyr.name, columns=date_cols, geometry=False)
                    for c in date_cols:
                        s = pd.to_datetime(df[c], errors="coerce").dropna()
                        if len(s):
                            last_dates.append((s.min(), s.max()))
                except Exception:  # noqa: BLE001
                    pass
        else:
            untracked.append(lyr.name)
    ctx.log(f"Editor-tracking / creation fields present on {len(tracked)} of {len(fcs)} feature classes")
    if untracked:
        ctx.finding(
            "info",
            f"{len(untracked)} feature classes have no editor-tracking fields (who/when edited)",
            count=len(untracked),
            detail=", ".join(untracked),
        )
    if last_dates:
        lo = min(d[0] for d in last_dates)
        hi = max(d[1] for d in last_dates)
        ctx.log(f"Edit/creation dates span {lo:%Y-%m-%d} to {hi:%Y-%m-%d}")
        ctx.state.setdefault("summary", {})["edit_span"] = f"{lo:%Y-%m-%d} to {hi:%Y-%m-%d}"
    ctx.score(100.0 * len(tracked) / len(fcs), weight=0.5, label="Edit tracking coverage")


def _find_mapping_files(path: Path) -> tuple[Path | None, Path | None]:
    """Esri data-loading match table / DataMapping workbook next to the dataset (or one folder up).

    The folder above is only searched when it is still inside a configured data folder.
    """
    roots = [path.parent]
    up = path.parent.parent
    if config.ALLOW_ANY_PATH or any(up.resolve() == r or r in up.resolve().parents for r in resolved_roots()):
        roots.append(up)
    mt = next((p for r in roots for p in sorted(r.glob("*MatchTable*.csv"))), None)
    xl = next((p for r in roots for p in sorted(r.glob("DataMapping*.xlsx"))), None)
    return mt, xl


@check("lineage", "Field-mapping coverage (migration)", order=40)
def mapping_coverage(ctx):
    """Use Esri data-loading match tables / DataMapping.xlsx to measure source->target field coverage."""
    mt_path, xl_path = _find_mapping_files(ctx.dataset.path)
    target = ctx.state.get("target")
    if not mt_path and not xl_path:
        ctx.log("No data-loading match table or DataMapping workbook next to this dataset - skipped")
        return
    if not target:
        ctx.log("Mapping files found but no reference model matched - skipped", "warn")
        return
    ctx.log(f"Mapping files: {', '.join(p.name for p in (mt_path, xl_path) if p)}; target model {target['name']}")
    tclasses = target["classes"]
    tfields_by_class = {k: {f.name.lower() for f in v.fields} for k, v in tclasses.items()}
    all_tfields = set().union(*tfields_by_class.values()) if tfields_by_class else set()

    fmap = defaultdict(set)
    if mt_path:
        mt = pd.read_csv(mt_path, dtype=str).fillna("")
        ws = (mt["match_strings"] != mt["match_strings"].str.strip()).sum()
        if ws:
            ctx.finding(
                "info",
                f"Match table has {ws} rows with stray whitespace in match_strings",
                layer=mt_path.name,
                count=int(ws),
            )
        mt["match_strings"] = mt["match_strings"].str.strip()
        fm = mt[(mt["type"].str.strip() == "field") & (mt["match_strings"] == "exact_match")]
        for a, b in zip(fm["SubstringsA"], fm["SubstringsB"]):
            fmap[a.strip().lower()].add(b.strip().lower())
        bad_t = sorted({b for bs in fmap.values() for b in bs if b not in all_tfields})
        ctx.log(
            f"Match table: {len(fm)} field matches, {int((mt['type'] == 'value').sum())} value matches, "
            f"{int((mt['match_strings'] == 'exact_block').sum())} blocks"
        )
        if bad_t:
            ctx.finding(
                "warning",
                f"Match table maps to {len(bad_t)} fields that do not exist in {target['name']}",
                layer=mt_path.name,
                count=len(bad_t),
                detail=", ".join(bad_t),
            )
        src_fields_all = {n.lower() for lyr in ctx.dataset.user_layers() for n, _ in lyr.fields}
        bad_s = sorted(a for a in fmap if a not in src_fields_all)
        if bad_s:
            ctx.finding(
                "info",
                f"Match table references {len(bad_s)} source fields not present in this dataset",
                layer=mt_path.name,
                count=len(bad_s),
                detail=", ".join(bad_s),
            )

    class_map = {}
    if xl_path:
        try:
            dm = pd.read_excel(xl_path, sheet_name="Data Mappings")
            ctx.log(f"DataMapping.xlsx: {len(dm)} class/subtype mapping rows")
            counts = {lyr.name.lower(): lyr.count for lyr in ctx.dataset.layers()}
            for sc_, grp in dm.groupby("Source Class"):
                class_map[str(sc_).lower()] = {str(t).lower() for t in grp["Target Class"].dropna()}
                exp = pd.to_numeric(grp.get("Source Row Count"), errors="coerce").sum()
                act = counts.get(str(sc_).lower())
                if act is None:
                    ctx.finding(
                        "warning",
                        f"Mapping workbook lists source class '{sc_}' which is not in the dataset",
                        layer=str(sc_),
                    )
                elif exp and int(exp) != act:
                    ctx.finding(
                        "info",
                        f"Mapping workbook expects {int(exp):,} rows for {sc_} across its subtype queries; dataset has {act:,}",
                        layer=str(sc_),
                        count=abs(int(exp) - act),
                    )
            missing_t = sorted({t for ts in class_map.values() for t in ts if t not in tclasses})
            if missing_t:
                ctx.finding(
                    "error",
                    f"Mapping workbook targets {len(missing_t)} classes missing from {target['name']}",
                    count=len(missing_t),
                    detail=", ".join(missing_t),
                )
            unmapped = [
                lyr.name for lyr in ctx.dataset.user_layers() if lyr.count and lyr.name.lower() not in class_map
            ]
            if unmapped:
                ctx.finding(
                    "warning",
                    f"{len(unmapped)} source classes with data have no class mapping",
                    count=len(unmapped),
                    detail=", ".join(unmapped),
                )
        except Exception as e:  # noqa: BLE001
            ctx.finding("info", f"Could not read DataMapping workbook: {e}")

    rows, covs = [], []
    layers = [lyr for lyr in ctx.dataset.user_layers() if lyr.readable and lyr.count]
    for i, lyr in enumerate(layers):
        tset = class_map.get(lyr.name.lower())
        tf = set().union(*(tfields_by_class.get(t, set()) for t in tset)) if tset else all_tfields
        # editor-tracking fields are re-created by ArcGIS, so they are not part of the mapping job
        src = [
            n
            for n, _ in lyr.fields
            if n.lower() not in SYSTEM_FIELDS and n.lower() not in config.EDITOR_TRACKING_FIELDS
        ]
        if not src:
            continue
        mapped, unmapped = [], []
        for f in src:
            fl = f.lower()
            # <Parent>_GlobalID foreign keys become UN associations rather than fields
            if fl in tf or any(b in tf for b in fmap.get(fl, ())) or (fl.endswith("_globalid") and fl != "globalid"):
                mapped.append(f)
            else:
                unmapped.append(f)
        cov = 100.0 * len(mapped) / len(src)
        covs.append((cov, lyr.count))
        rows.append(
            {
                "source_class": lyr.name,
                "target": ", ".join(sorted(tset)) if tset else "(any class)",
                "fields": len(src),
                "mapped": len(mapped),
                "coverage": round(cov, 1),
                "unmapped": ", ".join(unmapped),
            }
        )
        ctx.log(f"  {lyr.name} -> {rows[-1]['target']}: {len(mapped)}/{len(src)} fields mapped ({cov:.0f}%)")
        ctx.progress((i + 1) / len(layers), lyr.name)
        if unmapped and cov < 50:
            ctx.finding(
                "warning",
                f"Only {cov:.0f}% of {lyr.name} fields map to the target ({len(unmapped)} unmapped)",
                layer=lyr.name,
                count=len(unmapped),
                detail=", ".join(unmapped),
            )
    ctx.tables["mapping_coverage"] = rows
    if covs:
        w = sum(c for _, c in covs) or 1
        avg = sum(cv * c for cv, c in covs) / w
        ctx.log(f"Row-weighted field-mapping coverage: {avg:.1f}%")
        ctx.state.setdefault("summary", {})["mapping_coverage"] = round(avg, 1)
        ctx.score(avg, weight=2, label="Field-mapping coverage")
