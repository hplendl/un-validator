import pytest

from app.engine.scoring import clamp, grade, overall_score, penalty_score, weighted_mean


@pytest.mark.parametrize(
    "bad,total,factor,expected",
    [(0, 100, 10, 100.0), (1, 100, 10, 90.0), (5, 100, 10, 50.0), (20, 100, 10, 0.0), (1, 100, 3, 97.0)],
)
def test_penalty_score(bad, total, factor, expected):
    assert penalty_score(bad, total, factor) == pytest.approx(expected)


def test_penalty_score_no_items_is_none():
    assert penalty_score(0, 0) is None


def test_weighted_mean_ignores_none_and_rounds():
    assert weighted_mean([(100, 1), (50, 3)]) == 62.5
    assert weighted_mean([(None, 1), (80, 2)]) == 80.0
    assert weighted_mean([]) is None
    assert weighted_mean([(50, 0)]) is None


def test_overall_score_uses_stage_weights_and_skips_missing():
    w = {"metadata": 20, "lineage": 15, "schema": 25, "quality": 40}
    assert overall_score({"metadata": 100, "lineage": 100, "schema": 100, "quality": 100}, w) == 100.0
    # a missing stage is left out, not counted as zero
    assert overall_score({"metadata": None, "lineage": 50, "schema": None, "quality": None}, w) == 50.0
    assert overall_score({"metadata": 0, "lineage": 100, "schema": 100, "quality": 100}, w) == 80.0
    assert overall_score({}, w) is None


@pytest.mark.parametrize(
    "score,g",
    [(None, "n/a"), (100, "A"), (90, "A"), (89.9, "B"), (80, "B"), (70, "C"), (60, "D"), (59.9, "F"), (0, "F")],
)
def test_grade_boundaries(score, g):
    assert grade(score) == g


def test_clamp():
    assert clamp(150) == 100 and clamp(-5) == 0 and clamp(42) == 42


def test_compute_scores_empty_dataset_is_not_scored(ctx):
    from app.engine.results import compute_scores

    ctx.state["summary"] = {"layers": 0}
    ctx.scores["metadata"].append((100.0, 1.0, "x"))
    sc = compute_scores(ctx)
    assert sc["overall"] is None and sc["grade"] == "n/a"
    assert sc["stages"]["metadata"] == 100.0


def test_context_score_clamps_and_ignores_nan(ctx):
    ctx.current_stage = "quality"
    ctx.score(float("nan"))
    ctx.score(None)
    ctx.score(140, weight=2)
    assert ctx.scores["quality"] == [(100.0, 2.0, "")]
    assert ctx.stage_score("quality") == 100.0
