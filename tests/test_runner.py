"""Pipeline runner: isolation of failing checks and datasets, cancellation, job queue."""

import threading
import time

import pytest

from app.engine import registry
from app.engine.jobs import Job, JobManager
from app.engine.registry import check, load_checks
from app.engine.runner import run_job


def _kinds(job):
    return [e["kind"] for e in job.events]


def test_full_run_on_synthetic_gpkg(gpkg_path):
    job = Job("full1", [str(gpkg_path)], {})
    run_job(job)
    assert job.status == "done", job.events[-1]
    res = job.result["datasets"][0]
    assert res["status"] == "done" and res["overall"] is not None and res["grade"] in "ABCDF"
    assert set(res["stages"]) == {"metadata", "lineage", "schema", "quality"}
    assert res["display_path"].replace("\\", "/") == "data/network/net.gpkg"
    assert res["counts"]["error"] > 0
    assert res["map"]["context"] and res["map"]["flagged"]
    assert all(r.get("path") is None for r in res["reference_ranking"])
    k = _kinds(job)
    assert k[0] == "job_start" and k[-1] == "job_end" and k.count("stage_end") == 6
    assert not [e for e in job.events if e["kind"] == "check_end" and e["status"] == "error"]


def test_failing_check_does_not_kill_the_run(gpkg_path, registry_snapshot):
    load_checks()

    @check("quality", "Test: always fails", order=1)
    def boom(ctx):
        raise RuntimeError("kaboom")

    @check("quality", "Test: catches everything", order=2)
    def swallow(ctx):
        try:
            ctx.progress(0.5)
        except Exception:  # a broad except in a check must not swallow Stop (JobCancelled)
            pass

    job = Job("fail1", [str(gpkg_path)], {})
    run_job(job)
    assert job.status == "done"
    res = job.result["datasets"][0]
    f = [x for x in res["findings"] if x["check"] == "Test: always fails"]
    assert f and f[0]["severity"] == "warning" and "kaboom" in f[0]["message"]
    ends = [e for e in job.events if e["kind"] == "check_end" and e["check"] == "Test: always fails"]
    assert ends[0]["status"] == "error"
    assert res["stages"]["quality"] is not None  # the other quality checks still ran


def test_unreadable_dataset_becomes_failed_result(tmp_path, gpkg_path):
    bad = tmp_path / "broken.gpkg"
    bad.write_bytes(b"\x00" * 100)
    job = Job("bad1", [str(bad), str(gpkg_path)], {})
    run_job(job)
    assert job.status == "done"
    first, second = job.result["datasets"]
    assert first["status"] == "failed" and first["grade"] == "n/a" and first["findings"][0]["severity"] == "error"
    assert second["status"] == "done" and second["overall"] is not None
    assert job.result["overall"] == second["overall"]  # the failed dataset does not drag the mean


def test_stop_ends_run_with_partial_result(gpkg_path):
    job = Job("stop1", [str(gpkg_path), str(gpkg_path)], {"pace_ms": 300})
    t = threading.Thread(target=run_job, args=(job,))
    t.start()
    deadline = time.time() + 30
    while time.time() < deadline and not any(e["kind"] == "stage_end" for e in job.events):
        time.sleep(0.05)
    job.request_cancel()
    t.join(30)
    assert not t.is_alive()
    assert job.status == "cancelled"
    assert job.events[-1]["kind"] == "job_end" and job.events[-1]["status"] == "cancelled"
    assert len(job.result["datasets"]) == 1 and job.result["datasets"][0]["status"] == "cancelled"
    assert job.result["files"]  # a partial report is still written


def test_legacy_cancel_flag_still_works():
    job = Job("x", [], {})
    job.cancel = True
    assert job.cancelled and job.cancel


def test_job_manager_queues_beyond_limit(gpkg_path):
    mgr = JobManager(max_concurrent=1, max_kept=5)
    a = mgr.submit([str(gpkg_path)], {"pace_ms": 50})
    b = mgr.submit([str(gpkg_path)], {})
    assert b.events and b.events[0]["kind"] == "job_queued" and b.events[0]["position"] == 1
    mgr.cancel(a.id)
    deadline = time.time() + 120
    while time.time() < deadline and not (a.is_finished and b.is_finished):
        time.sleep(0.1)
    assert a.status == "cancelled" and b.status == "done"
    assert b.events[0]["kind"] == "job_queued" and "job_start" in _kinds(b)


def test_cancel_while_queued(gpkg_path):
    mgr = JobManager(max_concurrent=1)
    a = mgr.submit([str(gpkg_path)], {"pace_ms": 100})
    b = mgr.submit([str(gpkg_path)], {})
    mgr.cancel(b.id)
    mgr.cancel(a.id)
    deadline = time.time() + 60
    while time.time() < deadline and not (a.is_finished and b.is_finished):
        time.sleep(0.05)
    assert b.status == "cancelled" and b.result is None and "job_start" not in _kinds(b)


def test_job_manager_prunes_finished_jobs(gpkg_path):
    mgr = JobManager(max_concurrent=2, max_kept=2)
    jobs = []
    for _ in range(4):
        j = mgr.submit([str(gpkg_path)], {})
        jobs.append(j)
        while not j.is_finished:
            time.sleep(0.05)
    assert mgr.get(jobs[0].id) is None and mgr.get(jobs[-1].id) is not None


def test_events_since():
    job = Job("ev", [], {})
    for i in range(5):
        job.emit("log", msg=str(i))
    assert [e["seq"] for e in job.events_since(3)] == [3, 4]
    assert job.events_since(10) == []


def test_unknown_stage_rejected():
    with pytest.raises(ValueError):
        registry.check("nope", "x")
