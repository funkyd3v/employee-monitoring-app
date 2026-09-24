"""Unit tests for DummySyncProvider and payload contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.sync.provider import ConnectivityState, SyncResult
from app.infrastructure.network.sync_adapter import DummySyncProvider

if TYPE_CHECKING:
    from pathlib import Path


def test_dummy_online_sync_success() -> None:
    provider = DummySyncProvider()
    assert provider.check_connectivity() == ConnectivityState.ONLINE
    result = provider.sync_item(
        entity_type="work_session", entity_id=1, operation="CREATE", payload={"id": 1}
    )
    assert result.success is True
    assert len(provider.synced_items) == 1
    assert provider.call_count == 1


def test_dummy_offline_preserves_data() -> None:
    provider = DummySyncProvider(connectivity=ConnectivityState.OFFLINE)
    result = provider.sync_item(
        entity_type="work_session", entity_id=1, operation="CREATE", payload={}
    )
    assert result.success is False
    assert result.connectivity == ConnectivityState.OFFLINE
    assert result.retryable is True
    assert provider.synced_items == []  # nothing persisted

    # Screenshot also offline
    result2 = provider.upload_screenshot(
        screenshot_id=1, file_path="/tmp/x.jpg", metadata={}
    )
    assert result2.success is False
    assert result2.connectivity == ConnectivityState.OFFLINE


def test_dummy_backend_unavailable() -> None:
    provider = DummySyncProvider(connectivity=ConnectivityState.BACKEND_UNAVAILABLE)
    result = provider.sync_item(
        entity_type="break", entity_id=2, operation="CREATE", payload={}
    )
    assert result.success is False
    assert result.retryable is True


def test_dummy_auth_required_non_retryable() -> None:
    provider = DummySyncProvider(connectivity=ConnectivityState.AUTHENTICATION_REQUIRED)
    result = provider.sync_item(
        entity_type="user", entity_id=1, operation="CREATE", payload={}
    )
    assert result.success is False
    assert result.retryable is False
    assert result.connectivity == ConnectivityState.AUTHENTICATION_REQUIRED


def test_dummy_injected_failures_then_success() -> None:
    provider = DummySyncProvider(fail_next=2)
    assert (
        provider.sync_item(
            entity_type="work_session", entity_id=1, operation="CREATE", payload={}
        ).success
        is False
    )
    assert (
        provider.sync_item(
            entity_type="work_session", entity_id=1, operation="CREATE", payload={}
        ).success
        is False
    )
    # Third should succeed
    assert (
        provider.sync_item(
            entity_type="work_session", entity_id=1, operation="CREATE", payload={}
        ).success
        is True
    )
    assert len(provider.synced_items) == 1


def test_dummy_screenshot_validates_file(tmp_path: Path) -> None:
    provider = DummySyncProvider()
    missing = str(tmp_path / "nope.jpg")
    result = provider.upload_screenshot(
        screenshot_id=10, file_path=missing, metadata={}
    )
    assert result.success is False
    assert result.retryable is False  # missing file not retryable

    # Empty file
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    result2 = provider.upload_screenshot(
        screenshot_id=11, file_path=str(empty), metadata={}
    )
    assert result2.success is False
    assert result2.retryable is False

    # Valid file
    valid = tmp_path / "valid.jpg"
    valid.write_bytes(b"x" * 1024)
    result3 = provider.upload_screenshot(
        screenshot_id=12, file_path=str(valid), metadata={"checksum": "abc"}
    )
    assert result3.success is True
    assert len(provider.uploaded_screenshots) == 1


def test_dummy_always_fail_and_reset() -> None:
    provider = DummySyncProvider(always_fail=True)
    assert (
        provider.sync_item(
            entity_type="work_session", entity_id=1, operation="CREATE", payload={}
        ).success
        is False
    )
    provider.set_always_fail(False)
    assert (
        provider.sync_item(
            entity_type="work_session", entity_id=1, operation="CREATE", payload={}
        ).success
        is True
    )
    provider.reset()
    assert provider.call_count == 0
    assert provider.synced_items == []


def test_sync_result_helpers() -> None:
    assert SyncResult.ok().success is True
    assert SyncResult.failed("x").retryable is True
    assert (
        SyncResult.auth_required().connectivity
        == ConnectivityState.AUTHENTICATION_REQUIRED
    )
    assert SyncResult.offline().connectivity == ConnectivityState.OFFLINE
