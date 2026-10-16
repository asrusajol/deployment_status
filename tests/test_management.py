"""Management: the old hub URL, the Settings nav, and who may open it.

The Management access switch itself is saved by the Users grid — see
tests/test_admin_users_grid.py.

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
    nav = _nav(client.get("/requests").text)
    assert 'href="/management' not in nav
    assert 'href="/admin/users"' not in nav
    assert "nav-settings" not in nav


def test_management_redirects_a_management_user_to_the_checklist(web):
    """/management was the hub page; Settings now opens in the nav, so the old URL
    (bookmarks) lands on the first Settings page this user can open."""
    client, session = web
    _seed(session, dev_has_access=True)
    login_as(client, "devone")

    response = client.get("/management", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/management/checklists"
    # The design's Settings group: Management Checklist only — Users is admin-only.
    nav = _nav(client.get("/requests").text)
    assert 'href="/management/checklists"' in nav
    assert 'href="/admin/users"' not in nav


def test_management_redirects_an_admin_to_users(web):
    client, session = web
    _seed(session)
    login_as(client, "root")

    response = client.get("/management", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users"


def test_admin_nav_settings_group_holds_users_and_management_checklist(web):
    """The design's Settings item opens a group with Users and Management Checklist,
    rather than linking to the Management hub."""
    client, session = web
    _seed(session)
    login_as(client, "root")

    nav = _nav(client.get("/requests").text)

    assert 'class="nav-item nav-settings"' in nav
    assert 'href="/admin/users"' in nav
    assert 'href="/management/checklists"' in nav
    assert ">Management Checklist<" in nav
    assert nav.index('href="/admin/users"') < nav.index('href="/management/checklists"')


def test_settings_group_opens_on_its_own_pages_only(web):
    client, session = web
    _seed(session)
    login_as(client, "root")

    def settings_button(path):
        nav = _nav(client.get(path).text)
        return re.search(r'<button[^>]*class="nav-item nav-settings[^"]*"[^>]*>', nav).group(0)

    assert 'aria-expanded="false"' in settings_button("/requests")
    assert 'aria-expanded="true"' in settings_button("/admin/users")
    assert 'aria-expanded="true"' in settings_button("/management/checklists")
