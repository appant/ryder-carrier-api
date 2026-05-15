"""Shared pytest fixtures and structlog wiring for the test suite."""

from __future__ import annotations

import logging

import pytest
import structlog


@pytest.fixture(autouse=True)
def _configure_structlog() -> None:
    """Keep structlog quiet during tests; downstream code still binds and logs."""
    structlog.configure(
        processors=[structlog.processors.KeyValueRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        cache_logger_on_first_use=False,
    )
