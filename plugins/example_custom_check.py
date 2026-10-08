"""Example plugin: flag network lines shorter than 0.1 m.

Copy this file, rename the function and change the stage/logic to add your own check.
Files in this folder are imported at start-up; a file that fails to import is reported
in the log and skipped. A check that raises becomes a warning finding; the run continues.
"""

import shapely

from app.engine.core import check


@check(
    "quality",
    "Example: very short network lines",
    order=200,
    description="Lines shorter than 0.1 m (often digitising slivers)",
)
def short_lines(ctx):
    unit_m = (ctx.state.get("main_crs") or {}).get("unit_m") or 1.0
    limit = 0.1 / unit_m
    total = flagged = 0
    for layer in ctx.dataset.user_layers():
        if not layer.readable or not layer.count or "line" not in (layer.geometry_type or "").lower():
            continue
        if "subnet" in layer.name.lower():
            continue
        gdf = ctx.dataset.read_geometry(layer.name)  # geometry only; cached for other checks
        length = shapely.length(gdf.geometry.values)
        mask = (length > 0) & (length < limit)
        total += len(gdf)
        n = int(mask.sum())
        ctx.log(f"{layer.name}: {n:,} lines shorter than 0.1 m")
        if n:
            flagged += n
            ctx.finding(
                "warning",
                f"{n:,} lines shorter than 0.1 m",
                layer=layer.name,
                count=n,
                sample_ids=list(gdf.index[mask][:25]),
            )
            ctx.flag_features(layer.name, gdf[mask], "warning", "Very short line", max_n=60)
    if total:
        ctx.score(100.0 * (1 - flagged / total), weight=0.5, label="Short-line check")
