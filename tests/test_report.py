"""Report export: files written, HTML escaped, CSV formula injection neutralised."""

import csv

import pytest

from app.engine.report import csv_safe, write_csv, write_html, write_reports

EVIL = '<img src=x onerror="alert(1)"><script>alert(2)</script>'


def _result(job_id="abc123"):
    finding = {
        "severity": "error",
        "stage": "quality",
        "check": EVIL,
        "layer": EVIL,
        "count": 3,
        "message": "=cmd|' /C calc'!A0 " + EVIL,
        "detail": "@SUM(1+1)",
        "sample_ids": [1, 2],
        "dataset": EVIL,
    }
    return {
        "job_id": job_id,
        "generated": "2026-01-01 00:00:00",
        "overall": 50.0,
        "grade": "F",
        "datasets": [
            {
                "dataset": EVIL,
                "path": "/somewhere/" + EVIL,
                "display_path": "data/" + EVIL,
                "kind": "gpkg",
                "status": "cancelled",
                "summary": {"kind": EVIL, "reference_model": EVIL, "features": 10},
                "stages": {"metadata": 50.0, "lineage": None, "schema": 90.0, "quality": 10.0},
                "overall": 50.0,
                "grade": "F",
                "counts": {"error": 1, "warning": 0, "info": 0},
                "findings": [finding],
                "tables": {"layers": [{"name": EVIL, "geometry": "Point", "count": 1, "system": False}]},
                "map": {"context": [], "flagged": []},
                "seconds": 1.0,
            }
        ],
    }


def test_html_report_escapes_everything(tmp_path):
    p = tmp_path / "r.html"
    write_html(_result(), p)
    html = p.read_text(encoding="utf-8")
    assert "<img" not in html and "<script>alert" not in html
    assert "&lt;img src=x" in html
    assert "stopped early" in html
    assert "/somewhere/" not in html  # the display path is shown, not the absolute path


def test_csv_neutralises_formulas(tmp_path):
    p = tmp_path / "f.csv"
    write_csv(_result(), p)
    rows = list(csv.reader(p.open(encoding="utf-8")))
    assert rows[0][0] == "dataset"
    msg, detail = rows[1][6], rows[1][7]
    assert msg.startswith("'=") and detail.startswith("'@")


@pytest.mark.parametrize(
    "value,expected",
    [("=1+1", "'=1+1"), ("+x", "'+x"), ("-5", "-5"), ("-x", "'-x"), ("@a", "'@a"), ("ok", "ok"), (None, "")],
)
def test_csv_safe(value, expected):
    assert csv_safe(value) == expected


def test_write_reports_creates_three_files(workspace):
    files = write_reports(_result("job42"))
    for k in ("html", "csv", "json"):
        assert files[k].endswith({"html": "report.html", "csv": "findings.csv", "json": "result.json"}[k])
    import json
    from pathlib import Path

    data = json.loads(Path(files["json"]).read_text(encoding="utf-8"))
    assert "map" not in data["datasets"][0]


def test_write_reports_rejects_unsafe_job_id():
    with pytest.raises(ValueError):
        write_reports(_result("../../evil"))
