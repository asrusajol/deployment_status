"""DevOps must tick a pre-flight checklist before a db_dump_restore request can start.

A restored dump carries the source system's cron schedules, worker config and email
settings; started blind, the restore target begins mailing real customers. The gate lives
on the route (start_request), not only on the pop-up — hiding a button is not access
control, so several of these tests POST /start directly.
"""

import re
from datetime import datetime, timezone

import pytest

from app.models.deployment_execution import DeploymentExecution
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user

# Pinned here on purpose instead of imported: this is the contract the pop-up and the
# route share, so a rename in app code should have to change a test too.
CHECKLIST_IDS = [
    "close_scheduled_jobs",
    "restart_workers",
    "check_env_email",
    "remove_email_settings",
]


def _seed(session, *, request_type):
    make_user(
        session, id=1, name="Zunayed Islam", username="zunayed",
        password=DEFAULT_TEST_PASSWORD, role=UserRole.admin,
    )
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = DeploymentRequest(
        task_id="PR-DUMP", requested_by=2, status=RequestStatus.approved,
        request_type=request_type, dump_source="crm-live", restore_source="crm-staging",
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    session.add(request)
    session.commit()
    return request


def _assert_not_started(session, request):
    session.refresh(request)
    assert request.status == RequestStatus.approved
    assert session.query(DeploymentExecution).filter_by(request_id=request.id).count() == 0


def test_dump_start_without_checklist_is_rejected(web):
    client, session = web
    request = _seed(session, request_type=RequestType.db_dump_restore)
    login_as(client, "zunayed")

    response = client.post(f"/requests/{request.id}/start", follow_redirects=False)

    assert response.status_code == 400
    _assert_not_started(session, request)


def test_dump_start_with_partial_checklist_is_rejected(web):
    client, session = web
    request = _seed(session, request_type=RequestType.db_dump_restore)
    login_as(client, "zunayed")

    response = client.post(
        f"/requests/{request.id}/start", data={"checklist": CHECKLIST_IDS[:3]},
        follow_redirects=False,
    )

    assert response.status_code == 400
    _assert_not_started(session, request)


def test_dump_start_with_unknown_id_in_place_of_a_real_one_is_rejected(web):
    # Four values submitted, but one isn't on the list — counting them would let this
    # through, so the route has to check the ids, not the length.
    client, session = web
    request = _seed(session, request_type=RequestType.db_dump_restore)
    login_as(client, "zunayed")

    response = client.post(
        f"/requests/{request.id}/start",
        data={"checklist": CHECKLIST_IDS[:3] + ["something_else"]},
        follow_redirects=False,
    )

    assert response.status_code == 400
    _assert_not_started(session, request)


def test_dump_start_with_full_checklist_moves_to_in_progress(web):
    client, session = web
    request = _seed(session, request_type=RequestType.db_dump_restore)
    login_as(client, "zunayed")

    response = client.post(
        f"/requests/{request.id}/start", data={"checklist": CHECKLIST_IDS},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.in_progress
    execution = session.query(DeploymentExecution).filter_by(request_id=request.id).one()
    assert execution.executed_by == 1


@pytest.mark.parametrize("request_type", [RequestType.standard, RequestType.test_local])
def test_other_request_types_start_without_a_checklist(web, request_type):
    client, session = web
    request = _seed(session, request_type=request_type)
    login_as(client, "zunayed")

    response = client.post(f"/requests/{request.id}/start", follow_redirects=False)

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.in_progress


def _row_html(page: str, request_id: int) -> str:
    """The <tr> for one request — scoped so assertions can't match markup elsewhere on
    the page (the queue also embeds a JSON blob of every active request). Rows carry no
    id attribute, so the row is found by its own Return button, which every approved row
    has for a deploy-team viewer."""
    for row in re.findall(r"<tr>.*?</tr>", page, re.S):
        if f'data-return-action="/requests/{request_id}/return"' in row:
            return row
    raise AssertionError(f"no row found for request {request_id}")


def _dialog_html(page: str) -> str:
    match = re.search(r'<dialog id="start-checklist-modal".*?</dialog>', page, re.S)
    assert match, "start checklist dialog missing from the page"
    return match.group(0)


def test_dump_row_opens_the_checklist_instead_of_starting_directly(web):
    client, session = web
    request = _seed(session, request_type=RequestType.db_dump_restore)
    login_as(client, "zunayed")

    page = client.get("/requests").text

    row = _row_html(page, request.id)
    # The form tag, not bare `action="..."`: that would also match the button's own
    # data-start-action attribute and fail for the wrong reason.
    assert f'<form method="post" action="/requests/{request.id}/start"' not in row
    assert f'data-start-action="/requests/{request.id}/start"' in row


def test_checklist_dialog_lists_every_item_as_a_checkbox(web):
    client, session = web
    _seed(session, request_type=RequestType.db_dump_restore)
    login_as(client, "zunayed")

    dialog = _dialog_html(client.get("/requests").text)

    for item_id in CHECKLIST_IDS:
        assert f'name="checklist" value="{item_id}"' in dialog
    assert "cronjob" in dialog.lower()
    assert "workers" in dialog.lower()
    assert ".env" in dialog
    assert "basevisu" in dialog.lower()


@pytest.mark.parametrize("request_type", [RequestType.standard, RequestType.test_local])
def test_other_request_types_keep_the_direct_start_button(web, request_type):
    client, session = web
    request = _seed(session, request_type=request_type)
    login_as(client, "zunayed")

    page = client.get("/requests").text

    row = _row_html(page, request.id)
    assert f'<form method="post" action="/requests/{request.id}/start"' in row
    assert "data-start-action" not in row
