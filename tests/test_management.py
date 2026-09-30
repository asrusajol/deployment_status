"""Management tab: hub page, nav link, and the per-user Management access switch.

Access is a switch, not a role — a user holds exactly one role, and a fifth one would
strip a DevOps user of deploy rights. See
docs/superpowers/specs/2026-09-30-request-checklists-design.md.
"""

import re

from app.models.user import User, UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def _nav(page: str) -> str:
    match = re.search(r"<nav>.*?</nav>", page, re.S)
    assert match, "nav missing"
    return match.group(0)


def _seed(session, *, dev_has_access=False):
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    dev = make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    dev.can_access_management = dev_has_access
    session.commit()


def test_switches_default_off(web):
    _client, session = web
    _seed(session)
    dev = session.get(User, 2)
    assert dev.can_access_management is False
    assert dev.can_view_checklist_audit is False


def test_user_without_access_gets_403_and_no_nav_link(web):
    client, session = web
    _seed(session)
    login_as(client, "devone")

    assert client.get("/management").status_code == 403
    assert 'href="/management"' not in _nav(client.get("/requests").text)


def test_user_with_access_sees_hub_but_not_users_card(web):
    client, session = web
    _seed(session, dev_has_access=True)
    login_as(client, "devone")

    response = client.get("/management")

    assert response.status_code == 200
    assert 'href="/admin/users"' not in response.text
    assert 'href="/management"' in _nav(client.get("/requests").text)


def test_admin_sees_hub_with_users_card(web):
    client, session = web
    _seed(session)
    login_as(client, "root")

    response = client.get("/management")

    assert response.status_code == 200
    assert 'href="/admin/users"' in response.text


def test_admin_nav_shows_management_instead_of_admin(web):
    client, session = web
    _seed(session)
    login_as(client, "root")

    nav = _nav(client.get("/requests").text)

    assert 'href="/management"' in nav
    assert 'href="/admin/users"' not in nav


def test_admin_toggles_management_access(web):
    client, session = web
    _seed(session)
    login_as(client, "root")

    on = client.post("/admin/users/2/set-management-access", data={"can_access_management": "on"}, follow_redirects=False)
    assert on.status_code == 303
    session.expire_all()
    assert session.get(User, 2).can_access_management is True

    client.post("/admin/users/2/set-management-access", data={}, follow_redirects=False)
    session.expire_all()
    assert session.get(User, 2).can_access_management is False


def test_non_admin_cannot_toggle_management_access(web):
    client, session = web
    _seed(session, dev_has_access=True)
    login_as(client, "devone")

    response = client.post("/admin/users/2/set-management-access", data={}, follow_redirects=False)

    assert response.status_code == 403
    session.expire_all()
    assert session.get(User, 2).can_access_management is True
