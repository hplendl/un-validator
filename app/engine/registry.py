"""Check registry and plugin loader.

Checks are plain functions registered with :func:`check`. Built-in checks live in
``app/engine/checks``; any ``*.py`` file in the plugin folder is imported as well.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..log import get_logger
from .models import STAGE_NAMES

log = get_logger("registry")

PLUGIN_PREFIX = "unv_plugin_"


@dataclass
class CheckSpec:
    stage: str
    name: str
    func: Callable
    order: int = 100
    description: str = ""
    requires: tuple[str, ...] = ()  # optional python modules required (e.g. ("arcpy",))
    source: str = "builtin"  # "builtin" | "plugin"


@dataclass
class PluginReport:
    loaded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


_REGISTRY: list[CheckSpec] = []
_lock = threading.Lock()
_loaded = False
last_plugin_report = PluginReport()


def check(stage: str, name: str, order: int = 100, description: str = "", requires: tuple[str, ...] = ()):
    """Decorator that registers a validation check.

    The function receives a :class:`~app.engine.context.RunContext` and may call ``ctx.log()``,
    ``ctx.progress()``, ``ctx.finding()``, ``ctx.score()`` and ``ctx.flag_features()``.
    """
    if stage not in STAGE_NAMES:
        raise ValueError(f"unknown stage {stage!r}; use one of {list(STAGE_NAMES)}")

    def deco(fn: Callable) -> Callable:
        mod = getattr(fn, "__module__", "") or ""
        desc = description or (fn.__doc__ or "").strip().split("\n")[0]
        spec = CheckSpec(
            stage, name, fn, order, desc, tuple(requires), "plugin" if mod.startswith(PLUGIN_PREFIX) else "builtin"
        )
        with _lock:
            # re-importing a module (e.g. reloading a plugin) replaces its earlier registration
            _REGISTRY[:] = [
                c for c in _REGISTRY if not (c.stage == stage and c.name == name and c.source == spec.source)
            ]
            _REGISTRY.append(spec)
        return fn

    return deco


def registered_checks(stage: str | None = None) -> list[CheckSpec]:
    with _lock:
        specs = [c for c in _REGISTRY if stage is None or c.stage == stage]
    return sorted(specs, key=lambda c: (c.order, c.name))


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def load_plugins(plugin_dir: str | Path) -> PluginReport:
    """Import every ``*.py`` in *plugin_dir* (files starting with ``_`` are ignored).

    A plugin that fails to import is reported and skipped; it never stops the others.
    """
    report = PluginReport()
    pdir = Path(plugin_dir)
    if not pdir.is_dir():
        return report
    for py in sorted(pdir.glob("*.py")):
        if py.name.startswith("_"):
            continue
        modname = f"{PLUGIN_PREFIX}{py.stem}"
        try:
            spec = importlib.util.spec_from_file_location(modname, py)
            if spec is None or spec.loader is None:
                raise ImportError("not a loadable module")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[modname] = mod
            spec.loader.exec_module(mod)
            report.loaded.append(py.name)
        except Exception as e:  # noqa: BLE001 - a broken plugin must not break the app
            sys.modules.pop(modname, None)
            report.failed[py.name] = f"{type(e).__name__}: {e}"
            log.warning("plugin %s failed to load: %s", py.name, e)
    return report


def load_checks(plugin_dir: str | Path | None = None, force: bool = False) -> PluginReport:
    """Import the built-in checks and the plugins (once per process unless *force*)."""
    global _loaded, last_plugin_report
    if _loaded and not force:
        return last_plugin_report
    from .checks import discover, finalize, lineage, metadata, network, quality, schema  # noqa: F401

    last_plugin_report = load_plugins(plugin_dir if plugin_dir is not None else config.PLUGIN_DIR)
    _loaded = True
    return last_plugin_report
