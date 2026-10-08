"""RunContext: what a check sees while it runs (dataset, shared state, emit helpers)."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from .. import config
from ..log import get_logger
from .models import SEVERITIES, STAGES, Finding
from .scoring import clamp, weighted_mean

if TYPE_CHECKING:
    from .dataset import Dataset
    from .jobs import Job

log = get_logger("run")


class JobCancelled(BaseException):
    """Raised inside a check when the user pressed Stop."""


def _as_id(i: Any) -> Any:
    try:
        return int(i)
    except (TypeError, ValueError):
        return str(i)


class RunContext:
    """Everything a check needs: the dataset, shared state, and emit helpers."""

    def __init__(self, job: Job, dataset: Dataset, ds_index: int = 0, ds_total: int = 1):
        self.job = job
        self.dataset = dataset
        self.ds_index = ds_index
        self.ds_total = ds_total
        self.findings: list[Finding] = []
        self.scores: dict[str, list[tuple[float, float, str]]] = {s: [] for s, _ in STAGES}
        self.state: dict[str, Any] = {}  # shared between checks (target schema, main CRS, ...)
        self.map_features: list[dict] = []
        self.tables: dict[str, Any] = {}  # extra result tables (metadata items, layer summary, ...)
        self.current_stage = "discover"
        self.current_check = ""
        pace_ms = int(job.options.get("pace_ms", config.DEMO_PACE_MS) or 0)
        self.pace_s = max(0, min(config.MAX_PACE_MS, pace_ms)) / 1000.0

    # ---- cancellation ----
    @property
    def cancelled(self) -> bool:
        return self.job.cancelled

    def check_cancel(self) -> None:
        if self.job.cancelled:
            raise JobCancelled()

    # ---- emit helpers ----
    def log(self, msg: str, level: str = "info") -> None:
        log.debug("[%s/%s] %s", self.dataset.name, self.current_stage, msg)
        self.job.emit(
            "log",
            stage=self.current_stage,
            check=self.current_check,
            level=level,
            msg=str(msg),
            dataset=self.dataset.name,
        )

    def progress(self, frac: float, label: str = "") -> None:
        self.check_cancel()
        self.job.emit(
            "check_progress",
            stage=self.current_stage,
            check=self.current_check,
            frac=max(0.0, min(1.0, float(frac))),
            label=str(label),
        )

    def finding(
        self,
        severity: str,
        message: str,
        layer: str = "",
        count: int | None = None,
        sample_ids=None,
        detail: str = "",
        check: str | None = None,
    ) -> Finding:
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, not {severity!r}")
        ids = [_as_id(i) for i in list(sample_ids if sample_ids is not None else [])[: config.MAX_SAMPLE_IDS]]
        f = Finding(
            severity,
            self.current_stage,
            check or self.current_check,
            str(message),
            str(layer or ""),
            None if count is None else int(count),
            ids,
            str(detail or ""),
            self.dataset.name,
        )
        self.findings.append(f)
        self.job.emit("finding", **asdict(f))
        return f

    def score(self, value: float | None, weight: float = 1.0, label: str = "") -> None:
        if value is None or value != value:  # None or NaN
            return
        self.scores[self.current_stage].append((clamp(value), float(weight), label or self.current_check))

    def flag_features(self, layer: str, gdf, severity: str, label: str, max_n: int = 200) -> None:
        """Send a sample of flagged features (GeoDataFrame in the layer CRS) to the map preview."""
        from .geo import to_wgs84_features

        if gdf is None or len(gdf) == 0:
            return
        budget = config.MAX_MAP_FEATURES - len(self.map_features)
        if budget <= 0:
            return
        n = min(max_n, budget, len(gdf))
        sub = gdf.sample(n, random_state=0) if len(gdf) > n else gdf
        self.map_features.extend(
            to_wgs84_features(sub, layer=layer, severity=severity, label=label, check=self.current_check)
        )

    def flag_fids(self, layer: str, fids, severity: str, label: str, max_n: int = 200) -> None:
        """Like flag_features, but reads only the geometries of the given feature IDs.

        Sampling matches ``DataFrame.sample(n, random_state=0)`` so map output is stable.
        """
        import numpy as np

        fids = np.asarray(list(fids))
        budget = config.MAX_MAP_FEATURES - len(self.map_features)
        if not len(fids) or budget <= 0:
            return
        n = min(max_n, budget, len(fids))
        if len(fids) > n:
            fids = fids[np.random.RandomState(0).choice(len(fids), size=n, replace=False)]
        gdf = self.dataset.read_fids(layer, fids.tolist())
        self.flag_features(layer, gdf, severity, label, max_n=n)

    def pace(self) -> None:
        """Demo pacing: sleep a little (in small steps, so Stop stays responsive)."""
        self.check_cancel()
        end = time.monotonic() + self.pace_s
        while self.pace_s and time.monotonic() < end:
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))
            self.check_cancel()

    def stage_score(self, stage: str) -> float | None:
        return weighted_mean((v, w) for v, w, _ in self.scores.get(stage) or [])
