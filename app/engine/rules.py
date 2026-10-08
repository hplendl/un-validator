"""Per-run rules: built-in defaults, a JSON overlay, and a stable hash.

A rules file is a JSON object (``//`` and ``/* */`` comments are allowed) whose keys
replace or deep-merge the built-in defaults. Lists replace; objects merge. Unknown keys
are an error so a typo cannot silently do nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .. import config
from .models import SCORED_STAGES, SEVERITIES

TOP_LEVEL = ("thresholds", "required_fields", "asset_id_fields", "weights", "severities", "domains")
THRESHOLD_KEYS = ("snap_tolerance_m", "near_miss_m", "max_sample_ids")
# Keys a check looks up. Unknown names are rejected so the file cannot claim to do more than it does.
SEVERITY_KEYS = (
    "null_required",
    "null_optional",
    "duplicate_globalid",
    "duplicate_asset_id",
    "coded_value",
    "unmapped_asset_type",
    "missing_asset_group",
    "fuzzy_asset_type",
)


class RulesError(ValueError):
    """The rules file is not usable. ``errors`` is a list of plain sentences."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def builtin_rules() -> dict:
    """Defaults taken from the current config, so an empty overlay reproduces today's behaviour."""
    return {
        "thresholds": {
            "snap_tolerance_m": float(config.SNAP_TOLERANCE_M),
            "near_miss_m": float(config.NEAR_MISS_M),
            "max_sample_ids": int(config.MAX_SAMPLE_IDS),
        },
        "required_fields": list(config.REQUIRED_FIELDS),
        "asset_id_fields": list(config.ASSET_ID_FIELDS),
        "weights": {
            "stages": {k: float(config.STAGE_WEIGHTS[k]) for k in SCORED_STAGES},
            "metadata_elements": {k: float(v) for k, v in config.METADATA_ELEMENTS.items()},
        },
        "severities": {k: _default_severity(k) for k in SEVERITY_KEYS},
        "domains": {},
    }


def _default_severity(key: str) -> str:
    if key in ("null_optional", "duplicate_asset_id", "unmapped_asset_type"):
        return "warning"
    if key in ("missing_asset_group", "fuzzy_asset_type"):
        return "info"
    return "error"


def strip_json_comments(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments that sit outside strings."""
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    esc = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            if i + 1 >= n:
                raise RulesError(["Unclosed /* comment in the rules file."])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def deep_merge(base: Any, overlay: Any) -> Any:
    """Merge *overlay* onto *base*. Dicts merge recursively; every other value replaces."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = {k: _copy(v) for k, v in base.items()}
        for k, v in overlay.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = deep_merge(out[k], v)
            else:
                out[k] = _copy(v)
        return out
    return _copy(overlay)


def _copy(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _copy(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_copy(x) for x in v]
    return v


def _errors_for(doc: Any) -> list[str]:
    errs: list[str] = []
    if not isinstance(doc, dict):
        return ["The rules file must be a JSON object."]
    unknown = sorted(set(doc) - set(TOP_LEVEL))
    if unknown:
        errs.append("Unknown keys: " + ", ".join(unknown) + ". Allowed keys: " + ", ".join(TOP_LEVEL) + ".")
    thr = doc.get("thresholds", {})
    if "thresholds" in doc:
        if not isinstance(thr, dict):
            errs.append("thresholds must be an object.")
        else:
            bad = sorted(set(thr) - set(THRESHOLD_KEYS))
            if bad:
                errs.append("Unknown thresholds: " + ", ".join(bad) + ".")
            for key in ("snap_tolerance_m", "near_miss_m"):
                if key in thr and not _nonneg_number(thr[key]):
                    errs.append(f"thresholds.{key} must be a number greater than or equal to 0.")
            if "max_sample_ids" in thr and not _int_in(thr["max_sample_ids"], 1, 500):
                errs.append("thresholds.max_sample_ids must be an integer from 1 to 500.")
    for key in ("required_fields", "asset_id_fields"):
        if key in doc and not _str_list(doc[key]):
            errs.append(f"{key} must be a list of field names.")
    if "weights" in doc:
        w = doc["weights"]
        if not isinstance(w, dict):
            errs.append("weights must be an object with stages and metadata_elements.")
        else:
            bad = sorted(set(w) - {"stages", "metadata_elements"})
            if bad:
                errs.append("Unknown weights keys: " + ", ".join(bad) + ".")
            stages = w.get("stages", {})
            if "stages" in w:
                if not isinstance(stages, dict):
                    errs.append("weights.stages must be an object.")
                else:
                    extra = sorted(set(stages) - set(SCORED_STAGES))
                    if extra:
                        errs.append("Unknown stage weights: " + ", ".join(extra) + ".")
                    if not all(_positive_number(v) for v in stages.values()):
                        errs.append("Stage weights must be numbers greater than 0.")
            elements = w.get("metadata_elements", {})
            known = set(config.METADATA_ELEMENTS)
            if "metadata_elements" in w:
                if not isinstance(elements, dict):
                    errs.append("weights.metadata_elements must be an object.")
                else:
                    extra = sorted(set(elements) - known)
                    if extra:
                        errs.append("Unknown metadata element weights: " + ", ".join(extra) + ".")
                    if not all(_nonneg_number(v) for v in elements.values()):
                        errs.append("Metadata element weights must be numbers greater than or equal to 0.")
    if "severities" in doc:
        sev = doc["severities"]
        if not isinstance(sev, dict):
            errs.append("severities must be an object.")
        else:
            extra = sorted(set(sev) - set(SEVERITY_KEYS))
            if extra:
                errs.append(
                    "Unknown severity keys: " + ", ".join(extra) + ". Known keys: " + ", ".join(SEVERITY_KEYS) + "."
                )
            bad = [k for k, v in sev.items() if v not in SEVERITIES]
            if bad:
                errs.append("Severity must be error, warning or info for: " + ", ".join(bad) + ".")
    if "domains" in doc:
        dom = doc["domains"]
        if not isinstance(dom, dict):
            errs.append("domains must be an object of domain name to a list of extra allowed codes.")
        else:
            for name, codes in dom.items():
                if not isinstance(name, str) or not name.strip():
                    errs.append("Domain names must be non-empty strings.")
                    break
                if not isinstance(codes, list) or not all(
                    isinstance(c, (str, int, float)) and not isinstance(c, bool) for c in codes
                ):
                    errs.append(f"domains.{name} must be a list of code strings or numbers.")
    return errs


def _nonneg_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def _positive_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def _int_in(v: Any, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)


def validate_rules(doc: Any) -> dict:
    """Return the effective rules (defaults deep-merged with *doc*), or raise RulesError."""
    errs = _errors_for(doc if doc is not None else {})
    if errs:
        raise RulesError(errs)
    return deep_merge(builtin_rules(), doc or {})


def load_rules_text(text: str) -> dict:
    try:
        doc = json.loads(strip_json_comments(text) or "{}")
    except RulesError:
        raise
    except json.JSONDecodeError as e:
        raise RulesError([f"Rules file is not valid JSON: {e.msg} (line {e.lineno})."]) from e
    return validate_rules(doc)


def load_rules_file(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise RulesError([f"Rules file not found: {p.name}."])
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise RulesError([f"Could not read the rules file: {e.strerror or e}."]) from e
    return load_rules_text(text)


def canonical_rules(rules: dict) -> str:
    return json.dumps(rules, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def rules_sha256(rules: dict) -> str:
    return hashlib.sha256(canonical_rules(rules).encode("utf-8")).hexdigest()


def load_dispositions(path: str | Path) -> dict[str, dict]:
    """Map stable finding id -> {disposition, note}. Missing file is an error; empty object is fine."""
    p = Path(path)
    if not p.is_file():
        raise RulesError([f"Dispositions file not found: {p.name}."])
    try:
        doc = json.loads(strip_json_comments(p.read_text(encoding="utf-8")) or "{}")
    except RulesError:
        raise
    except json.JSONDecodeError as e:
        raise RulesError([f"Dispositions file is not valid JSON: {e.msg} (line {e.lineno})."]) from e
    except OSError as e:
        raise RulesError([f"Could not read the dispositions file: {e.strerror or e}."]) from e
    if not isinstance(doc, dict):
        raise RulesError(["The dispositions file must be a JSON object."])
    findings = doc.get("findings", doc)
    if not isinstance(findings, dict):
        raise RulesError(["dispositions.findings must be an object keyed by finding id."])
    out: dict[str, dict] = {}
    errs: list[str] = []
    for key, val in findings.items():
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-fA-F]{8,64}", key):
            errs.append(f"Disposition key {key!r} is not a finding id.")
            continue
        if isinstance(val, str):
            val = {"disposition": val}
        if not isinstance(val, dict):
            errs.append(f"Disposition for {key} must be an object or a status string.")
            continue
        status = str(val.get("disposition", "OPEN")).upper()
        if status not in ("OPEN", "CLOSED", "ACCEPTED"):
            errs.append(f"Disposition for {key} must be OPEN, CLOSED or ACCEPTED.")
            continue
        note = val.get("note", "")
        if not isinstance(note, str):
            errs.append(f"Disposition note for {key} must be text.")
            continue
        out[key.lower()] = {"disposition": status, "note": note[:500]}
    if errs:
        raise RulesError(errs[:20])
    return out
