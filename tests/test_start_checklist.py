"""Start Deployment requires every active checklist term for the request's type.

Terms live in checklist_items (managed at /management/checklists); each confirmed start
writes one checklist_confirmations row per term, in the same transaction as the
execution row. The gate is the route, not the pop-up — several tests POST directly.
"""

import re
from datetime import datetime, timezone

import pytest

from app.models.checklist import ChecklistConfirmation
from app.models.deployment_execution import DeploymentExecution
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_checklist_item, make_user

DUMP_TERMS = (
    "Close cronjobs / scheduled jobs",
    "Restart workers to apply the change",
    "Check .env for anything that can trigger emails",
    "After restoration, remove email settings from the settings table and the web UI (basevisu module)",
)


def _users(session):
    make_user(session, id=1, name="Zunayed Islam", username="zunayed", password=DEFAULT_TEST_PASSWORD, role=UserRole.admin)
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()


def _terms(session, request_type=RequestType.db_dump_restore, labels=DUMP_TERMS):
    return [
        make_checklist_item(session, label=label, request_types=(request_type,), position=n)
        for n, label in enumerate(labels, start=1)
    ]


def _request(session, request_type=RequestType.db_dump_restore):
    request = DeploymentRequest(
        task_id="PR-X", requested_by=2, status=RequestStatus.approved, request_type=request_type,
        dump_source="crm-live", restore_source="crm-staging", created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    session.add(request)
    session.commit()
    return request


def _ids(client, items):
    """The checkbox values the Start pop-ups actually render for these terms — read from
    the page, so the tests submit exactly what a browser would."""
    values = dict(re.findall(r'name="checklist" value="((\d+)[^"]*)"', client.get("/requests").text))
    by_id = {int(item_id): value for value, item_id in values.items()}
    return [by_id[item.id] for item in items]


def _start(client, request, ids=None):
    data = {"checklist": ids} if ids is not None else {}
    return client.post(f"/requests/{request.id}/start", data=data, follow_redirects=False)


def _assert_nothing_written(session, request):
    session.refresh(request)
    assert request.status == RequestStatus.approved
    assert session.query(DeploymentExecution).filter_by(request_id=request.id).count() == 0
    assert session.query(ChecklistConfirmation).filter_by(request_id=request.id).count() == 0


@pytest.fixture()
def dump(web):
    client, session = web
    _users(session)
    items = _terms(session)
    request = _request(session)
    login_as(client, "zunayed")
    return client, session, items, request


def test_start_without_checklist_is_rejected(dump):
    client, session, _items, request = dump
    assert _start(client, request).status_code == 400
    _assert_nothing_written(session, request)


def test_start_with_partial_checklist_is_rejected(dump):
    client, session, items, request = dump
    assert _start(client, request, _ids(client, items[:3])).status_code == 400
    _assert_nothing_written(session, request)


def test_start_with_full_checklist_records_each_confirmation(dump):
    client, session, items, request = dump

    assert _start(client, request, _ids(client, items)).status_code == 303

    session.refresh(request)
    assert request.status == RequestStatus.in_progress
    rows = session.query(ChecklistConfirmation).filter_by(request_id=request.id).order_by(ChecklistConfirmation.id).all()
    assert [r.item_label for r in rows] == list(DUMP_TERMS)
    assert {r.confirmed_by for r in rows} == {1}
    assert len({r.confirmed_at for r in rows}) == 1


def test_extra_unknown_ids_are_ignored(dump):
    client, session, items, request = dump
    assert _start(client, request, _ids(client, items) + ["99999", "not-a-number"]).status_code == 303
    assert session.query(ChecklistConfirmation).filter_by(request_id=request.id).count() == 4


def test_retired_term_is_not_required(dump):
    client, session, items, request = dump
    items[3].is_active = False
    session.commit()

    assert _start(client, request, _ids(client, items[:3])).status_code == 303
    assert session.query(ChecklistConfirmation).filter_by(request_id=request.id).count() == 3


def test_term_added_after_page_load_blocks_start(dump):
    client, session, items, request = dump
    seen_on_page = _ids(client, items)
    _terms(session, labels=("Snapshot the target DB",))

    response = _start(client, request, seen_on_page)

    assert response.status_code == 400
    assert "Snapshot the target DB" in response.text
    _assert_nothing_written(session, request)


@pytest.mark.parametrize("request_type", [RequestType.standard, RequestType.test_local])
def test_type_without_terms_starts_with_one_click(web, request_type):
    client, session = web
    _users(session)
    _terms(session)  # dump terms exist, but not for this type
    request = _request(session, request_type)
    login_as(client, "zunayed")

    assert _start(client, request).status_code == 303
    assert session.query(ChecklistConfirmation).count() == 0


def test_term_assigned_to_two_types_is_required_for_both(web):
    client, session = web
    _users(session)
    shared = make_checklist_item(
        session, label="Close cronjobs", request_types=(RequestType.db_dump_restore, RequestType.test_local)
    )
    dump_request = _request(session, RequestType.db_dump_restore)
    local_request = _request(session, RequestType.test_local)
    login_as(client, "zunayed")

    assert _start(client, dump_request).status_code == 400
    assert _start(client, local_request).status_code == 400
    assert _start(client, dump_request, _ids(client, [shared])).status_code == 303
    assert _start(client, local_request, _ids(client, [shared])).status_code == 303
    assert session.query(ChecklistConfirmation).filter_by(checklist_item_id=shared.id).count() == 2


def test_terms_added_to_standard_are_enforced(web):
    client, session = web
    _users(session)
    _terms(session, RequestType.standard, ("Confirm branch is merged",))
    request = _request(session, RequestType.standard)
    login_as(client, "zunayed")

    assert _start(client, request).status_code == 400
    _assert_nothing_written(session, request)


def test_confirmations_survive_return_and_restart(web):
    client, session = web
    _users(session)
    items = _terms(session, RequestType.test_local, ("Ping the box",))
    request = _request(session, RequestType.test_local)

    login_as(client, "zunayed")
    assert _start(client, request, _ids(client, items)).status_code == 303
    assert client.post(f"/requests/{request.id}/return", data={"reason": "wrong branch"}, follow_redirects=False).status_code == 303
    login_as(client, "devone")
    assert client.post(f"/requests/{request.id}/resubmit", follow_redirects=False).status_code == 303
    login_as(client, "zunayed")
    assert _start(client, request, _ids(client, items)).status_code == 303

    assert session.query(ChecklistConfirmation).filter_by(request_id=request.id).count() == 2


def test_term_reworded_after_page_load_blocks_start(dump):
    # The audit must record wording the deployer actually saw: a term reworded while the
    # pop-up was open is a changed checklist, same as one added.
    client, session, items, request = dump
    seen_on_page = _ids(client, items)
    client.post(f"/management/checklists/{items[0].id}/edit", data={"label": "Close cronjobs and disable SMTP relay"})

    response = _start(client, request, seen_on_page)

    assert response.status_code == 400
    _assert_nothing_written(session, request)


def test_confirmation_keeps_label_snapshot_after_edit(dump):
    client, session, items, request = dump
    _start(client, request, _ids(client, items))

    client.post(f"/management/checklists/{items[0].id}/edit", data={"label": "Reworded"})

    first = session.query(ChecklistConfirmation).filter_by(checklist_item_id=items[0].id).one()
    session.refresh(first)
    assert first.item_label == DUMP_TERMS[0]


def _row_html(page, request_id):
    for row in re.findall(r"<tr>.*?</tr>", page, re.S):
        if f'data-return-action="/requests/{request_id}/return"' in row:
            return row
    raise AssertionError(f"no row found for request {request_id}")


def _dialog_html(page, request_type):
    match = re.search(rf'<dialog id="start-checklist-modal-{request_type.value}".*?</dialog>', page, re.S)
    assert match, f"no start checklist dialog for {request_type.value}"
    return match.group(0)


def test_row_with_terms_opens_its_types_dialog(dump):
    client, _session, _items, request = dump

    row = _row_html(client.get("/requests").text, request.id)

    assert f'<form method="post" action="/requests/{request.id}/start"' not in row
    assert f'data-start-action="/requests/{request.id}/start"' in row
    assert 'data-checklist-type="db_dump_restore"' in row


def test_dialog_lists_active_terms_only(dump):
    client, session, items, _request_ = dump
    items[3].is_active = False
    session.commit()

    dialog = _dialog_html(client.get("/requests").text, RequestType.db_dump_restore)

    for item in items[:3]:
        assert f'name="checklist" value="{item.id}:' in dialog
    assert f'value="{items[3].id}:' not in dialog


def test_row_without_terms_keeps_direct_start_and_no_dialog(web):
    client, session = web
    _users(session)
    request = _request(session, RequestType.standard)
    login_as(client, "zunayed")

    page = client.get("/requests").text

    assert f'<form method="post" action="/requests/{request.id}/start"' in _row_html(page, request.id)
    assert 'id="start-checklist-modal-standard"' not in page


def test_non_deployer_gets_no_start_dialogs(web):
    client, session = web
    _users(session)
    _terms(session)
    _request(session)
    login_as(client, "devone")

    # The dialog tag, not the bare id: the page's JS always contains the id prefix.
    assert '<dialog id="start-checklist-modal' not in client.get("/requests").text
