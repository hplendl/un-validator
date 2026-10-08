"""Scoring helpers: penalty curves, weighted means, stage/overall scores and grades."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

GRADE_CUTS = (("A", 90.0), ("B", 80.0), ("C", 70.0), ("D", 60.0))


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(v)))


def penalty_score(bad: float, total: float, factor: float = 10.0) -> float | None:
    """100 minus *factor* points per percent of bad items (1 % bad at factor 10 -> 90)."""
    if not total:
        return None
    return max(0.0, 100.0 - factor * 100.0 * bad / total)


def weighted_mean(components: Iterable[tuple[float, float]]) -> float | None:
    """Weighted mean of (value, weight) pairs, rounded to 0.1; None when there is no weight."""
    items = [(v, w) for v, w in components if v is not None]
    tw = sum(w for _, w in items)
    if not items or tw == 0:
        return None
    return round(sum(v * w for v, w in items) / tw, 1)


def overall_score(stage_scores: Mapping[str, float | None], weights: Mapping[str, float]) -> float | None:
    """Weighted mean of the stage scores; stages without a score are left out."""
    w = {k: v for k, v in weights.items() if stage_scores.get(k) is not None}
    if not w:
        return None
    return round(sum(stage_scores[k] * v for k, v in w.items()) / sum(w.values()), 1)  # type: ignore[operator]


def grade(score: float | None) -> str:
    if score is None:
        return "n/a"
    for g, cut in GRADE_CUTS:
        if score >= cut:
            return g
    return "F"
