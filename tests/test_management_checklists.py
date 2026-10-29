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
    match = re.search(rf'<table class="grid-table checklist-table" data-group="{key}"[^>]*>.*?</table>', page, re.S)
    assert match, f"no group {key}"
    return match.group(0)


def _row(page, item_id, group=None):
    """One term's <li>, optionally within one group — scoped so assertions can't match
    another term's controls."""
    scope = _group(page, group) if group else page
    match = re.search(rf'<tr class="checklist-term[^"]*" data-item-id="{item_id}".*?</tr>', scope, re.S)
    assert match, f"no row for item {item_id}"
    return match.group(0)


def _applies(row, request_type, yes):
    """The design's Yes / No cell for one request type."""
    return f'data-applies="{request_type.value}" data-yes="{1 if yes else 0}"' in row


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


def test_management_user_cannot_edit_or_retire(web):
    client, session = web
    _seed_users(session)
    first = _item(session, label="Close cronjobs", position=1)
    _item(session, label="Restart workers", position=2)
    login_as(client, "mgr")

    assert _edit(client, first, "changed", (LOCAL,)).status_code == 403
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

    assert "/edit" not in row and "/deactivate" not in row


# --- listing ------------------------------------------------------------------------

def test_term_shows_under_every_type_it_is_assigned_to_with_all_its_chips(web):
    client, session = web
    _seed_users(session)
    shared = _item(session, label="Close cronjobs", types=(DUMP, LOCAL))
    login_as(client, "mgr")

    page = client.get("/management/checklists").text

    for group in ("db_dump_restore", "test_local"):
        row = _row(page, shared.id, group)
        assert _applies(row, DUMP, True) and _applies(row, LOCAL, True) and _applies(row, STD, False)
    assert f'data-item-id="{shared.id}"' not in _group(page, "standard")


def test_retired_terms_sit_behind_the_retire_switch(web):
    """The design's Active / Retire segmented button: retired rows are marked and
    hidden until Retire is picked; they keep only Restore."""
    client, session = web
    _seed_users(session)
    active = _item(session, label="Close cronjobs", position=1)
    retired = _item(session, label="Old step", position=2, is_active=False)
    login_as(client, "root")

    page = client.get("/management/checklists").text

    retired_row = _row(_group(page, "all"), retired.id)
    assert re.search(r'<tr class="checklist-term is-retired" data-item-id="\d+" data-retired hidden', retired_row)
    assert "/activate" in retired_row and "/deactivate" not in retired_row
    assert "data-retired" not in _row(page, active.id)
    assert 'data-checklist-state="retired"' in page


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

    dialog = re.search(r'<dialog[^>]*class="dialog checklist-edit-dialog"[^>]*>', row).group(0)
    assert not re.search(r'\bopen\b', dialog)
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


# --- retire ------------------------------------------------------------------

# --- sort order (the design's Sort Order, kept per type) -------------------------------

def _sort(client, item, position, request_type="db_dump_restore", **extra):
    data = {"label": item.label, "position": position, "position_type": request_type, **extra}
    return client.post(f"/management/checklists/{item.id}/edit", data=data, follow_redirects=False)


def test_admin_sets_a_terms_sort_order_within_one_type_only(web):
    client, session = web
    _seed_users(session)
    a = _item(session, label="A", types=(DUMP,), position=1)
    b = _item(session, label="B", types=(DUMP,), position=2)
    c = _item(session, label="C", types=(DUMP, LOCAL), position=3)
    login_as(client, "root")

    assert _sort(client, c, "1").status_code == 303

    assert (_types(session, c), _types(session, a), _types(session, b)) == ({DUMP: 1, LOCAL: 3}, {DUMP: 2}, {DUMP: 3})


def test_sort_order_past_the_end_puts_the_term_last(web):
    client, session = web
    _seed_users(session)
    a = _item(session, label="A", types=(DUMP,), position=1)
    b = _item(session, label="B", types=(DUMP,), position=2)
    login_as(client, "root")

    _sort(client, a, "99")

    assert (_types(session, a), _types(session, b)) == ({DUMP: 2}, {DUMP: 1})


def test_sort_order_counts_active_terms_only(web):
    client, session = web
    _seed_users(session)
    old = _item(session, label="Old", types=(DUMP,), position=1, is_active=False)
    a = _item(session, label="A", types=(DUMP,), position=2)
    b = _item(session, label="B", types=(DUMP,), position=3)
    login_as(client, "root")

    _sort(client, b, "1")

    assert (_types(session, b), _types(session, a), _types(session, old)) == ({DUMP: 1}, {DUMP: 2}, {DUMP: 3})


@pytest.mark.parametrize("position,request_type", [("0", "db_dump_restore"), ("two", "db_dump_restore"), ("1", "test_local"), ("1", "nope")])
def test_sort_order_rejects_bad_input(web, position, request_type):
    client, session = web
    _seed_users(session)
    a = _item(session, label="A", types=(DUMP,))
    login_as(client, "root")

    assert _sort(client, a, position, request_type).status_code == 400
    assert _types(session, a) == {DUMP: 1}


def test_management_user_cannot_set_sort_order(web):
    client, session = web
    _seed_users(session)
    a = _item(session, label="A", types=(DUMP,))
    login_as(client, "mgr")

    assert _sort(client, a, "1").status_code == 403


def test_admin_retires_and_restores_term(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs")
    login_as(client, "root")

    client.post(f"/management/checklists/{item.id}/deactivate")
    session.expire_all()
    assert session.get(ChecklistItem, item.id).is_active is False
    assert "data-retired" in _row(_group(client.get("/management/checklists").text, "all"), item.id)

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


def test_all_view_lists_each_term_once_with_all_its_types(web):
    # Regression: the All view repeated a shared term under every type it belongs to,
    # separated by per-type header rows — cluttered, and the same term twice.
    client, session = web
    _seed_users(session)
    shared = _item(session, label="Close cronjobs", types=(STD, DUMP))
    only_dump = _item(session, label="Restart workers", types=(DUMP,), position=2)
    login_as(client, "root")

    all_view = _group(client.get("/management/checklists").text, "all")

    assert all_view.count(f'data-item-id="{shared.id}"') == 1
    assert all_view.count(f'data-item-id="{only_dump.id}"') == 1
    row = _row(all_view, shared.id)
    assert _applies(row, STD, True) and _applies(row, DUMP, True) and _applies(row, LOCAL, False)
    assert "checklist-group-head" not in all_view
    # Order is per type, so Sort Order belongs to a type's own tab, not the mixed list.
    assert 'name="position"' not in all_view
    assert "data-edit-toggle" in row


def test_type_view_shows_only_that_types_terms_in_its_order_with_sort_order(web):
    client, session = web
    _seed_users(session)
    first = _item(session, label="A", types=(DUMP,), position=2)
    second = _item(session, label="B", types=(DUMP, LOCAL), position=1)
    _item(session, label="Only local", types=(LOCAL,), position=2)
    login_as(client, "root")

    page = client.get("/management/checklists").text
    dump_view = _group(page, "db_dump_restore")

    assert re.search(r'<table class="grid-table checklist-table" data-group="db_dump_restore" data-column-order="dt-checklist-columns" hidden>', page)
    assert dump_view.index(f'data-item-id="{second.id}"') < dump_view.index(f'data-item-id="{first.id}"')
    assert "Only local" not in dump_view
    row = _row(dump_view, first.id)
    assert 'data-sort-order="2"' in row
    assert 'name="position" value="2"' in row and 'name="position_type" value="db_dump_restore"' in row


def test_empty_type_view_says_start_is_one_click(web):
    client, session = web
    _seed_users(session)
    _item(session, label="Close cronjobs", types=(DUMP,))
    login_as(client, "mgr")

    page = client.get("/management/checklists").text

    assert "No terms" in _group(page, "standard")
    note = re.search(r'<p class="checklist-empty-note"[^>]*>.*?</p>', page, re.S).group(0)
    assert "Standard Deployment" in note and "Test.local Deployment" in note
    assert "Database Dump" not in note


def test_unassigned_term_is_in_the_all_view_marked_not_assigned(web):
    client, session = web
    _seed_users(session)
    loose = _item(session, label="Snapshot DB", types=())
    login_as(client, "mgr")

    page = client.get("/management/checklists").text

    assert "Not assigned" in _row(_group(page, "all"), loose.id)
    for request_type in RequestType:
        assert f'data-item-id="{loose.id}"' not in _group(page, request_type.value)


def test_page_opens_on_the_view_named_in_the_url(web):
    client, session = web
    _seed_users(session)
    _item(session, label="Close cronjobs", types=(DUMP,))
    login_as(client, "mgr")

    page = client.get("/management/checklists?view=db_dump_restore").text

    assert re.search(r'<table class="grid-table checklist-table" data-group="db_dump_restore" data-column-order="dt-checklist-columns">', page)
    assert re.search(r'<table class="grid-table checklist-table" data-group="all" data-column-order="dt-checklist-columns" hidden>', page)
    assert re.search(r'class="tab on" data-checklist-filter="db_dump_restore"', page)


def test_unknown_view_falls_back_to_all(web):
    client, session = web
    _seed_users(session)
    login_as(client, "mgr")

    page = client.get("/management/checklists?view=nope").text

    assert re.search(r'<table class="grid-table checklist-table" data-group="all" data-column-order="dt-checklist-columns">', page)


@pytest.mark.parametrize("action,data", [
    ("deactivate", {}),
    ("edit", {"label": "Stop cron", "request_types": "db_dump_restore", "types_submitted": "1"}),
])
def test_actions_return_to_the_view_they_were_made_from(web, action, data):
    # Regression: every save/retire redirected to the bare page, dropping back to All.
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", types=(DUMP,))
    login_as(client, "root")

    response = client.post(f"/management/checklists/{item.id}/{action}", data={**data, "view": "db_dump_restore"},
                           follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/management/checklists?view=db_dump_restore"


def test_add_returns_to_its_view_and_forms_carry_the_view(web):
    client, session = web
    _seed_users(session)
    login_as(client, "mgr")

    response = client.post("/management/checklists",
                           data={"label": "Snapshot DB", "request_types": "test_local", "view": "test_local"},
                           follow_redirects=False)
    page = client.get("/management/checklists?view=test_local").text

    assert response.headers["location"] == "/management/checklists?view=test_local"
    assert 'name="view" value="test_local"' in _add_form(page)


def test_add_saves_when_the_term_is_due(web):
    client, session = web
    _seed_users(session)
    login_as(client, "mgr")

    client.post("/management/checklists",
                data={"label": "After deploy: smoke test", "request_types": "standard", "due": "before_complete"})
    client.post("/management/checklists", data={"label": "Backup taken", "request_types": "standard"})

    assert session.query(ChecklistItem).filter_by(label="After deploy: smoke test").one().due == "before_complete"
    assert session.query(ChecklistItem).filter_by(label="Backup taken").one().due == "before_start"


def test_add_and_edit_reject_an_unknown_due(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs")
    login_as(client, "root")

    assert client.post("/management/checklists",
                       data={"label": "x", "request_types": "standard", "due": "whenever"}).status_code == 400
    assert client.post(f"/management/checklists/{item.id}/edit",
                       data={"label": "Close cronjobs", "due": "whenever"}).status_code == 400


def test_admin_changes_when_a_term_is_due(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="After restoration, remove email settings")
    login_as(client, "root")

    client.post(f"/management/checklists/{item.id}/edit",
                data={"label": item.label, "due": "before_complete"})

    session.expire_all()
    assert session.get(ChecklistItem, item.id).due == "before_complete"


def test_rows_show_when_each_term_is_due_and_forms_offer_the_choice(web):
    client, session = web
    _seed_users(session)
    before = _item(session, label="Backup taken")
    after = make_checklist_item(session, label="Smoke test", due="before_complete", position=2)
    login_as(client, "root")

    page = client.get("/management/checklists").text

    # The design's two switches: Before Start on for one, Before Mark Deployed for the other.
    assert 'data-due-switch="before_start" aria-checked="true"' in _row(page, before.id)
    assert 'data-due-switch="before_complete" aria-checked="false"' in _row(page, before.id)
    assert 'data-due-switch="before_complete" aria-checked="true"' in _row(page, after.id)
    assert 'name="due" value="before_complete"' in _add_form(page)
    edit = re.search(r'<form[^>]*action="/management/checklists/\d+/edit".*?</form>', _row(page, after.id), re.S).group(0)
    assert re.search(r'name="due" value="before_complete"[^>]*\bchecked\b', edit)
