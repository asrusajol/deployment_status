"""Start Deployment requires every active "before start" term for the request's type to be
ticked — and ticks are saved one by one as they're clicked (POST
/requests/{id}/checklist/{item_id}), so closing the pop-up or a page reload loses nothing.
The gate is the route, not the pop-up: Start re-reads the ticks from the database.
"""

import re
from datetime import datetime, timezone

import pytest

from app.models.checklist import ChecklistConfirmation, ChecklistItem
from app.models.deployment_execution import DeploymentExecution
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_checklist_item, make_user

DUMP, LOCAL, STD = RequestType.db_dump_restore, RequestType.test_local, RequestType.standard

DUMP_TERMS = (
    "Close cronjobs / scheduled jobs",
    "Restart workers to apply the change",
    "Check .env for anything that can trigger emails",
)


def _users(session):
    make_user(session, id=1, name="Zunayed Islam", username="zunayed", password=DEFAULT_TEST_PASSWORD, role=UserRole.admin)
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()


def _terms(session, request_type=DUMP, labels=DUMP_TERMS, due="before_start"):
    return [
        make_checklist_item(session, label=label, request_types=(request_type,), position=n, due=due)
        for n, label in enumerate(labels, start=1)
    ]


def _request(session, request_type=DUMP, **extra):
    fields = dict(
        task_id="PR-X", requested_by=2, status=RequestStatus.approved, request_type=request_type,
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


def _assert_not_started(session, request):
    session.refresh(request)
    assert request.status == RequestStatus.approved
    assert session.query(DeploymentExecution).filter_by(request_id=request.id).count() == 0


@pytest.fixture()
def dump(web):
    client, session = web
    _users(session)
    items = _terms(session)
    request = _request(session)
    login_as(client, "zunayed")
    return client, session, items, request


def test_start_blocked_until_every_before_start_term_is_ticked(dump):
    client, session, items, request = dump
    for item in items[:2]:
        assert _tick(client, request, item).status_code == 200

    response = _start(client, request)

    assert response.status_code == 400
    assert DUMP_TERMS[2] in response.text
    _assert_not_started(session, request)


def test_start_allowed_once_all_ticked_and_ticks_are_kept(dump):
    client, session, items, request = dump
    for item in items:
        _tick(client, request, item)

    assert _start(client, request).status_code == 303

    session.refresh(request)
    assert request.status == RequestStatus.in_progress
    rows = session.query(ChecklistConfirmation).filter_by(request_id=request.id).all()
    assert sorted(r.item_label for r in rows) == sorted(DUMP_TERMS)
    assert {r.confirmed_by for r in rows} == {1}


def test_unticked_term_no_longer_counts(dump):
    client, session, items, request = dump
    for item in items:
        _tick(client, request, item)
    _tick(client, request, items[0], checked=False)

    assert _start(client, request).status_code == 400
    _assert_not_started(session, request)


def test_before_complete_terms_do_not_block_start(dump):
    client, session, items, request = dump
    make_checklist_item(session, label="After restoration, remove email settings", due="before_complete", position=9)
    for item in items:
        _tick(client, request, item)

    assert _start(client, request).status_code == 303


def test_term_added_after_ticking_blocks_start(dump):
    client, session, items, request = dump
    for item in items:
        _tick(client, request, item)
    _terms(session, labels=("Snapshot the target DB",))

    response = _start(client, request)

    assert response.status_code == 400
    assert "Snapshot the target DB" in response.text


def test_reworded_term_must_be_ticked_again(dump):
    # The audit must record wording the deployer actually saw.
    client, session, items, request = dump
    for item in items:
        _tick(client, request, item)
    client.post(f"/management/checklists/{items[0].id}/edit", data={"label": "Close cronjobs and disable SMTP relay"})

    assert _start(client, request).status_code == 400
    _tick(client, request, items[0])
    assert _start(client, request).status_code == 303
    confirmation = session.query(ChecklistConfirmation).filter_by(checklist_item_id=items[0].id).one()
    session.refresh(confirmation)
    assert confirmation.item_label == "Close cronjobs and disable SMTP relay"


def test_retired_term_is_not_required(dump):
    client, session, items, request = dump
    items[2].is_active = False
    session.commit()
    for item in items[:2]:
        _tick(client, request, item)

    assert _start(client, request).status_code == 303


@pytest.mark.parametrize("request_type", [STD, LOCAL])
def test_type_without_terms_starts_with_one_click(web, request_type):
    client, session = web
    _users(session)
    _terms(session)  # dump terms exist, but not for this type
    request = _request(session, request_type)
    login_as(client, "zunayed")

    assert _start(client, request).status_code == 303


def test_share_with_requestor_dump_skips_the_checklist(dump):
    client, session, _items, _restore = dump
    request = _request(session, share_with_requestor=True, restore_source=None)

    assert _start(client, request).status_code == 303
    assert session.query(ChecklistConfirmation).filter_by(request_id=request.id).count() == 0


def test_term_assigned_to_two_types_is_required_for_both(web):
    client, session = web
    _users(session)
    shared = make_checklist_item(session, label="Close cronjobs", request_types=(DUMP, LOCAL))
    dump_request = _request(session, DUMP)
    local_request = _request(session, LOCAL)
    login_as(client, "zunayed")

    assert _start(client, dump_request).status_code == 400
    assert _start(client, local_request).status_code == 400
    _tick(client, dump_request, shared)
    _tick(client, local_request, shared)
    assert _start(client, dump_request).status_code == 303
    assert _start(client, local_request).status_code == 303
    assert session.query(ChecklistItem).count() == 1


# --- rendering ----------------------------------------------------------------------

def _row_html(page, request_id):
    for row in re.findall(r"<tr>.*?</tr>", page, re.S):
        if f'data-return-action="/requests/{request_id}/return"' in row:
            return row
    raise AssertionError(f"no row found for request {request_id}")


def _dialog_html(page, dialog_id):
    match = re.search(rf'<dialog id="{dialog_id}".*?</dialog>', page, re.S)
    assert match, f"no dialog {dialog_id}"
    return match.group(0)


def test_row_with_terms_opens_its_start_dialog_carrying_saved_ticks(dump):
    client, _session, items, request = dump
    _tick(client, request, items[1])

    row = _row_html(client.get("/requests").text, request.id)

    assert f'<form method="post" action="/requests/{request.id}/start"' not in row
    assert f'data-start-action="/requests/{request.id}/start"' in row
    assert 'data-checklist-type="db_dump_restore"' in row
    assert f'data-ticked="{items[1].id}"' in row


def test_start_dialog_lists_only_active_before_start_terms(dump):
    client, session, items, _request_ = dump
    items[2].is_active = False
    after = make_checklist_item(session, label="After restoration", due="before_complete", position=9)
    session.commit()

    dialog = _dialog_html(client.get("/requests").text, "start-checklist-modal-db_dump_restore")

    for item in items[:2]:
        assert f'data-item-id="{item.id}"' in dialog
    assert f'data-item-id="{items[2].id}"' not in dialog
    assert f'data-item-id="{after.id}"' not in dialog


def test_row_without_terms_keeps_direct_start(web):
    client, session = web
    _users(session)
    request = _request(session, STD)
    login_as(client, "zunayed")

    page = client.get("/requests").text

    assert f'<form method="post" action="/requests/{request.id}/start"' in _row_html(page, request.id)
    assert '<dialog id="start-checklist-modal-standard"' not in page


def test_share_with_requestor_row_keeps_one_click_start(dump):
    client, session, _items, _restore = dump
    request = _request(session, share_with_requestor=True, restore_source=None)

    row = _row_html(client.get("/requests").text, request.id)

    assert f'<form method="post" action="/requests/{request.id}/start"' in row


def test_non_deployer_gets_no_checklist_dialogs(web):
    client, session = web
    _users(session)
    _terms(session)
    _request(session)
    login_as(client, "devone")

    assert '<dialog id="start-checklist-modal' not in client.get("/requests").text
