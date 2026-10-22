"""Seeded-error regression on the bundled public test data (testdata/)."""

from pathlib import Path

import pytest

from app import config
from app.engine.jobs import Job
from app.engine.runner import run_job

TD = Path(__file__).resolve().parents[1] / "testdata"
pytestmark = pytest.mark.skipif(not (TD / "seeded").is_dir(), reason="testdata not present")


def _run(rel, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOTS", [TD])
    monkeypatch.setattr(config, "DATA_ROOT", TD)
    job = Job("td", [str(TD / rel)], {})
    run_job(job)
    assert job.status == "done"
    return job.result["datasets"][0]["findings"]


def _has(findings, sev, check, layer=None, text=""):
    return any(
        f["severity"] == sev and f["check"] == check and (layer is None or f["layer"] == layer) and text in f["message"]
        for f in findings
    )


def test_seeded_gpkg_globalid_crs(monkeypatch):
    f = _run("seeded/Transmission_seeded.gpkg", monkeypatch)
    assert _has(f, "critical", "Duplicate GlobalIDs & asset IDs", "Busbar", "no GlobalID field")
    assert _has(f, "critical", "Duplicate GlobalIDs & asset IDs", "CircuitBreaker", "share a GlobalID")
    assert _has(f, "critical", "Required fields populated", "CircuitBreaker", "GlobalID")
    assert _has(f, "error", "Layer coordinate systems", "HVOverheadConductor")
    hv = [x for x in f if x["check"].startswith("Line connectivity") and x["layer"] == "HVOverheadConductor"]
    assert hv and max(x["count"] for x in hv) < 100  # mislabelled layer no longer explodes into false dangles


def test_seeded_gdb_orphan_junction(monkeypatch):
    clean = _run("clean/Transmission_Naperville_SourceData.gdb", monkeypatch)
    seeded = _run("seeded/Transmission_seeded.gdb", monkeypatch)

    def n(fs):
        return sum(x["count"] for x in fs if x["check"] == "Orphan junctions" and x["layer"] == "Disconnect")

    assert n(seeded) == n(clean) + 1
    assert not [x for x in clean if x["severity"] == "critical"]
