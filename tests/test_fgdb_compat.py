"""The GDAL field-type-22 workaround must never touch the source and must clean up safely."""

import hashlib
import os

import pytest

from app.engine import fgdb_compat
from app.engine.fgdb_compat import MARKER, cache_entries, clear_cache, prune_cache, readable_path

from . import synth


def _digest(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir()) if p.is_file()}


@pytest.fixture
def gdb(tmp_path):
    if not synth.can_write_fgdb():
        pytest.skip("GDAL cannot write OpenFileGDB")
    return synth.make_fgdb(tmp_path / "src" / "tiny.gdb")


def _fake_patch(monkeypatch, gdb):
    table = sorted(gdb.glob("a*.gdbtable"))[-1]
    monkeypatch.setattr(fgdb_compat, "needs_patch", lambda _g: {table.name: [41]})
    return table


def test_unpatched_gdb_is_read_in_place(gdb, tmp_path):
    assert fgdb_compat.needs_patch(gdb) == {}
    path, patches = readable_path(gdb, tmp_path / "cache")
    assert path == str(gdb) and patches == {}
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("mode", ["copy", "hardlink"])
def test_patched_copy_leaves_source_untouched(gdb, tmp_path, monkeypatch, mode):
    before = _digest(gdb)
    table = _fake_patch(monkeypatch, gdb)
    cache = tmp_path / "cache"
    path, patches = readable_path(gdb, cache, log=lambda m: None, link_mode=mode)
    assert _digest(gdb) == before  # the source is byte-for-byte unchanged
    dest = cache / os.path.basename(path)
    assert (dest / MARKER).exists() and patches == {table.name: [41]}
    src_b, dst_b = table.read_bytes(), (dest / table.name).read_bytes()
    assert dst_b[41] == src_b[41] | 4 and dst_b[:41] == src_b[:41] and dst_b[42:] == src_b[42:]
    # the patched table is a real copy, never a hard link to the source
    assert os.stat(dest / table.name).st_ino != os.stat(table).st_ino
    if mode == "copy":
        assert all(os.stat(dest / n).st_ino != os.stat(gdb / n).st_ino for n in before)
    assert not list(cache.glob(".build-*"))  # built atomically
    # second call reuses the cached copy
    assert readable_path(gdb, cache, link_mode=mode)[0] == path


def test_cache_inside_source_is_refused(gdb, monkeypatch):
    _fake_patch(monkeypatch, gdb)
    with pytest.raises(ValueError):
        readable_path(gdb, gdb / "cache")


def test_failed_build_leaves_nothing(gdb, tmp_path, monkeypatch):
    _fake_patch(monkeypatch, gdb)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(fgdb_compat, "_link_or_copy", boom)
    with pytest.raises(OSError):
        readable_path(gdb, tmp_path / "cache")
    assert list((tmp_path / "cache").iterdir()) == []


def test_prune_and_clear_only_delete_marked_entries(tmp_path):
    cache = tmp_path / "cache"
    for i in range(4):
        d = cache / f"x{i}.gdb"
        d.mkdir(parents=True)
        (d / MARKER).write_text("x")
        os.utime(d, (1000 + i, 1000 + i))
    keep = cache / "unrelated"
    keep.mkdir()
    (keep / "important.txt").write_text("keep me")
    assert prune_cache(cache, 2) == 2
    assert sorted(p.name for p in cache_entries(cache)) == ["x2.gdb", "x3.gdb"]
    assert clear_cache(cache) == 2
    assert (keep / "important.txt").exists()


def test_scan_table_ignores_short_or_foreign_files(tmp_path):
    f = tmp_path / "a00000009.gdbtable"
    f.write_bytes(b"\x00" * 10)
    assert fgdb_compat.scan_table(f) == []
    f.write_bytes(b"\xff" * 64)
    assert fgdb_compat.scan_table(f) == []
