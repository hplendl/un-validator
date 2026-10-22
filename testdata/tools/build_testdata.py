"""Build clean + seeded-error UN Validator test datasets from public Esri sample data (Apache-2.0)."""

import csv
import shutil
import sqlite3
import sys
import uuid
import warnings
from pathlib import Path

import geopandas as gpd
import pyogrio
from shapely.geometry import LineString, MultiLineString, Point

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
SRC = Path(sys.argv[1])  # original Esri Transmission_Naperville_SourceData.gdb
CLEAN_GDB = ROOT / "clean/Transmission_Naperville_SourceData.gdb"
CLEAN_GPKG = ROOT / "clean/Transmission_Naperville.gpkg"
SEED_GDB = ROOT / "seeded/Transmission_seeded.gdb"
SEED_GPKG = ROOT / "seeded/Transmission_seeded.gpkg"
man = []


def rec(eid, ds, layer, etype, desc, expect_check):
    man.append(
        dict(error_id=eid, dataset=ds, layer=layer, error_type=etype, description=desc, expected_check=expect_check)
    )


for p in (CLEAN_GDB, CLEAN_GPKG, SEED_GDB, SEED_GPKG):
    shutil.rmtree(p, ignore_errors=True) if p.suffix == ".gdb" else p.unlink(missing_ok=True)
shutil.copytree(SRC, CLEAN_GDB)
layers = [n for n, _ in pyogrio.list_layers(SRC)]
frames = {n: gpd.read_file(SRC, layer=n) for n in layers}
for n, df in frames.items():
    df.to_file(CLEAN_GPKG, layer=n, driver="GPKG")

# ---------- seeded FGDB: append bad rows, keeping the Esri catalog (domains) intact ----------
shutil.copytree(SRC, SEED_GDB)


def app(layer, rows):
    base = frames[layer]
    df = gpd.GeoDataFrame(rows, geometry="geometry", crs=base.crs)
    pyogrio.write_dataframe(df, SEED_GDB, layer=layer, driver="OpenFileGDB", append=True)


D = "Transmission_seeded.gdb"
pt = frames["PowerTransformer"].iloc[0]
app(
    "PowerTransformer",
    [
        dict(
            assetid="SEED-E01",
            Enabled=1,
            PrimaryVoltage=99999,
            SecondaryVoltage=pt.SecondaryVoltage,
            Rating=pt.Rating,
            subtypecd=pt.subtypecd,
            GlobalID=f"{{{str(uuid.uuid4()).upper()}}}",
            geometry=pt.geometry,
        )
    ],
)
rec(
    "G01",
    D,
    "PowerTransformer",
    "bad_domain_value",
    "assetid SEED-E01: PrimaryVoltage=99999 (not in NominalVoltage coded domain)",
    "Coded values within domains",
)
s = frames["Substation"].iloc[0]
app(
    "Substation",
    [dict(Name="SEED-G02 duplicate", GlobalID=s.GlobalID, geometry=Point(s.geometry.x + 50, s.geometry.y + 50))],
)
rec(
    "G02",
    D,
    "Substation",
    "duplicate_globalid",
    f"Name 'SEED-G02 duplicate' reuses GlobalID {s.GlobalID}",
    "Duplicate GlobalIDs & asset IDs",
)
tw = frames["Tower"].iloc[0]
app(
    "Tower",
    [
        dict(
            assetid="SEED-G03",
            TowerType=tw.TowerType,
            height=tw.height,
            GlobalID=f"{{{str(uuid.uuid4()).upper()}}}",
            geometry=None,
        )
    ],
)
rec("G03", D, "Tower", "null_geometry", "assetid SEED-G03 has no geometry", "Geometry validity")
app(
    "Tower",
    [
        dict(
            assetid=None,
            TowerType=tw.TowerType,
            height=tw.height,
            GlobalID=None,
            geometry=Point(tw.geometry.x + 30, tw.geometry.y + 30, 0),
        )
    ],
)
rec(
    "G04",
    D,
    "Tower",
    "null_required_field",
    "Tower row with GlobalID NULL and assetid NULL",
    "Required fields populated",
)
app(
    "Tower",
    [
        dict(
            assetid=tw.assetid,
            TowerType=tw.TowerType,
            height=tw.height,
            GlobalID=f"{{{str(uuid.uuid4()).upper()}}}",
            geometry=tw.geometry,
        )
    ],
)
rec(
    "G05",
    D,
    "Tower",
    "stacked_point_duplicate_assetid",
    f"Copy of Tower assetid {tw.assetid} at identical XY (stacked point + duplicate asset id)",
    "Geometry validity / Duplicate GlobalIDs & asset IDs",
)
c = frames["Connector"]
x0, y0, x1, y1 = c.total_bounds
orph = MultiLineString([LineString([(x1 + 5000, y1 + 5000, 0), (x1 + 5400, y1 + 5000, 0)])])
cc = c.iloc[0]
app("Connector", [dict(Enabled=1, Voltage=cc.Voltage, GlobalID=f"{{{str(uuid.uuid4()).upper()}}}", geometry=orph)])
rec(
    "G06",
    D,
    "Connector",
    "broken_connectivity",
    "Isolated 400 ft Connector line 5000 ft NE of the network (2 dangles, connects to nothing)",
    "Line connectivity: dangles & near-misses",
)
app(
    "Disconnect",
    [
        dict(
            voltage=frames["Disconnect"].iloc[0].voltage,
            GlobalID=f"{{{str(uuid.uuid4()).upper()}}}",
            geometry=Point(x1 + 8000, y1 + 8000, 0),
        )
    ],
)
rec(
    "G07",
    D,
    "Disconnect",
    "orphan_junction",
    "Disconnect point 8000 ft outside the network with no line touching it",
    "(no point-on-line check expected)",
)
zl = MultiLineString([LineString([(cc.geometry.geoms[0].coords[0]), (cc.geometry.geoms[0].coords[0])])])
app("Connector", [dict(Enabled=1, Voltage=cc.Voltage, GlobalID=f"{{{str(uuid.uuid4()).upper()}}}", geometry=zl)])
rec(
    "G08",
    D,
    "Connector",
    "zero_length_line",
    "Connector with both vertices identical (zero length)",
    "Geometry validity",
)

# ---------- seeded GPKG: rewrite layers with planted errors ----------
D = "Transmission_seeded.gpkg"
for n, df in frames.items():
    df = df.copy()
    crs = df.crs
    if n == "Busbar":
        df = df.drop(columns=["GlobalID"])
        rec(
            "P01",
            D,
            n,
            "missing_required_field",
            "GlobalID column dropped from Busbar",
            "Conformance to reference model / Required fields",
        )
    if n == "HVOverheadConductor":
        crs = "EPSG:4326"
        rec(
            "P02",
            D,
            n,
            "wrong_spatial_reference",
            "Layer tagged EPSG:4326 while coordinates are IL State Plane East ft (EPSG:3435); others stay 3435",
            "CRS & vertical datum consistency",
        )
    if n == "CircuitBreaker":
        df.loc[df.index[:3], "GlobalID"] = df.GlobalID.iloc[3]
        rec(
            "P03",
            D,
            n,
            "duplicate_globalid",
            "First 3 CircuitBreaker rows reuse row 4's GlobalID",
            "Duplicate GlobalIDs & asset IDs",
        )
        df.loc[df.index[4:9], "GlobalID"] = None
        rec(
            "P04",
            D,
            n,
            "null_required_field",
            "CircuitBreaker rows 5-9 have NULL GlobalID",
            "Required fields populated",
        )
    if n == "Tower":
        df.loc[df.index[:4], "geometry"] = None
        rec("P05", D, n, "null_geometry", "First 4 Tower rows have NULL geometry", "Geometry validity")
    if n == "PowerTransformer":
        df.loc[df.index[:5], "PrimaryVoltage"] = 99999
        rec(
            "P06",
            D,
            n,
            "bad_domain_value",
            "PrimaryVoltage=99999 on 5 rows (GeoPackage has no Esri domains)",
            "Coded values within domains (needs domain catalog)",
        )
    if n == "Connector":
        extra = gpd.GeoDataFrame(
            [dict(Enabled=1, Voltage=df.Voltage.iloc[0], GlobalID=f"{{{str(uuid.uuid4()).upper()}}}", geometry=orph)],
            geometry="geometry",
            crs=df.crs,
        )
        df = gpd.GeoDataFrame(__import__("pandas").concat([df, extra], ignore_index=True), crs=df.crs)
        rec(
            "P07",
            D,
            n,
            "broken_connectivity",
            "Isolated Connector line far from network",
            "Line connectivity: dangles & near-misses",
        )
    df = df.set_crs(crs, allow_override=True)
    df.to_file(SEED_GPKG, layer=n, driver="GPKG")
con = sqlite3.connect(SEED_GPKG)
con.executescript("""
CREATE TABLE IF NOT EXISTS gpkg_metadata(id INTEGER PRIMARY KEY AUTOINCREMENT, md_scope TEXT NOT NULL DEFAULT 'dataset', md_standard_uri TEXT NOT NULL, mime_type TEXT NOT NULL DEFAULT 'text/xml', metadata TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS gpkg_metadata_reference(reference_scope TEXT NOT NULL, table_name TEXT, column_name TEXT, row_id_value INTEGER, timestamp DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), md_file_id INTEGER NOT NULL, md_parent_id INTEGER);
INSERT INTO gpkg_metadata(id,md_standard_uri,metadata) VALUES (901,'http://www.isotc211.org/2005/gmd','<metadata><dataIdInfo><idPurp>TBD</idPurp><idAbs>REQUIRED: A brief narrative summary of the data set.</idAbs><searchKeys><keyword>todo</keyword></searchKeys></dataIdInfo></metadata>');
INSERT INTO gpkg_metadata_reference(reference_scope,table_name,md_file_id) VALUES ('table','Substation',901);
INSERT INTO gpkg_metadata(id,md_standard_uri,metadata) VALUES (902,'http://www.isotc211.org/2005/gmd','<metadata><dataIdInfo><idAbs>n/a</idAbs></dataIdInfo></metadata>');
INSERT INTO gpkg_metadata_reference(reference_scope,md_file_id) VALUES ('geopackage',902);
INSERT OR IGNORE INTO gpkg_extensions(table_name,column_name,extension_name,definition,scope) VALUES ('gpkg_metadata',NULL,'gpkg_metadata','http://www.geopackage.org/spec120/#extension_metadata','read-write');
INSERT OR IGNORE INTO gpkg_extensions(table_name,column_name,extension_name,definition,scope) VALUES ('gpkg_metadata_reference',NULL,'gpkg_metadata','http://www.geopackage.org/spec120/#extension_metadata','read-write');
""")
con.commit()
con.close()
rec(
    "P08",
    D,
    "Substation",
    "bad_metadata",
    "Substation metadata is Esri placeholder text ('REQUIRED: ...', purpose 'TBD', keyword 'todo')",
    "Evaluate item metadata",
)
rec(
    "P09",
    D,
    "(workspace)",
    "bad_metadata",
    "GeoPackage-level metadata abstract is just 'n/a'",
    "Evaluate dataset-level metadata",
)
with open(ROOT / "errors_manifest.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(man[0]))
    w.writeheader()
    w.writerows(man)
print(len(man), "errors planted")
