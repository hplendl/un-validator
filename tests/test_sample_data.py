"""Optional end-to-end regression on the public Esri / open-data samples.

Skipped unless the sample data is present. Point UNV_SAMPLE_DATA at the folder that holds
``prosdk/``, ``foundations/`` and ``other/`` (default: ./data inside the project).
"""

import os
from pathlib import Path

import pytest

from app import config
from app.engine import targets
from app.engine.jobs import Job
from app.engine.runner import run_job

SAMPLES = Path(os.environ.get("UNV_SAMPLE_DATA", Path(__file__).resolve().parents[1] / "data"))

# dataset (relative to SAMPLES) -> expected overall score and grade
EXPECTED = {
    "prosdk/Data/UtilityNetwork/NapervilleElectric.gdb": (71.2, "C"),
    "foundations/Water_Utility_Network_Essentials/Data Model/WaterEssentials_AssetPackage.gdb": (77.4, "C"),
    "other/seatac_StormwaterInfrastructure.gdb/StormwaterInfrastructure.gdb": (80.5, "B"),
    "other/langley_waterutility.gdb/Water Utility Jan 26 2026.gdb": (58.6, "F"),
}

pytestmark = [
    pytest.mark.sample_data,
    pytest.mark.skipif(not (SAMPLES / "foundations").is_dir(), reason="sample data not present"),
]


@pytest.fixture
def sample_config(monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOTS", [SAMPLES])
    monkeypatch.setattr(config, "DATA_ROOT", SAMPLES)
    monkeypatch.setattr(config, "FOUNDATION_ROOT", SAMPLES / "foundations")
    targets.reset_targets()
    yield
    targets.reset_targets()


@pytest.mark.parametrize("rel", sorted(EXPECTED))
def test_sample_scores_unchanged(rel, sample_config):
    path = SAMPLES / rel
    if not path.exists():
        pytest.skip(f"{rel} not present")
    job = Job("sample", [str(path)], {})
    run_job(job)
    assert job.status == "done"
    res = job.result["datasets"][0]
    assert (res["overall"], res["grade"]) == EXPECTED[rel]
    assert not [e for e in job.events if e["kind"] == "check_end" and e["status"] == "error"]
