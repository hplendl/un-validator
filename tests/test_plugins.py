"""Plugin loader: good plugins load, broken ones are reported, arcpy-only checks are skipped."""

import textwrap

from app.engine.jobs import Job
from app.engine.registry import load_checks, load_plugins, module_available, registered_checks
from app.engine.runner import run_job

GOOD = """
from app.engine.core import check

@check("quality", "Plugin test: count layers", order=150)
def count_layers(ctx):
    ctx.finding("info", f"{len(ctx.dataset.user_layers())} layers seen by plugin")
"""

NEEDS_ARCPY = """
from app.engine.core import check

@check("quality", "Plugin test: needs arcpy", order=151, requires=("arcpy",))
def needs_arcpy(ctx):
    import arcpy  # noqa: F401
    ctx.finding("error", "should never run without arcpy")
"""

BROKEN = "def oops(:\n"


def _write(folder, name, text):
    (folder / name).write_text(textwrap.dedent(text), encoding="utf-8")


def test_load_plugins_reports_failures(tmp_path, registry_snapshot):
    _write(tmp_path, "good.py", GOOD)
    _write(tmp_path, "broken.py", BROKEN)
    _write(tmp_path, "_private.py", "raise SystemExit('must not be imported')")
    rep = load_plugins(tmp_path)
    assert rep.loaded == ["good.py"]
    assert "broken.py" in rep.failed and "SyntaxError" in rep.failed["broken.py"]
    spec = [c for c in registered_checks("quality") if c.name == "Plugin test: count layers"]
    assert spec and spec[0].source == "plugin"


def test_reloading_a_plugin_does_not_duplicate_checks(tmp_path, registry_snapshot):
    _write(tmp_path, "good.py", GOOD)
    load_plugins(tmp_path)
    load_plugins(tmp_path)
    assert len([c for c in registered_checks() if c.name == "Plugin test: count layers"]) == 1


def test_missing_plugin_folder_is_fine(tmp_path):
    rep = load_plugins(tmp_path / "nope")
    assert rep.loaded == [] and rep.failed == {}


def test_arcpy_check_is_skipped_when_arcpy_missing(tmp_path, gpkg_path, registry_snapshot):
    assert not module_available("arcpy")
    load_checks()
    _write(tmp_path, "good.py", GOOD)
    _write(tmp_path, "needs_arcpy.py", NEEDS_ARCPY)
    load_plugins(tmp_path)
    job = Job("plug1", [str(gpkg_path)], {})
    run_job(job)
    assert job.status == "done"
    ends = {e["check"]: e["status"] for e in job.events if e["kind"] == "check_end"}
    assert ends["Plugin test: needs arcpy"] == "skipped"
    assert ends["Plugin test: count layers"] == "ok"
    msgs = [f["message"] for f in job.result["datasets"][0]["findings"]]
    assert "should never run without arcpy" not in msgs
    assert any("layers seen by plugin" in m for m in msgs)


def test_bundled_plugins_register(registry_snapshot):
    load_checks()
    names = {c.name for c in registered_checks()}
    assert "Example: very short network lines" in names
    assert "ArcPy: utility network topology errors" in names
