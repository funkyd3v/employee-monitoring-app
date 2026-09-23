"""Unit tests for container bootstrap + end-to-end logging to disk."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.settings import LocalConfig
from app.core.container import bootstrap_container

if TYPE_CHECKING:
    from pathlib import Path


def test_bootstrap_wires_settings_and_logger(
    live_container,
) -> None:
    assert live_container.settings.data_dir is not None
    assert live_container.logger.name == "employee_monitoring_agent.core"
    assert live_container.mode == "local"


def test_bootstrap_writes_log_file(
    live_container,
) -> None:
    live_container.logger.info("hello from test")
    for handler in live_container.logger.handlers:
        handler.flush()
    log_file = live_container.settings.subdir("logs") / "agent.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "hello from test" in content


def test_bootstrap_token_never_lands_in_log_file(
    live_container,
) -> None:
    from app.core.logging import discard_secret, register_secret

    secret = "DEADBEEF-token-value"
    register_secret(secret)
    try:
        live_container.logger.info("session key=%s attached", secret)
        for handler in live_container.logger.handlers:
            handler.flush()
    finally:
        discard_secret(secret)
    log_file = live_container.settings.subdir("logs") / "agent.log"
    assert secret not in log_file.read_text(encoding="utf-8")


def test_custom_local_config_is_respected(tmp_path: Path) -> None:
    container = bootstrap_container(
        local=LocalConfig(data_dir=tmp_path / "alt", log_level="DEBUG"),
        console_logging=False,
    )
    assert container.settings.data_dir == tmp_path / "alt"
