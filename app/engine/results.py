"""Turn a finished RunContext into the result document (scores, findings, tables, map)."""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import config
from .models import SCORED_STAGES, SEVERITIES, SEVERITY_RANK
from .paths import display_path
from .scoring import grade, overall_score

if TYPE_CHECKING:
    from .context import RunContext


def compute_scores(ctx: RunContext) -> dict:
    """Combine check scores into stage scores and an overall weighted score (stored in ctx.state)."""
    stages = {st: ctx.stage_score(st) for st in SCORED_STAGES}
    weights = ((getattr(ctx, "rules", None) or {}).get("weights") or {}).get("stages") or config.STAGE_WEIGHTS
    overall = overall_score(stages, weights)
    if not ctx.state.get("summary", {}).get("layers"):
        overall = None  # empty geodatabase: a score would be meaningless
    counts = {s: sum(1 for f in ctx.findings if f.severity == s) for s in SEVERITIES}
    scores = {
        "stages": stages,
        "overall": overall,
        "grade": grade(overall),
        "counts": counts,
        "components": {
            st: [{"label": lbl, "score": round(v, 1), "weight": wt} for v, wt, lbl in ctx.scores[st]]
            for st in ctx.scores
        },
    }
    ctx.state["scores"] = scores
    return scores


def sorted_findings(ctx: RunContext) -> list[dict]:
    return sorted((asdict(f) for f in ctx.findings), key=lambda f: (SEVERITY_RANK[f["severity"]], -(f["count"] or 0)))


def build_result(ctx: RunContext, status: str = "done") -> dict:
    """The per-dataset result document (also used as a fallback when a run stops early)."""
    sc = ctx.state.get("scores") or compute_scores(ctx)
    return {
        "dataset": ctx.dataset.name,
        "path": str(ctx.dataset.path),
        "display_path": display_path(ctx.dataset.path),
        "kind": ctx.dataset.kind,
        "status": status,
        "summary": ctx.state.get("summary", {}),
        "reference_ranking": [{k: v for k, v in r.items() if k != "path"} for r in ctx.state.get("target_ranking", [])],
        "stages": sc.get("stages", {}),
        "components": sc.get("components", {}),
        "overall": sc.get("overall"),
        "grade": sc.get("grade"),
        "counts": sc.get("counts", {}),
        "findings": sorted_findings(ctx),
        "tables": ctx.tables,
        "map": ctx.state.get("map", {"context": [], "flagged": ctx.map_features}),
    }


def failed_result(path: str | Path, error: BaseException, seconds: float = 0.0) -> dict:
    """Result for a dataset that could not even be opened."""
    p = Path(path)
    msg = f"Dataset could not be opened: {type(error).__name__}: {error}"
    from .models import action_for, classification_for, finding_id, rule_from_message

    rule = rule_from_message(msg[:500])
    cls = classification_for("discover", "error")
    finding = {
        "severity": "error",
        "stage": "discover",
        "check": "Open dataset",
        "message": msg[:500],
        "layer": "",
        "count": None,
        "sample_ids": [],
        "detail": "",
        "dataset": p.name,
        "finding_id": finding_id("Open dataset", "", rule, []),
        "rule": rule,
        "classification": cls,
        "confidence": 1.0,
        "evidence": msg[:500],
        "method": "Open dataset",
        "threshold": "",
        "affected_ids": [],
        "affected_count": 0,
        "recommended_action": action_for(cls),
        "disposition": "OPEN",
        "disposition_note": "",
    }
    return {
        "dataset": p.name,
        "path": str(p),
        "display_path": display_path(p),
        "kind": "gpkg" if p.suffix.lower() == ".gpkg" else "fgdb",
        "status": "failed",
        "summary": {},
        "reference_ranking": [],
        "stages": {s: None for s in SCORED_STAGES},
        "components": {},
        "overall": None,
        "grade": "n/a",
        "counts": {"error": 1, "warning": 0, "info": 0},
        "findings": [finding],
        "tables": {},
        "map": {"context": [], "flagged": []},
        "seconds": round(seconds, 1),
    }


def combine(job_id: str, results: list[dict], status: str = "done") -> dict[str, Any]:
    vals = [r["overall"] for r in results if r.get("overall") is not None]
    overall = round(sum(vals) / len(vals), 1) if vals else None
    return {
        "job_id": job_id,
        "title": config.APP_TITLE,
        "version": config.APP_VERSION,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "status": status,
        "datasets": results,
        "overall": overall,
        "grade": grade(overall),
    }
