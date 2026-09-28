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
from app.infrastructure.database.db import Database
from app.infrastructure.database.migrations import migrate

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

APP_LOGGER_NAME = "employee_monitoring_agent"


@pytest.fixture(autouse=True, scope="session")
def _isolate_dotenv() -> Iterator[None]:
    """Stop the suite reading the developer's own ``.env``.

    Settings are built from the environment and ``.env`` by default, so a
    developer's ``EM_MODE=api`` would silently change what the tests assert —
    and a green suite on one machine would be red on another. Tests that mean
    to exercise ``.env`` loading pass ``_env_file`` explicitly.
    """
    original = LocalConfig.model_config.get("env_file")
    LocalConfig.model_config["env_file"] = None
    yield
    LocalConfig.model_config["env_file"] = original


@pytest.fixture(autouse=True)
def _reset_app_logger() -> Iterator[None]:
    """Clear the app root logger's handlers between tests."""
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.handlers.clear()
    yield
    logger.handlers.clear()


@pytest.fixture
def local_settings(tmp_path: Path) -> LocalConfig:
    # `_env_file=None` is deliberate: a developer's own .env must not be able to
    # change what the suite asserts. Tests that specifically exercise .env
    # loading build their own in a tmp directory.
    data_dir = tmp_path / "data"
    return LocalConfig(_env_file=None, data_dir=data_dir, log_level="DEBUG")


@pytest.fixture
def app_settings(local_settings: LocalConfig) -> AppSettings:
    return AppSettings(local=local_settings, server=ServerPolicy())


@pytest.fixture
def live_container(local_settings: LocalConfig) -> Container:
    """A container wired to a temp log directory (no console output)."""
    return bootstrap_container(local=local_settings, console_logging=False)


@pytest.fixture
def database(tmp_path: Path) -> Database:
    """A migrated, isolated in-memory database per test."""
    db = Database(db_path=tmp_path / "test-agent.db")
    migrate(db.engine)
    yield db
    db.dispose()
