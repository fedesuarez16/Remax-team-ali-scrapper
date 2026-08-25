"""The funnel instrumentation only pays off if it actually reaches stdout.

Nothing in the app configured logging, so `logger.info` on `app.*` propagated
to a root logger sitting at WARNING and was dropped — the counters existed and
printed nowhere. `configure_logging` is what makes them visible, and
`LOG_LEVEL` is what lets an operator turn the noise down again.
"""
import logging

import pytest

from app.core.logging import configure_logging


@pytest.fixture(autouse=True)
def _restore_app_logger():
    app_logger = logging.getLogger('app')
    level, handlers = app_logger.level, list(app_logger.handlers)
    yield
    app_logger.setLevel(level)
    app_logger.handlers = handlers


def test_app_logger_emits_info_by_default(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging()

    assert logging.getLogger('app').isEnabledFor(logging.INFO)


def test_log_level_setting_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'LOG_LEVEL', 'WARNING')

    configure_logging()

    assert not logging.getLogger('app').isEnabledFor(logging.INFO)


def test_is_idempotent() -> None:
    """Called from both `main` import and tests — must not stack handlers."""
    configure_logging()
    configure_logging()

    assert len(logging.getLogger('app').handlers) == 1
