"""Type-aware editing for test_local/db_dump_restore requests.

Before this, the edit route and request_edit.html only ever understood `standard`
requests — can_edit_request() blanket-refused the other two types regardless of
status (see the now-rewritten test_a_returned_non_standard_request_is_not_editable
in test_request_return_routes.py). That meant a test_local request sitting in
Pending Deployment with a wrong branch had to be deleted and re-raised, and it
blocked the Return feature for those two types entirely: devops could return a
test_local request, but the requester had no way to act on it.

These tests cover the type-aware request_edit.html + edit_request() POST, and the
widened (but still bounded) editable window in can_edit_request() for test_local/
db_dump_restore: editable while `approved` or `returned`, not once `in_progress`
or later — see app/auth.py.
"""

from datetime import datetime, timezone

import pytest

from app.auth import can_edit_request
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from app.models.request_return import RequestReturn
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user

# Every status from in_progress onward — once a deploy-team member has pressed Start,
# nobody edits underneath them, for any request type and regardless of role. Return
# is the way back from in_progress; completed/failed/rolled_back/withdrawn/rejected are
# all settled outcomes past that point.
POST_START_STATUSES = (
    RequestStatus.in_progress,
    RequestStatus.completed,
    RequestStatus.failed,
    RequestStatus.rolled_back,
    RequestStatus.withdrawn,
    RequestStatus.rejected,
)


def _test_local_request(session, *, status=RequestStatus.approved, requester_id=1):
    request = DeploymentRequest(
        request_type=RequestType.test_local,
        server="crm.test.local",
        git_branch="feature/foo",
        version="V12",
        changes_description="initial",
        requested_by=requester_id,
        status=status,
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    session.add(request)
    session.commit()
    return request


def _db_dump_restore_request(session, *, status=RequestStatus.approved, requester_id=1):
    request = DeploymentRequest(
        request_type=RequestType.db_dump_restore,
        dump_source="crm-live DB",
        version="V12",
        restore_source="crm-staging DB",
        share_with_requestor=False,
        requested_by=requester_id,
        status=status,
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    session.add(request)
    session.commit()
    return request


def _returned_test_local_request(session, *, requester_id=1):
    request = _test_local_request(session, status=RequestStatus.returned, requester_id=requester_id)
    session.add(
        RequestReturn(
            request_id=request.id, reason="branch deleted", returned_by=2,
            returned_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            returned_from=RequestStatus.approved,
        )
    )
    session.commit()
    return request


def test_test_local_request_in_approved_is_editable_by_requester(web):
    client, session = web
    make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _test_local_request(session)
    login_as(client, "requester")

    response = client.get(f"/requests/{request.id}/edit")

    assert response.status_code == 200
    assert 'value="crm.test.local"' in response.text
    assert 'value="feature/foo"' in response.text
    assert 'value="V12"' in response.text
    # Standard-only fields must not be offered for a test_local request.
    assert 'name="client_id"' not in response.text
    assert 'name="environment"' not in response.text
    assert 'name="commit_hash"' not in response.text


def test_test_local_edit_post_saves_and_keeps_request_type(web):
    client, session = web
    make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _test_local_request(session)
    login_as(client, "requester")

    response = client.post(
        f"/requests/{request.id}/edit",
        data={
            "server": "tmp.test.local",
            "git_branch": "feature/bar",
            "version": "V13",
            "changes_description": "updated",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(request)
    assert request.request_type == RequestType.test_local
    assert request.server == "tmp.test.local"
    assert request.git_branch == "feature/bar"
    assert request.version == "V13"


def test_test_local_edit_post_rejects_non_test_local_server(web):
    client, session = web
    make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _test_local_request(session)
    login_as(client, "requester")

    response = client.post(
        f"/requests/{request.id}/edit",
        data={
            "server": "crm.example.com",
            "git_branch": "feature/bar",
            "version": "V13",
        },
    )

    assert response.status_code == 400
    assert "*.test.local" in response.text
    session.refresh(request)
    assert request.server == "crm.test.local"  # unchanged


def test_db_dump_restore_request_in_approved_is_editable(web):
    client, session = web
    make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _db_dump_restore_request(session)
    login_as(client, "requester")

    response = client.get(f"/requests/{request.id}/edit")

    assert response.status_code == 200
    assert 'value="crm-live DB"' in response.text
    assert 'name="client_id"' not in response.text


def test_db_dump_restore_edit_post_saves(web):
    client, session = web
    make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _db_dump_restore_request(session)
    login_as(client, "requester")

    response = client.post(
        f"/requests/{request.id}/edit",
        data={
            "dump_source": "crm-live DB v2",
            "version": "V13",
            "restore_source": "crm-staging DB v2",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(request)
    assert request.dump_source == "crm-live DB v2"
    assert request.version == "V13"
    assert request.restore_source == "crm-staging DB v2"


def test_db_dump_restore_edit_post_enforces_either_or_rule(web):
    client, session = web
    make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _db_dump_restore_request(session)
    login_as(client, "requester")

    response = client.post(
        f"/requests/{request.id}/edit",
        data={
            "dump_source": "crm-live DB v2",
            "version": "V13",
            "restore_source": "crm-staging DB v2",
            "share_with_requestor": "on",
        },
    )

    assert response.status_code == 400
    assert "not both" in response.text
    session.refresh(request)
    assert request.restore_source == "crm-staging DB"  # unchanged


def test_standard_request_in_approved_is_still_not_editable(web):
    # This is the regression that matters — a standard request in `approved` reflects a
    # real decision by a team lead, and that must not be reopened by widening the window
    # for the other two types.
    client, session = web
    make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = DeploymentRequest(
        request_type=RequestType.standard,
        task_id="PR-STD", requested_by=1, status=RequestStatus.approved,
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    session.add(request)
    session.commit()
    login_as(client, "requester")

    response = client.get(f"/requests/{request.id}/edit")

    assert response.status_code == 403


def test_test_local_request_in_progress_is_not_editable(web):
    client, session = web
    make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _test_local_request(session, status=RequestStatus.in_progress)
    login_as(client, "requester")

    response = client.get(f"/requests/{request.id}/edit")

    assert response.status_code == 403


def test_returned_test_local_request_is_editable(web):
    # This is the whole point of the feature: a return unblocks it.
    client, session = web
    make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=2, name="Devops", username="devops", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_test_local_request(session)
    login_as(client, "requester")

    response = client.get(f"/requests/{request.id}/edit")

    assert response.status_code == 200


def test_editing_a_test_local_request_cannot_change_its_request_type(web):
    # Validation must dispatch on the request's OWN stored request_type, never on
    # anything the POST submits — otherwise a crafted POST including standard-only
    # fields could smuggle a type change through.
    client, session = web
    make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _test_local_request(session)
    login_as(client, "requester")

    response = client.post(
        f"/requests/{request.id}/edit",
        data={
            "server": "tmp.test.local",
            "git_branch": "feature/bar",
            "version": "V13",
            # Fields that belong to `standard`/`db_dump_restore` — must be ignored.
            "client_id": "1",
            "environment": "live",
            "commit_hash": "abc123",
            "dump_source": "sneaky",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(request)
    assert request.request_type == RequestType.test_local
    assert request.dump_source is None
    assert request.client_id is None


def _standard_request(session, *, status, requester_id=1):
    request = DeploymentRequest(
        request_type=RequestType.standard,
        task_id="PR-STD", requested_by=requester_id, status=status,
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    session.add(request)
    session.commit()
    return request


@pytest.mark.parametrize("status", POST_START_STATUSES, ids=lambda s: s.value)
def test_standard_request_not_editable_by_requester_once_started_or_settled(web, status):
    client, session = web
    requester = make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _standard_request(session, status=status)

    assert can_edit_request(requester, request) is False


@pytest.mark.parametrize("status", POST_START_STATUSES, ids=lambda s: s.value)
def test_test_local_request_not_editable_by_requester_once_started_or_settled(web, status):
    client, session = web
    requester = make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _test_local_request(session, status=status)

    assert can_edit_request(requester, request) is False


@pytest.mark.parametrize("status", POST_START_STATUSES, ids=lambda s: s.value)
def test_db_dump_restore_request_not_editable_by_requester_once_started_or_settled(web, status):
    client, session = web
    requester = make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _db_dump_restore_request(session, status=status)

    assert can_edit_request(requester, request) is False


@pytest.mark.parametrize("status", POST_START_STATUSES, ids=lambda s: s.value)
def test_admin_cannot_edit_test_local_request_once_started_or_settled(web, status):
    # The admin branch in can_edit_request() must never short-circuit past the status
    # test — an admin is not exempt from "Return, not Edit, once devops has started".
    client, session = web
    admin = make_user(session, id=9, name="Root Admin", username="root", password=DEFAULT_TEST_PASSWORD, role=UserRole.admin)
    session.commit()
    request = _test_local_request(session, status=status, requester_id=1)

    assert can_edit_request(admin, request) is False


def test_post_edit_on_in_progress_test_local_request_is_403_not_a_save(web):
    # Not just the button hidden — the route itself must refuse the write.
    client, session = web
    make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _test_local_request(session, status=RequestStatus.in_progress)
    login_as(client, "requester")

    response = client.post(
        f"/requests/{request.id}/edit",
        data={"server": "tmp.test.local", "git_branch": "feature/bar", "version": "V13"},
    )

    assert response.status_code == 403
    session.refresh(request)
    assert request.server == "crm.test.local"
    assert request.git_branch == "feature/foo"


def test_post_edit_on_in_progress_standard_request_by_admin_is_403(web):
    # The admin case at the route level too, not just the auth-layer unit test above.
    client, session = web
    make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=9, name="Root Admin", username="root", password=DEFAULT_TEST_PASSWORD, role=UserRole.admin)
    session.commit()
    request = _standard_request(session, status=RequestStatus.in_progress)
    login_as(client, "root")

    response = client.post(
        f"/requests/{request.id}/edit",
        data={
            "deployable_task_ids": "1",
            "client_id": "1",
            "environment": "live",
            "git_branch": "release/v13",
            "commit_hash": "e5f6g7h",
            "version": "V13",
        },
    )

    assert response.status_code == 403
