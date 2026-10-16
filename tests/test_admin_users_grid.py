"""Users page in the design's layout: one editable grid, one Save for every change.

"Active" in the design is login access — a password on record — so switching it on
grants access (a new password is required) and switching it off revokes it. The old
per-field routes stay; this page posts everything to /admin/users/save in one go.
"""

import re

from app.auth import verify_password
from app.models.user import User, UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def _seed(session):
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=3, name="New Hire", role=UserRole.developer)  # synced, no access yet
    session.commit()


def _form(**rows):
    """The grid's POST body. Each row: role, plus optional active / show_others /
    management / audit (switches: present = on), password, username."""
    data = {"user_ids": []}
    for key, row in rows.items():
        uid = key.lstrip("u")
        data["user_ids"].append(uid)
        data[f"role-{uid}"] = row["role"]
        for switch in ("active", "show_others", "management", "audit"):
            if row.get(switch):
                data[f"{switch}-{uid}"] = "on"
        for text in ("password", "username"):
            if text in row:
                data[f"{text}-{uid}"] = row[text]
    return data


def _unchanged():
    return {
        "u1": {"role": "admin", "active": True},
        "u2": {"role": "developer", "active": True},
        "u3": {"role": "developer"},
    }


def _save(client, rows):
    return client.post("/admin/users/save", data=_form(**rows), follow_redirects=False)


def _fresh(session, user_id):
    session.expire_all()
    return session.get(User, user_id)


def test_page_is_one_form_with_a_row_of_controls_per_user(web):
    client, session = web
    _seed(session)
    login_as(client, "root")

    page = client.get("/admin/users").text

    form = re.search(r'<form[^>]*id="users-form"[^>]*>', page).group(0)
    assert 'action="/admin/users/save"' in form
    assert 'name="role-2"' in page
    assert 'name="password-3"' in page
    assert 'name="management-2"' in page
    assert 'name="active-2"' in page and 'name="active-3"' in page
    # A synced user with no username gets a field for one, as granting needs it.
    assert 'name="username-3"' in page
    assert 'name="username-2"' not in page
    assert "No login access" in page
    assert 'id="users-save"' in page


def test_non_admin_cannot_save(web):
    client, session = web
    _seed(session)
    login_as(client, "devone")

    assert _save(client, _unchanged()).status_code == 403


def test_saves_role_and_switches_together(web):
    client, session = web
    _seed(session)
    login_as(client, "root")
    rows = _unchanged()
    rows["u2"] = {"role": "team_lead", "active": True, "management": True, "audit": True}

    response = _save(client, rows)

    assert response.status_code == 303
    dev = _fresh(session, 2)
    assert dev.role == UserRole.team_lead
    assert dev.can_access_management is True
    assert dev.can_view_checklist_audit is True


def test_show_others_applies_to_admins_and_access_switches_to_the_rest(web):
    """Admins always have Management and Checklist Audit (the switches show on and
    locked); Show Others only does anything for admins. Values the page can't change
    for that role are left as they are."""
    client, session = web
    _seed(session)
    dev = _fresh(session, 2)
    dev.can_manage_other_returns = True
    session.commit()
    login_as(client, "root")
    rows = _unchanged()
    rows["u1"] = {"role": "admin", "active": True, "show_others": True}

    _save(client, rows)

    assert _fresh(session, 1).can_manage_other_returns is True
    assert _fresh(session, 1).can_access_management is False
    assert _fresh(session, 2).can_manage_other_returns is True


def test_password_grants_access_and_sets_the_missing_username(web):
    client, session = web
    _seed(session)
    login_as(client, "root")
    rows = _unchanged()
    rows["u3"] = {"role": "developer", "active": True, "password": "temp-pass-1", "username": "newhire"}

    assert _save(client, rows).status_code == 303

    hire = _fresh(session, 3)
    assert hire.username == "newhire"
    assert verify_password("temp-pass-1", hire.password_hash)
    assert hire.must_change_password is True


def test_password_resets_an_existing_users_password(web):
    client, session = web
    _seed(session)
    login_as(client, "root")
    rows = _unchanged()
    rows["u2"] = {"role": "developer", "active": True, "password": "fresh-pass-9"}

    _save(client, rows)

    dev = _fresh(session, 2)
    assert verify_password("fresh-pass-9", dev.password_hash)
    assert dev.must_change_password is True


def test_switching_active_off_revokes_login_access(web):
    client, session = web
    _seed(session)
    login_as(client, "root")
    rows = _unchanged()
    rows["u2"] = {"role": "developer"}

    _save(client, rows)

    assert _fresh(session, 2).password_hash is None


def test_admin_cannot_revoke_their_own_access(web):
    client, session = web
    _seed(session)
    login_as(client, "root")
    rows = _unchanged()
    rows["u1"] = {"role": "admin"}

    response = _save(client, rows)

    assert response.status_code == 400
    assert "your own" in response.text
    assert _fresh(session, 1).password_hash is not None


def test_active_without_a_password_is_refused(web):
    client, session = web
    _seed(session)
    login_as(client, "root")
    rows = _unchanged()
    rows["u3"] = {"role": "developer", "active": True, "username": "newhire"}

    response = _save(client, rows)

    assert response.status_code == 400
    assert "password" in response.text.lower()
    assert _fresh(session, 3).password_hash is None


def test_one_bad_row_saves_nothing(web):
    client, session = web
    _seed(session)
    login_as(client, "root")
    rows = _unchanged()
    rows["u2"] = {"role": "team_lead", "active": True}
    rows["u3"] = {"role": "developer", "active": True, "password": "short", "username": "newhire"}

    response = _save(client, rows)

    assert response.status_code == 400
    assert "at least" in response.text
    assert _fresh(session, 2).role == UserRole.developer
    assert _fresh(session, 3).username is None


def test_granting_needs_a_username_and_refuses_a_taken_one(web):
    client, session = web
    _seed(session)
    login_as(client, "root")
    rows = _unchanged()
    rows["u3"] = {"role": "developer", "active": True, "password": "temp-pass-1", "username": "devone"}

    response = _save(client, rows)

    assert response.status_code == 400
    assert "already taken" in response.text
    rows["u3"] = {"role": "developer", "active": True, "password": "temp-pass-1"}
    assert _save(client, rows).status_code == 400
    assert _fresh(session, 3).password_hash is None


def test_switches_turn_off_when_left_unticked(web):
    client, session = web
    _seed(session)
    dev = _fresh(session, 2)
    dev.can_access_management = dev.can_view_checklist_audit = True
    admin = _fresh(session, 1)
    admin.can_manage_other_returns = True
    session.commit()
    login_as(client, "root")

    _save(client, _unchanged())   # every switch above left off

    assert _fresh(session, 2).can_access_management is False
    assert _fresh(session, 2).can_view_checklist_audit is False
    assert _fresh(session, 1).can_manage_other_returns is False


def test_granted_user_can_log_in_and_must_pick_a_password(web):
    client, session = web
    _seed(session)
    login_as(client, "root")
    rows = _unchanged()
    rows["u3"] = {"role": "developer", "active": True, "password": "temp-pass-1", "username": "newhire"}
    _save(client, rows)
    client.post("/logout")

    response = client.post("/login", data={"username": "newhire", "password": "temp-pass-1"}, follow_redirects=False)

    assert response.status_code == 303
    assert client.get("/requests", follow_redirects=False).headers["location"] == "/change-password"


def test_show_others_is_only_switchable_on_admin_rows(web):
    client, session = web
    _seed(session)
    login_as(client, "root")

    page = client.get("/admin/users").text

    assert re.search(r'name="show_others-1" role="switch"(?![^>]*disabled)[^>]*>', page)
    assert re.search(r'name="show_others-2" role="switch"[^>]*disabled', page)
