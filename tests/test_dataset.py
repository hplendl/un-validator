"""Dataset reader: column cache, batched reads, feature fetch, discovery."""

import shutil

import numpy as np
import pytest

from app.engine.dataset import Dataset, _LRUCache, discover_datasets

from . import synth


def test_layers_and_counts(gpkg_path):
    ds = Dataset(gpkg_path)
    assert {lyr.name: lyr.count for lyr in ds.user_layers()} == {
        "WaterLine": 6,
        "WaterDevice": 3,
        "ServiceArea": 3,
        "Hydrant": 2,
        "Inspections": 2,
    }
    assert ds.layer("waterline").spatial and not ds.layer("Inspections").spatial


def test_read_columns_geometry_and_fids(gpkg_path):
    ds = Dataset(gpkg_path)
    df = ds.read_columns("WaterLine", ["ASSETID", "GLOBALID"])
    assert list(df.columns) == ["ASSETID", "GLOBALID"] and list(df.index) == [1, 2, 3, 4, 5, 6]
    g = ds.read_geometry("WaterLine")
    assert list(g.columns) == ["geometry"] and g.crs.to_epsg() == 26916
    sub = ds.read_fids("WaterLine", [4, 2])
    assert list(sub.index) == [4, 2]
    full = ds.read("WaterLine")
    assert "ASSETID" in full.columns and full.geometry.name == "geometry" and len(full) == 6
    assert len(ds.read_fids("WaterLine", [])) == 0


def test_read_fids_without_cached_geometry(gpkg_path):
    ds = Dataset(gpkg_path)
    assert list(ds.read_fids("WaterDevice", [3, 1]).index) == [3, 1]


def test_tiny_cache_budget_still_returns_correct_data(gpkg_path):
    ds = Dataset(gpkg_path, cache_mb=0)
    a = ds.read_columns("WaterLine", ["ASSETID"])
    b = ds.read_columns("WaterLine", ["ASSETID", "GLOBALID"])
    assert a["ASSETID"].tolist() == b["ASSETID"].tolist()


def test_lru_cache_evicts_oldest():
    c = _LRUCache(budget_bytes=2000)
    import pandas as pd

    for i in range(5):
        c.put(i, pd.Series(np.zeros(100)))  # ~800+ bytes each
    assert 4 in c and 0 not in c and c.size <= 2000


def test_iter_batches(tmp_path):
    p = synth.make_big_lines(tmp_path / "b.gpkg", n=20)
    ds = Dataset(p)
    sizes = [len(b) for b in ds.iter_batches("Lines", columns=["ASSETGROUP"], geometry=False, batch_rows=6)]
    assert sizes == [6, 6, 6, 2]
    assert [len(b) for b in ds.iter_batches("Lines", columns=[], geometry=True, batch_rows=100)] == [20]


def test_gpkg_catalog_handles_odd_characters_in_path(tmp_path, gpkg_path):
    odd = tmp_path / "dir with spaces & #hash%"
    odd.mkdir()
    shutil.copy(gpkg_path, odd / "net.gpkg")
    cat = Dataset(odd / "net.gpkg").catalog()
    assert cat["workspace_doc"] and cat["classes"]["waterline"].documentation


def test_open_failure_is_a_clear_error(tmp_path):
    bad = tmp_path / "broken.gpkg"
    bad.write_bytes(b"not a geopackage")
    with pytest.raises(ValueError, match="not a readable"):
        Dataset(bad)
    with pytest.raises(FileNotFoundError):
        Dataset(tmp_path / "missing.gpkg")


def test_discover_skips_hidden_and_searches_fake_gdb_folders(tmp_path, gpkg_path):
    root = tmp_path / "root"
    (root / ".hidden").mkdir(parents=True)
    shutil.copy(gpkg_path, root / ".hidden" / "x.gpkg")
    wrapper = root / "download.gdb"  # an unzipped download *named* .gdb that is not a geodatabase
    (wrapper / "inner").mkdir(parents=True)
    shutil.copy(gpkg_path, wrapper / "inner" / "y.gpkg")
    shutil.copy(gpkg_path, root / "z.gpkg")
    found = [p.relative_to(root).as_posix() for p in discover_datasets(root)]
    assert found == ["download.gdb/inner/y.gpkg", "z.gpkg"]
    assert discover_datasets(wrapper) == [wrapper / "inner" / "y.gpkg"]
    assert discover_datasets(root / "z.gpkg") == [root / "z.gpkg"]
    assert discover_datasets(root / "nope") == []


@pytest.mark.skipif(not synth.can_write_fgdb(), reason="GDAL cannot write OpenFileGDB")
def test_file_geodatabase_catalog(tmp_path):
    gdb = synth.make_fgdb(tmp_path / "tiny.gdb")
    assert discover_datasets(tmp_path) == [gdb]
    ds = Dataset(gdb)
    cat = ds.catalog()
    assert set(cat["classes"]) == {"waterline", "waterdevice"}
    wl = cat["classes"]["waterline"]
    assert wl.documentation and "Water mains" in wl.documentation
    assert {f.name for f in wl.fields} >= {"GLOBALID", "ASSETGROUP", "ISCONNECTED"}
    assert not ds.patched_tables  # GDAL-written tables never need the compatibility patch
