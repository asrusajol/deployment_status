from datetime import datetime, timezone

from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.user import User
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def _seed_release_tracker_row(session, *, client_id=1, client_name="CRM", current_version="2026.34.34"):
    session.add(Client(id=client_id, name=client_name))
    if session.get(User, 1) is None:
        make_user(session, id=1, name="Deployer", username="deployer", password=DEFAULT_TEST_PASSWORD)
    session.add(
        DeploymentRequest(
            id=client_id, request_type=RequestType.standard, client_id=client_id,
            environment=DeploymentEnvironment.live, status=RequestStatus.completed,
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    record = ClientVersionRecord(
        client_id=client_id, environment=DeploymentEnvironment.live, current_version=current_version,
        deployment_request_id=client_id, recorded_by=1,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    session.add(record)
    session.commit()
    return record


def test_release_tracker_page_renders_for_any_logged_in_user(web):
    client, session = web
    _seed_release_tracker_row(session)
    login_as(client, "deployer")

    response = client.get("/release-tracker")

    assert response.status_code == 200
    assert "2026.34.34" in response.text
    assert "CRM" in response.text


def test_release_tracker_requires_login(web):
    client, session = web
    _seed_release_tracker_row(session)

    response = client.get("/release-tracker", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_release_tracker_filters_by_client(web):
    client, session = web
    _seed_release_tracker_row(session, client_id=1, client_name="CRM", current_version="1.0")
    _seed_release_tracker_row(session, client_id=2, client_name="Acme", current_version="2.0")
    login_as(client, "deployer")

    response = client.get("/release-tracker", params={"client_id": "1"})

    assert "1.0" in response.text
    assert "2.0" not in response.text


def test_release_tracker_export_xlsx(web):
    client, session = web
    _seed_release_tracker_row(session)
    login_as(client, "deployer")

    response = client.get("/release-tracker/export.xlsx")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_nav_shows_release_tracker_link_for_logged_in_user(web):
    client, session = web
    _seed_release_tracker_row(session)
    login_as(client, "deployer")

    response = client.get("/requests")

    assert 'href="/release-tracker"' in response.text
