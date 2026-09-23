"""Shared pytest fixtures.

Provides hermetic settings/container/logging per test: the data directory
is always a tmp_path and the application logger is reset between tests so
bootstrap calls never leak handlers across a session.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from app.config.settings import AppSettings, LocalConfig, ServerPolicy
from app.core.container import Container, bootstrap_container

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

APP_LOGGER_NAME = "employee_monitoring_agent"


@pytest.fixture(autouse=True)
def _reset_app_logger() -> Iterator[None]:
    """Clear the app root logger's handlers between tests."""
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.handlers.clear()
    yield
    logger.handlers.clear()


@pytest.fixture
def local_settings(tmp_path: Path) -> LocalConfig:
    data_dir = tmp_path / "data"
    return LocalConfig(data_dir=data_dir, log_level="DEBUG")


@pytest.fixture
def app_settings(local_settings: LocalConfig) -> AppSettings:
    return AppSettings(local=local_settings, server=ServerPolicy())


@pytest.fixture
def live_container(local_settings: LocalConfig) -> Container:
    """A container wired to a temp log directory (no console output)."""
    return bootstrap_container(local=local_settings, console_logging=False)
