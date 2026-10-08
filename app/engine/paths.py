"""Path policy: keep user-supplied paths inside the configured data roots."""

from __future__ import annotations

import os
from pathlib import Path

from .. import config


class PathNotAllowed(ValueError):  # noqa: N818 - reads better at the call sites
    """The requested path is outside every configured data root."""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolved_roots() -> list[Path]:
    return [Path(r).resolve() for r in config.DATA_ROOTS]


def _resolve_relative(p: Path) -> Path:
    """Relative input is looked up in each data root. It may start with the root's folder name,
    as shown in the UI (``data/other/x.gdb``), or be relative to the root itself (``other/x.gdb``)."""
    candidates: list[Path] = []
    for raw_root in config.DATA_ROOTS:
        root = Path(raw_root)
        if p.parts and p.parts[0] == root.name:
            candidates.append(root.joinpath(*p.parts[1:]))
        candidates.append(root / p)
    return next((c for c in candidates if c.exists()), candidates[0])


def resolve_user_path(raw: str, allow_any: bool | None = None) -> Path:
    """Turn user input into an absolute path, rejecting anything outside the data roots.

    Relative input is taken relative to the first data root. Symlinks and ``..`` are resolved
    *before* the containment test, so neither can be used to escape a root.
    """
    if raw is None or not str(raw).strip() or "\x00" in str(raw):
        raise PathNotAllowed("Empty or invalid path")
    allow_any = config.ALLOW_ANY_PATH if allow_any is None else allow_any
    p = Path(os.path.expanduser(str(raw).strip()))
    if not p.is_absolute():
        p = _resolve_relative(p)
    p = p.resolve()
    if allow_any:
        return p
    if any(_is_within(p, r) for r in resolved_roots()):
        return p
    raise PathNotAllowed("Path is outside the configured data folders (set UNV_DATA_ROOTS or use --allow-any-path)")


def display_path(path: str | Path) -> str:
    """Path for display: relative to its data root (prefixed with the root's folder name) when possible."""
    p = Path(path)
    try:
        rp = p.resolve()
    except OSError:
        return str(p)
    for raw, root in zip(config.DATA_ROOTS, resolved_roots()):
        if _is_within(rp, root):
            rel = rp.relative_to(root)
            return str(Path(Path(raw).name or str(raw)) / rel)
    return str(p)
