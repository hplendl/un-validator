"""Profile-only pass: fields, types, null rates, distinct values and key candidates.

This does not add a score. It records a table and a few info findings so the same
question export used by a full run has something to list.
"""

from __future__ import annotations

import pandas as pd

from .checks._util import as_text

DISTINCT_CAP = 5000
NULL_QUESTION = 0.2


def _null_rate(s: pd.Series) -> tuple[float, int]:
    if len(s) == 0:
        return 0.0, 0
    blank = s.isna()
    if s.dtype == object or pd.api.types.is_string_dtype(s.dtype):
        blank = blank | as_text(s).str.strip().isin(["", "None", "nan"])
    n = int(blank.sum())
    return n / len(s), n


def _distinct(s: pd.Series) -> int | None:
    """Exact distinct count, or None when the value set is larger than the cap."""
    seen: set[str] = set()
    filled = s.dropna()
    if filled.dtype == object or pd.api.types.is_string_dtype(filled.dtype):
        values = as_text(filled).str.strip()
    else:
        values = filled.map(lambda v: str(v))
    for v in values:
        seen.add(v)
        if len(seen) > DISTINCT_CAP:
            return None
    return len(seen)


def profile_dataset(ctx) -> None:
    """Fill ``ctx.tables['profile']`` and raise info findings for obvious gaps."""
    ctx.current_stage = "discover"
    ctx.current_check = "Profile"
    rows = []
    layers = [lyr for lyr in ctx.dataset.user_layers() if lyr.readable]
    for i, lyr in enumerate(layers):
        ctx.check_cancel()
        ctx.progress((i + 1) / max(1, len(layers)), lyr.name)
        columns = [name for name, _ in lyr.fields]
        types = {name: typ for name, typ in lyr.fields}
        frame = None
        if lyr.count and columns:
            try:
                frame = ctx.dataset.read_columns(lyr.name, columns)
            except Exception as e:  # noqa: BLE001 - one unreadable layer must not stop the profile
                ctx.log(f"Could not profile {lyr.name}: {e}", "warn")
        fields = []
        key_names = []
        weak = []
        for name in columns:
            series = frame[name] if frame is not None and name in frame.columns else pd.Series(dtype=object)
            rate, n_null = _null_rate(series) if len(series) else (0.0, 0)
            distinct = _distinct(series) if len(series) else 0
            key = bool(
                lyr.count
                and n_null == 0
                and distinct is not None
                and distinct == lyr.count
                and "geom" not in name.lower()
            )
            if key:
                key_names.append(name)
            if rate >= NULL_QUESTION and lyr.count:
                weak.append(name)
            fields.append(
                {
                    "name": name,
                    "type": types.get(name) or "",
                    "null_rate": round(rate, 4),
                    "nulls": n_null,
                    "distinct": distinct if distinct is not None else f">{DISTINCT_CAP}",
                    "key_candidate": key,
                }
            )
        rows.append(
            {
                "layer": lyr.name,
                "geometry": lyr.geometry_type or "",
                "rows": lyr.count,
                "fields": len(columns),
                "key_candidates": ", ".join(key_names[:8]),
                "field_profile": fields,
            }
        )
        if lyr.count and not key_names and lyr.spatial:
            ctx.finding(
                "info",
                f"{lyr.name} has no field that is unique and fully populated, so there is no obvious record key",
                layer=lyr.name,
                rule="no key candidate",
                classification="discovery",
                evidence=f"{lyr.count} rows, {len(columns)} fields",
                recommended_action="Confirm which field identifies a record, or add one before the data is treated as trusted.",
            )
        if weak:
            shown = ", ".join(weak[:8])
            ctx.finding(
                "info",
                f"{lyr.name} has {len(weak)} field(s) that are blank on at least {int(NULL_QUESTION * 100)}% of rows ({shown})",
                layer=lyr.name,
                count=len(weak),
                rule="high null rate",
                classification="discovery",
                evidence=shown,
                threshold=f"null rate >= {NULL_QUESTION}",
                recommended_action="Decide whether those fields are optional or still need to be populated.",
            )
        ctx.log(f"{lyr.name}: {lyr.count:,} rows, {len(columns)} fields, {len(key_names)} key candidate(s)")
    ctx.tables["profile"] = rows
    ctx.log(
        f"Profiled {len(rows)} layers (no score in profile-only mode)"
        if ctx.job.options.get("profile_only")
        else f"Profiled {len(rows)} layers"
    )
