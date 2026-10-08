"""Rebuild app/data/foundation_assets.csv and .json from Foundation asset packages.

    python tools/build_foundation_assets.py /path/to/foundations

The folder is searched for *_AssetPackage.gdb. The output is a name/code table only.
It is derived from the public Esri Utility Network Foundation models (Apache-2.0);
see DATA_SOURCES.md. This script does not copy geodatabases into the repository.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.dataset import Dataset, is_system_name  # noqa: E402
from app.engine.targets import domain_of  # noqa: E402

COLUMNS = [
    "domain",
    "package",
    "class_name",
    "asset_group_code",
    "asset_group",
    "asset_type_code",
    "asset_type",
]


def _code(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def extract(root: Path) -> list[dict]:
    rows: list[dict] = []
    for gdb in sorted(root.rglob("*_AssetPackage.gdb")):
        ds = Dataset(gdb)
        try:
            cat = ds.catalog()
        finally:
            ds.release_cache()
        package = gdb.parent.parent.name
        domain = domain_of(gdb.stem) or domain_of(package) or "unknown"
        for cd in cat["classes"].values():
            if is_system_name(cd.name) or (cd.subtype_field or "").lower() != "assetgroup":
                continue
            for code, subtype in cd.subtypes.items():
                group = str(subtype.get("name") or code)
                domain_name = (subtype.get("domains") or {}).get("assettype")
                coded = cat["domains"].get(domain_name) if domain_name else None
                codes = coded.codes if coded is not None and coded.kind == "coded" else {}
                if not codes:
                    rows.append(
                        {
                            "domain": domain,
                            "package": package,
                            "class_name": cd.name,
                            "asset_group_code": _code(code),
                            "asset_group": group,
                            "asset_type_code": "",
                            "asset_type": "",
                        }
                    )
                    continue
                for type_code, type_name in codes.items():
                    rows.append(
                        {
                            "domain": domain,
                            "package": package,
                            "class_name": cd.name,
                            "asset_group_code": _code(code),
                            "asset_group": group,
                            "asset_type_code": _code(type_code),
                            "asset_type": str(type_name),
                        }
                    )
    best: dict[tuple, dict] = {}
    for row in rows:
        key = (
            row["domain"],
            row["class_name"].lower(),
            row["asset_group"].lower(),
            row["asset_type"].lower(),
        )
        current = best.get(key)
        if current is None or ("essential" in row["package"].lower() and "essential" not in current["package"].lower()):
            best[key] = row
    return sorted(
        best.values(),
        key=lambda r: (r["domain"], r["class_name"].lower(), r["asset_group"].lower(), r["asset_type"].lower()),
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python tools/build_foundation_assets.py FOUNDATION_ROOT")
        return 2
    root = Path(sys.argv[1])
    if not root.is_dir():
        print(f"Not a folder: {root.name}")
        return 2
    rows = extract(root)
    out_dir = ROOT / "app" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "meta": {
            "source": "Esri Utility Network Foundation asset packages",
            "license": "Apache-2.0",
            "derived": "Asset group and asset type names and codes. Not the source geodatabases.",
            "credit": "Copyright Esri. Licensed under Apache-2.0. See DATA_SOURCES.md.",
            "rows": len(rows),
        },
        "rows": rows,
    }
    (out_dir / "foundation_assets.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    with (out_dir / "foundation_assets.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
