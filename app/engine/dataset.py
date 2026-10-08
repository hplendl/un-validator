"""Open-source, read-only access to File Geodatabases (.gdb) and GeoPackages (.gpkg).

Uses pyogrio (GDAL) for layers and rows, and parses the geodatabase system catalog
(GDB_Items) for field definitions, domains, subtypes and metadata, so no ArcPy is needed.

Reads are column-wise and cached per column within a memory budget (least recently used
columns are dropped first), so checks only load what they need. Very large layers can be
streamed with :meth:`Dataset.iter_batches`.
"""

from __future__ import annotations

import os
import re
import sqlite3
import warnings
from collections import OrderedDict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyogrio

from .. import config
from ..log import get_logger
from .catalog import ClassDef, Domain, FieldDef, parse_class_definition, parse_domain
from .catalog import parse_xml as _parse_xml  # noqa: F401 - re-exported for plugins
from .fgdb_compat import readable_path

log = get_logger("dataset")

warnings.filterwarnings("ignore", message=".*Measured \\(M\\) geometry types are not supported.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pyogrio")
warnings.filterwarnings("ignore", message="Error parsing datetimes")  # handled in the date check

_SYSTEM_RE = [re.compile(p, re.I) for p in config.SYSTEM_TABLE_PATTERNS]
_GEOM = "__geometry__"
_FIDS = "__fid__"

__all__ = [
    "ClassDef",
    "Dataset",
    "Domain",
    "FieldDef",
    "LayerInfo",
    "discover_datasets",
    "is_system_name",
    "short_name",
]


def is_system_name(name: str) -> bool:
    return any(r.search(name) for r in _SYSTEM_RE)


def short_name(name: str) -> str:
    return name.split(".")[-1] if name else name


@dataclass
class LayerInfo:
    name: str
    geometry_type: str | None
    count: int = 0
    fields: list[tuple[str, str]] = field(default_factory=list)
    crs_wkt: str | None = None
    readable: bool = True
    error: str | None = None
    system: bool = False

    @property
    def spatial(self) -> bool:
        return bool(self.geometry_type)

    @property
    def annotation(self) -> bool:
        """Annotation / dimension feature classes (text features, not assets)."""
        names = {n.lower() for n, _ in self.fields}
        return ("annotationclassid" in names and ("textstring" in names or "element" in names)) or {
            "dimtype",
            "dimlength",
        } <= names


def _nbytes(obj: Any) -> int:
    """Rough in-memory size of a cached Series / GeoSeries / Index."""
    try:
        n = len(obj)
        if hasattr(obj, "geom_type") or getattr(getattr(obj, "dtype", None), "name", "") == "geometry":
            import shapely

            coords = int(shapely.get_num_coordinates(np.asarray(obj.values)).sum())
            return 100 * n + 24 * coords
        base = int(obj.memory_usage(index=True, deep=False)) if hasattr(obj, "memory_usage") else 8 * n
        if getattr(obj, "dtype", None) is not None and obj.dtype == object:
            base += 48 * n
        return base
    except Exception:  # noqa: BLE001
        return 0


class _LRUCache:
    """Byte-budgeted least-recently-used cache."""

    def __init__(self, budget_bytes: int):
        self.budget = max(0, int(budget_bytes))
        self._d: OrderedDict[Any, tuple[Any, int]] = OrderedDict()
        self.size = 0

    def get(self, key: Any) -> Any:
        hit = self._d.get(key)
        if hit is None:
            return None
        self._d.move_to_end(key)
        return hit[0]

    def __contains__(self, key: Any) -> bool:
        return key in self._d

    def put(self, key: Any, value: Any) -> None:
        nb = _nbytes(value)
        if key in self._d:
            self.size -= self._d.pop(key)[1]
        self._d[key] = (value, nb)
        self.size += nb
        while self.size > self.budget and len(self._d) > 1:
            _, (_, b) = self._d.popitem(last=False)
            self.size -= b

    def clear(self) -> None:
        self._d.clear()
        self.size = 0


class Dataset:
    """A geodatabase or GeoPackage opened read-only."""

    def __init__(self, path: str | Path, log: Callable[[str], None] | None = None, cache_mb: int | None = None):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"{self.path.name} does not exist")
        self.name = self.path.name
        self.kind = "gpkg" if self.path.suffix.lower() == ".gpkg" else "fgdb"
        self.log = log or (lambda *a, **k: None)
        self.read_path = str(self.path)
        self.patched_tables: dict[str, list[int]] = {}
        if self.kind == "fgdb":
            self.read_path, self.patched_tables = readable_path(
                self.path, Path(config.CACHE_DIR) / "fgdb_compat", log=self.log
            )
        try:
            pyogrio.list_layers(self.read_path)  # fail fast if GDAL cannot open it at all
        except Exception as e:
            raise ValueError(f"{self.name} is not a readable geodatabase or GeoPackage ({e})") from e
        self._layers: list[LayerInfo] | None = None
        self._catalog: dict | None = None
        self._cache = _LRUCache((cache_mb if cache_mb is not None else config.READ_CACHE_MB) * 1024 * 1024)

    # ---------------- layers ----------------
    def layers(self, include_system_tables: bool = True) -> list[LayerInfo]:
        if self._layers is None:
            out = []
            for name, gtype in pyogrio.list_layers(self.read_path):
                li = LayerInfo(name=name, geometry_type=gtype, system=is_system_name(name))
                try:
                    info = pyogrio.read_info(self.read_path, layer=name, force_feature_count=True)
                    li.count = int(info.get("features") or 0)
                    li.fields = list(zip(list(info.get("fields", [])), [str(d) for d in info.get("dtypes", [])]))
                    li.crs_wkt = info.get("crs")
                    if not li.fields and li.count == 0 and not li.system:
                        # GDAL could open the layer but not its schema
                        li.readable = False
                        li.error = "GDAL could not decode this table's field header"
                except Exception as e:  # noqa: BLE001
                    li.readable = False
                    li.error = str(e)[:300]
                out.append(li)
            self._layers = out
        return self._layers if include_system_tables else [lyr for lyr in self._layers if not lyr.system]

    def layer(self, name: str) -> LayerInfo | None:
        for lyr in self.layers():
            if lyr.name.lower() == name.lower():
                return lyr
        return None

    def user_layers(self) -> list[LayerInfo]:
        return [lyr for lyr in self.layers() if not lyr.system]

    def _is_spatial(self, layer: str) -> bool:
        li = self.layer(layer)
        return bool(li and li.spatial)

    def _crs(self, layer: str) -> str | None:
        li = self.layer(layer)
        return li.crs_wkt if li else None

    # ---------------- reading ----------------
    def _pyogrio(self, layer: str, columns: Sequence[str] | None, geometry: bool, list_all: bool = False, **kw):
        if list_all:
            kw["LIST_ALL_TABLES"] = "YES"
        return pyogrio.read_dataframe(
            self.read_path,
            layer=layer,
            columns=None if columns is None else list(columns),
            read_geometry=geometry,
            fid_as_index=True,
            **kw,
        )

    def read_columns(self, layer: str, columns: Sequence[str] | None = None, list_all: bool = False) -> pd.DataFrame:
        """Attribute columns (no geometry), indexed by feature ID. ``None`` means all columns."""
        if columns is None:
            li = self.layer(layer)
            if li is None or list_all:
                key = (layer, "__all__", list_all)
                df = self._cache.get(key)
                if df is None:
                    df = self._pyogrio(layer, None, False, list_all=list_all)
                    self._cache.put(key, df)
                return df
            columns = [n for n, _ in li.fields]
        cols = list(dict.fromkeys(columns))
        if not cols:
            return pd.DataFrame(index=self.fids(layer))
        cached = [self._cache.get((layer, c)) for c in cols]
        if all(s is not None for s in cached):
            return pd.DataFrame(dict(zip(cols, cached)))
        df = self._pyogrio(layer, cols, False, list_all=list_all)
        for c in cols:
            if c in df.columns:
                self._cache.put((layer, c), df[c])
        if (layer, _FIDS) not in self._cache:
            self._cache.put((layer, _FIDS), df.index)
        return df[[c for c in cols if c in df.columns]]

    def fids(self, layer: str) -> pd.Index:
        """Feature IDs of a layer, in storage order."""
        idx = self._cache.get((layer, _FIDS))
        if idx is None:
            idx = self._pyogrio(layer, [], False).index
            self._cache.put((layer, _FIDS), idx)
        return idx

    def read_geometry(self, layer: str):
        """GeoDataFrame with only the geometry column (fid index, layer CRS)."""
        g = self._cache.get((layer, _GEOM))
        if g is None:
            g = self._pyogrio(layer, [], True)
            self._cache.put((layer, _GEOM), g)
        return g

    def read(self, layer: str, columns: Sequence[str] | None = None, geometry: bool = True, list_all: bool = False):
        """Attributes (all, or *columns*) plus geometry for spatial layers. Prefer the narrower readers."""
        df = self.read_columns(layer, columns, list_all=list_all)
        if not geometry or not self._is_spatial(layer):
            return df
        import geopandas as gpd

        g = self.read_geometry(layer)
        if not g.index.equals(df.index):
            g = g.reindex(df.index)
        return gpd.GeoDataFrame(df, geometry=g.geometry.values, crs=g.crs, index=df.index)

    def read_fids(self, layer: str, fids: Sequence[int]):
        """Geometries of specific features (in the requested order)."""
        fids = [int(f) for f in fids]
        g = self._cache.get((layer, _GEOM))
        if g is not None:
            return g.loc[[f for f in fids if f in g.index]]
        if not fids:
            import geopandas as gpd

            return gpd.GeoDataFrame(geometry=[], crs=self._crs(layer))
        return self._pyogrio(layer, [], True, fids=np.asarray(fids, dtype=np.int64))

    def iter_batches(
        self, layer: str, columns: Sequence[str] | None = None, geometry: bool = True, batch_rows: int | None = None
    ) -> Iterator[pd.DataFrame]:
        """Yield the layer in row batches. Small or already-cached layers come back in one piece."""
        batch_rows = batch_rows or config.CHUNK_ROWS
        li = self.layer(layer)
        count = li.count if li else 0
        cached = (not geometry or (layer, _GEOM) in self._cache) and all(
            (layer, c) in self._cache for c in (columns or [])
        )
        if count <= batch_rows or (columns is not None and cached):
            yield self.read(layer, columns=columns, geometry=geometry)
            return
        geometry = geometry and bool(li and li.spatial)
        for skip in range(0, count, batch_rows):
            yield self._pyogrio(layer, columns, geometry, skip_features=skip, max_features=batch_rows)

    def read_system_table(self, name: str) -> pd.DataFrame | None:
        try:
            return self.read_columns(name, None, list_all=True)
        except Exception:  # noqa: BLE001
            return None

    def release_cache(self) -> None:
        self._cache.clear()

    # ---------------- catalog ----------------
    def catalog(self) -> dict:
        """Parsed system catalog: classes, domains, workspace documentation, item docs."""
        if self._catalog is not None:
            return self._catalog
        cat: dict[str, Any] = {
            "classes": {},
            "domains": {},
            "workspace_doc": None,
            "items": [],
            "feature_datasets": [],
            "utility_networks": [],
        }
        if self.kind == "fgdb":
            items = self.read_system_table("GDB_Items")
            types = self.read_system_table("GDB_ItemTypes")
            if items is not None and types is not None:
                tmap = dict(zip(types["UUID"], types["Name"]))
                for r in items.to_dict("records"):
                    tname = tmap.get(r.get("Type"), str(r.get("Type")))
                    nm = short_name(r.get("Name") or "")
                    doc = r.get("Documentation") if isinstance(r.get("Documentation"), str) else None
                    cat["items"].append({"name": nm, "type": tname, "path": r.get("Path"), "documentation": doc})
                    if tname in ("Feature Class", "Table"):
                        cat["classes"][nm.lower()] = parse_class_definition(
                            nm, tname, r.get("Definition"), r.get("Path"), doc
                        )
                    elif tname in ("Coded Value Domain", "Range Domain"):
                        d = parse_domain(r.get("Definition"))
                        if d:
                            cat["domains"][d.name] = d
                    elif tname == "Workspace":
                        cat["workspace_doc"] = doc
                    elif tname == "Feature Dataset":
                        cat["feature_datasets"].append(nm)
                    elif tname == "Utility Network":
                        cat["utility_networks"].append(nm)
        else:
            cat.update(self._gpkg_catalog())
        self._catalog = cat
        return cat

    def _gpkg_catalog(self) -> dict:
        out: dict[str, Any] = {"classes": {}, "domains": {}, "workspace_doc": None, "items": []}
        # A file: URI (percent-encoded, works for Windows drive letters and odd characters), read-only.
        con = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            docs = {}
            try:
                q = (
                    "SELECT r.table_name, m.metadata, r.reference_scope FROM gpkg_metadata m "
                    "JOIN gpkg_metadata_reference r ON r.md_file_id = m.id"
                )
                for tname, md, scope in con.execute(q):
                    if scope == "geopackage":
                        out["workspace_doc"] = md
                    elif tname:
                        docs[tname.lower()] = md
            except sqlite3.Error:
                pass
            for li in self.layers():
                if li.system:
                    continue
                cd = ClassDef(
                    name=li.name,
                    kind="Feature Class" if li.spatial else "Table",
                    fields=[FieldDef(name=n, type=t) for n, t in li.fields],
                    documentation=docs.get(li.name.lower()),
                )
                if li.geometry_type:
                    cd.has_z = " Z" in li.geometry_type or li.geometry_type.endswith("Z")
                out["classes"][li.name.lower()] = cd
                out["items"].append(
                    {"name": li.name, "type": cd.kind, "path": li.name, "documentation": cd.documentation}
                )
        finally:
            con.close()
        return out

    def class_def(self, name: str) -> ClassDef | None:
        return self.catalog()["classes"].get(name.lower())

    def domains(self) -> dict[str, Domain]:
        return self.catalog()["domains"]


def _is_fgdb_dir(p: Path) -> bool:
    return (p / "gdb").exists() or (p / "a00000001.gdbtable").exists()


def discover_datasets(root: str | Path) -> list[Path]:
    """Find .gdb folders and .gpkg files under *root* (hidden folders are skipped)."""
    root = Path(root)
    if root.suffix.lower() == ".gpkg" and root.is_file():
        return [root]
    if root.suffix.lower() == ".gdb" and root.is_dir() and _is_fgdb_dir(root):
        return [root]  # (a folder merely *named* .gdb, e.g. an unzipped download, is searched instead)
    if not root.is_dir():
        return []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        keep = []
        for d in dirnames:
            if d.startswith("."):
                continue
            if d.lower().endswith(".gdb") and _is_fgdb_dir(here / d):
                found.append(here / d)
                continue  # never descend into a geodatabase folder
            keep.append(d)
        dirnames[:] = keep
        found.extend(here / f for f in filenames if f.lower().endswith(".gpkg"))
    out = []
    for p in sorted(found):
        if p.suffix.lower() == ".gdb":
            try:
                if not pyogrio.list_layers(str(p)).size:
                    continue  # empty project default geodatabase
            except Exception:  # noqa: BLE001
                continue
        out.append(p)
    return out
