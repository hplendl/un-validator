"""Compatibility facade: the names plugins and tools import from ``app.engine.core``.

New code can import from the specific modules (registry, context, jobs, runner, scoring).
"""

from __future__ import annotations

from .context import JobCancelled, RunContext
from .jobs import Job, JobManager
from .models import SEVERITIES, STAGE_NAMES, STAGES, Finding
from .registry import CheckSpec, check, load_checks, registered_checks
from .runner import run_job
from .scoring import grade

__all__ = [
    "SEVERITIES",
    "STAGES",
    "STAGE_NAMES",
    "CheckSpec",
    "Finding",
    "Job",
    "JobCancelled",
    "JobManager",
    "RunContext",
    "check",
    "grade",
    "load_checks",
    "registered_checks",
    "run_job",
]
