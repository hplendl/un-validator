"""Small synthetic datasets generated at test time (no sample data needed).

``make_gpkg`` writes a GeoPackage with known defects:

* WaterLine: duplicate GLOBALID, null GLOBALID / ASSETGROUP, duplicate ASSETID, a zero-length
  (invalid) line, an empty geometry, a near-miss and free dangles, a future and a pre-1850
  date, one ISCONNECTED = 2 feature.
* WaterDevice: two stacked points.
* ServiceArea: a self-intersecting (bow-tie) polygon and a zero-area polygon.
* Hydrant: stored in EPSG:4326 while everything else is EPSG:26916 (mixed CRS).
* Inspections: a plain table.
* Metadata: a full ArcGIS document on WaterLine, a placeholder document on ServiceArea,
  a partial dataset-level document, nothing on the other items.

``make_fgdb`` writes a tiny File Geodatabase with the OpenFileGDB driver (GDAL >= 3.6).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
from shapely.geometry import LineString, Point, Polygon

UTM = "EPSG:26916"

FULL_ARCGIS_DOC = """<?xml version="1.0"?>
<metadata><Esri><ArcGISFormat>1.0</ArcGISFormat></Esri>
<dataIdInfo>
  <idPurp>Water mains of the synthetic test network.</idPurp>
  <idAbs>Every pressurised water main, digitised from as-built drawings.</idAbs>
  <searchKeys><keyword>water</keyword><keyword>mains</keyword></searchKeys>
  <idCredit>Test fixture</idCredit>
  <resConst><Consts><useLimit>Synthetic data, no restrictions.</useLimit></Consts></resConst>
  <idPoC><rpOrgName>Example Utility</rpOrgName></idPoC>
  <dataExt><geoEle><GeoBndBox><westBL>-87.7</westBL><eastBL>-87.6</eastBL><southBL>41.8</southBL><northBL>41.9</northBL></GeoBndBox></geoEle></dataExt>
</dataIdInfo>
<dqInfo><dataLineage><statement>Digitised from as-built drawings in 2020.</statement></dataLineage></dqInfo>
</metadata>"""

PLACEHOLDER_DOC = """<metadata><Esri><ArcGISFormat>1.0</ArcGISFormat></Esri>
<dataIdInfo><idPurp>REQUIRED: A brief summary.</idPurp><idAbs>There is no description for this item.</idAbs></dataIdInfo></metadata>"""

PARTIAL_WORKSPACE_DOC = """<metadata><dataIdInfo><idPurp>Synthetic water network.</idPurp>
<idAbs>Test GeoPackage for the validator's unit tests.</idAbs></dataIdInfo></metadata>"""


def _gid(i: int) -> str:
    return f"{{{i:08X}-0000-4000-8000-{i:012X}}}"


def water_lines() -> gpd.GeoDataFrame:
    geoms = [
        LineString([(0, 0), (100, 0)]),  # 1  start free (point WaterDevice sits there), end joins 2
        LineString([(100, 0), (100, 100)]),  # 2
        LineString([(100, 100.2), (200, 100.2)]),  # 3  starts 0.2 m from line 2's end: near-miss
        LineString([(500, 500), (600, 500)]),  # 4  isolated: two free dangles
        LineString([(50, 50), (50, 50)]),  # 5  zero length (and invalid: too few points)
        None,  # 6  empty geometry
    ]
    n = len(geoms)
    gids = [_gid(1), _gid(1), _gid(3), _gid(4), None, _gid(6)]  # 1 and 2 duplicate, 5 null
    return gpd.GeoDataFrame(
        {
            "GLOBALID": gids,
            "ASSETGROUP": pd.array([1, 1, None, 1, 1, 1], dtype="Int32"),
            "ASSETTYPE": pd.array([1] * n, dtype="Int32"),
            "ASSETID": ["A-1", "A-2", "A-3", "A-1", "A-5", "A-6"],  # A-1 duplicated
            "INSTALLDATE": pd.array(
                [datetime(2999, 1, 1), datetime(1800, 1, 1)] + [datetime(2010, 5, 1)] * (n - 2),
                dtype="datetime64[ms]",
            ),
            "ISCONNECTED": pd.array([1, 1, 1, 2, 1, 1], dtype="Int32"),
        },
        geometry=geoms,
        crs=UTM,
    )


def water_devices() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "GLOBALID": [_gid(101), _gid(102), _gid(103)],
            "ASSETGROUP": pd.array([1, 2, 1], dtype="Int32"),
            "ASSETTYPE": pd.array([1, 1, 1], dtype="Int32"),
        },
        geometry=[Point(0, 0), Point(0, 0), Point(100, 0)],  # first two are stacked
        crs=UTM,
    )


def service_areas() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"GLOBALID": [_gid(201), _gid(202), _gid(203)], "NAME": ["bowtie", "flat", "ok"]},
        geometry=[
            Polygon([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)]),  # self-intersection
            Polygon([(0, 0), (1, 0), (2, 0), (0, 0)]),  # zero area
            Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]),
        ],
        crs=UTM,
    )


def hydrants() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"GLOBALID": [_gid(301), _gid(302)]},
        geometry=[Point(-87.65, 41.85), Point(-87.651, 41.851)],
        crs="EPSG:4326",
    )


def inspections() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "GLOBALID": [_gid(401), _gid(402)],
            "NOTES": ["ok", '=HYPERLINK("http://example.invalid")'],  # CSV-injection probe
        }
    )


GPKG_LAYERS = ("WaterLine", "WaterDevice", "ServiceArea", "Hydrant", "Inspections")


def _write(gdf, path: Path, layer: str, **kw) -> None:
    pyogrio.write_dataframe(gdf, path, layer=layer, driver=kw.pop("driver", "GPKG"), append=path.exists(), **kw)


def add_gpkg_metadata(path: Path, workspace_doc: str | None, item_docs: dict[str, str]) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS gpkg_metadata (
              id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, md_scope TEXT NOT NULL DEFAULT 'dataset',
              md_standard_uri TEXT NOT NULL, mime_type TEXT NOT NULL DEFAULT 'text/xml',
              metadata TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS gpkg_metadata_reference (
              reference_scope TEXT NOT NULL, table_name TEXT, column_name TEXT, row_id_value INTEGER,
              timestamp DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
              md_file_id INTEGER NOT NULL, md_parent_id INTEGER);
            """
        )

        def add(scope: str, table: str | None, doc: str) -> None:
            cur = con.execute(
                "INSERT INTO gpkg_metadata (md_scope, md_standard_uri, metadata) VALUES ('dataset', 'http://www.isotc211.org/2005/gmd', ?)",
                (doc,),
            )
            con.execute(
                "INSERT INTO gpkg_metadata_reference (reference_scope, table_name, md_file_id) VALUES (?, ?, ?)",
                (scope, table, cur.lastrowid),
            )

        if workspace_doc:
            add("geopackage", None, workspace_doc)
        for table, doc in item_docs.items():
            add("table", table, doc)
        con.commit()
    finally:
        con.close()


def make_gpkg(path: Path, metadata: bool = True) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(water_lines(), path, "WaterLine")
    _write(water_devices(), path, "WaterDevice")
    _write(service_areas(), path, "ServiceArea")
    _write(hydrants(), path, "Hydrant")
    _write(inspections(), path, "Inspections")
    if metadata:
        add_gpkg_metadata(path, PARTIAL_WORKSPACE_DOC, {"WaterLine": FULL_ARCGIS_DOC, "ServiceArea": PLACEHOLDER_DOC})
    return path


def make_big_lines(path: Path, n: int = 50) -> Path:
    """A layer with *n* short lines (used to exercise batched reads with a tiny batch size)."""
    xs = np.arange(n) * 10.0
    gdf = gpd.GeoDataFrame(
        {
            "GLOBALID": [_gid(1000 + i) for i in range(n)],
            "ASSETGROUP": pd.array([None if i % 7 == 0 else 1 for i in range(n)], dtype="Int32"),
            "INSTALLDATE": pd.array(
                [datetime(2999, 1, 1) if i % 9 == 0 else datetime(2010, 1, 1) for i in range(n)],
                dtype="datetime64[ms]",
            ),
        },
        geometry=[None if i % 11 == 0 else LineString([(x, 0), (x + 5, 0)]) for i, x in enumerate(xs)],
        crs=UTM,
    )
    _write(gdf, Path(path), "Lines")
    return Path(path)


def can_write_fgdb() -> bool:
    caps = pyogrio.list_drivers().get("OpenFileGDB", "")
    return "w" in caps


def make_fgdb(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = water_lines()
    _write(lines, path, "WaterLine", driver="OpenFileGDB", layer_options={"DOCUMENTATION": FULL_ARCGIS_DOC})
    _write(water_devices(), path, "WaterDevice", driver="OpenFileGDB")
    return path
