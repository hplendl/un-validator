"""Read-only compatibility shim for File Geodatabases written by recent ArcGIS Pro releases.

The problem
    Some tables (notably utility-network feature classes) store a default value on fields
    that are *not* flagged editable. GDAL's OpenFileGDB driver (<= 3.12 at the time of
    writing) only skips the default-value bytes when the editable flag is set, so it loses
    its place in the field header and reports ``Unhandled field type : 22``. The table
    data itself is fine.

What this module does
    :func:`readable_path` scans every ``*.gdbtable`` header (read-only). If any table has
    the pattern, it builds a *separate* copy of the geodatabase under the cache folder in
    which only the affected header flag bytes have the editable bit set, and returns the
    path of that copy for reading. Nothing else changes.

Safety rules
    * The source geodatabase is opened read-only and is never written to.
    * Patched tables are always full copies. Unpatched files are copied too by default;
      ``UNV_FGDB_COMPAT_LINK_MODE=hardlink`` saves disk space by hard-linking them instead (safe
      because the validator only ever reads the copy, but off by default).
    * Copies are built in a temporary folder and renamed into place when complete, so an
      interrupted build never leaves a half-written copy that looks valid.
    * Cleanup (:func:`prune_cache`, :func:`clear_cache`) only removes folders that sit
      directly inside the cache folder and carry this module's marker file.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from ..log import get_logger

_logger = get_logger("fgdb_compat")

MASK_EDITABLE = 4
MARKER = ".un_validator_patched"
_build_lock = threading.Lock()


def _read_varuint(buf: bytes, p: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while True:
        c = buf[p]
        p += 1
        val |= (c & 0x7F) << shift
        shift += 7
        if not c & 0x80:
            return val, p


def scan_table(path: Path) -> list[int]:
    """Return absolute file offsets of field flag bytes that need the editable bit."""
    with open(path, "rb") as f:
        head = f.read(40)
        if len(head) < 40:
            return []
        fo = struct.unpack("<Q", head[32:40])[0]
        if fo <= 0 or fo >= os.fstat(f.fileno()).st_size:
            return []
        f.seek(fo)
        hl_raw = f.read(4)
        if len(hl_raw) < 4:
            return []
        hl = struct.unpack("<I", hl_raw)[0]
        h = hl_raw + f.read(hl)
    try:
        gt = struct.unpack("<I", h[8:12])[0]
        nf = struct.unpack("<H", h[12:14])[0]
        has_z = bool(gt & (1 << 31))
        has_m = bool(gt & (1 << 30))
        p = 14
        fixes: list[int] = []
        for _ in range(nf):
            n = h[p]
            p += 1 + 2 * n  # name
            m = h[p]
            p += 1 + 2 * m  # alias
            t = h[p]
            p += 1
            if t > 16:
                return fixes  # unknown type: stop, leave as is
            if t in (7, 9):  # geometry / raster
                p += 2
                if t == 9:
                    c = h[p]
                    p += 1 + 2 * c
                wl = struct.unpack("<H", h[p : p + 2])[0]
                p += 2 + wl
                gf = h[p]
                p += 1
                hm, hz = bool(gf & 2), bool(gf & 4)
                if t == 7 or gf > 0:
                    p += 8 * (3 + 2 * hm + 2 * hz + 1 + hm + hz)
                if t == 9:
                    p += 1
                else:
                    p += 32 + (16 if has_z else 0) + (16 if has_m else 0)
                    p += 1
                    ng = struct.unpack("<I", h[p : p + 4])[0]
                    p += 4 + 8 * ng
                continue
            if t == 4:  # string
                flag_pos = p + 4
                p += 5
                dl, p = _read_varuint(h, p)
            elif t in (6, 8, 10, 11, 12):  # objectid, binary, uuid, globalid, xml
                flag_pos = p + 1
                p += 2
                dl = 0
            else:
                flag_pos = p + 1
                dl = h[p + 2]
                p += 3
            flags = h[flag_pos]
            if dl and not flags & MASK_EDITABLE:
                fixes.append(fo + flag_pos)
            p += dl
        return fixes
    except (IndexError, struct.error):
        return []


def needs_patch(gdb: str | os.PathLike) -> dict[str, list[int]]:
    """Map table file name -> offsets that need patching (empty when the gdb is fine)."""
    out: dict[str, list[int]] = {}
    for t in sorted(Path(gdb).glob("a*.gdbtable")):
        try:
            fx = scan_table(t)
        except OSError:
            fx = []
        if fx:
            out[t.name] = fx
    return out


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_rmtree(path: Path, cache_root: Path) -> bool:
    """Remove a cache entry, but only a direct child of *cache_root* (or a temp build folder)."""
    try:
        if path.is_symlink() or path.resolve().parent != cache_root.resolve():
            return False
    except OSError:
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True


def _link_or_copy(src: Path, dst: Path, mode: str) -> None:
    if mode == "hardlink":
        try:
            os.link(src, dst)
            return
        except OSError:
            pass
    shutil.copy2(src, dst)


def readable_path(
    gdb: str | os.PathLike,
    cache_root: str | os.PathLike,
    log: Callable[[str], None] | None = None,
    link_mode: str | None = None,
    max_entries: int | None = None,
) -> tuple[str, dict[str, list[int]]]:
    """Return a path GDAL can read for *gdb* (the gdb itself, or a patched copy) and the patches."""
    from .. import config

    gdb = Path(gdb)
    if gdb.suffix.lower() != ".gdb" or not gdb.is_dir():
        return str(gdb), {}
    patches = needs_patch(gdb)
    if not patches:
        return str(gdb), {}
    cache_root = Path(cache_root)
    if _is_within(cache_root, gdb):
        raise ValueError("The patch cache folder must not be inside the source geodatabase")
    mode = (link_mode or config.FGDB_COMPAT_LINK_MODE or "copy").lower()
    files = sorted(p for p in gdb.iterdir() if p.is_file())
    stamp = "|".join(f"{p.name}:{p.stat().st_size}:{int(p.stat().st_mtime)}" for p in files)
    key = hashlib.sha1((str(gdb.resolve()) + stamp).encode(), usedforsecurity=False).hexdigest()[:12]
    dest = cache_root / f"{gdb.stem}_{key}.gdb"
    with _build_lock:
        if (dest / MARKER).exists():
            os.utime(dest)  # mark as recently used for pruning
            return str(dest), patches
        cache_root.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            _safe_rmtree(dest, cache_root)  # stale, incomplete entry from an older version
        tmp = cache_root / f".build-{uuid.uuid4().hex[:8]}"
        tmp.mkdir()
        try:
            for src in files:
                dst = tmp / src.name
                if src.name in patches:
                    shutil.copy2(src, dst)  # always a real copy: this file is modified
                    with open(dst, "r+b") as f:
                        for off in patches[src.name]:
                            f.seek(off)
                            b = f.read(1)[0]
                            f.seek(off)
                            f.write(bytes([b | MASK_EDITABLE]))
                else:
                    _link_or_copy(src, dst, mode)
            (tmp / MARKER).write_text(
                f"Read-only working copy of {gdb.name} with GDAL-compatible field header flags.\n"
                "Generated by app/engine/fgdb_compat.py; safe to delete.\n",
                encoding="utf-8",
            )
            os.replace(tmp, dest)
        except BaseException:
            _safe_rmtree(tmp, cache_root)
            raise
    prune_cache(cache_root, max_entries if max_entries is not None else config.FGDB_COMPAT_MAX_ENTRIES, keep=dest)
    n_flags = sum(len(v) for v in patches.values())
    msg = (
        f"Built GDAL-compatible view of {gdb.name}: patched {n_flags} header flags "
        f"in {len(patches)} tables (original untouched)"
    )
    (log or _logger.info)(msg)
    return str(dest), patches


def cache_entries(cache_root: str | os.PathLike) -> list[Path]:
    root = Path(cache_root)
    if not root.is_dir():
        return []
    return [p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and (p / MARKER).exists()]


def prune_cache(cache_root: str | os.PathLike, max_entries: int, keep: Path | None = None) -> int:
    """Delete the least recently used patched copies beyond *max_entries*; also stale temp folders."""
    root = Path(cache_root)
    removed = 0
    if not root.is_dir():
        return 0
    for p in root.glob(".build-*"):
        if p.is_dir() and time.time() - p.stat().st_mtime > 3600 and _safe_rmtree(p, root):
            removed += 1
    entries = sorted(cache_entries(root), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in entries[max(0, max_entries) :]:
        if keep is not None and p.resolve() == Path(keep).resolve():
            continue
        if _safe_rmtree(p, root):
            removed += 1
    return removed


def clear_cache(cache_root: str | os.PathLike) -> int:
    """Delete every patched copy under *cache_root*."""
    return prune_cache(cache_root, 0)
