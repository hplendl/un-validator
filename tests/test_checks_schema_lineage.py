"""Discover, metadata, lineage and schema checks."""

from app.engine.catalog import ClassDef, Domain, FieldDef
from app.engine.checks import discover, lineage, metadata, schema

from .conftest import findings, run


def test_inventory_summary(ctx):
    run(ctx, discover.inventory, "discover")
    s = ctx.state["summary"]
    assert s["layers"] == 5 and s["feature_classes"] == 4 and s["tables"] == 1
    assert s["features"] == 6 + 3 + 3 + 2
    assert {r["name"] for r in ctx.tables["layers"]} >= {"WaterLine", "Inspections"}


def test_catalog_and_reference_without_foundations(ctx):
    run(ctx, discover.inventory, "discover")
    run(ctx, discover.catalog, "discover")
    run(ctx, discover.match_reference, "discover", "ref")
    assert ctx.state["target"] is None
    assert findings(ctx, "ref", "info")


def test_item_metadata_missing_partial_and_placeholder(ctx):
    run(ctx, metadata.item_metadata, "metadata", "meta")
    rows = {r["item"]: r for r in ctx.tables["metadata_items"]}
    assert rows["WaterLine"]["score"] == 100.0
    assert rows["ServiceArea"]["summary"] == "placeholder"
    assert not rows["WaterDevice"]["has_xml"]
    msgs = [f.message for f in findings(ctx, "meta")]
    assert any(m.startswith("No metadata document on 2 of 4 feature classes") for m in msgs)
    assert any("placeholder" in m for m in msgs)


def test_workspace_metadata_partial(ctx):
    run(ctx, metadata.workspace_metadata, "metadata", "ws")
    f = findings(ctx, "ws")
    assert f and "lacks" in f[0].message and "Tags" in f[0].message
    assert 0 < ctx.scores["metadata"][0][0] < 100


def test_workspace_metadata_missing(make_ctx, data_root):
    c = run(make_ctx(data_root / "network" / "copy" / "net2.gpkg"), metadata.workspace_metadata, "metadata", "ws")
    assert findings(c, "ws", "warning") and c.scores["metadata"][0][0] == 0


def test_crs_mixed(ctx):
    run(ctx, lineage.crs_consistency, "lineage", "crs")
    f = [x for x in findings(ctx, "crs") if "Mixed horizontal CRS" in x.message]
    assert f and f[0].severity == "error"
    assert ctx.state["main_crs"]["epsg"] == 26916


def test_provenance_without_editor_tracking(ctx):
    run(ctx, lineage.provenance, "lineage", "prov")
    assert findings(ctx, "prov", "info") and ctx.scores["lineage"][0][0] == 0


def _inject_domains(ctx):
    """The GeoPackage has no Esri domains, so give the dataset a catalog that declares some."""
    cat = ctx.dataset.catalog()
    cd = cat["classes"]["waterline"]
    for f in cd.fields:
        if f.name == "ASSETTYPE":
            f.domain = "AssetTypeDomain"
        if f.name == "ISCONNECTED":
            f.domain = "IsConnected"
    cat["domains"]["AssetTypeDomain"] = Domain("AssetTypeDomain", "coded", "esriFieldTypeInteger", {2: "Main"})
    cat["domains"]["IsConnected"] = Domain(
        "IsConnected", "coded", "esriFieldTypeInteger", {1: "Connected", 2: "Disconnected"}
    )
    cat["domains"]["Unused"] = Domain("Unused", "range", "esriFieldTypeDouble", min=0, max=1)
    cat["classes"]["servicearea"].fields.append(FieldDef("NAME", "esriFieldTypeString", domain="Missing"))


def test_domain_integrity(ctx):
    _inject_domains(ctx)
    run(ctx, schema.domain_integrity, "schema", "dom")
    msgs = [f.message for f in findings(ctx, "dom")]
    assert any("point to domains that do not exist" in m for m in msgs)
    assert any("defined but not used" in m for m in msgs)


def test_coded_value_violations(ctx):
    _inject_domains(ctx)
    run(ctx, schema.coded_values, "schema", "cv")
    f = [x for x in findings(ctx, "cv") if "ASSETTYPE" in x.message]
    assert f and f[0].count == 6 and "Invalid values: 1" in f[0].detail
    assert not [x for x in findings(ctx, "cv") if "ISCONNECTED" in x.message]
    assert ctx.tables["domain_violations"][0]["field"] == "ASSETTYPE"
    assert ctx.scores["schema"][0][0] < 100


def test_subtype_specific_domains():
    cd = ClassDef(
        "Device",
        "Feature Class",
        fields=[
            FieldDef("ASSETGROUP", "esriFieldTypeInteger"),
            FieldDef("ASSETTYPE", "esriFieldTypeInteger", domain="D0"),
        ],
        subtype_field="ASSETGROUP",
        subtypes={1: {"name": "Valve", "domains": {"assettype": "D1"}}, 2: {"name": "Pump", "domains": {}}},
    )
    assert schema._domain_for_rows(cd, "assettype") == [(1, "D1"), (2, "D0")]


def test_reference_conformance_skips_without_target(ctx):
    run(ctx, schema.reference_conformance, "schema", "conf")
    assert not ctx.findings


def _target(classes):
    return {"name": "Test_AssetPackage", "classes": classes, "domains": {}, "crs_wkt": None}


def test_field_mapping_coverage(make_ctx, tmp_path, gpkg_path):
    import shutil

    import pandas as pd

    from app.engine.catalog import ClassDef, FieldDef

    folder = tmp_path / "migration"
    folder.mkdir()
    shutil.copy(gpkg_path, folder / "src.gpkg")
    pd.DataFrame(
        {
            "type": ["field", "field", "value"],
            "match_strings": ["exact_match", "exact_match ", "exact_match"],
            "SubstringsA": ["ASSETID", "NOTES", "x"],
            "SubstringsB": ["assetid", "nosuchfield", "y"],
        }
    ).to_csv(folder / "Water_MatchTable.csv", index=False)
    pd.DataFrame(
        {
            "Source Class": ["WaterLine", "WaterLine", "Ghost"],
            "Target Class": ["WaterLine", "WaterLine", "Missing"],
            "Source Row Count": [3, 4, 1],
        }
    ).to_excel(folder / "DataMapping.xlsx", sheet_name="Data Mappings", index=False)
    c = make_ctx(folder / "src.gpkg")
    c.state["target"] = _target(
        {"waterline": ClassDef("WaterLine", "Feature Class", fields=[FieldDef("ASSETID", "esriFieldTypeString")])}
    )
    run(c, lineage.mapping_coverage, "lineage", "map")
    msgs = [f.message for f in findings(c, "map")]
    assert any("stray whitespace" in m for m in msgs)
    assert any("maps to 1 fields that do not exist" in m for m in msgs)
    assert any("source class 'Ghost'" in m for m in msgs)
    assert any("expects 7 rows" in m for m in msgs)
    assert any("targets 1 classes missing" in m for m in msgs)
    rows = {r["source_class"]: r for r in c.tables["mapping_coverage"]}
    assert rows["WaterLine"]["mapped"] >= 1
    assert c.scores["lineage"] and "mapping_coverage" in c.state["summary"]


def test_mapping_coverage_skipped_without_files(ctx):
    run(ctx, lineage.mapping_coverage, "lineage", "map")
    assert not ctx.findings and not ctx.scores["lineage"]


def test_crs_vs_reference(ctx):
    from pyproj import CRS

    run(ctx, lineage.crs_consistency, "lineage", "crs")
    ctx.state["target"] = {"name": "T", "classes": {}, "crs_wkt": CRS.from_epsg(3857).to_wkt()}
    run(ctx, lineage.crs_vs_reference, "lineage", "ref")
    assert findings(ctx, "ref") and ctx.scores["lineage"][-1][0] < 100


def test_reference_conformance_with_target(ctx):
    from app.engine.catalog import ClassDef, FieldDef

    ctx.state["target"] = _target(
        {
            "waterline": ClassDef(
                "WaterLine",
                "Feature Class",
                fields=[
                    FieldDef("ASSETID", "esriFieldTypeString"),
                    FieldDef("ASSETGROUP", "esriFieldTypeInteger", nullable=False),
                    FieldDef("DIAMETER", "esriFieldTypeDouble"),
                    FieldDef("ISCONNECTED", "esriFieldTypeString"),
                ],
            ),
            "pump": ClassDef("Pump", "Feature Class", fields=[FieldDef("X", "esriFieldTypeInteger")]),
        }
    )
    run(ctx, schema.reference_conformance, "schema", "conf")
    msgs = [f.message for f in findings(ctx, "conf")]
    assert any("is missing 1 fields" in m for m in msgs)  # DIAMETER
    assert any("differ in data type" in m for m in msgs)  # ISCONNECTED int vs text
    assert any("reference feature classes are not present" in m for m in msgs)
    assert ctx.tables["conformance"]
