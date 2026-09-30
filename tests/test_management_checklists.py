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


def _section(page, request_type):
    match = re.search(rf'<section class="checklist-section" data-request-type="{request_type.value}">.*?</section>', page, re.S)
    assert match, f"no section for {request_type.value}"
    return match.group(0)


def _row(page, item_id):
    match = re.search(rf'<tr data-item-id="{item_id}".*?</tr>', page, re.S)
    assert match, f"no row for item {item_id}"
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

    assert f'data-item-id="{dump.id}"' in _section(page, RequestType.db_dump_restore)
    assert f'data-item-id="{local.id}"' in _section(page, RequestType.test_local)
    assert "data-item-id" not in _section(page, RequestType.standard)


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
    assert "Retired" in _row(client.get("/management/checklists").text, item.id)

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
