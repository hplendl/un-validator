"""Stage 5 - Data quality: required values, identifiers, geometry, Z/M and dates.

Network-specific quality checks (connectivity, ISCONNECTED, UN system tables) are in network.py.
Per-row checks stream large layers in batches (see ``Dataset.iter_batches``).
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import shapely

from ... import config
from ..registry import check
from ..scoring import penalty_score
from ._util import FirstIds, as_text, data_layers, field_lookup, normalize_guid

GEOMETRY_FIELDS = ("shape_length", "shape_area", "shape__length", "shape__area")


@check("quality", "Layer readability", order=5)
def readability(ctx) -> None:
    """Share of user layers that open-source tools can read."""
    user = [lyr for lyr in ctx.dataset.user_layers() if not lyr.name.lower().startswith("view_")]
    missing = ctx.state.get("catalog_missing", [])
    if not user and not missing:
        return
    ok = sum(1 for lyr in user if lyr.readable)
    total = len(user) + len(missing)
    ctx.log(
        f"{ok} of {total} registered user layers readable with GDAL/pyogrio"
        + (f" ({len(missing)} registered but missing: {', '.join(missing)})" if missing else "")
    )
    ctx.score(100.0 * ok / total, weight=1, label="Readability")


def _blank(s: pd.Series) -> pd.Series:
    m = s.isna()
    if s.dtype == object or pd.api.types.is_string_dtype(s.dtype):
        m |= as_text(s).str.strip().isin(["", "None", "nan"])
    return m


@check("quality", "Required fields populated", order=10)
def required_nulls(ctx) -> None:
    """Nulls/blanks in required fields (non-nullable in the schema, plus configured UN keys)."""
    req_cfg = {f.lower() for f in (ctx.rules.get("required_fields") or config.REQUIRED_FIELDS)}
    total = bad = 0
    layers = data_layers(ctx)
    for i, lyr in enumerate(layers):
        cd = ctx.dataset.class_def(lyr.name)
        present = field_lookup(lyr)
        req = {f for f in req_cfg if f in present}
        if cd:
            req |= {
                f.name.lower()
                for f in cd.fields
                if (not f.nullable or f.required)
                and f.type not in ("esriFieldTypeOID", "esriFieldTypeGeometry")
                and f.name.lower() in present
                and f.name.lower() not in config.EDITOR_TRACKING_FIELDS
                and f.name.lower() not in GEOMETRY_FIELDS
            }
        if not req:
            continue
        cols = [present[f] for f in sorted(req)]
        non_nullable = {f.name.lower() for f in cd.fields if not f.nullable} if cd else set()
        ctx.log(
            f"Checking {lyr.count:,} {lyr.name} {'features' if lyr.spatial else 'rows'} "
            f"for nulls in {len(cols)} required fields"
        )
        empty_n = dict.fromkeys(cols, 0)
        rows_n = dict.fromkeys(cols, 0)
        samples = {c: FirstIds(config.MAX_SAMPLE_IDS) for c in cols}
        flagged: list[np.ndarray] = []
        for df in ctx.dataset.iter_batches(lyr.name, columns=cols, geometry=False):
            mask_any = np.zeros(len(df), dtype=bool)
            for c in cols:
                m = _blank(df[c])
                rows_n[c] += len(m)
                n = int(m.sum())
                if n:
                    empty_n[c] += n
                    samples[c].add(df.index[m.values])
                    mask_any |= m.values
            if mask_any.any():
                flagged.append(np.asarray(df.index[mask_any]))
            ctx.check_cancel()
        for c in cols:
            total += rows_n[c]
            n = empty_n[c]
            if n:
                bad += n
                if c.lower() in req_cfg or c.lower() in non_nullable:
                    sev = ctx.sev("null_required", "error")
                else:
                    sev = ctx.sev("null_optional", "warning")
                ctx.finding(
                    sev,
                    f"{n:,} of {rows_n[c]:,} rows have no value in required field {c}",
                    layer=lyr.name,
                    count=n,
                    sample_ids=samples[c].ids,
                )
        if flagged and lyr.spatial:
            ctx.flag_fids(lyr.name, np.concatenate(flagged), "error", "Required field empty", max_n=100)
        ctx.progress((i + 1) / len(layers), lyr.name)
        ctx.pace()
    if total:
        ctx.log(f"{total:,} required values checked, {bad:,} empty")
        ctx.score(penalty_score(bad, total), weight=2, label="Required-field completeness")


@check("quality", "Duplicate GlobalIDs & asset IDs", order=20)
def duplicates(ctx) -> None:
    """GlobalIDs must be unique across the dataset; asset/facility IDs unique within a class."""
    gid_owner: list[pd.DataFrame] = []
    total_ids = 0
    bad = 0.0
    layers = data_layers(ctx)
    for i, lyr in enumerate(layers):
        present = field_lookup(lyr)
        g = present.get("globalid")
        if g:
            ctx.log(f"Checking {lyr.count:,} {lyr.name} {'features' if lyr.spatial else 'rows'} for duplicate GLOBALID")
            raw = ctx.dataset.read_columns(lyr.name, [g])[g]
            s = normalize_guid(raw)
            gid_owner.append(pd.DataFrame({"gid": s.values, "layer": lyr.name, "oid": raw.index}))
            d = s.duplicated(keep=False) & s.notna()
            total_ids += len(s)
            if d.any():
                n = int(d.sum())
                bad += n
                ctx.finding(
                    ctx.sev("duplicate_globalid", "error"),
                    f"{n:,} rows share a GlobalID with another row in {lyr.name}",
                    layer=lyr.name,
                    count=n,
                    sample_ids=list(raw.index[d.values][: config.MAX_SAMPLE_IDS]),
                )
                if lyr.spatial:
                    ctx.flag_fids(lyr.name, raw.index[d.values], "error", "Duplicate GlobalID", max_n=100)
        for af in ctx.rules.get("asset_id_fields") or config.ASSET_ID_FIELDS:
            col = present.get(af.lower())
            if not col:
                continue
            s = ctx.dataset.read_columns(lyr.name, [col])[col]
            s = s[s.notna() & (as_text(s).str.strip() != "")]
            ctx.log(f"Checking {len(s):,} populated {col} values in {lyr.name} for duplicates")
            d = s.duplicated(keep=False)
            total_ids += len(s)
            if d.any():
                n = int(d.sum())
                bad += n * 0.25
                top = as_text(s[d]).value_counts().head(5)
                ctx.finding(
                    ctx.sev("duplicate_asset_id", "warning"),
                    f"{n:,} {lyr.name} rows share a {col} value ({s[d].nunique():,} distinct values duplicated)",
                    layer=lyr.name,
                    count=n,
                    sample_ids=list(s.index[d][: config.MAX_SAMPLE_IDS]),
                    detail="Most repeated: " + ", ".join(f"{k} x{v}" for k, v in top.items()),
                )
                if lyr.spatial:
                    ctx.flag_fids(lyr.name, s.index[d], "warning", f"Duplicate {col}", max_n=80)
        ctx.progress((i + 1) / max(1, len(layers)), lyr.name)
    allg = pd.concat(gid_owner, ignore_index=True) if gid_owner else pd.DataFrame(columns=["gid", "layer", "oid"])
    allg = allg[allg.gid.notna() & (allg.gid != "")]
    if len(gid_owner) > 1:
        cross = allg[allg.gid.duplicated(keep=False)]
        cross = cross[cross.groupby("gid")["layer"].transform("nunique") > 1]
        if len(cross):
            ctx.finding(
                "error",
                f"{cross.gid.nunique():,} GlobalIDs are reused across different classes",
                count=len(cross),
                detail=", ".join(sorted(cross.layer.unique())),
            )
            bad += len(cross)
        ctx.log(f"Cross-class GlobalID check over {len(allg):,} IDs: {cross.gid.nunique():,} reused")
    ctx.state["all_globalids"] = set(allg.gid)
    if total_ids:
        ctx.score(penalty_score(bad, total_ids), weight=2, label="Identifier uniqueness")


@check("quality", "Geometry validity", order=30)
def geometry_validity(ctx) -> None:
    """Null/empty, invalid, zero-length and zero-area geometries; stacked duplicate points."""
    total = bad = 0
    layers = data_layers(ctx, spatial=True)
    for i, lyr in enumerate(layers):
        gt = (lyr.geometry_type or "").lower()
        ctx.log(f"Validating {lyr.count:,} {lyr.name} geometries ({lyr.geometry_type})")
        acc = {
            k: {"n": 0, "ids": FirstIds(config.MAX_SAMPLE_IDS), "fids": []}
            for k in ("empty", "invalid", "zero_len", "zero_area")
        }
        reason_counts: list[pd.Series] = []
        pt_xy: list[np.ndarray] = []
        pt_ids: list[np.ndarray] = []
        n_nonempty_pts = 0
        n_rows = 0
        for df in ctx.dataset.iter_batches(lyr.name, columns=[], geometry=True):
            geoms = df.geometry.values
            idx = df.index
            n_rows += len(df)
            empty = shapely.is_missing(geoms) | shapely.is_empty(geoms)
            valid_geoms = np.where(empty, None, geoms)

            def _add(key: str, mask: np.ndarray, _idx=idx, _acc=acc) -> None:
                k = int(mask.sum())
                if k:
                    _acc[key]["n"] += k
                    _acc[key]["ids"].add(_idx[mask])
                    _acc[key]["fids"].append(np.asarray(_idx[mask]))

            _add("empty", empty)
            if "polygon" in gt or "line" in gt:
                inval = ~shapely.is_valid(valid_geoms) & ~empty
                if inval.any():
                    _add("invalid", inval)
                    r = pd.Series(shapely.is_valid_reason(valid_geoms[inval])).str.replace(r"\[.*", "", regex=True)
                    reason_counts.append(r.value_counts())
            if "line" in gt:
                _add("zero_len", (shapely.length(valid_geoms) == 0) & ~empty)
            if "polygon" in gt:
                _add("zero_area", (shapely.area(valid_geoms) == 0) & ~empty)
            if "point" in gt:
                ne = valid_geoms[~empty]
                n_nonempty_pts += len(ne)
                pt_xy.append(shapely.get_coordinates(ne))
                pt_ids.append(np.asarray(idx[~empty]))
            ctx.check_cancel()
        total += n_rows
        if acc["empty"]["n"]:
            n = acc["empty"]["n"]
            bad += n
            ctx.finding(
                "error",
                f"{n:,} features have null or empty geometry",
                layer=lyr.name,
                count=n,
                sample_ids=acc["empty"]["ids"].ids,
            )
        if acc["invalid"]["n"]:
            n = acc["invalid"]["n"]
            bad += n
            if len(reason_counts) == 1:
                top = reason_counts[0].head(5)
            else:
                top = pd.concat(reason_counts).groupby(level=0, sort=False).sum()
                top = top.sort_values(ascending=False, kind="stable").head(5)
            ctx.finding(
                "error",
                f"{n:,} invalid geometries",
                layer=lyr.name,
                count=n,
                sample_ids=acc["invalid"]["ids"].ids,
                detail="; ".join(f"{k} x{int(v)}" for k, v in top.items()),
            )
            ctx.flag_fids(lyr.name, np.concatenate(acc["invalid"]["fids"]), "error", "Invalid geometry", max_n=100)
        if acc["zero_len"]["n"]:
            n = acc["zero_len"]["n"]
            bad += n
            ctx.finding(
                "error", f"{n:,} zero-length lines", layer=lyr.name, count=n, sample_ids=acc["zero_len"]["ids"].ids
            )
            ctx.flag_fids(lyr.name, np.concatenate(acc["zero_len"]["fids"]), "error", "Zero-length line", max_n=100)
        if acc["zero_area"]["n"]:
            n = acc["zero_area"]["n"]
            bad += n
            ctx.finding(
                "error", f"{n:,} zero-area polygons", layer=lyr.name, count=n, sample_ids=acc["zero_area"]["ids"].ids
            )
        if "point" in gt and n_rows > 1 and pt_xy:
            xy_all = np.concatenate(pt_xy) if len(pt_xy) > 1 else pt_xy[0]
            if len(xy_all) == n_nonempty_pts:  # single-part points only
                xy = pd.DataFrame(xy_all[:, :2], columns=["x", "y"]).round(4)
                dup = xy.duplicated(keep=False).values
                n = int(dup.sum())
                if n:
                    ids = np.concatenate(pt_ids)
                    ctx.finding(
                        "info",
                        f"{n:,} {lyr.name} points are stacked on another point of the same class",
                        layer=lyr.name,
                        count=n,
                        sample_ids=list(ids[dup][: config.MAX_SAMPLE_IDS]),
                    )
        ctx.progress((i + 1) / len(layers), lyr.name)
        ctx.pace()
    if total:
        ctx.score(penalty_score(bad, total), weight=2, label="Geometry validity")


@check("quality", "Z/M consistency", order=50)
def zm_consistency(ctx) -> None:
    """Z/M declared consistently across network classes; Z values actually populated."""
    layers = data_layers(ctx, spatial=True)
    if not layers:
        return
    decl = {}
    for lyr in layers:
        cd = ctx.dataset.class_def(lyr.name)
        hz = cd.has_z if cd and cd.has_z is not None else ("Z" in (lyr.geometry_type or "").split()[-1:])
        hm = cd.has_m if cd and cd.has_m is not None else None
        decl[lyr.name] = (hz, hm)
    zs = {v[0] for v in decl.values()}
    ms = {v[1] for v in decl.values() if v[1] is not None}
    ctx.log("Z-enabled: " + ", ".join(f"{k}={'Z' if v[0] else '-'}{'M' if v[1] else ''}" for k, v in decl.items()))
    sc = 100.0
    if len(zs) > 1:
        nz = [k for k, v in decl.items() if not v[0]]
        ctx.finding(
            "warning",
            f"Z-awareness is mixed: {len(nz)} of {len(decl)} feature classes are not Z-enabled",
            count=len(nz),
            detail=", ".join(nz),
        )
        sc -= 25
    if len(ms) > 1:
        nm = [k for k, v in decl.items() if v[1] is False]
        ctx.finding(
            "info",
            f"M-awareness is mixed: {len(nm)} feature classes are not M-enabled",
            count=len(nm),
            detail=", ".join(nm),
        )
        sc -= 10
    flat_layers = []
    for lyr in layers:
        if not decl[lyr.name][0]:
            continue
        n_geoms = n_coords = n_zero = 0
        any_z = False
        for df in ctx.dataset.iter_batches(lyr.name, columns=[], geometry=True):
            g = df.geometry.values
            g = g[~(shapely.is_missing(g) | shapely.is_empty(g))]
            n_geoms += len(g)
            if len(g) and shapely.has_z(g).any():
                any_z = True
            if len(g):
                z = shapely.get_coordinates(g, include_z=True)[:, 2]
                n_coords += len(z)
                n_zero += int(np.sum((z == 0) | np.isnan(z)))
            ctx.check_cancel()
        if not n_geoms or not any_z or (n_coords and n_zero / n_coords >= 0.999):
            flat_layers.append((lyr.name, n_geoms))
    if flat_layers:
        ctx.finding(
            "warning",
            f"{len(flat_layers)} Z-enabled classes have all Z values = 0 (no elevation captured)",
            count=sum(n for _, n in flat_layers),
            detail=", ".join(f"{n} ({c:,})" for n, c in flat_layers),
        )
        sc -= min(30, 5 * len(flat_layers))
    ctx.score(sc, weight=0.5, label="Z/M consistency")


def _parse_one(v) -> np.datetime64:
    try:
        return np.datetime64(str(v).strip()[:19].replace(" ", "T"), "s")
    except ValueError:
        return np.datetime64("NaT", "s")


def as_datetimes(s: pd.Series) -> pd.Series:
    """Datetimes without time zone, including years pandas' nanosecond type cannot hold.

    With pandas 2, GDAL returns a whole date column as text when one value is outside
    1677-2262 (for example a 9999-12-31 placeholder); those values must still be checked.
    """
    if pd.api.types.is_datetime64_any_dtype(s.dtype):
        out = s
    else:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = pd.to_datetime(s, errors="coerce")
    if getattr(out.dt, "tz", None) is not None:
        out = out.dt.tz_localize(None)
    missed = out.isna() & s.notna()
    if missed.any():
        arr = out.to_numpy(dtype="datetime64[s]").copy()
        arr[missed.to_numpy()] = np.array([_parse_one(v) for v in s[missed]], dtype="datetime64[s]")
        out = pd.Series(arr, index=s.index)
    return out


def _date_columns(lyr) -> list[str]:
    return [n for n, t in lyr.fields if "datetime" in t or t.startswith("<M8") or "date" in t.lower()]


@check("quality", "Date sanity", order=60)
def date_sanity(ctx) -> None:
    """Dates in the future or implausibly old."""
    now = pd.Timestamp(datetime.now() + timedelta(days=1))
    floor = pd.Timestamp(1850, 1, 1)
    total = bad = 0
    for lyr in data_layers(ctx):
        dcols = _date_columns(lyr)
        if not dcols:
            continue
        stats = {
            c: {"fut": 0, "old": 0, "max": None, "min": None, "fids": FirstIds(), "oids": FirstIds()} for c in dcols
        }
        for df in ctx.dataset.iter_batches(lyr.name, columns=dcols, geometry=False):
            for c in dcols:
                s = as_datetimes(df[c]).dropna()
                if s.empty:
                    continue
                total += len(s)
                fut, old = s[s > now], s[s < floor]
                st = stats[c]
                if len(fut):
                    st["fut"] += len(fut)
                    st["max"] = fut.max() if st["max"] is None else max(st["max"], fut.max())
                    st["fids"].add(fut.index)
                if len(old):
                    st["old"] += len(old)
                    st["min"] = old.min() if st["min"] is None else min(st["min"], old.min())
                    st["oids"].add(old.index)
            ctx.check_cancel()
        for c, st in stats.items():
            if st["fut"]:
                bad += st["fut"]
                ctx.finding(
                    "warning",
                    f"{st['fut']:,} {c} values are in the future (max {st['max']:%Y-%m-%d})",
                    layer=lyr.name,
                    count=st["fut"],
                    sample_ids=st["fids"].ids,
                )
            if st["old"]:
                bad += st["old"]
                ctx.finding(
                    "warning",
                    f"{st['old']:,} {c} values are before 1850 (min {st['min']:%Y-%m-%d})",
                    layer=lyr.name,
                    count=st["old"],
                    sample_ids=st["oids"].ids,
                )
    if total:
        ctx.log(f"{total:,} date values checked, {bad:,} implausible")
        ctx.score(penalty_score(bad, total), weight=0.5, label="Date plausibility")
    else:
        ctx.log("No populated date fields")
