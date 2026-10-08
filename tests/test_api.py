"""HTTP API: datasets, run, SSE stream (resume + clean end), stop, reports, path restriction."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app import server


@pytest.fixture(scope="module")
def client(workspace):
    with TestClient(server.app) as c:
        yield c


def _sse(text: str) -> list[dict]:
    out = []
    for block in text.strip().split("\n\n"):
        ev = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                continue
            k, _, v = line.partition(": ")
            ev[k] = v
        if ev:
            out.append(ev)
    return out


def _wait(client, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get(f"/api/jobs/{job_id}").json()["status"]
        if st in ("done", "failed", "cancelled"):
            return st
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_index_has_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_config_does_not_leak_absolute_paths(client, workspace):
    cfg = client.get("/api/config").json()
    assert cfg["data_root"] == "data" and str(workspace) not in json.dumps(cfg)
    assert set(cfg["checks"]) == {"discover", "metadata", "lineage", "schema", "quality", "report"}


def test_datasets_listing(client, workspace):
    d = client.get("/api/datasets?refresh=true").json()
    paths = sorted(x["path"].replace("\\", "/") for x in d["datasets"])
    assert paths == ["data/network/copy/net2.gpkg", "data/network/net.gpkg"]
    assert str(workspace) not in json.dumps(d)
    assert d["folders"][0]["rel"] == "(all sample data)"


@pytest.mark.parametrize("path", ["/etc", "../../", "data/../../..", "/tmp", "~"])
def test_run_rejects_paths_outside_data_roots(client, path):
    r = client.post("/api/run", json={"path": path})
    assert r.status_code == 403


def test_run_missing_and_empty_paths(client, data_root):
    assert client.post("/api/run", json={"path": "data/nope.gdb"}).status_code == 404
    (data_root / "emptydir").mkdir(exist_ok=True)
    assert client.post("/api/run", json={"path": "data/emptydir"}).status_code == 400
    assert client.post("/api/run", json={"path": ""}).status_code == 422


def test_run_stream_result_and_reports(client):
    r = client.post("/api/run", json={"path": "data/network/net.gpkg", "pace_ms": -5})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    with client.stream("GET", f"/api/jobs/{job_id}/events") as s:
        assert s.headers["content-type"].startswith("text/event-stream")
        body = "".join(s.iter_text())
    evs = _sse(body)
    kinds = [e["event"] for e in evs]
    assert kinds[0] == "job_start" and kinds[-1] == "eof" and kinds[-2] == "job_end"  # stream ends cleanly
    ids = [int(e["id"]) for e in evs if "id" in e]
    assert ids == list(range(len(ids)))

    # reconnect with Last-Event-ID: only later events are replayed
    with client.stream("GET", f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": str(ids[-3])}) as s:
        again = _sse("".join(s.iter_text()))
    assert [int(e["id"]) for e in again if "id" in e] == ids[-2:]

    res = client.get(f"/api/jobs/{job_id}/result").json()
    assert res["datasets"][0]["status"] == "done"
    html = client.get(f"/api/jobs/{job_id}/report.html")
    assert html.status_code == 200 and "validation report" in html.text
    assert "default-src 'none'" in html.headers["content-security-policy"]
    csv = client.get(f"/api/jobs/{job_id}/findings.csv")
    assert csv.status_code == 200 and csv.text.startswith("dataset,severity")


def test_stop_button(client):
    r = client.post("/api/run", json={"path": "data/network", "pace_ms": 400})
    job_id = r.json()["job_id"]
    time.sleep(0.5)
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["ok"]
    assert _wait(client, job_id) == "cancelled"
    with client.stream("GET", f"/api/jobs/{job_id}/events") as s:
        evs = _sse("".join(s.iter_text()))
    end = json.loads(next(e for e in evs if e["event"] == "job_end")["data"])
    assert end["status"] == "cancelled" and evs[-1]["event"] == "eof"


def test_unknown_and_malformed_job_ids(client):
    assert client.get("/api/jobs/doesnotexist").status_code == 404
    assert client.get("/api/jobs/..%2F..%2Fetc/result").status_code == 404
    assert client.post("/api/jobs/zzz/cancel").status_code == 404


def test_result_before_finish_is_409(client):
    r = client.post("/api/run", json={"path": "data/network/net.gpkg", "pace_ms": 300})
    job_id = r.json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}/result").status_code == 409
    assert client.get(f"/api/jobs/{job_id}/report.html").status_code == 409
    client.post(f"/api/jobs/{job_id}/cancel")
    _wait(client, job_id)


def test_allow_any_path_flag(client, monkeypatch, gpkg_path, tmp_path):
    import shutil

    from app import config

    outside = tmp_path / "elsewhere.gpkg"
    shutil.copy(gpkg_path, outside)
    assert client.post("/api/run", json={"path": str(outside)}).status_code == 403
    monkeypatch.setattr(config, "ALLOW_ANY_PATH", True)
    r = client.post("/api/run", json={"path": str(outside)})
    assert r.status_code == 200
    _wait(client, r.json()["job_id"])
