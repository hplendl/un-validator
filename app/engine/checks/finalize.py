"""Stage 6 - Score & report."""

from __future__ import annotations

from ... import config
from ..models import STAGE_NAMES
from ..registry import check
from ..results import build_result as _build_result
from ..results import compute_scores as _compute_scores


@check("report", "Compute scores", order=10)
def compute_scores(ctx) -> None:
    """Combine check scores into stage scores and an overall weighted score."""
    sc = _compute_scores(ctx)
    for st, v in sc["stages"].items():
        ctx.log(f"{STAGE_NAMES[st]}: {v if v is not None else 'n/a'}")
    c = sc["counts"]
    ctx.log(
        f"Overall quality score: {sc['overall']} ({sc['grade']}); "
        f"{c['error']} errors, {c['warning']} warnings, {c['info']} info"
    )


@check("report", "Assemble map preview", order=20)
def map_preview(ctx) -> None:
    """Grey context sample of the data plus flagged features, reprojected to WGS84."""
    from ..geo import to_wgs84_features

    layers = [
        lyr for lyr in ctx.dataset.user_layers() if lyr.spatial and lyr.readable and lyr.count and not lyr.annotation
    ]
    lines = [lyr for lyr in layers if "line" in (lyr.geometry_type or "").lower() and "subnet" not in lyr.name.lower()]
    pick = lines or layers
    ctx_feats: list[dict] = []
    budget = config.MAX_CONTEXT_FEATURES
    for lyr in sorted(pick, key=lambda x: -x.count)[:3]:
        ctx.check_cancel()
        df = ctx.dataset.read_geometry(lyr.name)
        n = min(budget // max(1, min(3, len(pick))), len(df))
        sub = df.sample(n, random_state=1) if len(df) > n else df
        ctx_feats += to_wgs84_features(sub, layer=lyr.name, severity="context", label="Context", check="")
    ctx.log(f"Map: {len(ctx_feats):,} context features (sampled), {len(ctx.map_features):,} flagged features")
    ctx.state["map"] = {"context": ctx_feats, "flagged": ctx.map_features}


@check("report", "Build report", order=30)
def build_result(ctx) -> None:
    """Collect findings, tables and scores into the result document."""
    ctx.state["result"] = _build_result(ctx)
    ctx.log(f"Report assembled: {len(ctx.findings)} findings")
