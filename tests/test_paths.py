"""Path restriction: browser-supplied paths must stay inside the data roots."""

import os

import pytest

from app import config
from app.engine.paths import PathNotAllowed, display_path, resolve_user_path


def test_relative_paths_resolve_inside_root(data_root):
    assert resolve_user_path("network/net.gpkg") == (data_root / "network" / "net.gpkg").resolve()
    # the form shown in the UI (prefixed with the root folder's name) works too
    assert resolve_user_path("data/network/net.gpkg") == (data_root / "network" / "net.gpkg").resolve()
    assert resolve_user_path(str(data_root)) == data_root.resolve()


@pytest.mark.parametrize(
    "raw", ["../", "../../etc/passwd", "network/../../..", "/", "/etc", "~", "data/../../outside", "", "   ", "a\x00b"]
)
def test_traversal_and_outside_paths_rejected(raw):
    with pytest.raises(PathNotAllowed):
        resolve_user_path(raw)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlink_escaping_root_rejected(data_root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = data_root / "sneaky"
    link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(PathNotAllowed):
            resolve_user_path("sneaky")
    finally:
        link.unlink()


def test_allow_any_path(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ALLOW_ANY_PATH", True)
    assert resolve_user_path(str(tmp_path)) == tmp_path.resolve()
    with pytest.raises(PathNotAllowed):
        resolve_user_path(str(tmp_path), allow_any=False)


def test_display_path_hides_parent_folders(data_root, tmp_path):
    assert display_path(data_root / "network" / "net.gpkg") == os.path.join("data", "network", "net.gpkg")
    assert display_path(tmp_path / "x.gdb") == str(tmp_path / "x.gdb")  # outside: shown as is
