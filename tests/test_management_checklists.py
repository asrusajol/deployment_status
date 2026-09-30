import re
from datetime import datetime, timezone

import pytest

from app.models.checklist import ChecklistItem
from app.models.deployment_request import RequestType
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def _seed_users(session):
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    manager = make_user(session, id=2, name="Mgr", username="mgr", password=DEFAULT_TEST_PASSWORD)
    manager.can_access_management = True
    make_user(session, id=3, name="Dev", username="dev", password=DEFAULT_TEST_PASSWORD)
    session.commit()


def _item(session, *, request_type=RequestType.db_dump_restore, label, position, is_active=True):
    item = ChecklistItem(request_type=request_type, label=label, position=position, is_active=is_active,
                         created_at=datetime.now(timezone.utc))
    session.add(item)
    session.commit()
    return item


def _row(page, item_id):
    """One term's <li> — scoped so assertions can't match another term's controls."""
    match = re.search(rf'<li class="checklist-term[^"]*" data-item-id="{item_id}".*?</li>', page, re.S)
    assert match, f"no row for item {item_id}"
    return match.group(0)


def _retired_fold(page):
    match = re.search(r'<details class="checklist-retired">.*?</details>', page, re.S)
    assert match, "retired fold missing"
    return match.group(0)


def _add_form(page):
    match = re.search(r'<form[^>]*class="checklist-add"[^>]*>.*?</form>', page, re.S)
    assert match, "add form missing"
    return match.group(0)


def test_user_without_management_access_gets_403(web):
    client, session = web
    _seed_users(session)
    login_as(client, "dev")
    assert client.get("/management/checklists").status_code == 403
    assert client.post("/management/checklists", data={"request_type": "standard", "label": "x"}).status_code == 403


def test_hub_links_to_checklists_for_management_user(web):
    client, session = web
    _seed_users(session)
    login_as(client, "mgr")
    assert 'href="/management/checklists"' in client.get("/management").text


def test_page_groups_terms_by_request_type(web):
    client, session = web
    _seed_users(session)
    dump = _item(session, label="Close cronjobs", position=1)
    local = _item(session, request_type=RequestType.test_local, label="Ping box", position=1)
    login_as(client, "mgr")

    page = client.get("/management/checklists").text

    dump_row, local_row = _row(page, dump.id), _row(page, local.id)
    assert 'data-request-type="db_dump_restore"' in dump_row
    assert "badge-type-db_dump_restore" in dump_row
    assert 'data-request-type="test_local"' in local_row
    assert "badge-type-test_local" in local_row
    # Grouped by type in RequestType order: db_dump_restore is declared before test_local.
    assert page.index(f'data-item-id="{dump.id}"') < page.index(f'data-item-id="{local.id}"')


def test_add_form_picks_exactly_one_type(web):
    # One type per term (decision D2) — a picker, never "applies to" checkboxes.
    client, session = web
    _seed_users(session)
    login_as(client, "mgr")

    form = _add_form(client.get("/management/checklists").text)

    assert '<select name="request_type"' in form
    for request_type in RequestType:
        assert f'<option value="{request_type.value}"' in form
    assert 'type="checkbox"' not in form


def test_edit_form_stays_hidden_until_opened(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", position=1)
    login_as(client, "root")

    row = _row(client.get("/management/checklists").text, item.id)

    assert re.search(r'<form[^>]*action="/management/checklists/\d+/edit"[^>]*\bhidden\b', row)
    assert f'data-edit-toggle="{item.id}"' in row


def test_retired_terms_are_folded_away(web):
    client, session = web
    _seed_users(session)
    active = _item(session, label="Close cronjobs", position=1)
    retired = _item(session, label="Old step", position=2, is_active=False)
    login_as(client, "mgr")

    page = client.get("/management/checklists").text

    fold = _retired_fold(page)
    assert f'data-item-id="{retired.id}"' in fold
    assert f'data-item-id="{active.id}"' not in fold
    assert "Retired (1)" in fold


def test_management_user_adds_term_at_end_of_its_type(web):
    client, session = web
    _seed_users(session)
    _item(session, label="Close cronjobs", position=1)
    _item(session, label="Restart workers", position=2)
    login_as(client, "mgr")

    response = client.post("/management/checklists", data={"request_type": "db_dump_restore", "label": "  Check .env  "},
                           follow_redirects=False)

    assert response.status_code == 303
    added = session.query(ChecklistItem).filter_by(label="Check .env").one()
    assert added.position == 3
    assert added.created_by == 2
    assert added.is_active is True


@pytest.mark.parametrize("data", [
    {"request_type": "db_dump_restore", "label": "   "},
    {"request_type": "not_a_type", "label": "x"},
    {"request_type": "db_dump_restore", "label": "x" * 501},
])
def test_add_rejects_bad_input(web, data):
    client, session = web
    _seed_users(session)
    login_as(client, "mgr")
    assert client.post("/management/checklists", data=data, follow_redirects=False).status_code == 400
    assert session.query(ChecklistItem).count() == 0


def test_management_user_cannot_edit_move_or_retire(web):
    client, session = web
    _seed_users(session)
    first = _item(session, label="Close cronjobs", position=1)
    _item(session, label="Restart workers", position=2)
    login_as(client, "mgr")

    assert client.post(f"/management/checklists/{first.id}/edit", data={"label": "changed"}).status_code == 403
    assert client.post(f"/management/checklists/{first.id}/move", data={"direction": "down"}).status_code == 403
    assert client.post(f"/management/checklists/{first.id}/deactivate").status_code == 403
    session.refresh(first)
    assert (first.label, first.position, first.is_active) == ("Close cronjobs", 1, True)


def test_management_user_sees_no_admin_controls(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", position=1)
    login_as(client, "mgr")

    row = _row(client.get("/management/checklists").text, item.id)

    assert "/edit" not in row and "/move" not in row and "/deactivate" not in row


def test_admin_edits_label_and_records_who(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", position=1)
    login_as(client, "root")

    response = client.post(f"/management/checklists/{item.id}/edit", data={"label": "Stop cron"}, follow_redirects=False)

    assert response.status_code == 303
    session.refresh(item)
    assert item.label == "Stop cron"
    assert item.updated_by == 1
    assert item.updated_at is not None


def test_admin_moves_term_within_its_type_only(web):
    client, session = web
    _seed_users(session)
    a = _item(session, label="A", position=1)
    b = _item(session, label="B", position=2)
    other = _item(session, request_type=RequestType.test_local, label="Other", position=3)
    login_as(client, "root")

    client.post(f"/management/checklists/{b.id}/move", data={"direction": "up"})

    for row in (a, b, other):
        session.refresh(row)
    assert (a.position, b.position, other.position) == (2, 1, 3)


def test_move_past_the_end_is_a_no_op(web):
    client, session = web
    _seed_users(session)
    a = _item(session, label="A", position=1)
    login_as(client, "root")

    response = client.post(f"/management/checklists/{a.id}/move", data={"direction": "up"}, follow_redirects=False)

    assert response.status_code == 303
    session.refresh(a)
    assert a.position == 1


def test_move_rejects_unknown_direction(web):
    client, session = web
    _seed_users(session)
    a = _item(session, label="A", position=1)
    login_as(client, "root")
    assert client.post(f"/management/checklists/{a.id}/move", data={"direction": "sideways"}).status_code == 400


def test_admin_retires_and_restores_term(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", position=1)
    login_as(client, "root")

    client.post(f"/management/checklists/{item.id}/deactivate")
    session.refresh(item)
    assert item.is_active is False
    assert f'data-item-id="{item.id}"' in _retired_fold(client.get("/management/checklists").text)

    client.post(f"/management/checklists/{item.id}/activate")
    session.refresh(item)
    assert item.is_active is True


def test_unknown_item_is_404(web):
    client, session = web
    _seed_users(session)
    login_as(client, "root")
    response = client.post("/management/checklists/999/deactivate")
    # The handler's own 404, not the router's "Not Found" for a route that doesn't exist.
    assert response.status_code == 404
    assert response.json()["detail"] == "Checklist term not found"


def test_admin_reassigns_term_to_the_end_of_another_type(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", position=1)
    _item(session, request_type=RequestType.test_local, label="Ping box", position=1)
    login_as(client, "root")

    response = client.post(
        f"/management/checklists/{item.id}/edit",
        data={"label": "Close cronjobs", "request_type": "test_local"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(item)
    assert item.request_type == RequestType.test_local
    assert item.position == 2
    assert item.updated_by == 1


@pytest.mark.parametrize("wording", ["Close cronjobs", "  close CRONJOBS "])
def test_reassign_blocked_when_target_type_already_has_it(web, wording):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", position=1)
    _item(session, request_type=RequestType.test_local, label="Close cronjobs", position=1)
    login_as(client, "root")

    response = client.post(
        f"/management/checklists/{item.id}/edit", data={"label": wording, "request_type": "test_local"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "already assigned" in response.json()["detail"]
    session.refresh(item)
    assert (item.request_type, item.label, item.position) == (RequestType.db_dump_restore, "Close cronjobs", 1)


def test_reassign_allowed_when_target_only_has_it_retired(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", position=1)
    _item(session, request_type=RequestType.test_local, label="Close cronjobs", position=1, is_active=False)
    login_as(client, "root")

    response = client.post(
        f"/management/checklists/{item.id}/edit", data={"label": "Close cronjobs", "request_type": "test_local"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(item)
    assert item.request_type == RequestType.test_local


def test_reassign_rejects_unknown_type(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", position=1)
    login_as(client, "root")

    response = client.post(
        f"/management/checklists/{item.id}/edit", data={"label": "Close cronjobs", "request_type": "nope"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    session.refresh(item)
    assert item.request_type == RequestType.db_dump_restore


def test_edit_form_offers_type_picker_preset_to_current_type(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", position=1)
    login_as(client, "root")

    row = _row(client.get("/management/checklists").text, item.id)
    form = re.search(r'<form[^>]*action="/management/checklists/\d+/edit".*?</form>', row, re.S).group(0)

    assert '<select name="request_type"' in form
    assert re.search(r'<option value="db_dump_restore"[^>]*\bselected\b', form)
    assert not re.search(r'<option value="test_local"[^>]*\bselected\b', form)
