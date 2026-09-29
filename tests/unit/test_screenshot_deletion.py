"""The local JPEG is removed once — and only once — the server confirms it.

A screenshot occupies real disk on the employee's machine, and at the default
60s capture interval that is ~1,440 images a day. The file is the thing worth
reclaiming: the SQLite row holds no image data, only the record that a capture
happened, so it is kept.

These tests pin both halves of that rule. Deleting on an *attempt* would be
data loss the moment the backend is briefly unreachable, and the drain runs
retries for exactly that reason.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.domain.activity.activity import ActivityState
from app.domain.sync.provider import ConnectivityState
from app.domain.sync.sync import SyncStatus
from app.infrastructure.database.db import Database
from app.infrastructure.database.migrations import migrate
from app.infrastructure.database.repositories import ScreenshotRepository
from app.infrastructure.network.sync_adapter import DummySyncProvider
from app.services.screenshot_service import ScreenshotService
from app.services.sync_service import SyncService

if TYPE_CHECKING:
    from pathlib import Path

T0 = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)


def _make_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "shot_delete.db")
    migrate(db.engine)
    return db


def _seed_user_and_session(db: Database) -> int:
    from app.infrastructure.database.repositories import (
        SessionRepository,
        UserRepository,
    )

    with db.session() as s:
        user = UserRepository(s).upsert(
            external_user_id="ext-1",
            email="a@example.com",
            display_name="A",
            workspace_name="T",
        )
        s.flush()
        ws = SessionRepository(s).create(user_id=user.id, started_at=T0)
        s.commit()
        return int(ws.id)


def _seed_screenshot(db: Database, session_id: int, image: Path) -> int:
    with db.session() as s:
        row = ScreenshotRepository(s).add(
            session_id=session_id,
            captured_at=T0,
            activity_state=ActivityState.ACTIVE,
            file_path=str(image),
            file_size=image.stat().st_size if image.exists() else 0,
            checksum="a" * 64,
        )
        s.commit()
        return int(row.id)


def _make_image(tmp_path: Path, name: str = "shot.jpg") -> Path:
    """A byte sequence the dummy provider accepts as a readable file."""
    image = tmp_path / name
    image.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")
    return image


def test_a_confirmed_screenshot_deletes_its_local_file(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        image = _make_image(tmp_path)
        sid = _seed_user_and_session(db)
        shot_id = _seed_screenshot(db, sid, image)

        svc = SyncService(provider=DummySyncProvider(), session_factory=db.session)
        result = svc.sync_once(at=T0)

        assert result["synced"] == 1
        assert not image.exists(), "the local copy is redundant once confirmed"
        with db.session() as s:
            row = ScreenshotRepository(s).get_by_id(shot_id)
            assert row is not None
            assert row.sync_status == SyncStatus.SYNCED.value
            assert row.synced_at is not None
    finally:
        db.dispose()


def test_the_metadata_row_survives_its_file(tmp_path: Path) -> None:
    """The row is the only local record that a capture ever happened."""
    db = _make_db(tmp_path)
    try:
        image = _make_image(tmp_path)
        sid = _seed_user_and_session(db)
        shot_id = _seed_screenshot(db, sid, image)

        SyncService(provider=DummySyncProvider(), session_factory=db.session).sync_once(
            at=T0
        )

        with db.session() as s:
            row = ScreenshotRepository(s).get_by_id(shot_id)
            assert row is not None, "the row must outlive the file"
            assert row.file_path == str(image), "path is kept as history"
            assert row.file_size is not None
            assert row.checksum == "a" * 64
    finally:
        db.dispose()


def test_a_rejected_upload_keeps_the_local_file(tmp_path: Path) -> None:
    """Confirm-then-delete, never attempt-then-delete."""
    db = _make_db(tmp_path)
    try:
        image = _make_image(tmp_path)
        sid = _seed_user_and_session(db)
        _seed_screenshot(db, sid, image)

        provider = DummySyncProvider()
        provider.set_always_fail(True)
        result = SyncService(provider=provider, session_factory=db.session).sync_once(
            at=T0
        )

        assert result["synced"] == 0
        assert result["failed"] == 1
        assert image.exists(), "an upload that failed must leave the bytes alone"
    finally:
        db.dispose()


def test_an_offline_machine_keeps_the_local_file(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        image = _make_image(tmp_path)
        sid = _seed_user_and_session(db)
        _seed_screenshot(db, sid, image)

        provider = DummySyncProvider()
        provider.set_connectivity(ConnectivityState.OFFLINE)
        SyncService(provider=provider, session_factory=db.session).sync_once(at=T0)

        assert image.exists()
    finally:
        db.dispose()


def test_a_failed_unlink_does_not_fail_the_sync(tmp_path: Path) -> None:
    """Windows refuses to unlink a file that is still held open.

    The bytes are already on the server, so this must cost disk, not the sync.
    """
    db = _make_db(tmp_path)
    try:
        image = _make_image(tmp_path)
        sid = _seed_user_and_session(db)
        _seed_screenshot(db, sid, image)

        svc = SyncService(provider=DummySyncProvider(), session_factory=db.session)
        svc._discard_local_file = lambda _p: False  # type: ignore[method-assign]
        result = svc.sync_once(at=T0)

        assert result["synced"] == 1
        assert image.exists(), "a failed unlink leaves the file for CleanupService"
    finally:
        db.dispose()


# ── Startup orphan pass ────────────────────────────────────────────────────
class _NullScreenshotProvider:
    """`reconcile_orphans` never captures, so the provider is never called."""

    def capture(self, *_a: object, **_kw: object) -> object:
        raise AssertionError("reconcile_orphans must not capture")


def _reconcile(db: Database, data_dir: Path) -> dict[str, int]:
    pending = data_dir / "screenshots" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    service = ScreenshotService(
        provider=_NullScreenshotProvider(),
        session_factory=db.session,
        data_dir=data_dir,
    )
    return service.recover_orphans()


def test_orphan_pass_keeps_a_synced_row_whose_file_is_gone(tmp_path: Path) -> None:
    """A file-less SYNCED row is now expected, not an orphan.

    Without this the drain's own deletion would be undone at the next launch,
    and every synced screenshot would be logged as a removed orphan.
    """
    db = _make_db(tmp_path)
    data_dir = tmp_path / "data"
    try:
        image = _make_image(tmp_path)
        sid = _seed_user_and_session(db)
        shot_id = _seed_screenshot(db, sid, image)

        SyncService(provider=DummySyncProvider(), session_factory=db.session).sync_once(
            at=T0
        )
        assert not image.exists()

        counts = _reconcile(db, data_dir)

        assert counts["orphan_records_removed"] == 0
        with db.session() as s:
            assert ScreenshotRepository(s).get_by_id(shot_id) is not None
    finally:
        db.dispose()


def test_orphan_pass_still_reaps_an_unsynced_row_with_no_file(
    tmp_path: Path,
) -> None:
    """A row the server never saw, with no bytes to retry, is a real orphan."""
    db = _make_db(tmp_path)
    data_dir = tmp_path / "data"
    try:
        missing = tmp_path / "never-written.jpg"
        sid = _seed_user_and_session(db)
        shot_id = _seed_screenshot(db, sid, missing)
        assert not missing.exists()

        counts = _reconcile(db, data_dir)

        assert counts["orphan_records_removed"] == 1
        with db.session() as s:
            assert ScreenshotRepository(s).get_by_id(shot_id) is None
    finally:
        db.dispose()
