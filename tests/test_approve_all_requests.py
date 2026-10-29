"""User.can_approve_all_requests: a per-user grant to approve/reject any team's request,
on top of whatever the user's role already allows (app/auth.py's
can_approve_deployment_request())."""
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


def test_admin_toggles_approve_all_switch(web):
    client, session = web
    _seed_pending_request(session)
    _seed_admin(session)
    login_as(client, "root")

    on = client.post("/admin/users/2/set-approve-all-access", data={"can_approve_all_requests": "on"}, follow_redirects=False)
    assert on.status_code == 303
    session.expire_all()
    assert session.get(User, 2).can_approve_all_requests is True

    client.post("/admin/users/2/set-approve-all-access", data={}, follow_redirects=False)
    session.expire_all()
    assert session.get(User, 2).can_approve_all_requests is False


def test_non_admin_cannot_toggle_approve_all_switch(web):
    client, session = web
    _seed_pending_request(session)
    login_as(client, "lead")

    response = client.post("/admin/users/2/set-approve-all-access", data={"can_approve_all_requests": "on"}, follow_redirects=False)

    assert response.status_code == 403
    session.expire_all()
    assert session.get(User, 2).can_approve_all_requests is False


def test_users_page_shows_approve_all_switch(web):
    client, session = web
    _seed_pending_request(session)
    _seed_admin(session)
    login_as(client, "root")

    page = client.get("/admin/users").text

    assert "Approve all requests" in page
    assert 'action="/admin/users/2/set-approve-all-access"' in page
