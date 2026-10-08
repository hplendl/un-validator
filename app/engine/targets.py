"""Reference schemas: Esri UN Foundation asset packages, and target matching.

The asset packages under ``FOUNDATION_ROOT`` are parsed once per process; the parsed
schemas are also cached on disk (``CACHE_DIR/targets.pickle``, keyed by file sizes and
modification times) so later starts are fast.
"""

from __future__ import annotations

import hashlib
import pickle
import threading
from pathlib import Path

from .. import config
from ..log import get_logger
from .dataset import Dataset, is_system_name

log = get_logger("targets")

_lock = threading.Lock()
_targets: dict[str, dict] | None = None
_CACHE_VERSION = 2

DOMAIN_TOKENS = {
    "stormwater": ["storm"],
    "sewer": ["sewer", "sanitary", "wastewater"],
    "water": ["water"],
    "electric": ["electric", "transmission", "distribution", "power"],
    "gas": ["gas", "pipeline", "updm"],
    "communications": ["communication", "telecom", "fiber"],
    "district_energy": ["districtenergy", "district_energy", "district energy", "steam"],
}


def domain_of(text: str) -> str | None:
    t = text.lower()
    for dom, toks in DOMAIN_TOKENS.items():
        if any(tok in t for tok in toks):
            if dom == "water" and ("storm" in t or "waste" in t or "sewer" in t):
                continue
            return dom
    return None


def _asset_packages(root: Path) -> list[Path]:
    return sorted(root.rglob("*_AssetPackage.gdb")) if root.is_dir() else []


def _fingerprint(gdbs: list[Path]) -> str:
    h = hashlib.sha1(str(_CACHE_VERSION).encode(), usedforsecurity=False)
    for g in gdbs:
        h.update(str(g.resolve()).encode())
        for p in sorted(g.iterdir()):
            if p.is_file():
                st = p.stat()
                h.update(f"{p.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()


def _parse_package(gdb: Path) -> dict:
    ds = Dataset(gdb)
    cat = ds.catalog()
    classes = {k: v for k, v in cat["classes"].items() if not is_system_name(v.name)}
    pkg = gdb.parent.parent.name  # e.g. Water_Utility_Network_Essentials
    return {
        "path": str(gdb),
        "name": gdb.stem,
        "package": pkg,
        "domain": domain_of(gdb.stem) or domain_of(pkg),
        "classes": classes,
        "domains": cat["domains"],
        "crs_wkt": next((lyr.crs_wkt for lyr in ds.layers() if lyr.crs_wkt and not lyr.system), None),
    }


def load_targets(use_disk_cache: bool = True) -> dict[str, dict]:
    """Parse every *_AssetPackage.gdb under FOUNDATION_ROOT once (cached in memory and on disk)."""
    global _targets
    with _lock:
        if _targets is not None:
            return _targets
        gdbs = _asset_packages(Path(config.FOUNDATION_ROOT))
        cache = Path(config.CACHE_DIR) / "targets.pickle"
        fp = _fingerprint(gdbs) if gdbs else ""
        if use_disk_cache and gdbs and cache.is_file():
            try:
                with open(cache, "rb") as f:
                    saved = pickle.load(f)  # our own cache file, written below
                if saved.get("fingerprint") == fp:
                    _targets = saved["targets"]
                    log.info("Loaded %d reference models from cache", len(_targets))
                    return _targets
            except Exception as e:  # noqa: BLE001
                log.warning("ignoring unreadable target cache: %s", e)
        out: dict[str, dict] = {}
        for gdb in gdbs:
            try:
                out[str(gdb)] = _parse_package(gdb)
            except Exception as e:  # noqa: BLE001
                log.warning("could not load reference model %s: %s", gdb.name, e)
        if use_disk_cache and out:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix(".tmp")
                with open(tmp, "wb") as f:
                    pickle.dump({"fingerprint": fp, "targets": out}, f)
                tmp.replace(cache)
            except OSError as e:
                log.warning("could not write target cache: %s", e)
        log.info("Loaded %d reference models from %s", len(out), config.FOUNDATION_ROOT)
        _targets = out
        return out


def reset_targets() -> None:
    """Forget the in-memory reference models (tests, or after changing FOUNDATION_ROOT)."""
    global _targets
    with _lock:
        _targets = None


def path_hint(path: str | Path, parts: int = 3) -> str:
    """The last few components of a path: enough to name the dataset, without parent folders
    (a user's home or project folder name must not influence model matching)."""
    return "/".join(Path(path).parts[-parts:])


def _fields_of(classes) -> set[str]:
    s = set()
    for cd in classes.values():
        for f in cd.fields:
            if f.type not in ("esriFieldTypeOID", "esriFieldTypeGeometry"):
                s.add(f.name.lower())
    return s


def match_target(dataset: Dataset, src_classes: dict) -> tuple[dict | None, list[dict]]:
    """Pick the Foundation asset package that best matches the dataset.

    Score = class-name overlap (x3) + field-name Jaccard + domain keyword bonus.
    The dataset itself is excluded when it *is* an asset package.
    """
    targets = load_targets()
    src_names = {k for k, v in src_classes.items() if not is_system_name(v.name)}
    src_fields = _fields_of({k: v for k, v in src_classes.items() if k in src_names})
    tail = path_hint(dataset.path)
    hint = domain_of(tail)
    ranking = []
    for path, t in targets.items():
        if Path(path).resolve() == Path(dataset.path).resolve():
            continue
        tnames = set(t["classes"].keys())
        cls_overlap = len(src_names & tnames) / max(1, len(src_names | tnames)) if src_names else 0
        tf = _fields_of(t["classes"])
        jac = len(src_fields & tf) / max(1, len(src_fields | tf)) if src_fields else 0
        bonus = 0.5 if hint and t["domain"] == hint else 0.0
        # prefer same "flavour" (essentials/expanded/balanced/unbalanced/transmission) when present in the path
        p = tail.lower()
        for flav in ("unbalanced", "transmission", "combined", "expanded", "essentials"):
            if flav in p and flav in t["name"].lower():
                bonus += 0.15
                break
        if (
            "balanced" in p
            and "unbalanced" not in p
            and "balanced" in t["name"].lower()
            and "unbalanced" not in t["name"].lower()
        ):
            bonus += 0.15
        ranking.append(
            {
                "path": path,
                "name": t["name"],
                "domain": t["domain"],
                "score": round(cls_overlap * 3 + jac + bonus, 3),
                "class_overlap": round(cls_overlap, 3),
                "field_jaccard": round(jac, 3),
            }
        )
    ranking.sort(key=lambda r: -r["score"])
    best = ranking[0] if ranking and ranking[0]["score"] >= 0.25 else None
    return (targets[best["path"]] if best else None), ranking[:5]
