"""Integration: the real sync service draining through the real API provider.

The unit tests pin each provider's behaviour in isolation; this one pins the
*combination* that the confirm-then-delete rule actually depends on
(docs/ENGINEERING_RULES.md §Data Deletion Rule): a local row survives every
failure mode and disappears only on an unambiguous server confirmation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from app.domain.activity.activity import ActivityState
from app.domain.auth.token_holder import AuthTokenHolder
from app.domain.sync.provider import ConnectivityState
from app.infrastructure.database.models import (
    ActivityPeriodRecord,
    ScreenshotMetadata,
    SyncQueueItem,
    User,
    WorkSession,
)
from app.infrastructure.database.repositories import (
    ScreenshotRepository,
    SyncQueueRepository,
)
from app.infrastructure.network.http_client import ApiHttpClient
from app.infrastructure.network.sync_adapter import ApiSyncProvider
from app.services.sync_service import SyncService
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sqlalchemy.orm import Session

BASE_URL = "http://backend.test/api/v1"
TOKEN = "1|token"
NOW = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)


class Backend:
    """A scripted backend: per-path responses, with a full request log."""

    def __init__(self, **responses: httpx.Response) -> None:
        self.responses: dict[str, httpx.Response] = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/v1")
        response = self.responses.get(path)
        if response is None:
            return httpx.Response(404, json={"message": f"no script for {path}"})
        # httpx responses are single-use; hand out a fresh copy each time.
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers={"content-type": "application/json"},
        )

    def paths(self) -> list[str]:
        return [r.url.path.removeprefix("/api/v1") for r in self.requests]

    def last_body(self) -> dict[str, Any]:
        import json

        return json.loads(self.requests[-1].read() or b"{}")


def build_service(
    backend: Backend,
    session_factory: Callable[[], Session],
) -> SyncService:
    """Wire the real SyncService to the real API provider over a mock wire."""
    holder = AuthTokenHolder()
    holder.set(TOKEN)
    return SyncService(
        provider=ApiSyncProvider(
            client=ApiHttpClient(
                base_url=BASE_URL, transport=httpx.MockTransport(backend)
            ),
            token_holder=holder,
        ),
        session_factory=session_factory,
        batch_limit=20,
    )


def seed_work_session(session_factory: Callable[[], Session]) -> int:
    """Create a local user + session, and enqueue the session for sync."""
    with session_factory() as db:
        user = User(
            external_user_id="1",
            email="employee@example.com",
            display_name="Employee",
        )
        db.add(user)
        db.flush()

        session = WorkSession(user_id=user.id, started_at=NOW, status="WORKING")
        db.add(session)
        db.flush()

        SyncQueueRepository(db).enqueue(
            entity_type="work_session", entity_id=session.id, operation="CREATE"
        )
        db.commit()
        return session.id


def seed_period(session_factory: Callable[[], Session], session_id: int) -> int:
    with session_factory() as db:
        period = ActivityPeriodRecord(
            session_id=session_id, started_at=NOW, state="ACTIVE", duration_seconds=0
        )
        db.add(period)
        db.flush()
        SyncQueueRepository(db).enqueue(
            entity_type="activity_period", entity_id=period.id, operation="CREATE"
        )
        db.commit()
        return period.id


def seed_screenshot(
    session_factory: Callable[[], Session], session_id: int, image: Path
) -> int:
    with session_factory() as db:
        row = ScreenshotRepository(db).add(
            session_id=session_id,
            captured_at=NOW,
            activity_state=ActivityState.ACTIVE,
            file_path=str(image),
            file_size=image.stat().st_size,
            checksum="f" * 64,
        )
        db.commit()
        return row.id


def count(session_factory: Callable[[], Session], model: type[Any]) -> int:
    with session_factory() as db:
        return len(list(db.scalars(select(model))))


ONLINE = {
    "/health": httpx.Response(200, json={"status": "ok"}),
    "/me": httpx.Response(200, json={"user": {"id": 1}}),
}


class TestConfirmThenDelete:
    def test_queue_row_survives_a_503_and_is_retried(self, database: Any) -> None:
        session_factory = database.session
        backend = Backend(
            **ONLINE,
            **{"/work-sessions/1": httpx.Response(503, json={"message": "later"})},
        )
        seed_work_session(session_factory)
        service = build_service(backend, session_factory)

        result = service.sync_once(at=NOW)

        assert result["failed"] == 1
        assert result["synced"] == 0
        assert count(session_factory, SyncQueueItem) == 1, (
            "an attempted upload must never delete the local row"
        )

    def test_queue_row_disappears_only_after_a_200(self, database: Any) -> None:
        session_factory = database.session
        backend = Backend(
            **ONLINE,
            **{"/work-sessions/1": httpx.Response(200, json={"data": {"id": 1}})},
        )
        seed_work_session(session_factory)
        service = build_service(backend, session_factory)

        result = service.sync_once(at=NOW)

        assert result["synced"] == 1
        assert result["pending"] == 0
        assert count(session_factory, SyncQueueItem) == 0

    def test_replaying_a_confirmed_item_is_harmless(self, database: Any) -> None:
        """A crash between "server confirmed" and "row deleted" resends the
        same item; the provider must send an identical request."""
        session_factory = database.session
        backend = Backend(
            **ONLINE,
            **{"/work-sessions/1": httpx.Response(200, json={"data": {"id": 1}})},
        )
        session_id = seed_work_session(session_factory)
        service = build_service(backend, session_factory)
        service.sync_once(at=NOW)

        with session_factory() as db:
            SyncQueueRepository(db).enqueue(
                entity_type="work_session", entity_id=session_id, operation="CREATE"
            )
            db.commit()

        result = service.sync_once(at=NOW)

        assert result["synced"] == 1
        assert count(session_factory, WorkSession) == 1


class TestOfflineShortCircuit:
    def test_nothing_is_sent_when_the_backend_is_down(self, database: Any) -> None:
        def dead(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        session_factory = database.session
        seed_work_session(session_factory)
        service = SyncService(
            provider=ApiSyncProvider(
                client=ApiHttpClient(
                    base_url=BASE_URL, transport=httpx.MockTransport(dead)
                ),
                token_holder=_holder(),
            ),
            session_factory=session_factory,
        )

        result = service.sync_once(at=NOW)

        assert result["skipped_offline"] == 1
        assert result["synced"] == 0
        assert result["pending"] == 1
        assert service.connectivity is ConnectivityState.BACKEND_UNAVAILABLE
        assert count(session_factory, SyncQueueItem) == 1

    def test_a_rejected_token_stops_the_drain_before_writing(
        self, database: Any
    ) -> None:
        session_factory = database.session
        backend = Backend(
            **{
                "/health": httpx.Response(200, json={"status": "ok"}),
                "/me": httpx.Response(401, json={"message": "Unauthenticated."}),
            }
        )
        seed_work_session(session_factory)
        service = build_service(backend, session_factory)

        result = service.sync_once(at=NOW)

        assert result["synced"] == 0
        assert result["pending"] == 1
        assert backend.paths() == ["/health", "/me"]
        assert count(session_factory, SyncQueueItem) == 1


class TestScreenshotConfirmThenDelete:
    def test_local_file_and_row_survive_a_failed_upload(
        self, tmp_path: Path, database: Any
    ) -> None:
        image = _write_image(tmp_path)
        session_factory = database.session
        session_id = seed_work_session(session_factory)
        seed_screenshot(session_factory, session_id, image)

        backend = Backend(
            **ONLINE,
            **{
                "/work-sessions/1": httpx.Response(200, json={"data": {"id": 1}}),
                "/screenshots/1/file": httpx.Response(500, json={"message": "later"}),
            },
        )
        service = build_service(backend, session_factory)

        result = service.sync_once(at=NOW)

        assert result["failed"] == 1
        assert image.exists(), "a failed upload must never delete the local file"
        with session_factory() as db:
            row = db.get(ScreenshotMetadata, 1)
            assert row is not None
            assert row.sync_status != "SYNCED"

    def test_marked_synced_only_after_confirmation(
        self, tmp_path: Path, database: Any
    ) -> None:
        image = _write_image(tmp_path)
        session_factory = database.session
        session_id = seed_work_session(session_factory)
        seed_screenshot(session_factory, session_id, image)

        backend = Backend(
            **ONLINE,
            **{
                "/work-sessions/1": httpx.Response(200, json={"data": {"id": 1}}),
                "/screenshots/1/file": httpx.Response(
                    200, json={"data": {"has_file": True}}
                ),
            },
        )
        service = build_service(backend, session_factory)

        result = service.sync_once(at=NOW)

        assert result["failed"] == 0
        with session_factory() as db:
            row = db.get(ScreenshotMetadata, 1)
            assert row is not None
            assert row.sync_status == "SYNCED"
            assert row.synced_at is not None


class TestPayloadShapes:
    def test_activity_periods_reach_their_documented_endpoint(
        self, database: Any
    ) -> None:
        session_factory = database.session
        session_id = seed_work_session(session_factory)
        period_id = seed_period(session_factory, session_id)

        backend = Backend(
            **ONLINE,
            **{
                "/work-sessions/1": httpx.Response(200, json={"data": {"id": 1}}),
                f"/activity-periods/{period_id}": httpx.Response(
                    200, json={"data": {"id": period_id}}
                ),
            },
        )
        service = build_service(backend, session_factory)

        result = service.sync_once(at=NOW)

        assert result["synced"] == 2
        assert f"/activity-periods/{period_id}" in backend.paths()
        assert backend.last_body()["state"] == "ACTIVE"


def _holder() -> AuthTokenHolder:
    holder = AuthTokenHolder()
    holder.set(TOKEN)
    return holder


def _write_image(tmp_path: Path) -> Path:
    image = tmp_path / "screenshots" / "pending" / "shot.jpg"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"\xff\xd8\xff\xe0 fake jpeg bytes")
    return image
