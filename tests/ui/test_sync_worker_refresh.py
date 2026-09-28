"""The sync tick also refreshes the two server-owned settings.

Adding them here is the whole point of the design: the workspace label and the
operator policy are read on a worker thread that is already talking to the
backend, so a new endpoint of that kind is wired in one place and the UI,
services and domain never learn it exists.

The behaviours worth pinning are the failure ones — a read that fails must not
disturb a queue drain, and a poll that finds nothing new must not repaint the
UI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.domain.auth.auth import AuthenticatedUser
from app.domain.auth.workspace import LocalWorkspaceProvider, WorkspaceProvider
from app.domain.sync.provider import ConnectivityState
from app.workers.sync_worker import SyncWorker

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


class FakeService:
    def __init__(self, state: ConnectivityState = ConnectivityState.ONLINE) -> None:
        self.connectivity = state
        self.ticks = 0

    def sync_once(self) -> dict[str, Any]:
        self.ticks += 1
        return {"synced": 0, "failed": 0, "skipped_offline": 0}


class FakeWorkspaceProvider(WorkspaceProvider):
    def __init__(self, name: str | Exception | None) -> None:
        self.name = name
        self.calls = 0

    def fetch_workspace(self) -> AuthenticatedUser | None:
        self.calls += 1
        if isinstance(self.name, Exception):
            raise self.name
        return AuthenticatedUser(
            external_user_id="1", email="a@b.c", workspace_name=self.name
        )


class FakePolicyService:
    def __init__(self, explode: bool = False) -> None:
        self.calls = 0
        self._explode = explode

    def refresh(self) -> None:
        self.calls += 1
        if self._explode:
            raise RuntimeError("policy read failed")


def make_worker(
    qtbot: QtBot,
    *,
    service: FakeService | None = None,
    workspace: WorkspaceProvider | None = None,
    policy: object | None = None,
) -> SyncWorker:
    worker = SyncWorker(
        service or FakeService(),  # type: ignore[arg-type]
        poll_interval_ms=600_000,
        workspace_provider=workspace,
        policy_service=policy,
    )
    # Exercise the tick without arming a real timer: the guard below is what
    # `start()` would set, and the periodic trigger is not what is under test.
    worker._running = True
    return worker
    return worker


class TestWorkspaceRefresh:
    def test_a_new_name_is_announced(self, qtbot: QtBot) -> None:
        workspace = FakeWorkspaceProvider("Acme Support")
        worker = make_worker(qtbot, workspace=workspace)
        seen: list[str] = []
        worker.workspace_changed.connect(seen.append)

        worker._tick()

        assert seen == ["Acme Support"]

    def test_an_unchanged_name_announces_nothing(self, qtbot: QtBot) -> None:
        workspace = FakeWorkspaceProvider("Acme Support")
        worker = make_worker(qtbot, workspace=workspace)
        seen: list[str] = []
        worker.workspace_changed.connect(seen.append)

        worker._tick()
        worker._tick()

        assert workspace.calls == 2
        assert seen == ["Acme Support"]  # emitted once, not every poll

    def test_a_rename_is_announced_once(self, qtbot: QtBot) -> None:
        workspace = FakeWorkspaceProvider("Acme Support")
        worker = make_worker(qtbot, workspace=workspace)
        seen: list[str] = []
        worker.workspace_changed.connect(seen.append)

        worker._tick()
        workspace.name = "Field Ops"
        worker._tick()
        workspace.name = "Field Ops"
        worker._tick()

        assert seen == ["Acme Support", "Field Ops"]

    def test_a_failed_read_leaves_the_label_alone(self, qtbot: QtBot) -> None:
        workspace = FakeWorkspaceProvider(RuntimeError("workspace read failed"))
        worker = make_worker(qtbot, workspace=workspace)
        seen: list[str] = []
        errors: list[str] = []
        worker.workspace_changed.connect(seen.append)
        worker.error_occurred.connect(errors.append)

        worker._tick()  # must neither emit nor restart the worker

        assert workspace.calls == 1
        assert seen == []
        assert errors == []

    def test_an_unreachable_backend_is_not_asked(self, qtbot: QtBot) -> None:
        """An offline machine should not spend a request budget proving it."""
        workspace = FakeWorkspaceProvider("Acme Support")
        service = FakeService(ConnectivityState.BACKEND_UNAVAILABLE)
        worker = make_worker(qtbot, service=service, workspace=workspace)

        worker._tick()

        assert workspace.calls == 0
        assert service.ticks == 1  # the drain still happened

    def test_no_provider_means_no_problem(self, qtbot: QtBot) -> None:
        worker = make_worker(qtbot)
        worker._tick()  # local mode: nothing to refresh, nothing to fail

    def test_the_local_provider_never_returns_anything(self) -> None:
        assert LocalWorkspaceProvider().fetch_workspace() is None


class TestPolicyRefresh:
    def test_policy_is_read_on_every_online_tick(self, qtbot: QtBot) -> None:
        policy = FakePolicyService()
        worker = make_worker(qtbot, policy=policy)

        worker._tick()

        assert policy.calls == 1

    def test_a_failing_policy_read_does_not_disturb_the_drain(
        self, qtbot: QtBot
    ) -> None:
        policy = FakePolicyService(explode=True)
        service = FakeService()
        worker = make_worker(qtbot, service=service, policy=policy)
        errors: list[str] = []
        worker.error_occurred.connect(errors.append)

        worker._tick()

        assert errors == []  # the worker did not restart
        assert service.ticks == 1

    def test_policy_is_not_read_while_offline(self, qtbot: QtBot) -> None:
        policy = FakePolicyService()
        worker = make_worker(
            qtbot,
            service=FakeService(ConnectivityState.BACKEND_UNAVAILABLE),
            policy=policy,
        )

        worker._tick()

        assert policy.calls == 0


class TestTickIsInertWhenNotRunning:
    def test_a_stopped_worker_does_nothing(self, qtbot: QtBot) -> None:
        service = FakeService()
        workspace = FakeWorkspaceProvider("Acme Support")
        worker = SyncWorker(
            service,  # type: ignore[arg-type]
            workspace_provider=workspace,
        )
        worker._running = False

        worker._tick()

        assert service.ticks == 0
        assert workspace.calls == 0
