"""Stage 4 - Schema & domain conformance."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ... import config
from ..dataset import is_system_name
from ..registry import check

SKIP_TYPES = {
    "esriFieldTypeOID",
    "esriFieldTypeGeometry",
    "esriFieldTypeBlob",
    "esriFieldTypeRaster",
    "esriFieldTypeXML",
}
NUMERIC = ("Integer", "SmallInteger", "BigInteger", "Double", "Single")


def _family(t: str | None) -> str:
    t = (t or "").lower()
    if any(k in t for k in ("smallinteger", "integer", "int16", "int32", "int64", "biginteger")):
        return "int"
    if any(k in t for k in ("double", "single", "float", "real")):
        return "float"
    if "date" in t or "time" in t:
        return "date"
    if "guid" in t or "globalid" in t:
        return "guid"
    if "string" in t or "object" in t or "str" in t:
        return "text"
    return t or "?"


@check("schema", "Domain integrity", order=10)
def domain_integrity(ctx):
    """Every domain referenced by a field or subtype must exist and match the field type."""
    cat = ctx.dataset.catalog()
    doms = cat["domains"]
    if not doms and ctx.dataset.kind == "gpkg":
        ctx.log("GeoPackage: no Esri domains to validate")
        return
    used, missing, mismatched = set(), [], []
    classes = [c for c in cat["classes"].values() if not is_system_name(c.name)]
    for cd in classes:
        fmap = cd.field_map()
        refs = [(f.name, f.domain) for f in cd.fields if f.domain]
        for st in cd.subtypes.values():
            refs += [(fn, dn) for fn, dn in st["domains"].items()]
        for fn, dn in refs:
            used.add(dn)
            if dn not in doms:
                missing.append(f"{cd.name}.{fn} -> {dn}")
                continue
            f = fmap.get(fn.lower())
            if (
                f
                and doms[dn].field_type
                and _family(f.type) != _family(doms[dn].field_type)
                and {_family(f.type), _family(doms[dn].field_type)} != {"int", "float"}
            ):
                mismatched.append(
                    f"{cd.name}.{f.name} ({f.type.replace('esriFieldType', '')}) uses {dn} ({doms[dn].field_type.replace('esriFieldType', '')})"
                )
    ctx.log(f"{len(doms)} domains defined; {len(used)} referenced by {len(classes)} classes")
    sc = 100.0
    if missing:
        ctx.finding(
            "error",
            f"{len(missing)} field/subtype domain references point to domains that do not exist",
            count=len(missing),
            detail="; ".join(sorted(set(missing))[:60]),
        )
        sc -= min(60, 5 * len(missing))
    if mismatched:
        ctx.finding(
            "warning",
            f"{len(mismatched)} fields use a domain of a different data type",
            count=len(mismatched),
            detail="; ".join(mismatched[:60]),
        )
        sc -= min(30, 3 * len(mismatched))
    unused = sorted(set(doms) - used)
    if unused:
        ctx.finding(
            "info",
            f"{len(unused)} domains are defined but not used by any field",
            count=len(unused),
            detail=", ".join(unused[:100]),
        )
    ctx.score(sc, weight=1, label="Domain integrity")


def _domain_for_rows(cd, field_lower):
    """Return list of (subtype_code or None, domain_name) assignments for a field."""
    default = next((f.domain for f in cd.fields if f.name.lower() == field_lower), None)
    out = []
    if cd.subtype_field and cd.subtypes:
        for code, st in cd.subtypes.items():
            dn = st["domains"].get(field_lower, default)
            if dn:
                out.append((code, dn))
    elif default:
        out.append((None, default))
    return out


@check("schema", "Coded values within domains", order=20)
def coded_values(ctx):
    """Values in domain-controlled fields must be valid codes (subtype-aware) or within range."""
    cat = ctx.dataset.catalog()
    doms = cat["domains"]
    layers = [lyr for lyr in ctx.dataset.user_layers() if lyr.readable and lyr.count]
    total_checked = total_bad = 0
    rows_out = []
    for li, lyr in enumerate(layers):
        cd = ctx.dataset.class_def(lyr.name)
        if cd is None:
            continue
        present = {n.lower(): n for n, _ in lyr.fields}
        dfields = sorted(
            {f.name.lower() for f in cd.fields if f.domain}
            | {fn for st in cd.subtypes.values() for fn in st["domains"]}
        )
        dfields = [f for f in dfields if f in present]
        if not dfields:
            continue
        stf = present.get((cd.subtype_field or "").lower())
        cols = [present[f] for f in dfields] + ([stf] if stf and stf not in [present[f] for f in dfields] else [])
        ctx.log(
            f"Checking {lyr.count:,} {lyr.name} {'features' if lyr.spatial else 'rows'}: {len(dfields)} domain-controlled fields"
            + (f" (subtype field {stf})" if stf else "")
        )
        df = ctx.dataset.read_columns(lyr.name, cols)
        bad_mask_any = np.zeros(len(df), dtype=bool)
        for fl in dfields:
            col = present[fl]
            assigns = _domain_for_rows(cd, fl)
            if not assigns:
                continue
            series = df[col]
            for code, dn in assigns:
                dom = doms.get(dn)
                if dom is None:
                    continue
                if code is not None and stf:
                    sel = pd.to_numeric(df[stf], errors="coerce") == code
                else:
                    sel = pd.Series(True, index=df.index)
                vals = series[sel & series.notna()]
                if vals.empty:
                    continue
                extra = (ctx.rules.get("domains") or {}).get(dn, [])
                if dom.kind == "coded":
                    if any(isinstance(k, (int, float)) and not isinstance(k, bool) for k in dom.codes):
                        valid = {float(k) for k in dom.codes if isinstance(k, (int, float)) and not isinstance(k, bool)}
                        for ex in extra:
                            try:
                                valid.add(float(ex))
                            except (TypeError, ValueError):
                                pass
                        num = pd.to_numeric(vals, errors="coerce")
                        badm = ~num.isin(valid)
                    else:
                        valid = {str(k).strip() for k in dom.codes}
                        valid |= {str(ex).strip() for ex in extra}
                        sv = (
                            vals.map(lambda v: v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else v)
                            .astype(str)
                            .str.strip()
                        )
                        badm = ~sv.isin(valid) & (sv != "")
                else:
                    num = pd.to_numeric(vals, errors="coerce")
                    badm = (num < (dom.min if dom.min is not None else -np.inf)) | (
                        num > (dom.max if dom.max is not None else np.inf)
                    )
                total_checked += len(vals)
                nbad = int(badm.sum())
                if nbad:
                    total_bad += nbad
                    bad_idx = vals.index[badm.values]
                    bad_mask_any |= df.index.isin(bad_idx)
                    sample_vals = ", ".join(str(v) for v in pd.Series(vals[badm]).value_counts().head(6).index)
                    stname = cd.subtypes.get(code, {}).get("name") if code is not None else None
                    ctx.finding(
                        ctx.sev("coded_value", "error"),
                        f"{nbad:,} values in {col} not in domain {dn}" + (f" (subtype {stname})" if stname else ""),
                        layer=lyr.name,
                        count=nbad,
                        sample_ids=list(bad_idx[:25]),
                        detail=f"Invalid values: {sample_vals}",
                    )
                    rows_out.append({"layer": lyr.name, "field": col, "domain": dn, "subtype": stname, "invalid": nbad})
        if bad_mask_any.any() and lyr.spatial:
            ctx.flag_fids(lyr.name, df.index[bad_mask_any], "error", "Value outside domain", max_n=150)
        ctx.progress((li + 1) / len(layers), lyr.name)
        ctx.pace()
    ctx.tables["domain_violations"] = rows_out
    if total_checked:
        pct = 100.0 * (1 - total_bad / total_checked)
        ctx.log(f"{total_checked:,} domain-controlled values checked; {total_bad:,} invalid ({100 - pct:.2f}%)")
        # amplify small error rates: 1% invalid -> 90
        ctx.score(max(0.0, 100 - 10 * (100 - pct)), weight=2, label="Coded-value validity")
    else:
        ctx.log("No domain-controlled values with data to check")


def _best_counterpart(cd, tclasses):
    if cd.name.lower() in tclasses:
        return tclasses[cd.name.lower()], 1.0, "name"
    sf = {f.name.lower() for f in cd.fields if f.type not in SKIP_TYPES}
    best, bj = None, 0.0
    for tc in tclasses.values():
        if (tc.kind == "Feature Class") != (cd.kind == "Feature Class"):
            continue
        tf = {f.name.lower() for f in tc.fields if f.type not in SKIP_TYPES}
        j = len(sf & tf) / max(1, len(sf | tf))
        if j > bj:
            best, bj = tc, j
    return (best, bj, "fields") if best is not None and bj >= 0.5 else (best, bj, "none")


@check("schema", "Conformance to reference model", order=30)
def reference_conformance(ctx):
    """Missing / extra fields, type and domain mismatches vs the matched UN Foundation asset package."""
    target = ctx.state.get("target")
    if not target:
        ctx.log("No reference model matched - skipped")
        return
    tclasses = target["classes"]
    cat = ctx.dataset.catalog()
    src = [c for c in cat["classes"].values() if not is_system_name(c.name)]
    ctx.log(f"Comparing {len(src)} classes with {len(tclasses)} classes in {target['name']}")
    rows, scores = [], []
    best_overlap = 0.0
    for i, cd in enumerate(src):
        tc, sim, how = _best_counterpart(cd, tclasses)
        best_overlap = max(best_overlap, sim)
        if how == "none":
            rows.append(
                {
                    "class": cd.name,
                    "counterpart": None,
                    "match": f"best field overlap {sim:.0%}" + (f" ({tc.name})" if tc else ""),
                    "missing": None,
                    "extra": None,
                    "type_mismatch": None,
                    "score": None,
                }
            )
            continue
        sf, tf = cd.field_map(), tc.field_map()
        tfields = {k: v for k, v in tf.items() if v.type not in SKIP_TYPES}
        sfields = {k: v for k, v in sf.items() if v.type not in SKIP_TYPES}

        def _has(k, sfields=sfields):
            if k in sfields:
                return True
            grp = next((g for g in config.EDITOR_TRACKING_GROUPS if k in g), None)
            return bool(grp and grp & set(sfields))

        missing = [tfields[k] for k in tfields if not _has(k)]
        extra = [
            sfields[k].name
            for k in sfields
            if k not in tfields
            and not (k in config.EDITOR_TRACKING_FIELDS and config.EDITOR_TRACKING_FIELDS & set(tfields))
        ]
        tmis = [
            f"{sfields[k].name} ({_family(sfields[k].type)} vs {_family(tfields[k].type)})"
            for k in tfields
            if k in sfields and _family(sfields[k].type) != _family(tfields[k].type)
        ]
        dmis = [
            f"{sfields[k].name}: {sfields[k].domain or 'none'} vs {tfields[k].domain}"
            for k in tfields
            if k in sfields and tfields[k].domain and (sfields[k].domain or "") != tfields[k].domain
        ]
        req_missing = [
            f.name
            for f in missing
            if ((not f.nullable) or f.required or f.name.upper() in ("ASSETGROUP", "ASSETTYPE"))
            and f.name.lower() not in config.EDITOR_TRACKING_FIELDS
        ]
        ok = len(tfields) - len(missing) - len(tmis)
        cs = 100.0 * ok / max(1, len(tfields))
        scores.append((cs, max(1, len(tfields))))
        rows.append(
            {
                "class": cd.name,
                "counterpart": tc.name,
                "match": "same name" if how == "name" else f"field overlap {sim:.0%}",
                "missing": len(missing),
                "extra": len(extra),
                "type_mismatch": len(tmis),
                "score": round(cs, 1),
            }
        )
        ctx.log(f"  {cd.name} vs {tc.name}: {len(missing)} missing, {len(extra)} extra, {len(tmis)} type mismatches")
        if req_missing:
            ctx.finding(
                "error",
                f"{cd.name} lacks {len(req_missing)} required fields of {target['name']}/{tc.name}",
                layer=cd.name,
                count=len(req_missing),
                detail=", ".join(req_missing),
            )
        opt_missing = [f.name for f in missing if f.name not in req_missing]
        if opt_missing:
            ctx.finding(
                "warning",
                f"{cd.name} is missing {len(opt_missing)} fields defined in {target['name']}/{tc.name}",
                layer=cd.name,
                count=len(opt_missing),
                detail=", ".join(opt_missing),
            )
        if extra:
            ctx.finding(
                "info",
                f"{cd.name} has {len(extra)} fields not in the reference {tc.name}",
                layer=cd.name,
                count=len(extra),
                detail=", ".join(extra),
            )
        if tmis:
            ctx.finding(
                "warning",
                f"{cd.name}: {len(tmis)} fields differ in data type from the reference {tc.name}",
                layer=cd.name,
                count=len(tmis),
                detail="; ".join(tmis),
            )
        if dmis:
            ctx.finding(
                "info",
                f"{cd.name}: {len(dmis)} fields use a different domain than the reference {tc.name}",
                layer=cd.name,
                count=len(dmis),
                detail="; ".join(dmis[:40]),
            )
        ctx.progress((i + 1) / max(1, len(src)), cd.name)
    ctx.tables["conformance"] = rows
    if scores:
        tw = sum(w for _, w in scores)
        avg = sum(s * w for s, w in scores) / tw
        ctx.log(f"Field-weighted conformance: {avg:.1f}%")
        ctx.score(avg, weight=2, label="Reference-model conformance")
        absent = [tc.name for k, tc in tclasses.items() if k not in cat["classes"] and tc.kind == "Feature Class"]
        if absent and len(rows) and any(r["match"] == "same name" for r in rows):
            ctx.finding(
                "info",
                f"{len(absent)} reference feature classes are not present in the dataset",
                count=len(absent),
                detail=", ".join(absent),
            )
    else:
        ctx.finding(
            "warning",
            f"No class corresponds to {target['name']} (best field-name overlap {best_overlap:.0%}); "
            "dataset is not modelled on the UN Foundation schema",
        )
        ctx.score(100 * best_overlap, weight=1, label="Reference-model conformance")
