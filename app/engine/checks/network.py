"""Stage 5 - Data quality, network part: line connectivity, ISCONNECTED status, UN system tables."""

from __future__ import annotations

import re
from collections import Counter

import numpy as np
import pandas as pd
import shapely
from shapely import STRtree

from ... import config
from ..geo import crs_info, horizontal_crs
from ..registry import check
from ..scoring import penalty_score
from ._util import data_layers, field_lookup, normalize_guid

NETWORK_EXCLUDE = re.compile(r"subnetline|serviceterritory|dirtyarea|boundary", re.I)


def _non_empty(g: np.ndarray) -> np.ndarray:
    return ~(shapely.is_missing(g) | shapely.is_empty(g))


def _reference_crs(layers) -> str | None:
    """WKT of the most common horizontal CRS among *layers* (first layer wins ties)."""
    names = Counter(crs_info(lyr.crs_wkt).get("horizontal") for lyr in layers if lyr.crs_wkt)
    if not names:
        return None
    top = names.most_common(1)[0][0]
    return next(lyr.crs_wkt for lyr in layers if lyr.crs_wkt and crs_info(lyr.crs_wkt).get("horizontal") == top)


def _geoms_in(ctx, lyr, ref_wkt: str | None):
    """Non-empty geometries of a layer (fid index) in the reference CRS; None if it cannot be reprojected."""
    gdf = ctx.dataset.read_geometry(lyr.name)
    gdf = gdf[_non_empty(gdf.geometry.values)]
    if ref_wkt and lyr.crs_wkt and crs_info(lyr.crs_wkt).get("horizontal") != crs_info(ref_wkt).get("horizontal"):
        try:
            gdf = gdf.set_crs(horizontal_crs(lyr.crs_wkt), allow_override=True).to_crs(horizontal_crs(ref_wkt))
            ctx.log(f"  {lyr.name} reprojected to {crs_info(ref_wkt).get('horizontal')} for the connectivity test")
        except Exception as e:  # noqa: BLE001
            ctx.log(f"  {lyr.name} skipped: cannot reproject ({e})", "warn")
            return None
    return gdf


@check("quality", "Line connectivity: dangles & near-misses", order=40)
def dangles(ctx) -> None:
    """Line endpoints that touch nothing (dangles) and those that just miss another feature (near-misses)."""
    import geopandas as gpd

    layers = data_layers(ctx, spatial=True)
    lines = [
        lyr for lyr in layers if "line" in (lyr.geometry_type or "").lower() and not NETWORK_EXCLUDE.search(lyr.name)
    ]
    points = [
        lyr for lyr in layers if "point" in (lyr.geometry_type or "").lower() and not NETWORK_EXCLUDE.search(lyr.name)
    ]
    if not lines:
        ctx.log("No line layers to test")
        return
    ref_wkt = _reference_crs(lines)
    main = ctx.state.get("main_crs")
    ci = main if main and main.get("horizontal") == crs_info(ref_wkt).get("horizontal") else crs_info(ref_wkt)
    unit_m = ci.get("unit_m") or 1.0
    units = ci.get("units") or "units"
    tols = []
    for lyr in lines:
        cd = ctx.dataset.class_def(lyr.name)
        if cd and cd.xy_tolerance:
            tols.append(cd.xy_tolerance)
    snap_m = float((ctx.rules.get("thresholds") or {}).get("snap_tolerance_m", config.SNAP_TOLERANCE_M))
    near_m = float((ctx.rules.get("thresholds") or {}).get("near_miss_m", config.NEAR_MISS_M))
    snap = max(tols) if tols else snap_m / unit_m
    near = near_m / unit_m
    ctx.log(f"Snap tolerance {snap:.4g} {units}; near-miss threshold {near:.3g} {units}")
    geoms, owner_layer, owner_oid = [], [], []
    used_lines = []
    for lyr in lines:
        gdf = _geoms_in(ctx, lyr, ref_wkt)
        if gdf is None:
            continue
        used_lines.append(lyr)
        geoms.append(gdf.geometry.values)
        owner_layer += [lyr.name] * len(gdf)
        owner_oid += list(gdf.index)
        ctx.check_cancel()
    geoms = np.concatenate(geoms) if geoms else np.array([])
    owner_layer = np.array(owner_layer)
    owner_oid = np.array(owner_oid)
    parts, pidx = shapely.get_parts(geoms, return_index=True)
    parts = shapely.force_2d(parts)
    starts, ends = shapely.get_point(parts, 0), shapely.get_point(parts, -1)
    eps = np.concatenate([starts, ends])
    ep_owner = np.concatenate([pidx, pidx])
    ctx.log(
        f"Testing {len(eps):,} endpoints of {len(geoms):,} lines in {len(used_lines)} layers "
        f"against lines and {len(points)} point layers"
    )
    ctx.progress(0.2, "building spatial index")
    tree = STRtree(parts)
    a, b = tree.query(eps, predicate="dwithin", distance=snap)
    other = pidx[b] != ep_owner[a]
    connected = np.zeros(len(eps), dtype=bool)
    connected[np.unique(a[other])] = True
    ctx.progress(0.5, "matching endpoints to point features")
    pts = []
    for lyr in points:
        gdf = _geoms_in(ctx, lyr, ref_wkt)
        if gdf is not None:
            pts.append(gdf.geometry.values)
    ptree = None
    if pts:
        allp = shapely.force_2d(np.concatenate(pts))
        if len(allp):
            ptree = STRtree(allp)
            pa, _ = ptree.query(eps, predicate="dwithin", distance=snap)
            connected[np.unique(pa)] = True
    dang = np.where(~connected)[0]
    ctx.progress(0.75, "classifying dangles")
    nearmiss = np.zeros(len(eps), dtype=bool)
    if len(dang):
        a2, b2 = tree.query(eps[dang], predicate="dwithin", distance=near)
        o2 = pidx[b2] != ep_owner[dang][a2]
        nearmiss[dang[np.unique(a2[o2])]] = True
        if ptree is not None:
            pa2, _ = ptree.query(eps[dang], predicate="dwithin", distance=near)
            nearmiss[dang[np.unique(pa2)]] = True
    n_nm = int(nearmiss.sum())
    n_dg = len(dang) - n_nm
    ctx.log(f"{len(dang):,} unconnected endpoints: {n_nm:,} near-misses (within {near:.3g}), {n_dg:,} free dangles")
    crs = horizontal_crs(ref_wkt) if ref_wkt else None
    rows = []
    unit_txt = ci.get("units") or ""
    for lyr in used_lines:
        sel = owner_layer[ep_owner] == lyr.name if len(eps) else np.zeros(0, dtype=bool)
        free = ~connected & ~nearmiss & sel
        rows.append(
            {
                "layer": lyr.name,
                "endpoints": int(sel.sum()),
                "near_miss": int((nearmiss & sel).sum()),
                "dangles": int(free.sum()),
            }
        )
        nm_oids = owner_oid[ep_owner[nearmiss & sel]]
        dg_oids = owner_oid[ep_owner[free]]
        if len(nm_oids):
            ctx.finding(
                "warning",
                f"{len(nm_oids):,} line ends in {lyr.name} stop within {near:.3g} {unit_txt} "
                "of another feature without connecting (near-miss)",
                layer=lyr.name,
                count=len(nm_oids),
                sample_ids=list(dict.fromkeys(nm_oids.tolist()))[: config.MAX_SAMPLE_IDS],
            )
            gdf = gpd.GeoDataFrame(index=nm_oids, geometry=eps[nearmiss & sel], crs=crs)
            ctx.flag_features(lyr.name, gdf, "warning", "Near-miss endpoint", max_n=250)
        if len(dg_oids):
            ctx.finding(
                "info",
                f"{len(dg_oids):,} free dangling line ends in {lyr.name} (no feature within {near:.3g})",
                layer=lyr.name,
                count=len(dg_oids),
                sample_ids=list(dict.fromkeys(dg_oids.tolist()))[: config.MAX_SAMPLE_IDS],
            )
            gdf = gpd.GeoDataFrame(index=dg_oids, geometry=eps[free], crs=crs)
            ctx.flag_features(lyr.name, gdf, "info", "Dangling endpoint", max_n=120)
    ctx.tables["connectivity"] = rows
    ctx.progress(1.0, "done")
    if len(eps):
        ctx.score(
            max(0.0, 100.0 * (1 - min(1.0, (5 * n_nm + 0.5 * n_dg) / len(eps)))), weight=2, label="Line connectivity"
        )


@check("quality", "Network connectivity status (ISCONNECTED)", order=70)
def isconnected(ctx) -> None:
    """Features the utility network marks as not connected."""
    doms = ctx.dataset.domains()
    tot = dis = 0
    for lyr in data_layers(ctx, spatial=True):
        col = field_lookup(lyr).get("isconnected")
        if not col:
            continue
        cd = ctx.dataset.class_def(lyr.name)
        dn = next((f.domain for f in cd.fields if f.name.lower() == "isconnected"), None) if cd else None
        codes = doms[dn].codes if dn in doms else {}
        bad_codes = [k for k, v in codes.items() if re.search(r"disconnect|not connected", str(v), re.I)] or [2]
        raw = ctx.dataset.read_columns(lyr.name, [col])[col]
        s = pd.to_numeric(raw, errors="coerce")
        m = s.isin([float(c) for c in bad_codes])
        tot += len(s)
        n = int(m.sum())
        ctx.log(f"{lyr.name}: {n:,} of {len(s):,} features flagged not connected")
        if n:
            dis += n
            ctx.finding(
                "warning",
                f"{n:,} {lyr.name} features are marked disconnected from the network (ISCONNECTED)",
                layer=lyr.name,
                count=n,
                sample_ids=list(raw.index[m.values][: config.MAX_SAMPLE_IDS]),
            )
            ctx.flag_fids(lyr.name, raw.index[m.values], "warning", "Disconnected (ISCONNECTED)", max_n=120)
    if tot:
        ctx.score(penalty_score(dis, tot, factor=3), weight=1, label="Network connectivity status")
    else:
        ctx.log("No ISCONNECTED attribute in this dataset")


UN_TABLES = (
    "ASSOCIATIONS",
    "SUBNETWORKS",
    "RULES",
    "SYSTEMJUNCTIONS",
    "DIRTYAREAS",
    "POINTERRORS",
    "LINEERRORS",
    "POLYGONERRORS",
    "TRACECONFIGURATIONS",
)


def _guids(df: pd.DataFrame, col: str) -> pd.Series:
    return normalize_guid(df[col])


def _built_network(ctx, names: dict, pref: str, gids: set, rows: list) -> float:
    ds = ctx.dataset
    sc = 100.0

    def cnt(t: str) -> int | None:
        lyr = names.get(f"{pref}_{t}")
        return lyr.count if lyr else None

    for t in UN_TABLES:
        c = cnt(t)
        if c is not None:
            rows.append({"table": f"{pref}_{t}", "rows": c})
            ctx.log(f"  {pref}_{t}: {c:,} rows")
    if (cnt("RULES") or 0) == 0:
        ctx.finding("error", "Utility network has no connectivity/containment rules", layer=f"{pref}_RULES")
        sc -= 40
    if (cnt("SUBNETWORKS") or 0) == 0:
        ctx.finding("warning", "No subnetworks have been created/updated", layer=f"{pref}_SUBNETWORKS")
        sc -= 15
    da = cnt("DIRTYAREAS") or 0
    if da:
        ctx.finding(
            "warning", f"{da:,} dirty areas: network topology must be validated", layer=f"{pref}_DIRTYAREAS", count=da
        )
        sc -= 20
    else:
        ctx.log("No dirty areas - network topology is validated")
    errs = sum(cnt(t) or 0 for t in ("POINTERRORS", "LINEERRORS", "POLYGONERRORS"))
    if errs:
        ctx.finding("error", f"{errs:,} network topology errors recorded", count=errs)
        sc -= 30
    sub = ds.read_system_table(f"{pref}_SUBNETWORKS")
    if sub is not None and "ISDIRTY" in sub.columns and len(sub):
        nd = int((pd.to_numeric(sub["ISDIRTY"], errors="coerce") == 1).sum())
        ctx.log(f"Subnetworks: {len(sub):,} rows, {nd:,} dirty")
        if nd:
            ctx.finding(
                "warning",
                f"{nd:,} of {len(sub):,} subnetwork records are dirty (Update Subnetwork needed)",
                layer=f"{pref}_SUBNETWORKS",
                count=nd,
            )
            sc -= min(20, 100 * nd / len(sub))
    assoc = ds.read_system_table(f"{pref}_ASSOCIATIONS")
    sj = ds.read_system_table(f"{pref}_SYSTEMJUNCTIONS")
    if assoc is not None and len(assoc) and gids and {"FROMGLOBALID", "TOGLOBALID"} <= set(assoc.columns):
        known = set(gids)
        if sj is not None and "GLOBALID" in sj.columns:
            known |= set(_guids(sj, "GLOBALID").dropna())
        f = _guids(assoc, "FROMGLOBALID")
        t = _guids(assoc, "TOGLOBALID")
        orphan = ~f.isin(known) | ~t.isin(known)
        n = int(orphan.sum())
        ctx.log(
            f"Associations: {len(assoc):,}; checked endpoints against {len(known):,} known GlobalIDs - {n:,} orphaned"
        )
        if n:
            ctx.finding(
                "error",
                f"{n:,} associations reference features that do not exist",
                layer=f"{pref}_ASSOCIATIONS",
                count=n,
                sample_ids=list(assoc.index[orphan.values][: config.MAX_SAMPLE_IDS]),
            )
            sc -= min(30, 300 * n / len(assoc))
        types = assoc["ASSOCIATIONTYPE"].value_counts().to_dict() if "ASSOCIATIONTYPE" in assoc.columns else {}
        ctx.state.setdefault("summary", {})["association_types"] = {str(k): int(v) for k, v in types.items()}
    return sc


def _asset_package(ctx, names: dict, gids: set, rows: list) -> float:
    ds = ctx.dataset
    sc = 100.0
    ctx.log("Asset package: checking C_Associations / C_SubnetworkControllers / B_Rules")
    for t in ("B_RULES", "C_ASSOCIATIONS", "C_SUBNETWORKCONTROLLERS", "B_ATTRIBUTERULES", "B_TERMINALCONFIGURATION"):
        if t in names:
            rows.append({"table": names[t].name, "rows": names[t].count})
            ctx.log(f"  {names[t].name}: {names[t].count:,} rows")
    if names.get("B_RULES") and names["B_RULES"].count == 0:
        ctx.finding("error", "Asset package has no rules (B_Rules empty)", layer="B_Rules")
        sc -= 40
    known = set(gids)
    for t, cols in (
        ("C_Associations", ("from_global_id", "to_global_id")),
        ("C_SubnetworkControllers", ("global_id",)),
    ):
        df = ds.read_system_table(t)
        if df is None or not len(df) or not known:
            continue
        m = np.zeros(len(df), dtype=bool)
        for c in cols:
            if c in df.columns:
                m |= ~_guids(df, c).isin(known).values
        n = int(m.sum())
        ctx.log(f"{t}: {len(df):,} rows, {n:,} reference GlobalIDs not found in the sample features")
        if n:
            ctx.finding(
                "warning",
                f"{n:,} of {len(df):,} {t} rows reference GlobalIDs not present in the data",
                layer=t,
                count=n,
                sample_ids=list(df.index[m][: config.MAX_SAMPLE_IDS]),
            )
            sc -= min(30, 100 * n / len(df))
    return sc


@check("quality", "UN system table sanity", order=80)
def un_tables(ctx) -> None:
    """Associations, subnetworks, rules, dirty areas and error tables of a built UN (or asset-package C_/B_ tables)."""
    names = {lyr.name.upper(): lyr for lyr in ctx.dataset.layers()}
    gids = ctx.state.get("all_globalids", set())
    un = sorted(n for n in names if re.match(r"UN_\d+_ASSOCIATIONS$", n))
    rows: list[dict] = []
    if un:
        sc = _built_network(ctx, names, un[0].rsplit("_", 1)[0], gids, rows)
    elif "C_ASSOCIATIONS" in names or "C_SUBNETWORKCONTROLLERS" in names:
        sc = _asset_package(ctx, names, gids, rows)
    else:
        ctx.log("No utility network system tables (not a UN or asset package)")
        return
    ctx.tables["un_tables"] = rows
    ctx.score(sc, weight=1, label="UN system tables")
