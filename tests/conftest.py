"""Shared fixtures: an isolated configuration (temp data/cache/report folders) and synthetic data."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.engine import registry, targets  # noqa: E402
from app.engine.context import RunContext  # noqa: E402
from app.engine.dataset import Dataset  # noqa: E402
from app.engine.jobs import Job  # noqa: E402

from . import synth  # noqa: E402


@pytest.fixture(scope="session")
def workspace(tmp_path_factory) -> Path:
    """A throw-away data root with synthetic datasets; config points at it for the whole session."""
    ws = tmp_path_factory.mktemp("unv")
    data = ws / "data"
    synth.make_gpkg(data / "network" / "net.gpkg")
    (data / "network" / "copy").mkdir(parents=True)
    synth.make_gpkg(data / "network" / "copy" / "net2.gpkg", metadata=False)
    (ws / "foundations").mkdir()
    config.configure(
        data_roots=[data],
        foundation_root=ws / "foundations",
        allow_any_path=False,
        cache_dir=ws / ".cache",
        report_dir=ws / "reports",
    )
    targets.reset_targets()
    return ws


@pytest.fixture(autouse=True)
def _isolated_config(workspace, monkeypatch):
    """Every test starts from the session configuration; monkeypatch undoes per-test changes."""
    monkeypatch.setattr(config, "ALLOW_ANY_PATH", False)
    monkeypatch.setattr(config, "DEMO_PACE_MS", 0)
    yield


@pytest.fixture(scope="session")
def data_root(workspace) -> Path:
    return workspace / "data"


@pytest.fixture(scope="session")
def gpkg_path(data_root) -> Path:
    return data_root / "network" / "net.gpkg"


@pytest.fixture
def make_ctx():
    """Build a RunContext for a dataset so a single check function can be called directly."""
    made: list[RunContext] = []

    def _make(path, options: dict | None = None) -> RunContext:
        job = Job("test", [str(path)], options or {})
        ctx = RunContext(job, Dataset(path))
        made.append(ctx)
        return ctx

    yield _make
    for c in made:
        c.dataset.release_cache()


@pytest.fixture
def ctx(make_ctx, gpkg_path) -> RunContext:
    return make_ctx(gpkg_path)


@pytest.fixture
def registry_snapshot():
    """Restore the check registry after a test that registers checks."""
    saved = list(registry._REGISTRY)
    yield
    registry._REGISTRY[:] = saved


def findings(ctx, check: str | None = None, severity: str | None = None, layer: str | None = None) -> list:
    return [
        f
        for f in ctx.findings
        if (check is None or f.check == check)
        and (severity is None or f.severity == severity)
        and (layer is None or f.layer == layer)
    ]


def run(ctx, fn, stage: str, name: str = ""):
    """Call one check function the way the runner does (stage and check name set)."""
    ctx.current_stage = stage
    ctx.current_check = name or fn.__name__
    fn(ctx)
    return ctx
