"""Integration tests for the CLI entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app import main

if TYPE_CHECKING:
    from pathlib import Path


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main.main(["--version"])
    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "employee-monitoring-agent" in captured.out


def test_main_boots_with_temp_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EM_DATA_DIR", str(tmp_path / "runtime"))
    assert main.main([]) == 0
    assert (tmp_path / "runtime" / "logs").exists()
