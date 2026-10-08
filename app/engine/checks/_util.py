"""Helpers shared by the built-in checks."""

from __future__ import annotations

import pandas as pd

from ..dataset import LayerInfo
from ..scoring import penalty_score


def data_layers(ctx, spatial: bool | None = None) -> list[LayerInfo]:
    """Readable, non-empty user layers (annotation excluded); optionally only (non-)spatial ones."""
    out = []
    for lyr in ctx.dataset.user_layers():
        if not lyr.readable or not lyr.count or lyr.annotation:
            continue
        if spatial is True and not lyr.spatial:
            continue
        if spatial is False and lyr.spatial:
            continue
        out.append(lyr)
    return out


def field_lookup(layer: LayerInfo) -> dict[str, str]:
    """Lower-case field name -> actual field name."""
    return {n.lower(): n for n, _ in layer.fields}


def as_text(s: pd.Series) -> pd.Series:
    """astype(str) that tolerates raw bytes (blob fields) and non-UTF-8 content."""
    if s.dtype == object and s.map(lambda v: isinstance(v, (bytes, bytearray))).any():
        s = s.map(lambda v: v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else v)
    return s.astype(str)


def normalize_guid(s: pd.Series) -> pd.Series:
    """Upper-case GUID text without braces; nulls stay null (never the strings 'NONE' / 'NAN')."""
    out = pd.Series(pd.NA, index=s.index, dtype=object)
    m = s.notna()
    if m.any():
        out[m] = as_text(s[m]).str.upper().str.strip().str.strip("{}").values
    return out


class FirstIds:
    """Collects the first N feature IDs across chunks (for finding samples)."""

    def __init__(self, limit: int = 25):
        self.limit = limit
        self.ids: list = []

    def add(self, idx) -> None:
        room = self.limit - len(self.ids)
        if room > 0:
            self.ids.extend(list(idx[:room]))


amplified = penalty_score  # backward-compatible name used by older plugins
