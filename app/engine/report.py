"""Report export: self-contained HTML, CSV findings and JSON result.

Every value taken from the data (layer names, messages, metadata text) is HTML-escaped,
and CSV cells that a spreadsheet would treat as a formula are neutralised.
"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from .. import config
from .models import STAGE_NAMES

E = html.escape


def _score_color(v):
    if v is None:
        return "#8a94a6"
    return (
        "#1f9d55"
        if v >= 90
        else "#5fa82e"
        if v >= 80
        else "#d69e2e"
        if v >= 70
        else "#dd6b20"
        if v >= 60
        else "#c53030"
    )


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(v) -> str:
    """Text for a CSV cell; a leading formula character gets a quote prefix (CSV injection)."""
    s = "" if v is None else str(v)
    if s.startswith(_FORMULA_START) and not _is_number(s):
        return "'" + s
    return s


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def write_csv(result: dict, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "dataset",
                "severity",
                "stage",
                "check",
                "layer",
                "count",
                "message",
                "detail",
                "sample_object_ids",
                "finding_id",
                "rule",
                "classification",
                "confidence",
                "evidence",
                "method",
                "threshold",
                "affected_count",
                "affected_record_ids",
                "recommended_action",
                "disposition",
                "disposition_note",
            ]
        )
        for ds in result["datasets"]:
            for fd in ds.get("findings", []):
                w.writerow(
                    [
                        csv_safe(fd.get("dataset")),
                        fd["severity"],
                        STAGE_NAMES.get(fd["stage"], fd["stage"]),
                        csv_safe(fd["check"]),
                        csv_safe(fd.get("layer", "")),
                        "" if fd.get("count") is None else fd["count"],
                        csv_safe(fd["message"]),
                        csv_safe(fd.get("detail", "")),
                        " ".join(str(i) for i in fd.get("sample_ids", [])),
                        csv_safe(fd.get("finding_id", "")),
                        csv_safe(fd.get("rule", "")),
                        csv_safe(fd.get("classification", "")),
                        "" if fd.get("confidence") is None else fd.get("confidence"),
                        csv_safe(fd.get("evidence", "")),
                        csv_safe(fd.get("method", "")),
                        csv_safe(fd.get("threshold", "")),
                        "" if fd.get("affected_count") is None else fd.get("affected_count"),
                        " ".join(str(i) for i in fd.get("affected_ids") or []),
                        csv_safe(fd.get("recommended_action", "")),
                        csv_safe(fd.get("disposition", "")),
                        csv_safe(fd.get("disposition_note", "")),
                    ]
                )


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _status_note(ds: dict) -> str:
    st = ds.get("status")
    if st == "cancelled":
        return " &middot; <b>stopped early: partial result</b>"
    if st == "failed":
        return " &middot; <b>could not be opened</b>"
    return ""


def _fmt_count(c):
    return "" if c is None else f"{c:,}"


def _table(rows, cols, max_rows=400):
    if not rows:
        return "<p class='muted'>None.</p>"
    head = "".join(f"<th>{E(c)}</th>" for c in cols)
    body = []
    for r in rows[:max_rows]:
        body.append(
            "<tr>" + "".join(f"<td>{E('' if r.get(c) is None else str(r.get(c)))}</td>" for c in cols) + "</tr>"
        )
    more = f"<p class='muted'>{len(rows) - max_rows} more rows in CSV/JSON.</p>" if len(rows) > max_rows else ""
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>{more}"


def write_html(result: dict, path: Path) -> None:
    parts = []
    for ds in result["datasets"]:
        st = ds.get("stages", {})
        cards = "".join(
            f"<div class='card'><div class='lbl'>{E(STAGE_NAMES[k])}</div><div class='val' style='color:{_score_color(v)}'>"
            f"{'n/a' if v is None else f'{v:.0f}'}</div></div>"
            for k, v in st.items()
        )
        c = ds.get("counts", {})
        s = ds.get("summary", {})
        fnd = ds.get("findings", [])
        frows = "".join(
            f"<tr class='{E(f['severity'])}'><td><span class='sev {E(f['severity'])}'>{E(f['severity'])}</span></td>"
            f"<td>{E(STAGE_NAMES.get(f['stage'], f['stage']))}</td><td>{E(f.get('layer') or '')}</td>"
            f"<td>{_fmt_count(f.get('count'))}</td><td>{E(f['message'])}"
            f"{'<div class=det>' + E(f['detail'][:600]) + '</div>' if f.get('detail') and 'Traceback' not in f['detail'] else ''}</td></tr>"
            for f in fnd
        )
        t = ds.get("tables", {})
        ov = "n/a" if ds.get("overall") is None else f"{ds['overall']:.1f}"
        parts.append(f"""
<section>
 <h2>{E(ds["dataset"])} <span class='grade' style='background:{_score_color(ds.get("overall"))}'>{ov} &middot; {E(str(ds.get("grade")))}</span></h2>
 <p class='muted'>{E(str(ds.get("display_path") or ds["path"]))}{_status_note(ds)}<br>{E(str(s.get("kind", "")))} &middot; {_int(s.get("feature_classes"))} feature classes, {_int(s.get("tables"))} tables,
 {_int(s.get("features")):,} features &middot; reference model: {E(str(s.get("reference_model") or "none"))} &middot; {E(str(ds.get("seconds", "?")))} s</p>
 <div class='cards'>{cards}<div class='card'><div class='lbl'>Findings</div><div class='val small'><span class='e'>{_int(c.get("error"))} errors</span><br>
 <span class='w'>{_int(c.get("warning"))} warnings</span><br><span class='i'>{_int(c.get("info"))} info</span></div></div></div>
 <h3>Findings</h3>
 <table class='findings'><thead><tr><th>Severity</th><th>Stage</th><th>Layer</th><th>Count</th><th>Finding</th></tr></thead><tbody>{frows}</tbody></table>
 <h3>Metadata by item</h3>{_table(t.get("metadata_items", []), ["item", "kind", "rows", "standard", "score", "summary", "description", "tags", "credits", "use_limits", "contact", "extent", "lineage"])}
 <h3>Layers</h3>{_table([r for r in t.get("layers", []) if not r.get("system")], ["name", "geometry", "count", "fields", "crs", "vertical", "readable"])}
 {("<h3>Field-mapping coverage</h3>" + _table(t["mapping_coverage"], ["source_class", "target", "fields", "mapped", "coverage", "unmapped"])) if t.get("mapping_coverage") else ""}
 {("<h3>Reference-model conformance</h3>" + _table(t["conformance"], ["class", "counterpart", "match", "missing", "extra", "type_mismatch", "score"])) if t.get("conformance") else ""}
 {("<h3>Line connectivity</h3>" + _table(t["connectivity"], ["layer", "endpoints", "near_miss", "dangles"])) if t.get("connectivity") else ""}
 {("<h3>UN system tables</h3>" + _table(t["un_tables"], ["table", "rows"])) if t.get("un_tables") else ""}
</section>""")
    ovr = "n/a" if result.get("overall") is None else f"{result['overall']:.1f}"
    doc = f"""<!doctype html><html><head><meta charset='utf-8'><title>{E(config.APP_TITLE)} report</title>
<style>
body{{font-family:Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;color:#1d2433;background:#f4f6fa}}
header{{background:#141a26;color:#fff;padding:18px 32px}} header h1{{margin:0;font-size:22px}} header p{{margin:4px 0 0;color:#a9b4c8;font-size:13px}}
main{{padding:20px 32px}} section{{background:#fff;border:1px solid #dde3ee;border-radius:8px;padding:18px 22px;margin-bottom:22px}}
h2{{margin:0 0 6px;font-size:19px}} h3{{margin:22px 0 8px;font-size:15px;color:#33415c}}
.grade{{color:#fff;border-radius:14px;padding:2px 12px;font-size:14px;margin-left:8px}}
.cards{{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}} .card{{border:1px solid #dde3ee;border-radius:8px;padding:10px 14px;min-width:120px}}
.lbl{{font-size:11px;text-transform:uppercase;color:#6b778c;letter-spacing:.04em}} .val{{font-size:28px;font-weight:600}} .val.small{{font-size:13px;line-height:1.5}}
table{{border-collapse:collapse;width:100%;font-size:12.5px}} th,td{{border-bottom:1px solid #e6eaf2;padding:5px 7px;text-align:left;vertical-align:top}}
th{{background:#f0f3f8;font-weight:600}} .muted{{color:#6b778c;font-size:12.5px}} .det{{color:#6b778c;font-size:11.5px;margin-top:2px;word-break:break-word}}
.sev{{border-radius:10px;padding:1px 8px;font-size:11px;color:#fff;text-transform:uppercase}} .sev.error{{background:#c53030}} .sev.warning{{background:#d69e2e}} .sev.info{{background:#3182ce}}
.e{{color:#c53030}} .w{{color:#b7791f}} .i{{color:#2b6cb0}}
footer{{font-size:11.5px;color:#6b778c;padding:10px 32px 30px}}
</style></head><body>
<header><h1>{E(config.APP_TITLE)} &mdash; validation report</h1><p>Generated {E(result["generated"])} &middot; job {E(result["job_id"])} &middot;
overall score {ovr} ({E(str(result.get("grade")))}) &middot; {len(result["datasets"])} dataset(s)</p></header>
<main>{"".join(parts)}</main><footer>{config.FOOTER_HTML}</footer></body></html>"""
    path.write_text(doc, encoding="utf-8")


def _write_asset_mapping(result: dict, path: Path) -> bool:
    rows = []
    for ds in result["datasets"]:
        for rec in (ds.get("tables") or {}).get("asset_mapping") or []:
            rows.append((ds.get("dataset"), rec))
    if not rows:
        return False
    cols = [
        "dataset",
        "class_name",
        "asset_group",
        "asset_type",
        "features",
        "match",
        "confidence",
        "baseline_class",
        "baseline_group",
        "baseline_type",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for dataset, rec in rows:
            w.writerow(
                [
                    csv_safe(dataset),
                    csv_safe(rec.get("class_name")),
                    csv_safe(rec.get("asset_group")),
                    csv_safe(rec.get("asset_type")),
                    rec.get("features", ""),
                    rec.get("match", ""),
                    rec.get("confidence", ""),
                    csv_safe(rec.get("baseline_class")),
                    csv_safe(rec.get("baseline_group")),
                    csv_safe(rec.get("baseline_type")),
                ]
            )
    return True


def write_reports(result: dict) -> dict:
    """Write the report files for a run. Returns their paths."""
    if not str(result.get("job_id", "")).isalnum():
        raise ValueError("invalid job id")
    from .manifest import write_manifest
    from .questions import write_questions_csv, write_questions_xlsx

    out = Path(config.REPORT_DIR) / result["job_id"]
    out.mkdir(parents=True, exist_ok=True)
    write_csv(result, out / "findings.csv")
    findings = [f for ds in result["datasets"] for f in ds.get("findings", [])]
    (out / "findings.json").write_text(json.dumps(findings, indent=1, default=str), encoding="utf-8")
    write_html(result, out / "report.html")
    # The on-disk JSON keeps paths so a replay can find the inputs. It is not committed.
    slim = {**result, "datasets": [{k: v for k, v in d.items() if k != "map"} for d in result["datasets"]]}
    (out / "result.json").write_text(json.dumps(slim, indent=1, default=str), encoding="utf-8")
    questions = result.get("questions") or []
    write_questions_csv(questions, out / "questions.csv")
    write_questions_xlsx(questions, out / "questions.xlsx")
    write_manifest(result, out / "manifest.json")
    files = {
        "html": str(out / "report.html"),
        "csv": str(out / "findings.csv"),
        "json": str(out / "result.json"),
        "findings_json": str(out / "findings.json"),
        "questions_csv": str(out / "questions.csv"),
        "questions_xlsx": str(out / "questions.xlsx"),
        "manifest": str(out / "manifest.json"),
    }
    if _write_asset_mapping(result, out / "asset_mapping.csv"):
        files["asset_mapping"] = str(out / "asset_mapping.csv")
    return files
