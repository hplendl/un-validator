"""Unit tests for the data-quality checks, on the synthetic GeoPackage."""

import pytest

from app import config
from app.engine.checks import network, quality

from .conftest import findings, run


def test_readability_all_layers_readable(ctx):
    run(ctx, quality.readability, "quality")
    assert ctx.scores["quality"][0][0] == 100.0


def test_required_fields_nulls(ctx):
    run(ctx, quality.required_nulls, "quality", "req")
    msgs = {(f.layer, f.message) for f in findings(ctx, "req")}
    assert ("WaterLine", "1 of 6 rows have no value in required field GLOBALID") in msgs
    assert ("WaterLine", "1 of 6 rows have no value in required field ASSETGROUP") in msgs
    f = next(f for f in findings(ctx, "req") if "ASSETGROUP" in f.message)
    assert f.severity == "error" and f.sample_ids == [3]
    assert ctx.scores["quality"][0][0] < 100
    # flagged features are sent to the map
    assert any(m["properties"]["label"] == "Required field empty" for m in ctx.map_features)


def test_duplicate_globalids_and_asset_ids(ctx):
    run(ctx, quality.duplicates, "quality", "dup")
    gid = [f for f in findings(ctx, "dup") if "GlobalID" in f.message and f.layer == "WaterLine"]
    assert gid and gid[0].count == 2 and sorted(gid[0].sample_ids) == [1, 2]
    asset = [f for f in findings(ctx, "dup") if "ASSETID" in f.message]
    assert asset and asset[0].severity == "warning" and asset[0].count == 2
    # nulls are not GlobalIDs: no "NONE"/"NAN" placeholders leak into the set
    gids = ctx.state["all_globalids"]
    assert gids and not {"NONE", "NAN", "<NA>", ""} & gids


def test_globalid_reused_across_classes(make_ctx, tmp_path):
    import geopandas as gpd
    from shapely.geometry import Point

    from . import synth

    p = tmp_path / "x.gpkg"
    a = gpd.GeoDataFrame({"GLOBALID": ["{ABC}"]}, geometry=[Point(0, 0)], crs=synth.UTM)
    b = gpd.GeoDataFrame({"GLOBALID": ["abc"]}, geometry=[Point(1, 1)], crs=synth.UTM)
    synth._write(a, p, "A")
    synth._write(b, p, "B")
    c = run(make_ctx(p), quality.duplicates, "quality", "dup")
    assert any("reused across different classes" in f.message for f in findings(c, "dup"))


def test_geometry_validity(ctx):
    run(ctx, quality.geometry_validity, "quality", "geom")
    by = {(f.layer, f.message.split(" ", 1)[1]) for f in findings(ctx, "geom")}
    assert ("WaterLine", "features have null or empty geometry") in by
    assert ("WaterLine", "zero-length lines") in by
    assert ("WaterLine", "invalid geometries") in by
    assert ("ServiceArea", "invalid geometries") in by
    assert ("ServiceArea", "zero-area polygons") in by
    stacked = [f for f in findings(ctx, "geom") if "stacked" in f.message]
    assert stacked and stacked[0].layer == "WaterDevice" and stacked[0].count == 2
    inval = next(f for f in findings(ctx, "geom") if f.layer == "ServiceArea" and "invalid" in f.message)
    assert "Self-intersection" in inval.detail


def test_dangles_and_near_misses(ctx):
    run(ctx, network.dangles, "quality", "dangles")
    nm = [f for f in findings(ctx, "dangles") if "near-miss" in f.message]
    dg = [f for f in findings(ctx, "dangles") if "free dangling" in f.message]
    assert nm and nm[0].layer == "WaterLine" and set(nm[0].sample_ids) == {2, 3}
    assert dg and 4 in dg[0].sample_ids
    rows = {r["layer"]: r for r in ctx.tables["connectivity"]}
    assert rows["WaterLine"]["near_miss"] == 2
    assert ctx.scores["quality"][0][0] < 100


def test_dangles_reprojects_mixed_crs_layers(make_ctx, tmp_path):
    """A point layer in another CRS must still connect line ends (it used to be compared raw)."""
    import geopandas as gpd
    from shapely.geometry import LineString, Point

    from . import synth

    p = tmp_path / "mixed.gpkg"
    line = gpd.GeoDataFrame({"a": [1]}, geometry=[LineString([(500000, 4630000), (500100, 4630000)])], crs=synth.UTM)
    ends = gpd.GeoDataFrame({"a": [1, 2]}, geometry=[Point(500000, 4630000), Point(500100, 4630000)], crs=synth.UTM)
    synth._write(line, p, "Main")
    synth._write(ends.to_crs(4326), p, "Valve")
    c = run(make_ctx(p), network.dangles, "quality", "dangles")
    assert not findings(c, "dangles"), [f.message for f in c.findings]
    assert c.tables["connectivity"][0]["dangles"] == 0


def test_zm_consistency(ctx):
    run(ctx, quality.zm_consistency, "quality", "zm")
    # no layer is Z-enabled: consistent, nothing flagged
    assert not findings(ctx, "zm") and ctx.scores["quality"][0][0] == 100.0


def test_date_sanity(ctx):
    run(ctx, quality.date_sanity, "quality", "dates")
    msgs = [f.message for f in findings(ctx, "dates")]
    assert any("in the future (max 2999-01-01)" in m for m in msgs)
    assert any("before 1850 (min 1800-01-01)" in m for m in msgs)


def test_isconnected(ctx):
    run(ctx, network.isconnected, "quality", "iscon")
    f = findings(ctx, "iscon")
    assert len(f) == 1 and f[0].count == 1 and f[0].sample_ids == [4]


def test_un_tables_absent_is_skipped(ctx):
    run(ctx, network.un_tables, "quality", "un")
    assert not ctx.findings and not ctx.scores["quality"]


@pytest.mark.parametrize("chunk", [1, 3, 7])
def test_batched_reads_give_same_findings(make_ctx, tmp_path, monkeypatch, chunk):
    """Large-layer streaming (iter_batches) must not change any finding."""
    from . import synth

    p = synth.make_big_lines(tmp_path / "big.gpkg", n=50)

    def collect():
        c = make_ctx(p)
        for fn in (quality.required_nulls, quality.geometry_validity, quality.date_sanity, quality.zm_consistency):
            run(c, fn, "quality", fn.__name__)
        return [(f.check, f.message, f.count, f.sample_ids) for f in c.findings], c.scores["quality"]

    whole = collect()
    monkeypatch.setattr(config, "CHUNK_ROWS", chunk)
    assert collect() == whole
    assert whole[0]  # the fixture does contain defects


def test_as_datetimes_handles_out_of_range_text_dates():
    import pandas as pd

    s = pd.Series(["2010-01-01", "9999-12-31", None, "garbage", "1700-05-01 10:00:00"], dtype=object)
    out = quality.as_datetimes(s)
    assert out.isna().tolist() == [False, False, True, True, False]
    assert out.iloc[1].year == 9999 and out.iloc[4].year == 1700
