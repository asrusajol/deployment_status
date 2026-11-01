"""User.can_approve_all_requests: a per-user grant to approve/reject any team's request,
on top of whatever the user's role already allows (app/auth.py's
can_approve_deployment_request())."""
import re

from app.models.deployment_request import RequestStatus
from app.models.user import User, UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user
from tests.test_dashboard import REQUESTER_TEAM_MACHINE_GROUP_ID, _seed_pending_request


def _seed_other_team_lead(session, *, approves_all):
    lead = make_user(
        session, id=4, name="Other Team Lead", role=UserRole.team_lead, username="otherlead",
        password=DEFAULT_TEST_PASSWORD, machine_group_id=REQUESTER_TEAM_MACHINE_GROUP_ID + 1,
    )
    lead.can_approve_all_requests = approves_all
    session.commit()


def test_switch_defaults_off(web):
    _client, session = web
    _seed_pending_request(session)
    assert session.get(User, 2).can_approve_all_requests is False


def test_lead_with_switch_sees_approve_reject_on_another_teams_request(web):
    client, session = web
    _seed_pending_request(session)
    _seed_other_team_lead(session, approves_all=True)
    login_as(client, "otherlead")

    response = client.get("/requests")

    assert 'action="/requests/1/approve"' in response.text
    assert 'action="/requests/1/reject"' in response.text


def test_lead_with_switch_approves_another_teams_request(web):
    client, session = web
    request = _seed_pending_request(session)
    _seed_other_team_lead(session, approves_all=True)
    login_as(client, "otherlead")

    response = client.post(f"/requests/{request.id}/approve", follow_redirects=False)

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.approved


def test_lead_with_switch_rejects_another_teams_request(web):
    client, session = web
    request = _seed_pending_request(session)
    _seed_other_team_lead(session, approves_all=True)
    login_as(client, "otherlead")

    response = client.post(f"/requests/{request.id}/reject", data={"comment": "Not ready"}, follow_redirects=False)

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.rejected


def test_lead_without_switch_still_gets_403_on_another_teams_request(web):
    client, session = web
    request = _seed_pending_request(session)
    _seed_other_team_lead(session, approves_all=False)
    login_as(client, "otherlead")

    response = client.post(f"/requests/{request.id}/approve")

    assert response.status_code == 403
    session.refresh(request)
    assert request.status == RequestStatus.pending_approval


def test_switch_is_not_tied_to_the_team_lead_role(web):
    # A grant, like the Management switch — not inherited from, or limited to, a role.
    client, session = web
    request = _seed_pending_request(session)
    make_user(
        session, id=4, name="Dev Elsewhere", username="develsewhere", password=DEFAULT_TEST_PASSWORD,
        machine_group_id=REQUESTER_TEAM_MACHINE_GROUP_ID + 1,
    ).can_approve_all_requests = True
    session.commit()
    login_as(client, "develsewhere")

    response = client.post(f"/requests/{request.id}/approve", follow_redirects=False)

    assert response.status_code == 303


def _seed_admin(session):
    make_user(session, id=5, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    session.commit()


def _save_lead(client, *, approve_all):
    # The Users grid's one Save (app/routers/admin.py's save_users()), for the lead's row
    # only — a switch left out of the body means off, as with an unticked checkbox.
    data = {"user_ids": ["2"], "role-2": "team_lead", "active-2": "on"}
    if approve_all:
        data["approve_all-2"] = "on"
    return client.post("/admin/users/save", data=data, follow_redirects=False)


def test_admin_toggles_approve_all_switch(web):
    client, session = web
    _seed_pending_request(session)
    _seed_admin(session)
    login_as(client, "root")

    assert _save_lead(client, approve_all=True).status_code == 303
    session.expire_all()
    assert session.get(User, 2).can_approve_all_requests is True

    _save_lead(client, approve_all=False)
    session.expire_all()
    assert session.get(User, 2).can_approve_all_requests is False


def test_non_admin_cannot_toggle_approve_all_switch(web):
    client, session = web
    _seed_pending_request(session)
    login_as(client, "lead")

    assert _save_lead(client, approve_all=True).status_code == 403
    session.expire_all()
    assert session.get(User, 2).can_approve_all_requests is False


def test_users_grid_lets_admins_switch_approve_all_too(web):
    # Admins used to show it locked on; the switch now governs admins as well.
    client, session = web
    _seed_pending_request(session)
    _seed_admin(session)
    login_as(client, "root")

    page = client.get("/admin/users").text

    assert "Approve All Requests" in page
    assert re.search(r'name="approve_all-2" role="switch">', page)
    assert re.search(r'name="approve_all-5" role="switch">', page)   # admin, off, editable


def test_admin_saves_their_own_approve_all_switch(web):
    client, session = web
    _seed_pending_request(session)
    _seed_admin(session)
    login_as(client, "root")

    client.post(
        "/admin/users/save",
        data={"user_ids": ["5"], "role-5": "admin", "active-5": "on", "approve_all-5": "on"},
        follow_redirects=False,
    )

    session.expire_all()
    assert session.get(User, 5).can_approve_all_requests is True


def test_admin_without_switch_cannot_approve_another_teams_request(web):
    client, session = web
    request = _seed_pending_request(session)
    _seed_admin(session)
    login_as(client, "root")

    response = client.post(f"/requests/{request.id}/approve")

    assert response.status_code == 403
    session.refresh(request)
    assert request.status == RequestStatus.pending_approval


def test_admin_without_switch_still_approves_their_own_teams_request(web):
    # Without the switch an admin is treated like their own team's lead.
    client, session = web
    request = _seed_pending_request(session)
    make_user(
        session, id=5, name="Team Admin", role=UserRole.admin, username="teamadmin",
        password=DEFAULT_TEST_PASSWORD, machine_group_id=REQUESTER_TEAM_MACHINE_GROUP_ID,
    )
    session.commit()
    login_as(client, "teamadmin")

    response = client.post(f"/requests/{request.id}/approve", follow_redirects=False)

    assert response.status_code == 303


def test_admin_with_switch_approves_any_teams_request(web):
    client, session = web
    request = _seed_pending_request(session)
    _seed_admin(session)
    session.get(User, 5).can_approve_all_requests = True
    session.commit()
    login_as(client, "root")

    response = client.post(f"/requests/{request.id}/approve", follow_redirects=False)

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.approved
