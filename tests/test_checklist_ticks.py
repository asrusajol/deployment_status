"""Ticks are saved one at a time; "before Mark Deployed" terms gate Mark Deployed; and
Return keeps working, with each attempt (round) ticked afresh while earlier rounds stay
in the audit."""

import re
from datetime import datetime, timezone

import pytest

from app.models.checklist import ChecklistConfirmation
from app.models.client import Client
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_checklist_item, make_user

DUMP, LOCAL, STD = RequestType.db_dump_restore, RequestType.test_local, RequestType.standard


def _users(session):
    make_user(session, id=1, name="Zunayed Islam", username="zunayed", password=DEFAULT_TEST_PASSWORD, role=UserRole.admin)
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()


def _request(session, request_type=DUMP, **extra):
    fields = dict(
        task_id="PR-T", requested_by=2, status=RequestStatus.approved, request_type=request_type,
        dump_source="crm-live", restore_source="crm-staging", created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    fields.update(extra)
    request = DeploymentRequest(**fields)
    session.add(request)
    session.commit()
    return request


def _tick(client, request, item, checked=True):
    return client.post(f"/requests/{request.id}/checklist/{item.id}", data={"checked": "1" if checked else "0"})


def _start(client, request):
    return client.post(f"/requests/{request.id}/start", follow_redirects=False)


def _deploy(client, request, **data):
    return client.post(f"/requests/{request.id}/deploy", data=data, follow_redirects=False)


@pytest.fixture()
def setup(web):
    """A dump request with one before-start and two before-Mark-Deployed terms."""
    client, session = web
    _users(session)
    before = make_checklist_item(session, label="Close cronjobs", position=1)
    after_a = make_checklist_item(session, label="After restoration, remove email settings", position=2, due="before_complete")
    after_b = make_checklist_item(session, label="After deploy: smoke test", position=3, due="before_complete")
    request = _request(session)
    login_as(client, "zunayed")
    return client, session, request, before, (after_a, after_b)


def _started(client, request, before):
    _tick(client, request, before)
    assert _start(client, request).status_code == 303


# --- tick endpoint --------------------------------------------------------------------

def test_tick_is_saved_and_untick_removes_it(setup):
    client, session, request, before, _after = setup

    response = _tick(client, request, before)

    assert response.status_code == 200
    assert response.json() == {"due": "before_start", "ticked": 1, "required": 1, "done": True}
    assert session.query(ChecklistConfirmation).filter_by(request_id=request.id).count() == 1

    assert _tick(client, request, before, checked=False).json()["ticked"] == 0
    assert session.query(ChecklistConfirmation).filter_by(request_id=request.id).count() == 0


def test_ticking_twice_keeps_one_row(setup):
    client, session, request, before, _after = setup
    _tick(client, request, before)
    _tick(client, request, before)
    assert session.query(ChecklistConfirmation).filter_by(request_id=request.id).count() == 1


def test_non_deployer_cannot_tick(setup):
    client, session, request, before, _after = setup
    login_as(client, "devone")
    assert _tick(client, request, before).status_code == 403
    assert session.query(ChecklistConfirmation).count() == 0


def test_tick_rejects_term_not_assigned_to_the_requests_type(setup):
    client, session, request, _before, _after = setup
    other = make_checklist_item(session, label="Ping box", request_types=(LOCAL,))
    assert _tick(client, request, other).status_code == 400


def test_tick_unknown_term_is_404(setup):
    client, _session, request, _before, _after = setup
    assert client.post(f"/requests/{request.id}/checklist/999", data={"checked": "1"}).status_code == 404


def test_before_start_ticks_freeze_once_started(setup):
    client, session, request, before, _after = setup
    _started(client, request, before)

    assert _tick(client, request, before, checked=False).status_code == 409
    assert session.query(ChecklistConfirmation).filter_by(checklist_item_id=before.id).count() == 1


def test_before_complete_ticks_only_while_in_progress(setup):
    client, _session, request, before, (after_a, _after_b) = setup

    assert _tick(client, request, after_a).status_code == 409  # not started yet
    _started(client, request, before)
    assert _tick(client, request, after_a).status_code == 200


def test_share_with_requestor_dump_accepts_no_ticks(setup):
    client, session, _request_, before, _after = setup
    shared = _request(session, share_with_requestor=True, restore_source=None)
    assert _tick(client, shared, before).status_code == 400


# --- Mark Deployed gate ---------------------------------------------------------------

def test_mark_deployed_blocked_until_every_after_term_is_ticked(setup):
    client, session, request, before, (after_a, after_b) = setup
    _started(client, request, before)
    _tick(client, request, after_a)

    response = _deploy(client, request)

    assert response.status_code == 400
    assert "smoke test" in response.text
    session.refresh(request)
    assert request.status == RequestStatus.in_progress

    _tick(client, request, after_b)
    assert _deploy(client, request).status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.completed


def test_mark_deployed_unaffected_when_type_has_no_after_terms(web):
    client, session = web
    _users(session)
    before = make_checklist_item(session, label="Close cronjobs", request_types=(LOCAL,))
    request = _request(session, LOCAL)
    login_as(client, "zunayed")
    _started(client, request, before)

    assert _deploy(client, request).status_code == 303


def test_share_with_requestor_dump_marks_deployed_without_checklist(setup):
    client, session, _request_, _before, _after = setup
    shared = _request(session, share_with_requestor=True, restore_source=None)
    assert _start(client, shared).status_code == 303
    assert _deploy(client, shared).status_code == 303


def test_v12_mark_deployed_is_gated_too(web):
    client, session = web
    _users(session)
    session.add(Client(id=1, name="CRM"))
    session.commit()
    after = make_checklist_item(session, label="After deploy: smoke test", request_types=(STD,), due="before_complete")
    request = _request(session, STD, client_id=1, environment=DeploymentEnvironment.test, version="V12",
                       dump_source=None, restore_source=None)
    login_as(client, "zunayed")
    assert _start(client, request).status_code == 303

    assert _deploy(client, request, current_version="2026.40.1").status_code == 400
    _tick(client, request, after)
    assert _deploy(client, request, current_version="2026.40.1").status_code == 303


# --- Mark Deployed rendering ----------------------------------------------------------

def _row_html(page, request_id):
    for row in re.findall(r"<tr>.*?</tr>", page, re.S):
        if f'data-return-action="/requests/{request_id}/return"' in row:
            return row
    raise AssertionError(f"no row found for request {request_id}")


def test_in_progress_row_opens_mark_deployed_checklist_with_saved_ticks(setup):
    client, _session, request, before, (after_a, _after_b) = setup
    _started(client, request, before)
    _tick(client, request, after_a)

    page = client.get("/requests").text
    row = _row_html(page, request.id)

    assert f'<form method="post" action="/requests/{request.id}/deploy"' not in row
    assert f'data-complete-action="/requests/{request.id}/deploy"' in row
    # data-ticked lists every tick this round (both stages); each pop-up reads its own ids.
    ticked = re.search(r'data-complete-action="[^"]*"[^>]*data-ticked="([^"]*)"', row).group(1).split(",")
    assert str(after_a.id) in ticked and str(_after_b.id) not in ticked
    dialog = re.search(r'<dialog id="complete-checklist-modal-db_dump_restore".*?</dialog>', page, re.S).group(0)
    for item in (after_a, _after_b):
        assert f'data-item-id="{item.id}"' in dialog
    assert f'data-item-id="{before.id}"' not in dialog


def test_v12_version_popup_carries_the_checklist(web):
    client, session = web
    _users(session)
    session.add(Client(id=1, name="CRM"))
    session.commit()
    after = make_checklist_item(session, label="After deploy: smoke test", request_types=(STD,), due="before_complete")
    request = _request(session, STD, client_id=1, environment=DeploymentEnvironment.test, version="V12",
                       dump_source=None, restore_source=None)
    login_as(client, "zunayed")
    _start(client, request)

    page = client.get("/requests").text

    assert 'data-checklist-type="standard"' in _row_html(page, request.id)
    modal = re.search(r'<dialog id="deploy-version-modal".*?</dialog>', page, re.S).group(0)
    assert re.search(rf'data-complete-checklist-type="standard".*?data-item-id="{after.id}"', modal, re.S)


# --- Return keeps working; rounds -----------------------------------------------------

def test_return_from_in_progress_works_with_a_half_ticked_list(setup):
    client, session, request, before, (after_a, _after_b) = setup
    _started(client, request, before)
    _tick(client, request, after_a)

    response = client.post(f"/requests/{request.id}/return", data={"reason": "wrong branch"}, follow_redirects=False)

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.returned


def test_returned_request_accepts_no_ticks(setup):
    client, _session, request, before, _after = setup
    client.post(f"/requests/{request.id}/return", data={"reason": "wrong branch"})
    assert _tick(client, request, before).status_code == 409


def test_ticks_from_before_a_return_do_not_count_after_resubmit(setup):
    client, session, request, before, (after_a, after_b) = setup
    _started(client, request, before)
    _tick(client, request, after_a)
    _tick(client, request, after_b)
    client.post(f"/requests/{request.id}/return", data={"reason": "wrong branch"})
    login_as(client, "devone")
    assert client.post(f"/requests/{request.id}/resubmit", follow_redirects=False).status_code == 303
    login_as(client, "zunayed")

    assert _start(client, request).status_code == 400  # round 0's tick doesn't count
    _started(client, request, before)
    assert _deploy(client, request).status_code == 400  # nor do round 0's after-ticks
    _tick(client, request, after_a)
    _tick(client, request, after_b)
    assert _deploy(client, request).status_code == 303

    rounds = sorted({c.round for c in session.query(ChecklistConfirmation).filter_by(request_id=request.id)})
    assert rounds == [0, 1]
    assert session.query(ChecklistConfirmation).filter_by(request_id=request.id).count() == 6
