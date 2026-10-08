"""Shared data types: pipeline stages, severities and findings."""

from __future__ import annotations

import hashlib
import math
import re
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
DISPOSITIONS = ("OPEN", "CLOSED", "ACCEPTED")
CLASSIFICATIONS = ("defect", "conformance", "completeness", "lineage", "discovery", "observation")

_STAGE_CLASS = {
    "quality": "defect",
    "schema": "conformance",
    "metadata": "completeness",
    "lineage": "lineage",
    "discover": "discovery",
    "report": "observation",
}
_ACTIONS = {
    "defect": "Correct the affected records, or mark the finding accepted if it is a known exception.",
    "conformance": "Align the class with the reference model, or document why the local difference is intentional.",
    "completeness": "Add the missing metadata so a later reader can tell what the data is and how it may be used.",
    "lineage": "Record where the data came from, which coordinate system it uses, and who last edited it.",
    "discovery": "Confirm the dataset is complete and that anything missing is expected.",
    "observation": "Review the note and close it if no change is required.",
}
_NUM = re.compile(r"\d[\d,.\u00a0]*")
_WS = re.compile(r"\s+")


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
    finding_id: str = ""
    rule: str = ""
    classification: str = ""
    confidence: float = 1.0
    evidence: str = ""
    method: str = ""
    threshold: str = ""
    affected_ids: list = field(default_factory=list)
    affected_count: int = 0
    recommended_action: str = ""
    disposition: str = "OPEN"
    disposition_note: str = ""


def rule_from_message(message: str) -> str:
    """A stable rule key: the message with counts and measurements replaced, so the same
    check on the same layer keeps one id when only the numbers change."""
    text = _NUM.sub("#", message or "")
    text = _WS.sub(" ", text).strip().lower()
    return text[:300]


def classification_for(stage: str, severity: str) -> str:
    if severity == "info" and stage not in ("discover",):
        return "observation"
    return _STAGE_CLASS.get(stage, "observation")


def action_for(classification: str) -> str:
    return _ACTIONS.get(classification, _ACTIONS["observation"])


def finding_id(check: str, layer: str, rule: str, record_keys: list) -> str:
    """Stable id from the check, layer, rule and affected-record keys. Order does not matter."""
    keys = sorted({str(k) for k in record_keys})
    blob = "\n".join(["unv-finding-1", check or "", layer or "", rule or "", *keys])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


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
