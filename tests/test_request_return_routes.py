from datetime import datetime, timezone

from app.models.deployment_execution import DeploymentExecution, ExecutionStatus
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from app.models.request_return import RequestReturn
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def _deploy_team_user(session, settings_group="MG-00013"):
    """The deploy team is identified by machine_group_id — see
    require_deploy_team_member() in app/auth.py. An admin qualifies too."""
    return make_user(
        session, id=1, name="Zunayed Islam", username="zunayed",
        password=DEFAULT_TEST_PASSWORD, role=UserRole.admin,
    )


def _approved_request(session, *, task_id="PR-RET", request_type=RequestType.standard):
    request = DeploymentRequest(
        task_id=task_id, requested_by=2, status=RequestStatus.approved,
        request_type=request_type, created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    session.add(request)
    session.commit()
    return request


def test_deploy_team_can_return_an_approved_request(web):
    client, session = web
    _deploy_team_user(session)
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _approved_request(session)
    login_as(client, "zunayed")

    response = client.post(
        f"/requests/{request.id}/return",
        data={"reason": "branch client/foo was deleted"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.returned
    logged = session.query(RequestReturn).one()
    assert logged.reason == "branch client/foo was deleted"
    assert logged.returned_from == RequestStatus.approved
    assert logged.returned_by == 1


def test_returning_requires_a_reason(web):
    client, session = web
    _deploy_team_user(session)
    session.commit()
    request = _approved_request(session)
    login_as(client, "zunayed")

    response = client.post(f"/requests/{request.id}/return", data={"reason": "   "}, follow_redirects=False)

    assert response.status_code == 400
    session.refresh(request)
    assert request.status == RequestStatus.approved
    assert session.query(RequestReturn).count() == 0


def test_a_developer_cannot_return(web):
    client, session = web
    make_user(session, id=1, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _approved_request(session)
    login_as(client, "devone")

    response = client.post(f"/requests/{request.id}/return", data={"reason": "nope"}, follow_redirects=False)

    assert response.status_code == 403
    assert session.query(RequestReturn).count() == 0


def test_returning_an_unfinished_status_is_refused(web):
    """Only a request sitting in the deploy queue can be handed back."""
    client, session = web
    _deploy_team_user(session)
    session.commit()
    request = _approved_request(session)
    request.status = RequestStatus.completed
    session.commit()
    login_as(client, "zunayed")

    response = client.post(f"/requests/{request.id}/return", data={"reason": "x"}, follow_redirects=False)

    assert response.status_code == 409


def test_returning_from_in_progress_drops_the_claim(web):
    """DeploymentExecution.request_id is unique on purpose, so a leftover claim
    would make the later Start Deployment insert fail — at the exact moment
    devops picks the request back up. The claim is still recorded as
    returned_from."""
    client, session = web
    _deploy_team_user(session)
    session.commit()
    request = _approved_request(session, task_id="PR-INPROG")
    request.status = RequestStatus.in_progress
    session.add(
        DeploymentExecution(
            request_id=request.id, executed_by=1,
            claimed_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            started_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            status=ExecutionStatus.claimed,
        )
    )
    session.commit()
    login_as(client, "zunayed")

    response = client.post(
        f"/requests/{request.id}/return", data={"reason": "migration error on live"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert session.query(DeploymentExecution).filter_by(request_id=request.id).count() == 0
    assert session.query(RequestReturn).one().returned_from == RequestStatus.in_progress
