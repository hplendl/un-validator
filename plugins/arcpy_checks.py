"""Plugin hook for ArcPy-only checks (runs only where ArcGIS Pro's Python is available).

Any *.py file in this folder is imported at start-up. Checks declared with
``requires=("arcpy",)`` are skipped automatically (shown as "skipped" in the
pipeline) when arcpy cannot be imported, so the open-source build stays portable.
"""

from app.engine.core import check


@check(
    "quality",
    "ArcPy: utility network topology errors",
    order=900,
    description="Uses arcpy to read UN topology errors (requires ArcGIS Pro)",
    requires=("arcpy",),
)
def arcpy_topology_errors(ctx):
    import arcpy  # noqa: F401  (only imported when available)

    # Example (not executed in the open-source build):
    # desc = arcpy.Describe(un_path); arcpy.un.ValidateNetworkTopology(...)
    ctx.log("ArcPy available - add topology / trace checks here")
