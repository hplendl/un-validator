"""Run signature, rules overlay, dispositions, profile mode, questions and asset-type matching."""

import csv
import json

import pytest

from app.engine.assets import _match
from app.engine.jobs import Job
from app.engine.manifest import result_signature
from app.engine.models import finding_id
from app.engine.questions import build_questions, write_questions_csv, write_questions_xlsx
from app.engine.rules import RulesError, load_rules_text, rules_sha256
from app.engine.runner import run_job


def test_finding_id_ignores_record_order_and_duplicates():
    a = finding_id("Duplicates", "WaterLine", "shared globalid", [3, 1, 3, 2])
    b = finding_id("Duplicates", "WaterLine", "shared globalid", [2, 1, 3])
    c = finding_id("Duplicates", "WaterDevice", "shared globalid", [1, 2, 3])
    assert a == b and a != c
    assert len(a) == 20


def test_rules_comments_merge_and_reject_unknown_keys():
    rules = load_rules_text(
        """
        {
          // local utility
          "thresholds": { "near_miss_m": 1.5 },
          "severities": { "null_required": "warning" },
          "domains": { "Lifecycle": [1, "Proposed"] }
        }
        """
    )
    assert rules["thresholds"]["near_miss_m"] == 1.5
    assert rules["thresholds"]["snap_tolerance_m"] == 0.001  # default kept
    assert rules["severities"]["null_required"] == "warning"
    assert rules["required_fields"] == ["GLOBALID", "ASSETGROUP", "ASSETTYPE"]
    with pytest.raises(RulesError) as exc:
        load_rules_text('{"thresholds": {"not_a_real_setting": 1}, "severities": {"null_required": "fatal"}}')
    text = " ".join(exc.value.errors)
    assert "not_a_real_setting" in text and "null_required" in text


def test_two_runs_share_a_signature_and_dispositions_do_not_change_it(gpkg_path):
    first = Job("sig1", [str(gpkg_path)], {})
    run_job(first)
    assert first.status == "done"
    second = Job("sig2", [str(gpkg_path)], {})
    run_job(second)
    assert first.result["signature"] == second.result["signature"]
    assert len(first.result["signature"]) == 64
    finding = first.result["datasets"][0]["findings"][0]
    assert finding["finding_id"] and finding["disposition"] == "OPEN"
    assert finding["classification"] and finding["recommended_action"]
    accepted = Job(
        "sig3",
        [str(gpkg_path)],
        {"dispositions": {finding["finding_id"]: {"disposition": "ACCEPTED", "note": "known"}}},
    )
    run_job(accepted)
    assert accepted.result["signature"] == first.result["signature"]
    matched = [f for f in accepted.result["datasets"][0]["findings"] if f["finding_id"] == finding["finding_id"]]
    assert matched and matched[0]["disposition"] == "ACCEPTED"
    assert all(q["finding_ids"] for q in accepted.result["questions"])
    assert finding["finding_id"] not in {i for q in accepted.result["questions"] for i in q["finding_ids"]}
    # a different rule changes the hash that the signature is tied to
    looser = load_rules_text('{"weights": {"stages": {"quality": 80}}}')
    assert rules_sha256(looser) != first.result["rules_sha256"]


def test_signature_ignores_timestamps_and_paths():
    base = {
        "rules_sha256": "abc",
        "generated": "2020-01-01",
        "datasets": [
            {
                "dataset": "net.gpkg",
                "path": "/tmp/secret/net.gpkg",
                "kind": "gpkg",
                "status": "done",
                "overall": 70.0,
                "grade": "C",
                "stages": {"quality": 70.0},
                "counts": {"error": 1, "warning": 0, "info": 0},
                "seconds": 9,
                "tables": {"layers": [{"name": "WaterLine", "count": 6, "system": False}]},
                "findings": [
                    {
                        "finding_id": "abc123abc123abc123ab",
                        "severity": "error",
                        "stage": "quality",
                        "check": "Duplicates",
                        "layer": "WaterLine",
                        "rule": "shared id",
                        "classification": "defect",
                        "confidence": 1,
                        "count": 2,
                        "affected_count": 2,
                        "message": "2 duplicates",
                        "recommended_action": "Fix",
                        "disposition": "OPEN",
                    }
                ],
            }
        ],
    }
    other = json.loads(json.dumps(base))
    other["generated"] = "2030-01-01"
    other["datasets"][0]["path"] = "/elsewhere/net.gpkg"
    other["datasets"][0]["seconds"] = 1
    other["datasets"][0]["findings"][0]["disposition"] = "ACCEPTED"
    assert result_signature(base) == result_signature(other)


def test_profile_only_does_not_score(gpkg_path):
    job = Job("prof1", [str(gpkg_path)], {"profile_only": True})
    run_job(job)
    assert job.status == "done"
    ds = job.result["datasets"][0]
    assert ds["overall"] is None and ds["grade"] == "n/a"
    assert ds["tables"]["profile"]
    assert any(r["layer"] == "WaterLine" for r in ds["tables"]["profile"])
    assert job.result["questions"]
    assert job.result["files"]["questions_xlsx"]


def test_questions_exports(tmp_path):
    rows = build_questions(
        {
            "datasets": [
                {
                    "dataset": "net.gpkg",
                    "findings": [
                        {
                            "finding_id": "aaaaaaaaaaaaaaaaaaaa",
                            "disposition": "OPEN",
                            "stage": "quality",
                            "check": "Duplicates",
                            "layer": "WaterLine",
                            "severity": "error",
                            "message": "dup",
                        },
                        {
                            "finding_id": "bbbbbbbbbbbbbbbbbbbb",
                            "disposition": "CLOSED",
                            "stage": "quality",
                            "check": "Duplicates",
                            "layer": "WaterLine",
                            "severity": "error",
                            "message": "dup",
                        },
                    ],
                }
            ]
        }
    )
    assert len(rows) == 1 and rows[0]["role"] == "Data editor" and rows[0]["priority"] == "high"
    assert rows[0]["finding_ids"] == ["aaaaaaaaaaaaaaaaaaaa"]
    write_questions_csv(rows, tmp_path / "q.csv")
    assert "why_it_matters" in next(csv.reader((tmp_path / "q.csv").open(encoding="utf-8")))
    write_questions_xlsx(rows, tmp_path / "q.xlsx")
    assert (tmp_path / "q.xlsx").stat().st_size > 100


def test_asset_name_matching():
    base = [
        {
            "domain": "water",
            "class_name": "WaterDevice",
            "asset_group": "Flow Valve",
            "asset_type": "Check",
        }
    ]
    exact = _match({"class_name": "WaterDevice", "asset_group": "Flow Valve", "asset_type": "Check"}, base)
    fuzzy = _match({"class_name": "WaterDevice", "asset_group": "Flow Valve", "asset_type": "Chek"}, base)
    missing = _match({"class_name": "WaterDevice", "asset_group": "Pump", "asset_type": "Booster"}, base)
    assert exact["method"] == "exact" and exact["confidence"] == 1.0
    assert fuzzy["method"] == "fuzzy" and fuzzy["confidence"] >= 0.84
    assert missing is None


def test_shipped_baseline_covers_the_foundation_domains():
    from app.engine.assets import load_baseline

    load_baseline.cache_clear()
    rows = load_baseline()
    domains = {r["domain"] for r in rows}
    assert {"electric", "water", "gas", "sewer", "stormwater", "communications", "district_energy"} <= domains
    assert all(r.get("class_name") and r.get("asset_group") for r in rows)


def test_rules_file_errors(tmp_path):
    from app.engine.rules import load_dispositions, load_rules_file, strip_json_comments

    missing = tmp_path / "nope.json"
    with pytest.raises(RulesError) as exc:
        load_rules_file(missing)
    assert "not found" in exc.value.errors[0]
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(RulesError) as exc:
        load_rules_file(bad)
    assert "not valid JSON" in exc.value.errors[0]
    with pytest.raises(RulesError):
        strip_json_comments("/* never closed")
    with pytest.raises(RulesError) as exc:
        load_rules_file_text = __import__("app.engine.rules", fromlist=["load_rules_text"]).load_rules_text
        load_rules_file_text('{"required_fields": [1], "weights": {"stages": {"metadata": 0}, "extra": 1}}')
    assert exc.value.errors
    disp = tmp_path / "d.json"
    disp.write_text('{"findings": {"zzzz": {"disposition": "MAYBE"}}}', encoding="utf-8")
    with pytest.raises(RulesError):
        load_dispositions(disp)
    ok = tmp_path / "ok.json"
    ok.write_text('{"abcdef0123456789": "CLOSED"}', encoding="utf-8")
    loaded = load_dispositions(ok)
    assert loaded["abcdef0123456789"]["disposition"] == "CLOSED"


def test_manifest_hashes_inputs_and_describes_a_mismatch(gpkg_path, tmp_path):
    from app.engine.manifest import compare_signatures, hash_input, write_manifest

    info = hash_input(gpkg_path)
    assert info["bytes"] > 0 and len(info["sha256"]) == 64
    folder = tmp_path / "mini.gdb"
    folder.mkdir()
    (folder / "a.gdbtable").write_bytes(b"abc")
    (folder / "b.gdbtable").write_bytes(b"def")
    again = hash_input(folder)
    assert again["files"] == 2 and hash_input(folder)["sha256"] == again["sha256"]
    result = {
        "job_id": "m1",
        "generated": "t",
        "rules_sha256": "abc",
        "rules": {"thresholds": {}},
        "datasets": [
            {
                "dataset": "net.gpkg",
                "path": str(gpkg_path),
                "kind": "gpkg",
                "status": "done",
                "overall": 10,
                "grade": "F",
                "counts": {"error": 1},
                "tables": {"layers": [{"name": "WaterLine", "count": 6}]},
                "findings": [],
            }
        ],
    }
    doc = write_manifest(result, tmp_path / "manifest.json")
    assert doc["signature"] == result_signature(result)
    diff = compare_signatures(
        doc["signature"],
        "0" * 64,
        {"datasets": [{"dataset": "net.gpkg", "overall": 10, "grade": "F", "findings": []}]},
        {"datasets": [{"dataset": "net.gpkg", "overall": 11, "grade": "F", "findings": [{}]}]},
    )
    assert diff["match"] is False and any("10" in line and "11" in line for line in diff["differences"])


def test_asset_mapping_exact_unmapped_and_missing(make_ctx, gpkg_path, monkeypatch):
    from app.engine import assets
    from app.engine.profile import profile_dataset

    rows = [
        {"domain": "water", "class_name": "WaterLine", "asset_group": "1", "asset_type": "1"},
        {"domain": "water", "class_name": "WaterLine", "asset_group": "Main", "asset_type": "Distribution"},
        {"domain": "water", "class_name": "WaterDevice", "asset_group": "Service Valve", "asset_type": "Butterfly"},
    ]
    monkeypatch.setattr(assets, "load_baseline", lambda: rows)
    ctx = make_ctx(gpkg_path)
    ctx.current_stage = "schema"
    ctx.current_check = "Asset type mapping"
    ctx.state["target"] = {"domain": "water"}
    assets.map_assets(ctx)
    matches = {r["match"] for r in ctx.tables["asset_mapping"]}
    assert "exact" in matches and "unmapped" in matches
    texts = " ".join(f.message for f in ctx.findings)
    assert "not in the Foundation baseline" in texts
    assert "does not use" in texts
    empty = make_ctx(gpkg_path)
    empty.current_stage = "schema"
    empty.current_check = "Asset type mapping"
    monkeypatch.setattr(assets, "load_baseline", lambda: [])
    assets.map_assets(empty)
    assert empty.tables["asset_mapping"] == []
    profile_dataset(empty)
    assert empty.tables["profile"]
