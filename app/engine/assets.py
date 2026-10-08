"""Compare a dataset's asset groups and asset types with the UN Foundation baseline.

The baseline is a small table derived from the public Esri Utility Network Foundation
asset packages (Apache-2.0). See DATA_SOURCES.md. This module only reads; it does not
change the dataset and it does not add a score.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ..log import get_logger
from .targets import domain_of

log = get_logger("assets")

BASELINE_PATH = Path(__file__).resolve().parents[1] / "data" / "foundation_assets.json"
FUZZY_MIN = 0.84
_WORD = re.compile(r"[^a-z0-9]+")


def _norm(value: object) -> str:
    """Fold a name for comparison. Whole numbers match whether they arrive as 1, 1.0 or '1'."""
    text = str(value or "").strip()
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None and number.is_integer():
        return str(int(number))
    return _WORD.sub("", text.lower())


@lru_cache(maxsize=1)
def load_baseline() -> list[dict]:
    if not BASELINE_PATH.is_file():
        return []
    try:
        doc = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("asset baseline unreadable: %s", e)
        return []
    rows = doc.get("rows") if isinstance(doc, dict) else None
    return list(rows or [])


def baseline_for(domain: str | None) -> list[dict]:
    rows = load_baseline()
    if not domain:
        return rows
    return [r for r in rows if r.get("domain") == domain]


def _domain(ctx) -> str | None:
    target = ctx.state.get("target") or {}
    if target.get("domain"):
        return target["domain"]
    summary = ctx.state.get("summary") or {}
    for text in (summary.get("reference_model"), ctx.dataset.name):
        dom = domain_of(str(text or ""))
        if dom:
            return dom
    names = {lyr.name.lower() for lyr in ctx.dataset.user_layers()}
    found = {
        r.get("domain") for r in load_baseline() if str(r.get("class_name") or "").lower() in names and r.get("domain")
    }
    if len(found) == 1:
        return next(iter(found))
    return None


def _pairs(ctx, layer) -> list[dict]:
    """Distinct (asset group, asset type) values, with names when the catalog has them."""
    names = {n.lower(): n for n, _ in layer.fields}
    if "assetgroup" not in names or "assettype" not in names:
        return []
    cd = ctx.dataset.class_def(layer.name)
    groups = {}
    type_domains: dict[object, dict] = {}
    if cd and (cd.subtype_field or "").lower() == "assetgroup":
        for code, st in cd.subtypes.items():
            groups[code] = st.get("name") or str(code)
            dn = (st.get("domains") or {}).get("assettype")
            dom = (ctx.dataset.catalog().get("domains") or {}).get(dn) if dn else None
            if dom is not None and getattr(dom, "codes", None):
                type_domains[code] = {str(k): v for k, v in dom.codes.items()}
                type_domains[code].update(
                    {
                        str(int(k)) if isinstance(k, float) and k.is_integer() else str(k): v
                        for k, v in dom.codes.items()
                    }
                )
    try:
        df = ctx.dataset.read_columns(layer.name, [names["assetgroup"], names["assettype"]])
    except Exception as e:  # noqa: BLE001
        ctx.log(f"Could not read asset types on {layer.name}: {e}", "warn")
        return []
    gcol, tcol = names["assetgroup"], names["assettype"]
    out = []
    grouped = df.groupby([gcol, tcol], dropna=False).size().reset_index(name="n")
    for rec in grouped.itertuples(index=False):
        gcode, tcode, n = rec[0], rec[1], int(rec[2])
        gname = groups.get(gcode)
        if gname is None and pd.notna(gcode):
            try:
                gname = groups.get(int(gcode))
            except (TypeError, ValueError):
                gname = None
        tname = None
        lookup = type_domains.get(gcode) or {}
        if not lookup and pd.notna(gcode):
            try:
                lookup = type_domains.get(int(gcode)) or {}
            except (TypeError, ValueError):
                lookup = {}
        if lookup and pd.notna(tcode):
            tname = lookup.get(str(tcode))
            if tname is None:
                try:
                    tname = lookup.get(str(int(tcode)))
                except (TypeError, ValueError):
                    tname = None
        out.append(
            {
                "class_name": layer.name,
                "asset_group_code": None if pd.isna(gcode) else gcode,
                "asset_group": gname or ("" if pd.isna(gcode) else str(gcode)),
                "asset_type_code": None if pd.isna(tcode) else tcode,
                "asset_type": tname or ("" if pd.isna(tcode) else str(tcode)),
                "features": n,
            }
        )
    return out


def _match(pair: dict, candidates: list[dict]) -> dict | None:
    """Exact name match, then a fuzzy name match inside the same class."""
    cls = _norm(pair["class_name"])
    group = _norm(pair["asset_group"])
    typ = _norm(pair["asset_type"])
    same_class = [c for c in candidates if _norm(c.get("class_name")) == cls] or candidates
    for c in same_class:
        if _norm(c.get("asset_group")) == group and _norm(c.get("asset_type")) == typ and typ:
            return {**c, "method": "exact", "confidence": 1.0}
    if not typ and not group:
        return None
    best = None
    best_score = 0.0
    for c in same_class:
        cg, ct = _norm(c.get("asset_group")), _norm(c.get("asset_type"))
        if group and cg and group != cg and SequenceMatcher(None, group, cg).ratio() < 0.8:
            continue
        score = SequenceMatcher(None, typ, ct).ratio() if typ and ct else 0.0
        if group and cg:
            score = (
                (score + SequenceMatcher(None, group, cg).ratio()) / 2
                if typ
                else SequenceMatcher(None, group, cg).ratio()
            )
        if score > best_score:
            best_score = score
            best = c
    if best is not None and best_score >= FUZZY_MIN:
        return {**best, "method": "fuzzy", "confidence": round(best_score, 3)}
    return None


def map_assets(ctx) -> None:
    """Propose mappings and flag unmapped dataset values and missing baseline groups."""
    ctx.current_check = ctx.current_check or "Asset type mapping"
    domain = _domain(ctx)
    rows = baseline_for(domain)
    if not rows:
        ctx.log("No Utility Network Foundation asset-type baseline for this dataset")
        ctx.finding(
            "info",
            "No Foundation asset-type baseline matched this dataset, so asset types were not compared",
            rule="no asset baseline",
            classification="observation",
            evidence=domain or "domain not recognised",
        )
        ctx.tables["asset_mapping"] = []
        return
    classes = {str(r.get("class_name") or "").lower() for r in rows}
    proposals = []
    unmapped_by_layer: dict[str, list[str]] = {}
    seen_groups: dict[str, set[str]] = {}
    layers = [lyr for lyr in ctx.dataset.user_layers() if lyr.readable and lyr.count]
    compared = 0
    for lyr in layers:
        if lyr.name.lower() not in classes and "assetgroup" not in {n.lower() for n, _ in lyr.fields}:
            continue
        pairs = _pairs(ctx, lyr)
        if not pairs:
            continue
        compared += 1
        seen_groups.setdefault(lyr.name.lower(), set())
        for pair in pairs:
            hit = _match(pair, rows)
            label = f"{pair['asset_group'] or '?'} / {pair['asset_type'] or '?'}"
            if pair["asset_group"]:
                seen_groups[lyr.name.lower()].add(_norm(pair["asset_group"]))
            if hit and hit["method"] == "exact":
                proposals.append(
                    {
                        **pair,
                        "match": "exact",
                        "confidence": 1.0,
                        "baseline_group": hit.get("asset_group"),
                        "baseline_type": hit.get("asset_type"),
                        "baseline_class": hit.get("class_name"),
                    }
                )
            elif hit:
                proposals.append(
                    {
                        **pair,
                        "match": "fuzzy",
                        "confidence": hit["confidence"],
                        "baseline_group": hit.get("asset_group"),
                        "baseline_type": hit.get("asset_type"),
                        "baseline_class": hit.get("class_name"),
                    }
                )
                ctx.finding(
                    ctx.sev("fuzzy_asset_type", "info"),
                    f"{lyr.name} value {label} is close to {hit.get('asset_group')} / {hit.get('asset_type')} ({hit['confidence']:.0%})",
                    layer=lyr.name,
                    count=pair["features"],
                    rule=f"fuzzy {label}",
                    classification="conformance",
                    confidence=hit["confidence"],
                    evidence=f"{pair['features']} features",
                    method="name similarity against the Utility Network Foundation baseline",
                    threshold=str(FUZZY_MIN),
                    recommended_action="Confirm the proposed asset group and asset type, then use that name in the data.",
                )
            else:
                proposals.append(
                    {
                        **pair,
                        "match": "unmapped",
                        "confidence": 0.0,
                        "baseline_group": "",
                        "baseline_type": "",
                        "baseline_class": "",
                    }
                )
                unmapped_by_layer.setdefault(lyr.name, []).append(f"{label} ({pair['features']})")
    for layer, labels in unmapped_by_layer.items():
        shown = "; ".join(labels[:12])
        ctx.finding(
            ctx.sev("unmapped_asset_type", "warning"),
            f"{len(labels)} asset group/type value(s) on {layer} are not in the Foundation baseline",
            layer=layer,
            count=len(labels),
            rule="unmapped asset type",
            classification="conformance",
            evidence=shown,
            method="exact and fuzzy name match against the Utility Network Foundation baseline",
            recommended_action="Map each value to a Foundation asset group and asset type, or record why it is local-only.",
        )
    # missing groups: baseline groups for a class that appears in the data but lacks that group
    present_classes = {lyr.name.lower(): lyr.name for lyr in layers}
    missing_n = 0
    for cls_key, cls_name in present_classes.items():
        if cls_key not in seen_groups:
            continue
        expected = {}
        for r in rows:
            if str(r.get("class_name") or "").lower() == cls_key and r.get("asset_group"):
                expected.setdefault(_norm(r["asset_group"]), r["asset_group"])
        absent = [name for key, name in sorted(expected.items()) if key not in seen_groups.get(cls_key, set())]
        # Unknown / empty groups are not worth a question each; skip the catch-all name
        absent = [n for n in absent if _norm(n) not in ("unknown", "")]
        if not absent:
            continue
        missing_n += len(absent)
        ctx.finding(
            ctx.sev("missing_asset_group", "info"),
            f"{cls_name} does not use {len(absent)} Foundation asset group(s): {', '.join(absent[:12])}",
            layer=cls_name,
            count=len(absent),
            rule="missing asset group",
            classification="conformance",
            evidence=", ".join(absent[:20]),
            method="groups present in the Utility Network Foundation baseline for this class",
            recommended_action="Confirm these groups are intentionally absent from this dataset.",
        )
    ctx.tables["asset_mapping"] = proposals
    exact = sum(1 for r in proposals if r["match"] == "exact")
    ctx.log(
        f"Asset types ({domain}): {len(proposals)} distinct values, {exact} exact, "
        f"{sum(1 for r in proposals if r['match'] == 'fuzzy')} proposed, "
        f"{sum(1 for r in proposals if r['match'] == 'unmapped')} unmapped; {missing_n} missing groups"
    )
