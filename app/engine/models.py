"""Shared data types: pipeline stages, severities and findings."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

STAGES: list[tuple[str, str]] = [
    ("discover", "Discover"),
    ("metadata", "Metadata validation"),
    ("lineage", "Data source & lineage"),
    ("schema", "Schema & domain conformance"),
    ("quality", "Data quality"),
    ("report", "Score & report"),
]
STAGE_NAMES: dict[str, str] = dict(STAGES)
SCORED_STAGES = ("metadata", "lineage", "schema", "quality")
SEVERITIES = ("error", "warning", "info")
SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


@dataclass
class Finding:
    severity: str
    stage: str
    check: str
    message: str
    layer: str = ""
    count: int | None = None
    sample_ids: list = field(default_factory=list)
    detail: str = ""
    dataset: str = ""


def jsonable(o: Any) -> Any:
    """Convert numpy/pandas scalars, sets and NaN to plain JSON-safe values."""
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [jsonable(v) for v in o]
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, (str, int, bool)) or o is None:
        return o
    if hasattr(o, "item") and callable(o.item):
        try:
            return jsonable(o.item())
        except Exception:  # noqa: BLE001
            return str(o)
    return str(o)
