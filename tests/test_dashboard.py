from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models on Base.metadata
from app.config import get_settings
from app.database import Base
from app.models.client import Client
from app.models.deployable_task import DeployableTask
from app.models.deployment_execution import DeploymentExecution, ExecutionStatus
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.user import User, UserRole
from app.services.dashboard import current_deployment_status, deployment_history
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user

# Matches whatever machine_group_id the app is actually configured to treat as "the
# deploy team" (task_api_deployable_machine_group_id, default 13 / "Team Rajib") — this
# is who's allowed to deploy (require_deploy_team_member), a *different* axis from who's
# allowed to approve (can_approve_deployment_request, which follows the REQUESTER's own
# team instead — see the real bug this was fixed for: a team lead of an unrelated team
# couldn't approve their own team's request because approval was wrongly scoped to this
# one team too). Pulled from settings rather than hardcoded so these tests can't
# silently drift from app/auth.py.
DEPLOY_TEAM_MACHINE_GROUP_ID = get_settings().task_api_deployable_machine_group_id
# Deliberately far from DEPLOY_TEAM_MACHINE_GROUP_ID so tests can't accidentally collide
# with it regardless of what that setting's default happens to be.
REQUESTER_TEAM_MACHINE_GROUP_ID = DEPLOY_TEAM_MACHINE_GROUP_ID + 1000


@pytest.fixture()
def db_session():
    """Standalone in-memory DB for unit-testing current_deployment_status() directly,
    without going through the HTTP layer (same pattern as tests/test_reports.py)."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _completed_request(db, *, client_id, environment, git_branch, commit_hash, requester_id, completed_at):
    request = DeploymentRequest(
        task_id="PR-1",
        client_id=client_id,
        environment=environment,
        git_branch=git_branch,
        commit_hash=commit_hash,
        requested_by=requester_id,
        status=RequestStatus.completed,
        created_at=completed_at - timedelta(hours=1),
    )
    db.add(request)
    db.flush()
    db.add(
        DeploymentExecution(
            request_id=request.id,
            executed_by=requester_id,
            claimed_at=completed_at,
            started_at=completed_at,
            completed_at=completed_at,
            status=ExecutionStatus.completed,
        )
    )
    return request


def _add_deployable_task(session, *, id, task_id, client_name, target, target_status="PLANNED"):
    task = DeployableTask(
        id=id,
        order_id=id,
        task_id=task_id,
        item_name="Some Item",
        client_name=client_name,
        pos_id="0040",
        target=target,
        target_status=target_status,
    )
    session.add(task)
    return task


# --- current_deployment_status() unit tests -------------------------------------------


def test_current_deployment_status_empty_when_nothing_completed(db_session):
    assert current_deployment_status(db_session) == []


def test_current_deployment_status_returns_completed_deployment(db_session):
    db_session.add(Client(id=1, name="CRM"))
    db_session.add(User(id=1, name="Rajib Ahamad", role=UserRole.developer))
    db_session.commit()
    _completed_request(
        db_session,
        client_id=1,
        environment=DeploymentEnvironment.live,
        git_branch="release/v12",
        commit_hash="a1b2c3d",
        requester_id=1,
        completed_at=datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc),
    )
    db_session.commit()

    rows = current_deployment_status(db_session)

    assert len(rows) == 1
    assert rows[0].client_name == "CRM"
    assert rows[0].environment == "live"
    assert rows[0].git_branch == "release/v12"
    assert rows[0].commit_hash == "a1b2c3d"
    assert rows[0].deployed_by == "Rajib Ahamad"


def test_current_deployment_status_includes_requested_by(db_session):
    db_session.add(Client(id=1, name="CRM"))
    db_session.add(User(id=1, name="Alice Requester", role=UserRole.developer))
    db_session.add(User(id=2, name="Bob Deployer", role=UserRole.developer))
    db_session.commit()
    request = DeploymentRequest(
        task_id="PR-1", client_id=1, environment=DeploymentEnvironment.live,
        git_branch="release/v12", commit_hash="a1b2c3d", requested_by=1,
        status=RequestStatus.completed, created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    db_session.add(request)
    db_session.flush()
    db_session.add(
        DeploymentExecution(
            request_id=request.id,
            executed_by=2,
            claimed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
            started_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
            completed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
            status=ExecutionStatus.completed,
        )
    )
    db_session.commit()

    rows = current_deployment_status(db_session)

    assert rows[0].requested_by == "Alice Requester"
    assert rows[0].deployed_by == "Bob Deployer"


def test_current_deployment_status_orders_latest_deployed_first(db_session):
    db_session.add(Client(id=1, name="CRM"))
    db_session.add(Client(id=2, name="Acme Corp"))
    db_session.add(User(id=1, name="Rajib Ahamad", role=UserRole.developer))
    db_session.commit()
    older = datetime(2026, 7, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 7, 30, tzinfo=timezone.utc)
    _completed_request(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        git_branch="release/v11", commit_hash="old0000", requester_id=1, completed_at=older,
    )
    _completed_request(
        db_session, client_id=2, environment=DeploymentEnvironment.live,
        git_branch="release/v12", commit_hash="new1111", requester_id=1, completed_at=newer,
    )
    db_session.commit()

    rows = current_deployment_status(db_session)

    # CRM's own deployment is older than Acme's, so Acme (the more recently deployed
    # client) sorts first, not alphabetically.
    assert [r.client_name for r in rows] == ["Acme Corp", "CRM"]


def test_current_deployment_status_picks_latest_per_client_and_environment(db_session):
    db_session.add(Client(id=1, name="CRM"))
    db_session.add(User(id=1, name="Rajib Ahamad", role=UserRole.developer))
    db_session.commit()
    older = datetime(2026, 7, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 7, 30, tzinfo=timezone.utc)
    _completed_request(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        git_branch="release/v11", commit_hash="old0000", requester_id=1, completed_at=older,
    )
    _completed_request(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        git_branch="release/v12", commit_hash="new1111", requester_id=1, completed_at=newer,
    )
    db_session.commit()

    rows = current_deployment_status(db_session)

    assert len(rows) == 1
    assert rows[0].git_branch == "release/v12"
    assert rows[0].commit_hash == "new1111"


def test_current_deployment_status_keeps_test_and_live_separate(db_session):
    db_session.add(Client(id=1, name="CRM"))
    db_session.add(User(id=1, name="Rajib Ahamad", role=UserRole.developer))
    db_session.commit()
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    _completed_request(
        db_session, client_id=1, environment=DeploymentEnvironment.test,
        git_branch="develop", commit_hash="test0001", requester_id=1, completed_at=now,
    )
    _completed_request(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        git_branch="release/v12", commit_hash="live0001", requester_id=1, completed_at=now,
    )
    db_session.commit()

    rows = current_deployment_status(db_session)

    assert {(r.environment, r.git_branch) for r in rows} == {("test", "develop"), ("live", "release/v12")}


def test_current_deployment_status_ignores_rejected_and_pending_requests(db_session):
    db_session.add(Client(id=1, name="CRM"))
    db_session.add(User(id=1, name="Rajib Ahamad", role=UserRole.developer))
    db_session.commit()
    db_session.add(
        DeploymentRequest(
            task_id="PR-2",
            client_id=1,
            environment=DeploymentEnvironment.live,
            git_branch="feature/broken",
            commit_hash="dead000",
            requested_by=1,
            status=RequestStatus.rejected,
            created_at=datetime.now(timezone.utc),
        )
    )
    db_session.add(
        DeploymentRequest(
            task_id="PR-3",
            client_id=1,
            environment=DeploymentEnvironment.live,
            git_branch="feature/pending",
            commit_hash="ffff000",
            requested_by=1,
            status=RequestStatus.pending_approval,
            created_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    assert current_deployment_status(db_session) == []


def test_current_deployment_status_filters_by_client_environment_task_id(db_session):
    db_session.add(Client(id=1, name="CRM"))
    db_session.add(Client(id=2, name="Acme Corp"))
    db_session.add(User(id=1, name="Rajib Ahamad", role=UserRole.developer))
    db_session.commit()
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    crm_live = _completed_request(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        git_branch="release/v12", commit_hash="a1b2c3d", requester_id=1, completed_at=now,
    )
    crm_live.task_id = "PR-03045"
    acme_test = _completed_request(
        db_session, client_id=2, environment=DeploymentEnvironment.test,
        git_branch="develop", commit_hash="deadbee", requester_id=1, completed_at=now,
    )
    acme_test.task_id = "PR-99999"
    db_session.commit()

    assert [r.client_name for r in current_deployment_status(db_session, client_id=1)] == ["CRM"]
    assert [r.client_name for r in current_deployment_status(db_session, environment=DeploymentEnvironment.test)] == [
        "Acme Corp"
    ]
    assert [r.client_name for r in current_deployment_status(db_session, task_id="03045")] == ["CRM"]
    assert current_deployment_status(db_session, client_id=1, environment=DeploymentEnvironment.test) == []


# --- deployment_history() unit tests ---------------------------------------------------


def test_deployment_history_returns_every_deployment_not_just_latest(db_session):
    db_session.add(Client(id=1, name="CRM"))
    db_session.add(User(id=1, name="Rajib Ahamad", role=UserRole.developer))
    db_session.commit()
    older = datetime(2026, 7, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 7, 30, tzinfo=timezone.utc)
    _completed_request(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        git_branch="release/v11", commit_hash="old0000", requester_id=1, completed_at=older,
    )
    _completed_request(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        git_branch="release/v12", commit_hash="new1111", requester_id=1, completed_at=newer,
    )
    db_session.commit()

    rows = deployment_history(db_session)

    assert [r.commit_hash for r in rows] == ["new1111", "old0000"]  # newest first, both kept


# --- Web UI (router) tests, now behind login ------------------------------------------


def test_protected_routes_redirect_anonymous_visitors_to_login(web):
    client, _session = web
    for path in ("/dashboard", "/requests", "/requests/new"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/login", path


def test_home_redirects_to_dashboard_regardless_of_login(web):
    client, _session = web
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"


def test_dashboard_shows_empty_state_when_nothing_deployed(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "No deployments have completed yet" in response.text


def _seed_two_completed_deployments(session):
    session.add(Client(id=1, name="CRM"))
    session.add(Client(id=2, name="Acme Corp"))
    session.commit()
    _completed_request(
        session, client_id=1, environment=DeploymentEnvironment.live,
        git_branch="release/v12", commit_hash="a1b2c3d", requester_id=1,
        completed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    ).task_id = "PR-03045"
    _completed_request(
        session, client_id=2, environment=DeploymentEnvironment.test,
        git_branch="develop", commit_hash="deadbee", requester_id=1,
        completed_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
    ).task_id = "PR-99999"
    session.commit()


def test_dashboard_filters_by_client(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    _seed_two_completed_deployments(session)
    login_as(client, "rajib")

    response = client.get("/dashboard", params={"client_id": "1"})

    assert response.status_code == 200
    # "Acme Corp" itself still appears in the client filter dropdown — check the row data
    # (branch/task id), not the client name, to actually confirm the table is filtered.
    assert "release/v12" in response.text
    assert "develop" not in response.text


def test_dashboard_filters_by_task_id_substring(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    _seed_two_completed_deployments(session)
    login_as(client, "rajib")

    response = client.get("/dashboard", params={"task_id": "03045"})

    assert response.status_code == 200
    assert "PR-03045" in response.text
    assert "PR-99999" not in response.text


def test_dashboard_history_lists_every_deployment(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    _seed_two_completed_deployments(session)
    login_as(client, "rajib")

    response = client.get("/dashboard/history")

    assert response.status_code == 200
    assert "CRM" in response.text
    assert "Acme Corp" in response.text


def test_dashboard_export_xlsx_returns_workbook_with_filtered_rows(web):
    from io import BytesIO

    from openpyxl import load_workbook

    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    _seed_two_completed_deployments(session)
    login_as(client, "rajib")

    response = client.get("/dashboard/export.xlsx", params={"client_id": "1"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0][0] == "Client"  # header row
    assert rows[1][0] == "CRM"
    assert len(rows) == 2  # header + the one filtered row, Acme excluded


def test_dashboard_history_export_xlsx_includes_every_deployment(web):
    from io import BytesIO

    from openpyxl import load_workbook

    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    _seed_two_completed_deployments(session)
    login_as(client, "rajib")

    response = client.get("/dashboard/history/export.xlsx")

    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook.active
    client_names = {row[0] for row in sheet.iter_rows(min_row=2, values_only=True)}
    assert client_names == {"CRM", "Acme Corp"}


def test_new_request_form_lists_clients_and_deployable_tasks(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="CRM"))
    _add_deployable_task(session, id=100, task_id="PR-03045", client_name="CRM", target="live")
    session.commit()
    login_as(client, "rajib")

    response = client.get("/requests/new")

    assert response.status_code == 200
    assert "CRM" in response.text
    assert "PR-03045" in response.text
    assert "+ Add new client" in response.text
    assert "Rajib Ahamad" in response.text  # static "Requested by" field, not a dropdown
    assert "requested_by" not in response.text  # no such form field anymore


def test_new_request_form_only_lists_planned_tasks(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    _add_deployable_task(session, id=100, task_id="PR-PLANNED", client_name="CRM", target="live")
    _add_deployable_task(
        session, id=101, task_id="PR-DONE", client_name="CRM", target="live", target_status="COMPLETED"
    )
    session.commit()
    login_as(client, "rajib")

    response = client.get("/requests/new")

    assert "PR-PLANNED" in response.text
    assert "PR-DONE" not in response.text


def test_new_request_form_encodes_empty_client_name_for_internal_tasks(web):
    # Regression guard for a bug where the auto-fill JS's `if (clientName)` check treated
    # an empty string the same as "no data" and skipped resetting the Client field — the
    # template must render an explicit empty data-client-name (not omit the attribute or
    # fall back to some placeholder text) so the script can tell "this task has no
    # client" apart from "no task selected yet."
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    _add_deployable_task(session, id=100, task_id="PR-INTERNAL", client_name=None, target="live")
    _add_deployable_task(session, id=101, task_id="PR-CLIENT", client_name="Acme Corp", target="test")
    session.commit()
    login_as(client, "rajib")

    response = client.get("/requests/new")

    assert 'data-id="100"' in response.text
    assert 'data-client-name=""' in response.text  # the internal task's explicit empty value
    assert 'data-id="101"' in response.text
    assert 'data-client-name="Acme Corp"' in response.text


def test_sync_deployable_tasks_now_requires_login(web):
    client, _session = web
    response = client.post("/requests/new/sync-deployable-tasks", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_sync_deployable_tasks_now_shows_success_notice(web, monkeypatch):
    # Doesn't hit the real CRM — monkeypatches the sync function itself (this
    # environment can't reach the CRM API anyway), just checks the button's redirect +
    # flash-message wiring in app/routers/dashboard.py's sync_deployable_tasks_now().
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    class FakeResult:
        total = 5

    monkeypatch.setattr("app.routers.dashboard.sync_deployable_tasks", lambda db, provider: FakeResult())

    response = client.post("/requests/new/sync-deployable-tasks", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/requests/new?synced=5"
    follow_up = client.get(response.headers["location"])
    assert "Synced 5 deployable task(s) from the CRM." in follow_up.text


def test_sync_deployable_tasks_now_shows_error_on_failure(web, monkeypatch):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    def _raise(db, provider):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.routers.dashboard.sync_deployable_tasks", _raise)

    response = client.post("/requests/new/sync-deployable-tasks", follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/requests/new?sync_error=")
    follow_up = client.get(location)
    assert "Could not sync from the CRM" in follow_up.text
    assert "connection refused" in follow_up.text


def test_create_request_uses_deployable_task_and_logged_in_user(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="CRM"))
    _add_deployable_task(session, id=100, task_id="PR-03045", client_name="CRM", target="live")
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests",
        data={
            "deployable_task_ids": "100",
            "client_id": "1",
            "environment": "live",
            "git_branch": "release/v12",
            "commit_hash": "a1b2c3d",
            "version": "V12",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    request = session.query(DeploymentRequest).one()
    assert request.status == RequestStatus.pending_approval
    assert request.task_id == "PR-03045"  # copied from the DeployableTask, not typed
    assert request.client_id == 1
    assert request.requested_by == 1  # the logged-in user, not a form field
    assert request.version == "V12"


def test_create_request_rejects_unknown_deployable_task(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="CRM"))
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests",
        data={
            "deployable_task_ids": "999",
            "client_id": "1",
            "environment": "live",
            "git_branch": "release/v12",
            "commit_hash": "a1b2c3d",
            "version": "V12",
        },
    )

    assert response.status_code == 400
    assert "Select at least one Task ID from the list" in response.text
    assert session.query(DeploymentRequest).count() == 0


def test_create_request_rejects_blank_deployable_task_ids(web):
    # Mirrors what the hidden field looks like when nothing was ever successfully added
    # from the Task ID search box (request_form.html's JS).
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="CRM"))
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests",
        data={
            "deployable_task_ids": "",
            "client_id": "1",
            "environment": "live",
            "git_branch": "release/v12",
            "commit_hash": "a1b2c3d",
            "version": "V12",
        },
    )

    assert response.status_code == 400
    assert "Select at least one Task ID from the list" in response.text
    assert session.query(DeploymentRequest).count() == 0


def test_create_request_combines_multiple_tasks_for_the_same_client(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="CRM"))
    _add_deployable_task(session, id=100, task_id="PR-03045", client_name="CRM", target="live")
    _add_deployable_task(session, id=101, task_id="PR-03046", client_name="CRM", target="live")
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests",
        data={
            "deployable_task_ids": "100,101",
            "client_id": "1",
            "environment": "live",
            "git_branch": "release/v12",
            "commit_hash": "a1b2c3d",
            "version": "V12",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    request = session.query(DeploymentRequest).one()
    assert request.task_id == "PR-03045, PR-03046"


def test_create_request_rejects_tasks_from_different_clients(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="CRM"))
    _add_deployable_task(session, id=100, task_id="PR-03045", client_name="CRM", target="live")
    _add_deployable_task(session, id=101, task_id="PR-99999", client_name="Acme Corp", target="live")
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests",
        data={
            "deployable_task_ids": "100,101",
            "client_id": "1",
            "environment": "live",
            "git_branch": "release/v12",
            "commit_hash": "a1b2c3d",
            "version": "V12",
        },
    )

    assert response.status_code == 400
    assert "must belong to the same client" in response.text
    assert session.query(DeploymentRequest).count() == 0


def test_create_request_rejects_tasks_with_different_targets(web):
    # Regression test for a reported bug: the same client's Test and Live orders (e.g.
    # two DeployableTask rows both named "PR-02960 — PlanVisu" for "SCT Technology GmbH",
    # one target="test" and one target="live") passed the same-client check and got
    # combined into one request — but a request has exactly one `environment`, so this
    # would silently deploy one of the two orders to the wrong system.
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="CRM"))
    _add_deployable_task(session, id=100, task_id="PR-02960", client_name="CRM", target="live")
    _add_deployable_task(session, id=101, task_id="PR-02960", client_name="CRM", target="test")
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests",
        data={
            "deployable_task_ids": "100,101",
            "client_id": "1",
            "environment": "live",
            "git_branch": "release/v12",
            "commit_hash": "a1b2c3d",
            "version": "V12",
        },
    )

    assert response.status_code == 400
    assert "must be for the same system" in response.text
    assert session.query(DeploymentRequest).count() == 0


def test_create_request_ignores_duplicate_task_ids_in_the_list(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="CRM"))
    _add_deployable_task(session, id=100, task_id="PR-03045", client_name="CRM", target="live")
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests",
        data={
            "deployable_task_ids": "100,100",
            "client_id": "1",
            "environment": "live",
            "git_branch": "release/v12",
            "commit_hash": "a1b2c3d",
            "version": "V12",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    request = session.query(DeploymentRequest).one()
    assert request.task_id == "PR-03045"


def test_create_request_missing_version_rerenders_form_with_error(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="CRM"))
    _add_deployable_task(session, id=100, task_id="PR-03045", client_name="CRM", target="live")
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests",
        data={
            "deployable_task_ids": "100",
            "client_id": "1",
            "environment": "live",
            "git_branch": "release/v12",
            "commit_hash": "a1b2c3d",
            "version": "  ",
        },
    )

    assert response.status_code == 400
    assert session.query(DeploymentRequest).count() == 0


def test_create_request_with_new_client_name_creates_client_on_the_fly(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    _add_deployable_task(session, id=100, task_id="PR-03045", client_name="Acme Corp", target="test")
    session.commit()
    login_as(client, "rajib")
    assert session.query(Client).count() == 0

    response = client.post(
        "/requests",
        data={
            "deployable_task_ids": "100",
            "client_id": "__new__",
            "new_client_name": "  Acme Corp  ",
            "environment": "test",
            "git_branch": "develop",
            "commit_hash": "abc1234",
            "version": "V1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    new_client = session.query(Client).one()
    assert new_client.name == "Acme Corp"  # whitespace stripped
    request = session.query(DeploymentRequest).one()
    assert request.client_id == new_client.id


def test_create_request_missing_client_selection_rerenders_form_with_error(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    _add_deployable_task(session, id=100, task_id="PR-03045", client_name="CRM", target="test")
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests",
        data={
            "deployable_task_ids": "100",
            "client_id": "",
            "environment": "test",
            "git_branch": "develop",
            "commit_hash": "abc1234",
            "version": "V1",
        },
    )

    assert response.status_code == 400
    assert "Select a client" in response.text
    assert session.query(DeploymentRequest).count() == 0


def _seed_pending_request(session):
    session.add(Client(id=1, name="CRM"))
    # Requester and Lead share a team that is deliberately NOT the deploy team — approval
    # follows the requester's own team (can_approve_deployment_request), which is a
    # different axis from who's allowed to deploy (require_deploy_team_member, scoped to
    # DEPLOY_TEAM_MACHINE_GROUP_ID specifically). Mixing these up was the actual bug.
    make_user(
        session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD,
        machine_group_id=REQUESTER_TEAM_MACHINE_GROUP_ID,
    )
    make_user(
        session, id=2, name="Lead", role=UserRole.team_lead, username="lead", password=DEFAULT_TEST_PASSWORD,
        machine_group_id=REQUESTER_TEAM_MACHINE_GROUP_ID,
    )
    # Deployer belongs to the deploy team, not the requester's team — required for
    # deploy to succeed under require_deploy_team_member() (app/auth.py).
    make_user(
        session, id=3, name="Deployer", username="deployer", password=DEFAULT_TEST_PASSWORD,
        machine_group_id=DEPLOY_TEAM_MACHINE_GROUP_ID,
    )
    session.commit()
    request = DeploymentRequest(
        task_id="PR-03045",
        client_id=1,
        environment=DeploymentEnvironment.live,
        git_branch="release/v12",
        commit_hash="a1b2c3d",
        version="V12",
        requested_by=1,
        status=RequestStatus.pending_approval,
        created_at=datetime.now(timezone.utc),
    )
    session.add(request)
    session.commit()
    return request


def test_requests_queue_hides_approve_reject_from_non_approvers(web):
    client, session = web
    _seed_pending_request(session)
    login_as(client, "requester")  # plain developer, not a team_lead/admin

    response = client.get("/requests")

    assert response.status_code == 200
    assert "Awaiting the requester's team lead" in response.text
    assert 'action="/requests/1/approve"' not in response.text


def test_requests_queue_shows_approve_reject_to_team_lead(web):
    client, session = web
    _seed_pending_request(session)
    login_as(client, "lead")

    response = client.get("/requests")

    assert 'action="/requests/1/approve"' in response.text
    assert 'action="/requests/1/reject"' in response.text


def test_requests_queue_hides_approve_reject_from_team_lead_of_a_different_team(web):
    client, session = web
    _seed_pending_request(session)
    make_user(
        session, id=4, name="Other Team Lead", role=UserRole.team_lead, username="otherlead",
        password=DEFAULT_TEST_PASSWORD, machine_group_id=REQUESTER_TEAM_MACHINE_GROUP_ID + 1,
    )
    session.commit()
    login_as(client, "otherlead")

    response = client.get("/requests")

    assert "Awaiting the requester's team lead" in response.text
    assert 'action="/requests/1/approve"' not in response.text


def test_approve_requires_approver_role(web):
    client, session = web
    request = _seed_pending_request(session)
    login_as(client, "requester")  # plain developer

    response = client.post(f"/requests/{request.id}/approve")

    assert response.status_code == 403
    session.refresh(request)
    assert request.status == RequestStatus.pending_approval


def test_approve_rejects_team_lead_of_a_different_team(web):
    # Only the REQUESTER's own team lead may approve — not just any team_lead in the CRM
    # roster, and specifically not a team_lead of some unrelated team (including the
    # deploy team itself) — app/auth.py's can_approve_deployment_request().
    client, session = web
    request = _seed_pending_request(session)
    make_user(
        session, id=4, name="Other Team Lead", role=UserRole.team_lead, username="otherlead",
        password=DEFAULT_TEST_PASSWORD, machine_group_id=REQUESTER_TEAM_MACHINE_GROUP_ID + 1,
    )
    session.commit()
    login_as(client, "otherlead")

    response = client.post(f"/requests/{request.id}/approve")

    assert response.status_code == 403
    session.refresh(request)
    assert request.status == RequestStatus.pending_approval


def test_approve_rejects_deploy_team_lead_who_is_not_the_requesters_own_lead(web):
    # Regression test for the actual reported bug: being a team_lead who belongs to the
    # deploy team does NOT make you the requester's team lead — approval must follow the
    # requester's own team, not the deploy team (app/auth.py's
    # can_approve_deployment_request(), which is deliberately independent of
    # require_deploy_team_member()).
    client, session = web
    request = _seed_pending_request(session)
    make_user(
        session, id=8, name="Deploy Team Lead", role=UserRole.team_lead, username="deployteamlead",
        password=DEFAULT_TEST_PASSWORD, machine_group_id=DEPLOY_TEAM_MACHINE_GROUP_ID,
    )
    session.commit()
    login_as(client, "deployteamlead")

    response = client.post(f"/requests/{request.id}/approve")

    assert response.status_code == 403
    session.refresh(request)
    assert request.status == RequestStatus.pending_approval


def test_approve_allows_admin_regardless_of_team(web):
    client, session = web
    request = _seed_pending_request(session)
    make_user(
        session, id=5, name="Root Admin", role=UserRole.admin, username="root",
        password=DEFAULT_TEST_PASSWORD, machine_group_id=None,
    )
    session.commit()
    login_as(client, "root")

    response = client.post(f"/requests/{request.id}/approve", follow_redirects=False)

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.approved


def test_start_requires_deploy_team_membership(web):
    client, session = web
    request = _seed_pending_request(session)
    login_as(client, "lead")
    client.post(f"/requests/{request.id}/approve")
    make_user(
        session, id=6, name="Outside Developer", username="outsider",
        password=DEFAULT_TEST_PASSWORD, machine_group_id=DEPLOY_TEAM_MACHINE_GROUP_ID + 1,
    )
    session.commit()
    login_as(client, "outsider")

    response = client.post(f"/requests/{request.id}/start")

    assert response.status_code == 403
    session.refresh(request)
    assert request.status == RequestStatus.approved


def test_start_before_approval_is_rejected_with_409(web):
    client, session = web
    request = _seed_pending_request(session)
    login_as(client, "deployer")

    response = client.post(f"/requests/{request.id}/start")

    assert response.status_code == 409
    session.refresh(request)
    assert request.status == RequestStatus.pending_approval


def test_start_moves_request_to_in_progress_and_records_who(web):
    client, session = web
    request = _seed_pending_request(session)
    login_as(client, "lead")
    client.post(f"/requests/{request.id}/approve")
    login_as(client, "deployer")

    response = client.post(f"/requests/{request.id}/start", follow_redirects=False)

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.in_progress

    execution = session.query(DeploymentExecution).filter_by(request_id=request.id).one()
    assert execution.executed_by == 3  # "deployer"
    assert execution.status == ExecutionStatus.in_progress
    assert execution.started_at is not None
    assert execution.completed_at is None


def test_deploy_before_start_is_rejected_with_409(web):
    client, session = web
    request = _seed_pending_request(session)
    login_as(client, "lead")
    client.post(f"/requests/{request.id}/approve")
    login_as(client, "deployer")

    response = client.post(f"/requests/{request.id}/deploy")

    assert response.status_code == 409
    session.refresh(request)
    assert request.status == RequestStatus.approved


def test_deploy_requires_deploy_team_membership(web):
    client, session = web
    request = _seed_pending_request(session)
    login_as(client, "lead")
    client.post(f"/requests/{request.id}/approve")
    login_as(client, "deployer")
    client.post(f"/requests/{request.id}/start")
    make_user(
        session, id=6, name="Outside Developer", username="outsider",
        password=DEFAULT_TEST_PASSWORD, machine_group_id=DEPLOY_TEAM_MACHINE_GROUP_ID + 1,
    )
    session.commit()
    login_as(client, "outsider")

    response = client.post(f"/requests/{request.id}/deploy")

    assert response.status_code == 403
    session.refresh(request)
    assert request.status == RequestStatus.in_progress


def test_deploy_allows_admin_regardless_of_team(web):
    client, session = web
    request = _seed_pending_request(session)
    login_as(client, "lead")
    client.post(f"/requests/{request.id}/approve")
    make_user(
        session, id=7, name="Root Admin", role=UserRole.admin, username="root2",
        password=DEFAULT_TEST_PASSWORD, machine_group_id=None,
    )
    session.commit()
    login_as(client, "root2")
    client.post(f"/requests/{request.id}/start")

    response = client.post(f"/requests/{request.id}/deploy", follow_redirects=False)

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.completed


def test_deploy_can_be_done_by_a_different_deploy_team_member_than_who_started_it(web):
    # "Any member of the deploy team" (project_plan.md Section 3), not a personal claim
    # lock — starting and completing don't have to be the same person.
    client, session = web
    request = _seed_pending_request(session)
    login_as(client, "lead")
    client.post(f"/requests/{request.id}/approve")
    login_as(client, "deployer")
    client.post(f"/requests/{request.id}/start")
    make_user(
        session, id=9, name="Second Deployer", username="deployer2",
        password=DEFAULT_TEST_PASSWORD, machine_group_id=DEPLOY_TEAM_MACHINE_GROUP_ID,
    )
    session.commit()
    login_as(client, "deployer2")

    response = client.post(f"/requests/{request.id}/deploy", follow_redirects=False)

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.completed
    execution = session.query(DeploymentExecution).filter_by(request_id=request.id).one()
    assert execution.executed_by == 3  # still attributed to whoever started it
    assert execution.completed_at is not None


def test_approve_then_start_then_deploy_updates_dashboard_with_logged_in_users(web):
    client, session = web
    request = _seed_pending_request(session)

    login_as(client, "lead")
    approve_response = client.post(f"/requests/{request.id}/approve", follow_redirects=False)
    assert approve_response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.approved

    login_as(client, "deployer")
    start_response = client.post(f"/requests/{request.id}/start", follow_redirects=False)
    assert start_response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.in_progress
    assert "In Progress" in client.get("/requests").text

    deploy_response = client.post(f"/requests/{request.id}/deploy", follow_redirects=False)
    assert deploy_response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.completed

    execution = session.query(DeploymentExecution).filter_by(request_id=request.id).one()
    assert execution.executed_by == 3  # "deployer", not a form-picked name

    dashboard_response = client.get("/dashboard")
    assert "CRM" in dashboard_response.text
    assert "release/v12" in dashboard_response.text
    assert "a1b2c3d" in dashboard_response.text
    assert "Deployer" in dashboard_response.text


def test_reject_marks_request_rejected_and_keeps_it_off_the_dashboard(web):
    client, session = web
    request = _seed_pending_request(session)
    login_as(client, "lead")

    response = client.post(
        f"/requests/{request.id}/reject", data={"comment": "wrong branch"}, follow_redirects=False
    )

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.rejected

    dashboard_response = client.get("/dashboard")
    assert "No deployments have completed yet" in dashboard_response.text


def test_approve_twice_is_rejected_with_409(web):
    client, session = web
    request = _seed_pending_request(session)
    login_as(client, "lead")
    client.post(f"/requests/{request.id}/approve")

    second_attempt = client.post(f"/requests/{request.id}/approve")

    assert second_attempt.status_code == 409


def test_deploy_before_approval_is_rejected_with_409(web):
    client, session = web
    request = _seed_pending_request(session)
    login_as(client, "deployer")

    response = client.post(f"/requests/{request.id}/deploy")

    assert response.status_code == 409


def test_approve_unknown_request_returns_404(web):
    client, session = web
    make_user(
        session, id=2, name="Lead", role=UserRole.team_lead, username="lead", password=DEFAULT_TEST_PASSWORD,
        machine_group_id=DEPLOY_TEAM_MACHINE_GROUP_ID,
    )
    session.commit()
    login_as(client, "lead")

    response = client.post("/requests/999/approve")

    assert response.status_code == 404


# --- Database Dump & Restore requests (no approval required) -------------------------


def test_new_request_form_shows_test_local_server_suggestions(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.get("/requests/new")

    assert response.status_code == 200
    assert "crm.test.local" in response.text
    assert "tmp.test.local" in response.text
    assert "vop.test.local" in response.text


def test_create_db_dump_restore_request_with_restore_source_skips_approval(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests/db-dump-restore",
        data={"dump_source": "crm-live", "version": "V12", "restore_source": "crm-staging"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    request = session.query(DeploymentRequest).one()
    assert request.request_type == RequestType.db_dump_restore
    assert request.dump_source == "crm-live"
    assert request.version == "V12"
    assert request.restore_source == "crm-staging"
    assert request.share_with_requestor is False
    assert request.requested_by == 1
    # No approval required — lands straight in the deploy team's queue.
    assert request.status == RequestStatus.approved


def test_create_db_dump_restore_request_with_share_with_requestor(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests/db-dump-restore",
        data={"dump_source": "crm-live", "version": "V10", "share_with_requestor": "on"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    request = session.query(DeploymentRequest).one()
    assert request.share_with_requestor is True
    assert request.restore_source is None
    assert request.version == "V10"
    assert request.status == RequestStatus.approved


def test_create_db_dump_restore_request_requires_dump_source(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests/db-dump-restore",
        data={"dump_source": "  ", "version": "V12", "restore_source": "crm-staging"},
    )

    assert response.status_code == 400
    assert "Dump source is required" in response.text
    assert session.query(DeploymentRequest).count() == 0


def test_create_db_dump_restore_request_requires_version(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests/db-dump-restore",
        data={"dump_source": "crm-live", "version": "  ", "restore_source": "crm-staging"},
    )

    assert response.status_code == 400
    assert "Application version is required" in response.text
    assert session.query(DeploymentRequest).count() == 0


def test_create_db_dump_restore_request_rejects_both_restore_and_share(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests/db-dump-restore",
        data={
            "dump_source": "crm-live",
            "version": "V12",
            "restore_source": "crm-staging",
            "share_with_requestor": "on",
        },
    )

    assert response.status_code == 400
    assert "not both" in response.text
    assert session.query(DeploymentRequest).count() == 0


def test_create_db_dump_restore_request_rejects_neither_restore_nor_share(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests/db-dump-restore", data={"dump_source": "crm-live", "version": "V12"}
    )

    assert response.status_code == 400
    assert "Provide a restore source" in response.text
    assert session.query(DeploymentRequest).count() == 0


# --- Test.local deployment requests (no approval required) ---------------------------


def test_create_test_local_request_skips_approval(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests/test-local",
        data={"server": "crm.test.local", "git_branch": "feature/my-branch", "changes_description": "quick check"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    request = session.query(DeploymentRequest).one()
    assert request.request_type == RequestType.test_local
    assert request.server == "crm.test.local"
    assert request.git_branch == "feature/my-branch"
    assert request.changes_description == "quick check"
    assert request.requested_by == 1
    assert request.status == RequestStatus.approved


def test_create_test_local_request_rejects_non_test_local_host(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.post(
        "/requests/test-local", data={"server": "crm-live.example.com", "git_branch": "develop"}
    )

    assert response.status_code == 400
    assert "test.local" in response.text
    assert session.query(DeploymentRequest).count() == 0


def test_create_test_local_request_requires_branch(web):
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "rajib")

    response = client.post("/requests/test-local", data={"server": "crm.test.local", "git_branch": "  "})

    assert response.status_code == 400
    assert "Branch name is required" in response.text
    assert session.query(DeploymentRequest).count() == 0


def test_no_approval_required_requests_go_straight_to_deploy_queue(web):
    # The whole point of these two request types: they never touch pending_approval, so
    # a deploy-team member sees "Start Deployment" immediately, with no team-lead step at
    # all — then the usual start -> deploy sequence applies from there.
    client, session = web
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    make_user(
        session, id=2, name="Deployer", username="deployer", password=DEFAULT_TEST_PASSWORD,
        machine_group_id=DEPLOY_TEAM_MACHINE_GROUP_ID,
    )
    session.commit()
    login_as(client, "rajib")
    client.post("/requests/test-local", data={"server": "tmp.test.local", "git_branch": "develop"})

    login_as(client, "deployer")
    response = client.get("/requests")

    assert response.status_code == 200
    assert "Awaiting the requester's team lead" not in response.text
    request = session.query(DeploymentRequest).one()
    assert f'action="/requests/{request.id}/start"' in response.text

    start_response = client.post(f"/requests/{request.id}/start", follow_redirects=False)
    assert start_response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.in_progress

    deploy_response = client.post(f"/requests/{request.id}/deploy", follow_redirects=False)
    assert deploy_response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.completed
