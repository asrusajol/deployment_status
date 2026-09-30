import re

import pytest

from app.models.checklist import ChecklistItem, ChecklistItemType
from app.models.deployment_request import RequestType
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_checklist_item, make_user

DUMP, LOCAL, STD = RequestType.db_dump_restore, RequestType.test_local, RequestType.standard


def _seed_users(session):
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    manager = make_user(session, id=2, name="Mgr", username="mgr", password=DEFAULT_TEST_PASSWORD)
    manager.can_access_management = True
    make_user(session, id=3, name="Dev", username="dev", password=DEFAULT_TEST_PASSWORD)
    session.commit()


def _item(session, *, label, types=(DUMP,), position=1, is_active=True):
    return make_checklist_item(session, label=label, request_types=types, position=position, is_active=is_active)


def _group(page, key):
    """One type's list (key = RequestType value), or "unassigned"."""
    match = re.search(rf'<ol class="checklist-list" data-group="{key}">.*?</ol>', page, re.S)
    assert match, f"no group {key}"
    return match.group(0)


def _row(page, item_id, group=None):
    """One term's <li>, optionally within one group — scoped so assertions can't match
    another term's controls."""
    scope = _group(page, group) if group else page
    match = re.search(rf'<li class="checklist-term[^"]*" data-item-id="{item_id}".*?</li>', scope, re.S)
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


def _types(session, item):
    session.expire_all()
    return {t.request_type: t.position for t in session.get(ChecklistItem, item.id).types}


def _edit(client, item, label, types):
    data = {"label": label, "request_types": [t.value for t in types], "types_submitted": "1"}
    return client.post(f"/management/checklists/{item.id}/edit", data=data, follow_redirects=False)


# --- access -------------------------------------------------------------------------

def test_user_without_management_access_gets_403(web):
    client, session = web
    _seed_users(session)
    login_as(client, "dev")
    assert client.get("/management/checklists").status_code == 403
    assert client.post("/management/checklists", data={"request_types": "standard", "label": "x"}).status_code == 403


def test_hub_links_to_checklists_for_management_user(web):
    client, session = web
    _seed_users(session)
    login_as(client, "mgr")
    assert 'href="/management/checklists"' in client.get("/management").text


def test_management_user_cannot_edit_move_or_retire(web):
    client, session = web
    _seed_users(session)
    first = _item(session, label="Close cronjobs", position=1)
    _item(session, label="Restart workers", position=2)
    login_as(client, "mgr")

    assert _edit(client, first, "changed", (LOCAL,)).status_code == 403
    assert client.post(f"/management/checklists/{first.id}/move", data={"direction": "down", "request_type": "db_dump_restore"}).status_code == 403
    assert client.post(f"/management/checklists/{first.id}/deactivate").status_code == 403
    session.expire_all()
    assert session.get(ChecklistItem, first.id).label == "Close cronjobs"
    assert _types(session, first) == {DUMP: 1}


def test_management_user_sees_no_admin_controls(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs")
    login_as(client, "mgr")

    row = _row(client.get("/management/checklists").text, item.id)

    assert "/edit" not in row and "/move" not in row and "/deactivate" not in row


# --- listing ------------------------------------------------------------------------

def test_term_shows_under_every_type_it_is_assigned_to_with_all_its_chips(web):
    client, session = web
    _seed_users(session)
    shared = _item(session, label="Close cronjobs", types=(DUMP, LOCAL))
    login_as(client, "mgr")

    page = client.get("/management/checklists").text

    for group in ("db_dump_restore", "test_local"):
        row = _row(page, shared.id, group)
        assert "badge-type-db_dump_restore" in row and "badge-type-test_local" in row
    assert f'data-item-id="{shared.id}"' not in _group(page, "standard")


def test_unassigned_term_is_listed_as_not_assigned(web):
    client, session = web
    _seed_users(session)
    loose = _item(session, label="Snapshot DB", types=())
    login_as(client, "mgr")

    assert f'data-item-id="{loose.id}"' in _group(client.get("/management/checklists").text, "unassigned")


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


def test_add_form_offers_a_checkbox_per_type(web):
    client, session = web
    _seed_users(session)
    login_as(client, "mgr")

    form = _add_form(client.get("/management/checklists").text)

    for request_type in RequestType:
        assert f'name="request_types" value="{request_type.value}"' in form


def test_edit_form_hidden_until_opened_with_current_types_ticked(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", types=(DUMP,))
    login_as(client, "root")

    row = _row(client.get("/management/checklists").text, item.id)
    form = re.search(r'<form[^>]*action="/management/checklists/\d+/edit".*?</form>', row, re.S).group(0)

    assert re.search(r'<form[^>]*\bhidden\b', form)
    assert re.search(r'value="db_dump_restore"[^>]*\bchecked\b', form)
    assert not re.search(r'value="test_local"[^>]*\bchecked\b', form)
    assert "data-edit-toggle" in row


# --- add ----------------------------------------------------------------------------

def test_management_user_adds_term_to_several_types_at_the_end_of_each(web):
    client, session = web
    _seed_users(session)
    _item(session, label="Close cronjobs", position=1)
    _item(session, label="Restart workers", position=2)
    login_as(client, "mgr")

    response = client.post(
        "/management/checklists",
        data={"label": "  Check .env  ", "request_types": ["db_dump_restore", "test_local"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    added = session.query(ChecklistItem).filter_by(label="Check .env").one()
    assert _types(session, added) == {DUMP: 3, LOCAL: 1}
    assert added.created_by == 2


@pytest.mark.parametrize("data", [
    {"label": "   ", "request_types": "db_dump_restore"},
    {"label": "x", "request_types": "not_a_type"},
    {"label": "x" * 501, "request_types": "db_dump_restore"},
    {"label": "x"},
])
def test_add_rejects_bad_input(web, data):
    client, session = web
    _seed_users(session)
    login_as(client, "mgr")
    assert client.post("/management/checklists", data=data, follow_redirects=False).status_code == 400
    assert session.query(ChecklistItem).count() == 0


def test_add_blocked_when_a_type_already_has_that_wording(web):
    client, session = web
    _seed_users(session)
    _item(session, label="Close cronjobs", types=(LOCAL,))
    login_as(client, "mgr")

    response = client.post(
        "/management/checklists", data={"label": " close CRONJOBS", "request_types": ["db_dump_restore", "test_local"]},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "already assigned" in response.json()["detail"]
    assert session.query(ChecklistItem).count() == 1


# --- edit / assign ------------------------------------------------------------------

def test_admin_rewords_and_records_who(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs")
    login_as(client, "root")

    assert _edit(client, item, "Stop cron", (DUMP,)).status_code == 303

    session.expire_all()
    edited = session.get(ChecklistItem, item.id)
    assert edited.label == "Stop cron"
    assert edited.updated_by == 1 and edited.updated_at is not None
    assert _types(session, item) == {DUMP: 1}


def test_admin_assigns_another_type_keeping_existing_order(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", types=(DUMP,), position=4)
    _item(session, label="Ping box", types=(LOCAL,), position=1)
    login_as(client, "root")

    assert _edit(client, item, "Close cronjobs", (DUMP, LOCAL)).status_code == 303

    assert _types(session, item) == {DUMP: 4, LOCAL: 2}


def test_admin_unassigns_a_type(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", types=(DUMP, LOCAL))
    login_as(client, "root")

    assert _edit(client, item, "Close cronjobs", (DUMP,)).status_code == 303

    assert _types(session, item) == {DUMP: 1}


def test_admin_can_unassign_every_type(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", types=(DUMP,))
    login_as(client, "root")

    assert _edit(client, item, "Close cronjobs", ()).status_code == 303

    assert _types(session, item) == {}
    assert session.get(ChecklistItem, item.id).is_active is True


def test_wording_only_post_keeps_assignments(web):
    # A direct POST without the form's types marker must not silently unassign.
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", types=(DUMP, LOCAL))
    login_as(client, "root")

    response = client.post(f"/management/checklists/{item.id}/edit", data={"label": "Stop cron"}, follow_redirects=False)

    assert response.status_code == 303
    assert _types(session, item) == {DUMP: 1, LOCAL: 1}


@pytest.mark.parametrize("wording", ["Close cronjobs", "  close CRONJOBS "])
def test_assign_blocked_when_target_type_already_has_it(web, wording):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", types=(DUMP,))
    _item(session, label="Close cronjobs", types=(LOCAL,))
    login_as(client, "root")

    response = _edit(client, item, wording, (DUMP, LOCAL))

    assert response.status_code == 400
    assert "already assigned" in response.json()["detail"]
    assert _types(session, item) == {DUMP: 1}


def test_assign_allowed_when_target_only_has_it_retired(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", types=(DUMP,))
    _item(session, label="Close cronjobs", types=(LOCAL,), is_active=False)
    login_as(client, "root")

    assert _edit(client, item, "Close cronjobs", (DUMP, LOCAL)).status_code == 303
    assert LOCAL in _types(session, item)


def test_edit_rejects_unknown_type(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs")
    login_as(client, "root")

    response = client.post(
        f"/management/checklists/{item.id}/edit",
        data={"label": "Close cronjobs", "request_types": "nope", "types_submitted": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert _types(session, item) == {DUMP: 1}


# --- move / retire ------------------------------------------------------------------

def test_admin_moves_term_within_one_type_only(web):
    client, session = web
    _seed_users(session)
    a = _item(session, label="A", types=(DUMP, LOCAL), position=1)
    b = _item(session, label="B", types=(DUMP, LOCAL), position=2)
    login_as(client, "root")

    client.post(f"/management/checklists/{b.id}/move", data={"direction": "up", "request_type": "db_dump_restore"})

    assert _types(session, a) == {DUMP: 2, LOCAL: 1}
    assert _types(session, b) == {DUMP: 1, LOCAL: 2}


def test_move_past_the_end_is_a_no_op(web):
    client, session = web
    _seed_users(session)
    a = _item(session, label="A")
    login_as(client, "root")

    response = client.post(f"/management/checklists/{a.id}/move", data={"direction": "up", "request_type": "db_dump_restore"},
                           follow_redirects=False)

    assert response.status_code == 303
    assert _types(session, a) == {DUMP: 1}


@pytest.mark.parametrize("data", [
    {"direction": "sideways", "request_type": "db_dump_restore"},
    {"direction": "up", "request_type": "test_local"},  # not assigned there
])
def test_move_rejects_bad_input(web, data):
    client, session = web
    _seed_users(session)
    a = _item(session, label="A")
    login_as(client, "root")
    assert client.post(f"/management/checklists/{a.id}/move", data=data).status_code == 400


def test_admin_retires_and_restores_term(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs")
    login_as(client, "root")

    client.post(f"/management/checklists/{item.id}/deactivate")
    session.expire_all()
    assert session.get(ChecklistItem, item.id).is_active is False
    assert f'data-item-id="{item.id}"' in _retired_fold(client.get("/management/checklists").text)

    client.post(f"/management/checklists/{item.id}/activate")
    session.expire_all()
    assert session.get(ChecklistItem, item.id).is_active is True


def test_unknown_item_is_404(web):
    client, session = web
    _seed_users(session)
    login_as(client, "root")
    response = client.post("/management/checklists/999/deactivate")
    # The handler's own 404, not the router's "Not Found" for a route that doesn't exist.
    assert response.status_code == 404
    assert response.json()["detail"] == "Checklist term not found"


def test_link_rows_have_no_orphans_after_unassign(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", types=(DUMP, LOCAL))
    login_as(client, "root")

    _edit(client, item, "Close cronjobs", ())

    assert session.query(ChecklistItemType).count() == 0


def test_every_type_group_is_labelled_so_terms_never_look_like_another_types(web):
    # Regression: in the All view only an empty type showed a label, so DB Dump's terms
    # sat directly under "Standard Deployment — No terms" and read as Standard's.
    client, session = web
    _seed_users(session)
    _item(session, label="Close cronjobs", types=(DUMP,))
    login_as(client, "mgr")

    page = client.get("/management/checklists").text

    for request_type in RequestType:
        group = _group(page, request_type.value)
        head = re.search(r'<li class="checklist-group-head">.*?</li>', group, re.S)
        assert head, f"{request_type.value} group has no header"
        assert f"badge-type-{request_type.value}" in head.group(0)
    assert "1 active" in _group(page, "db_dump_restore")
