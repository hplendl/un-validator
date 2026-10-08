"""CRS and geometry helpers."""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS


@lru_cache(maxsize=256)
def crs_info(wkt: str | None) -> dict:
    """Describe a CRS: horizontal name/EPSG/datum/units and vertical CRS if compound."""
    if not wkt:
        return {"defined": False, "name": None, "horizontal": None, "vertical": None}
    try:
        crs = CRS.from_user_input(wkt)
    except Exception:  # noqa: BLE001
        return {
            "defined": True,
            "name": str(wkt)[:80],
            "horizontal": str(wkt)[:80],
            "vertical": None,
            "epsg": None,
            "datum": None,
            "units": None,
            "unit_m": 1.0,
        }
    horiz, vert = crs, None
    if crs.is_compound:
        subs = crs.sub_crs_list
        horiz = subs[0]
        vert = subs[1] if len(subs) > 1 else None
    elif crs.is_vertical:
        vert, horiz = crs, None
    unit_m = 1.0
    units = None
    try:
        ax = horiz.axis_info[0] if horiz is not None and horiz.axis_info else None
        if ax is not None:
            units = ax.unit_name
            unit_m = float(ax.unit_conversion_factor or 1.0)
            if horiz.is_geographic:
                unit_m = 111320.0  # approx metres per degree, for tolerances only
    except Exception:  # noqa: BLE001
        pass
    epsg = None
    try:
        epsg = horiz.to_epsg(min_confidence=70) if horiz is not None else None
    except Exception:  # noqa: BLE001
        pass
    datum = None
    try:
        datum = horiz.datum.name if horiz is not None and horiz.datum else None
    except Exception:  # noqa: BLE001
        pass
    return {
        "defined": True,
        "name": crs.name,
        "horizontal": horiz.name if horiz is not None else None,
        "vertical": vert.name if vert is not None else None,
        "epsg": epsg,
        "datum": datum,
        "units": units,
        "unit_m": unit_m,
        "geographic": bool(horiz is not None and horiz.is_geographic),
    }


def horizontal_crs(wkt: str | None):
    if not wkt:
        return None
    crs = CRS.from_user_input(wkt)
    if crs.is_compound:
        return crs.sub_crs_list[0]
    return crs


def to_wgs84_features(gdf, layer: str, severity: str, label: str, check: str) -> list[dict]:
    """Reproject a GeoDataFrame sample to WGS84 GeoJSON features (2D)."""
    if gdf is None or len(gdf) == 0 or gdf.crs is None:
        return []
    try:
        g = gdf[[gdf.geometry.name]].copy()
        g = g.set_crs(horizontal_crs(gdf.crs.to_wkt()), allow_override=True)
        g = g[~g.geometry.isna()]
        g[g.geometry.name] = shapely.force_2d(g.geometry.values)
        g = g.to_crs(4326)
    except Exception:  # noqa: BLE001
        return []
    feats = []
    for fid, geom in zip(g.index, g.geometry):
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type in ("LineString", "MultiLineString", "Polygon", "MultiPolygon"):
            geom = shapely.simplify(geom, 0.000005, preserve_topology=True)
        feats.append(
            {
                "type": "Feature",
                "geometry": json.loads(shapely.to_geojson(geom)),
                "properties": {
                    "layer": layer,
                    "oid": int(fid) if np.issubdtype(type(fid), np.integer) or isinstance(fid, int) else str(fid),
                    "severity": severity,
                    "label": label,
                    "check": check,
                },
            }
        )
    return feats
