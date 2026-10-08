"""Stage 2 - Metadata validation (ArcGIS, FGDC CSDGM and ISO 19139 metadata XML)."""

from __future__ import annotations

import html
import re

from ... import config
from ..catalog import parse_xml, strip_ns
from ..dataset import is_system_name
from ..registry import check

PLACEHOLDER = re.compile(
    r"REQUIRED:|There is no description|No description available|^\s*(none|n/?a|tbd|todo)\s*$", re.I
)

# element -> list of local-name paths (relative, '//' search) for ArcGIS, FGDC and ISO
PATHS = {
    "summary": ["idPurp", "purpose"],
    "description": ["idAbs", "abstract"],
    "tags": ["searchKeys/keyword", "themeKeys/keyword", "placeKeys/keyword", "themekey", "placekey", "keyword"],
    "credits": ["idCredit", "datacred", "credit"],
    "use_limits": ["useLimit", "othConsts", "useconst", "accconst", "useLimitation", "otherConstraints"],
    "contact": ["idPoC", "mdContact", "ptcontac", "pointOfContact", "contact"],
    "extent": ["GeoBndBox", "bounding", "EX_GeographicBoundingBox"],
    "lineage": ["dataLineage", "lineage", "LI_Lineage"],
}
LABELS = {
    "summary": "Summary (purpose)",
    "description": "Description (abstract)",
    "tags": "Tags / keywords",
    "credits": "Credits",
    "use_limits": "Use limitations",
    "contact": "Point of contact",
    "extent": "Geographic extent",
    "lineage": "Lineage",
}


def _clean(t: str) -> str:
    t = html.unescape(t or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _parse(doc: str):
    root = parse_xml(doc)
    return strip_ns(root) if root is not None else None


def _is_gp_history(el) -> bool:
    """``Esri/DataProperties/lineage``: the geoprocessing history ArcGIS writes automatically."""
    parent = el.getparent()
    return parent is not None and parent.tag == "DataProperties"


def evaluate_document(doc: str | None) -> dict:
    """Return {element: status} where status is 'present' | 'placeholder' | 'auto' | 'missing'."""
    res = {k: "missing" for k in PATHS}
    res["_standard"] = None
    if not doc:
        return res
    root = _parse(doc)
    if root is None:
        return res
    tags = {el.tag for el in root.iter() if isinstance(el.tag, str)}
    res["_standard"] = (
        "ISO 19139"
        if "MD_Metadata" in tags
        else "FGDC CSDGM"
        if "idinfo" in tags
        else "ArcGIS"
        if ("Esri" in tags or "dataIdInfo" in tags)
        else "Unknown"
    )
    for key, paths in PATHS.items():
        status = "missing"
        for p in paths:
            for el in root.iterfind(".//" + p):
                if key == "lineage" and _is_gp_history(el):
                    continue  # geoprocessing history is automatic lineage, scored below
                if key == "extent":
                    nums = [
                        c.text for c in el.iter() if isinstance(c.tag, str) and c.text and re.search(r"-?\d", c.text)
                    ]
                    if len(nums) >= 4:
                        status = "present"
                elif key == "contact":
                    txt = _clean(" ".join(el.itertext()))
                    if len(txt) >= 3 and not PLACEHOLDER.search(txt):
                        status = "present"
                else:
                    txt = _clean(" ".join(el.itertext()))
                    if not txt:
                        continue
                    status = "placeholder" if PLACEHOLDER.search(txt) and status != "present" else "present"
                if status == "present":
                    break
            if status == "present":
                break
        if key == "lineage" and status == "missing":
            # ArcGIS geoprocessing history is automatic lineage: partial credit
            if root.find(".//Esri/DataProperties/lineage/Process") is not None:
                status = "auto"
        res[key] = status
    return res


def score_document(ev: dict) -> float:
    w = config.METADATA_ELEMENTS
    tot = sum(w.values())
    got = sum(w[k] * (1.0 if ev.get(k) == "present" else 0.5 if ev.get(k) == "auto" else 0.0) for k in w)
    return round(100.0 * got / tot, 1)


@check("metadata", "Evaluate item metadata", order=10)
def item_metadata(ctx):
    """Score metadata completeness for every feature class and table."""
    cat = ctx.dataset.catalog()
    counts = {lyr.name.lower(): lyr.count for lyr in ctx.dataset.layers()}
    items = [cd for cd in cat["classes"].values() if not is_system_name(cd.name)]
    ctx.log(
        f"Evaluating metadata on {len(items)} items against 8 elements "
        f"(summary, description, tags, credits, use limits, contact, extent, lineage)"
    )
    rows = []
    for i, cd in enumerate(sorted(items, key=lambda c: (c.kind != "Feature Class", c.name.lower()))):
        ev = evaluate_document(cd.documentation)
        sc = score_document(ev) if cd.documentation else 0.0
        rows.append(
            {
                "item": cd.name,
                "kind": cd.kind,
                "rows": counts.get(cd.name.lower(), 0),
                "has_xml": bool(cd.documentation),
                "standard": ev.get("_standard"),
                "score": sc,
                **{k: ev[k] for k in PATHS},
            }
        )
        ctx.progress((i + 1) / max(1, len(items)), cd.name)
        if i < 60:
            missing = [LABELS[k] for k in PATHS if ev[k] != "present"]
            ctx.log(
                f"  {cd.name}: {sc:.0f}/100"
                + (
                    f" - missing {', '.join(missing[:4])}{'...' if len(missing) > 4 else ''}"
                    if missing
                    else " - complete"
                )
            )
    ctx.tables["metadata_items"] = rows
    if not rows:
        ctx.finding("info", "No feature classes or tables to evaluate")
        return

    no_xml_fc = [r["item"] for r in rows if not r["has_xml"] and r["kind"] == "Feature Class"]
    no_xml_tb = [r["item"] for r in rows if not r["has_xml"] and r["kind"] != "Feature Class"]
    if no_xml_fc:
        ctx.finding(
            "error",
            f"No metadata document on {len(no_xml_fc)} of {sum(r['kind'] == 'Feature Class' for r in rows)} feature classes",
            count=len(no_xml_fc),
            detail=", ".join(no_xml_fc),
        )
    if no_xml_tb:
        ctx.finding(
            "warning",
            f"No metadata document on {len(no_xml_tb)} tables",
            count=len(no_xml_tb),
            detail=", ".join(no_xml_tb),
        )
    with_xml = [r for r in rows if r["has_xml"]]
    for k in PATHS:
        miss = [r["item"] for r in with_xml if r[k] in ("missing", "placeholder")]
        ph = [r["item"] for r in with_xml if r[k] == "placeholder"]
        if miss:
            sev = "warning" if k in ("description", "summary", "tags", "use_limits", "contact") else "info"
            ctx.finding(
                sev,
                f"{LABELS[k]} missing on {len(miss)} of {len(with_xml)} documented items",
                count=len(miss),
                detail=", ".join(miss[:80]),
            )
        if ph:
            ctx.finding(
                "info",
                f"{LABELS[k]} contains template/placeholder text on {len(ph)} items",
                count=len(ph),
                detail=", ".join(ph[:80]),
            )
    auto_only = [r["item"] for r in with_xml if r["score"] <= 10]
    if auto_only:
        ctx.finding(
            "warning",
            f"{len(auto_only)} items carry only ArcGIS-generated metadata (no authored content)",
            count=len(auto_only),
            detail=", ".join(auto_only[:80]),
        )
    tot_w = sum(2 if r["kind"] == "Feature Class" else 1 for r in rows)
    avg = sum(r["score"] * (2 if r["kind"] == "Feature Class" else 1) for r in rows) / tot_w
    ctx.log(f"Average item metadata score: {avg:.1f}/100")
    ctx.score(avg, weight=4, label="Item metadata completeness")


@check("metadata", "Evaluate dataset-level metadata", order=20)
def workspace_metadata(ctx):
    """Check the geodatabase/GeoPackage-level metadata document."""
    doc = ctx.dataset.catalog().get("workspace_doc")
    if not doc:
        ctx.log("No dataset-level metadata document found", "warn")
        ctx.finding("warning", "Dataset-level (workspace) metadata is missing")
        ctx.score(0, weight=1, label="Dataset-level metadata")
        return
    ev = evaluate_document(doc)
    sc = score_document(ev)
    ctx.log(f"Dataset-level metadata ({ev.get('_standard')}) scores {sc:.0f}/100")
    missing = [LABELS[k] for k in PATHS if ev[k] != "present"]
    if missing:
        ctx.finding("info", f"Dataset-level metadata lacks: {', '.join(missing)}", count=len(missing))
    ctx.score(sc, weight=1, label="Dataset-level metadata")
