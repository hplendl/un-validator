"""Pipeline runner: validates each target dataset through the six stages.

Isolation rules: a failing check becomes a warning finding and the run continues; a dataset
that cannot be opened becomes a failed result and the next dataset runs; Stop ends the run
after the current step and still produces a (partial) result.
"""

from __future__ import annotations

import hashlib
import json
import time
import traceback
from pathlib import Path

from ..log import get_logger
from .context import JobCancelled, RunContext
from .dataset import Dataset
from .jobs import Job
from .models import SEVERITIES, STAGES, jsonable
from .paths import display_path
from .registry import load_checks, module_available, registered_checks
from .results import build_result, combine, compute_scores, failed_result

log = get_logger("runner")


def _run_stage(ctx: RunContext, stage: str, stage_name: str) -> None:
    job = ctx.job
    ctx.current_stage = stage
    ctx.current_check = ""
    specs = registered_checks(stage)
    job.emit("stage_start", stage=stage, name=stage_name, checks=[c.name for c in specs], dataset=ctx.dataset.name)
    t0 = time.time()
    for k, spec in enumerate(specs):
        ctx.check_cancel()
        ctx.current_check = spec.name
        job.emit("check_start", stage=stage, check=spec.name, index=k, total=len(specs), description=spec.description)
        missing = [m for m in spec.requires if not module_available(m)]
        if missing:
            ctx.log(f"Skipped: requires {', '.join(missing)} (not installed)", "warn")
            job.emit("check_end", stage=stage, check=spec.name, index=k, total=len(specs), status="skipped")
            continue
        try:
            spec.func(ctx)
            status = "ok"
        except JobCancelled:
            job.emit("check_end", stage=stage, check=spec.name, index=k, total=len(specs), status="cancelled")
            raise
        except Exception as e:
            status = "error"
            log.exception("check %r failed on %s", spec.name, ctx.dataset.name)
            ctx.finding("warning", f"Check failed to run: {e}", detail=traceback.format_exc()[-1500:])
            ctx.log(f"Check '{spec.name}' raised {type(e).__name__}: {e}", "error")
        job.emit("check_end", stage=stage, check=spec.name, index=k, total=len(specs), status=status)
        ctx.pace()
    job.emit(
        "stage_end",
        stage=stage,
        score=ctx.stage_score(stage),
        seconds=round(time.time() - t0, 2),
        dataset=ctx.dataset.name,
        counts={s: sum(1 for f in ctx.findings if f.stage == stage and f.severity == s) for s in SEVERITIES},
    )


def run_dataset(job: Job, target: str, index: int) -> dict:
    """Validate one dataset; never raises (except to propagate a Stop request)."""
    name = Path(target).name
    job.emit("dataset_start", index=index, total=len(job.targets), dataset=name, path=display_path(target))
    t0 = time.time()

    def open_log(m: str) -> None:
        job.emit("log", stage="discover", check="Open dataset", level="info", msg=m, dataset=name)

    try:
        ds = Dataset(target, log=open_log)
    except Exception as e:
        log.exception("could not open %s", target)
        job.emit(
            "log",
            stage="discover",
            check="Open dataset",
            level="error",
            msg=f"Could not open {name}: {e}",
            dataset=name,
        )
        res = failed_result(target, e, time.time() - t0)
        job.emit("dataset_end", index=index, dataset=name, overall=None, grade="n/a", status="failed")
        return res

    ctx = RunContext(job, ds, index, len(job.targets))
    status = "done"
    profile_only = bool(job.options.get("profile_only"))
    stages = [s for s in STAGES if s[0] == "discover"] if profile_only else list(STAGES)
    try:
        for stage, stage_name in stages:
            _run_stage(ctx, stage, stage_name)
        if profile_only:
            from .profile import profile_dataset

            ctx.check_cancel()
            profile_dataset(ctx)
    except JobCancelled:
        status = "cancelled"
        ctx.log("Run stopped by user; the result below is partial", "warn")
    finally:
        try:
            if status != "done" or "result" not in ctx.state:
                ctx.state.pop("scores", None)
                compute_scores(ctx)
                ctx.state["result"] = build_result(ctx, status=status)
        finally:
            ds.release_cache()
    res = ctx.state["result"]
    res["status"] = status
    res["seconds"] = round(time.time() - t0, 1)
    job.emit(
        "dataset_end", index=index, dataset=ds.name, overall=res.get("overall"), grade=res.get("grade"), status=status
    )
    return res


def run_job(job: Job) -> None:
    """Validate every target dataset in sequence and store the combined result on the job."""
    from .report import write_reports

    load_checks()
    job.status = "running"
    job.started = time.time()
    shown = [s for s in STAGES if s[0] == "discover"] if job.options.get("profile_only") else list(STAGES)
    job.emit(
        "job_start",
        targets=[display_path(t) for t in job.targets],
        stages=[{"id": s, "name": n} for s, n in shown],
        profile_only=bool(job.options.get("profile_only")),
    )
    results: list[dict] = []
    try:
        for i, target in enumerate(job.targets):
            if job.cancelled:
                break
            results.append(run_dataset(job, target, i))
        status = "cancelled" if job.cancelled else "done"
        combined = combine(job.id, results, status)
        from .manifest import result_signature
        from .questions import build_questions
        from .rules import builtin_rules, rules_sha256

        rules = job.options.get("rules") or builtin_rules()
        combined["rules"] = rules
        combined["rules_sha256"] = rules_sha256(rules)
        combined["profile_only"] = bool(job.options.get("profile_only"))
        disp = job.options.get("dispositions") or {}
        disp_blob = json.dumps(disp, sort_keys=True, separators=(",", ":"), default=str)
        combined["dispositions_sha256"] = hashlib.sha256(disp_blob.encode("utf-8")).hexdigest() if disp else ""
        combined["questions"] = build_questions(combined)
        combined["signature"] = result_signature(combined)
        try:
            combined["files"] = write_reports(combined)
        except Exception as e:
            log.exception("report export failed")
            combined["files"] = {}
            job.emit("log", stage="report", check="Export", level="error", msg=f"Report export failed: {e}", dataset="")
        job.result = jsonable(combined)
        job.status = status
        job.emit(
            "job_end",
            overall=combined["overall"],
            grade=combined["grade"],
            files=bool(combined["files"]),
            status=status,
        )
    except Exception as e:
        log.exception("job %s failed", job.id)
        job.status = "failed"
        job.emit("job_error", error=str(e), trace=traceback.format_exc()[-3000:])
    finally:
        job.finished = time.time()
