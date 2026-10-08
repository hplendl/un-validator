"""Stage 4 - asset group and asset type mapping against the Foundation baseline."""

from __future__ import annotations

from ..assets import map_assets
from ..registry import check


@check(
    "schema",
    "Asset type mapping",
    order=90,
    description="Compare asset groups and asset types with the Utility Network Foundation baseline",
)
def asset_type_mapping(ctx) -> None:
    map_assets(ctx)
