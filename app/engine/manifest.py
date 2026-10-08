"""Run manifest and a deterministic result signature.

The signature is a SHA-256 of the scores, layer counts and findings. It deliberately
omits timestamps, file paths, durations and dispositions, so the same inputs and the
same rules reproduce the same signature. The manifest around it records everything a
person needs to replay the run, including input sizes and hashes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .. import config


def hash_input(path: str | Path) -> dict:
    """Size and SHA-256 of a dataset file, or of every file inside a geodatabase folder."""
    p = Path(path)
    h = hashlib.sha256()
    total = 0
    nfiles = 0
    if p.is_file():
        total = p.stat().st_size
        nfiles = 1
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    elif p.is_dir():
        files = [f for f in p.rglob("*") if f.is_file() and not f.name.endswith(".lock")]
        for f in sorted(files, key=lambda x: x.relative_to(p).as_posix()):
            rel = f.relative_to(p).as_posix()
            size = f.stat().st_size
            total += size
            nfiles += 1
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            h.update(str(size).encode("ascii"))
            h.update(b"\0")
            with f.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
            h.update(b"\0")
    return {"name": p.name, "bytes": total, "files": nfiles, "sha256": h.hexdigest()}


def _finding_view(f: dict) -> dict:
    return {
        "id": f.get("finding_id") or "",
        "severity": f.get("severity"),
        "stage": f.get("stage"),
        "check": f.get("check"),
        "layer": f.get("layer") or "",
        "rule": f.get("rule") or "",
        "classification": f.get("classification") or "",
        "confidence": f.get("confidence"),
        "count": f.get("count"),
        "affected_count": f.get("affected_count"),
        "message": f.get("message") or "",
        "recommended_action": f.get("recommended_action") or "",
    }


def signature_payload(result: dict) -> dict:
    """The only content the signature depends on. No timestamps and no paths."""
    datasets = []
    for ds in result.get("datasets") or []:
        layers = [
            {"name": row.get("name"), "count": row.get("count")}
            for row in ((ds.get("tables") or {}).get("layers") or [])
            if not row.get("system")
        ]
        layers.sort(key=lambda r: (str(r["name"]), str(r["count"])))
        findings = [_finding_view(f) for f in ds.get("findings") or []]
        findings.sort(key=lambda f: (f["id"], f["check"], f["layer"], f["message"]))
        datasets.append(
            {
                "name": ds.get("dataset"),
                "kind": ds.get("kind"),
                "status": ds.get("status"),
                "overall": ds.get("overall"),
                "grade": ds.get("grade"),
                "stages": ds.get("stages") or {},
                "counts": ds.get("counts") or {},
                "layers": layers,
                "findings": findings,
            }
        )
    datasets.sort(key=lambda d: str(d["name"]))
    return {"rules_sha256": result.get("rules_sha256") or "", "datasets": datasets}


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def result_signature(result: dict) -> str:
    return hashlib.sha256(canonical(signature_payload(result)).encode("utf-8")).hexdigest()


def build_manifest(result: dict) -> dict:
    inputs = []
    per_ds = []
    for ds in result.get("datasets") or []:
        info = (
            hash_input(ds["path"])
            if ds.get("path")
            else {"name": ds.get("dataset"), "bytes": 0, "files": 0, "sha256": ""}
        )
        info["kind"] = ds.get("kind")
        inputs.append(info)
        layers = [
            {"layer": row.get("name"), "count": row.get("count")}
            for row in ((ds.get("tables") or {}).get("layers") or [])
        ]
        per_ds.append(
            {
                "name": ds.get("dataset"),
                "status": ds.get("status"),
                "overall": ds.get("overall"),
                "grade": ds.get("grade"),
                "layers": layers,
                "finding_counts": ds.get("counts") or {},
                "findings": len(ds.get("findings") or []),
            }
        )
    return {
        "manifest_version": 1,
        "run_id": result.get("job_id"),
        "tool": config.APP_TITLE,
        "tool_version": config.APP_VERSION,
        "timestamp": result.get("generated"),
        "inputs": inputs,
        "rules_sha256": result.get("rules_sha256") or "",
        "rules": result.get("rules") or {},
        "dispositions_sha256": result.get("dispositions_sha256") or "",
        "datasets": per_ds,
        "signature": result_signature(result),
        # paths stay in the manifest so a replay can find the inputs; they are not part of the signature
        "input_paths": [ds.get("path") for ds in result.get("datasets") or []],
        "profile_only": bool(result.get("profile_only")),
    }


def write_manifest(result: dict, path: Path) -> dict:
    doc = build_manifest(result)
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    return doc


def compare_signatures(expected: str, actual: str, previous: dict, current: dict) -> dict:
    """Human-readable match or mismatch between two result documents."""
    ok = expected == actual
    diffs = []
    prev = {d.get("dataset"): d for d in previous.get("datasets") or []}
    for ds in current.get("datasets") or []:
        old = prev.get(ds.get("dataset"))
        if old is None:
            diffs.append(f"{ds.get('dataset')}: not in the previous run")
            continue
        if old.get("overall") != ds.get("overall") or old.get("grade") != ds.get("grade"):
            diffs.append(
                f"{ds.get('dataset')}: score {old.get('overall')} ({old.get('grade')}) -> "
                f"{ds.get('overall')} ({ds.get('grade')})"
            )
        elif len(old.get("findings") or []) != len(ds.get("findings") or []):
            diffs.append(
                f"{ds.get('dataset')}: {len(old.get('findings') or [])} findings -> {len(ds.get('findings') or [])}"
            )
    return {"match": ok, "expected": expected, "actual": actual, "differences": diffs}
