"""Logging setup shared by the CLI, the server and the engine."""

from __future__ import annotations

import logging

LOGGER_NAME = "unvalidator"


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def setup_logging(level: str | int = "INFO") -> None:
    """Configure the package logger once (idempotent)."""
    root = logging.getLogger(LOGGER_NAME)
    if not any(getattr(h, "_unv", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
        handler._unv = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(level if isinstance(level, int) else level.upper())
    root.propagate = False
