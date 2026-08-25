"""Logging setup for the `app` package.

Nothing configured logging before, so every `logger.info` in the app
propagated to a root logger left at WARNING and was silently dropped —
diagnostics that cost nothing to emit and could never be read. This attaches
ONE stdout handler to the `app` logger (not root: uvicorn owns its own
loggers and reconfiguring root fights with it) at `settings.LOG_LEVEL`.
"""
from __future__ import annotations

import logging
import sys

from app.core.config import settings

_FORMAT = '%(asctime)s %(levelname)s %(name)s %(message)s'


def configure_logging() -> None:
    """Idempotent: safe to call from `main` import and from a test."""
    app_logger = logging.getLogger('app')
    app_logger.setLevel(settings.LOG_LEVEL.upper())

    if not app_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT))
        app_logger.addHandler(handler)
    # `propagate` stays ON: uvicorn attaches handlers to its OWN loggers, never
    # to root, so nothing double-prints — and turning it off would cut the
    # records off from anything downstream that listens on root (pytest's
    # caplog, an APM handler).
