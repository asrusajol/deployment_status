from app.models.client import Client
from app.models.client_system_url import ClientSystemUrl
from app.models.deployment_request import DeploymentEnvironment
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def test_clients_page_redirects_anonymous_to_login(web):
    client, _session = web
    response = client.get("/clients", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_clients_page_lists_synced_clients_read_only_for_developer(web):
    client, session = web
    make_user(session, id=1, name="Some Developer", role=UserRole.developer, username="dev", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="Intercable (ICT)"))
    session.add(ClientSystemUrl(client_id=1, environment=DeploymentEnvironment.test, url="http://intercable-test.local"))
    session.commit()
    login_as(client, "dev")

    response = client.get("/clients")

    assert response.status_code == 200
    assert "Intercable (ICT)" in response.text
    assert "http://intercable-test.local" in response.text
    # Read-only: no add/delete form for a plain developer.
    assert "/clients/1/urls" not in response.text


def test_devops_can_add_multiple_urls_to_the_same_client_and_system(web):
    client, session = web
    make_user(session, id=1, name="Devops User", role=UserRole.devops, username="devops1", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="Schertech GmbH"))
    session.commit()
    login_as(client, "devops1")

    client.post(
        "/clients/1/urls",
        data={"environment": "test", "label": "Line 1", "url": "http://schertech-test-1.local"},
        follow_redirects=False,
    )
    response = client.post(
        "/clients/1/urls",
        data={"environment": "test", "label": "Line 2", "url": "http://schertech-test-2.local"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    urls = session.query(ClientSystemUrl).filter_by(client_id=1).order_by(ClientSystemUrl.id).all()
    assert [(u.label, u.url, u.environment) for u in urls] == [
        ("Line 1", "http://schertech-test-1.local", DeploymentEnvironment.test),
        ("Line 2", "http://schertech-test-2.local", DeploymentEnvironment.test),
    ]


def test_admin_can_add_a_client_url(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="Schertech GmbH"))
    session.commit()
    login_as(client, "root")

    response = client.post(
        "/clients/1/urls", data={"environment": "live", "url": "http://schertech-live.local"}, follow_redirects=False
    )

    assert response.status_code == 303
    urls = session.query(ClientSystemUrl).filter_by(client_id=1).all()
    assert len(urls) == 1
    assert urls[0].url == "http://schertech-live.local"
    assert urls[0].environment == DeploymentEnvironment.live
    assert urls[0].label is None


def test_admin_can_delete_a_client_url(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="Schertech GmbH"))
    session.add(ClientSystemUrl(id=1, client_id=1, environment=DeploymentEnvironment.test, url="http://stale.local"))
    session.commit()
    login_as(client, "root")

    response = client.post("/clients/1/urls/1/delete", follow_redirects=False)

    assert response.status_code == 303
    assert session.get(ClientSystemUrl, 1) is None


def test_developer_cannot_add_or_delete_client_urls(web):
    client, session = web
    make_user(session, id=1, name="Some Developer", role=UserRole.developer, username="dev", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="Schertech GmbH"))
    session.add(ClientSystemUrl(id=1, client_id=1, environment=DeploymentEnvironment.test, url="http://x.local"))
    session.commit()
    login_as(client, "dev")

    add_response = client.post("/clients/1/urls", data={"environment": "test", "url": "http://y.local"})
    delete_response = client.post("/clients/1/urls/1/delete")

    assert add_response.status_code == 403
    assert delete_response.status_code == 403
    assert session.query(ClientSystemUrl).filter_by(client_id=1).count() == 1


def test_team_lead_cannot_add_client_urls(web):
    client, session = web
    make_user(session, id=1, name="A Lead", role=UserRole.team_lead, username="lead", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="Schertech GmbH"))
    session.commit()
    login_as(client, "lead")

    response = client.post("/clients/1/urls", data={"environment": "test", "url": "http://x.local"})

    assert response.status_code == 403


def test_devops_can_deactivate_and_reactivate_a_client(web):
    client, session = web
    make_user(session, id=1, name="Devops User", role=UserRole.devops, username="devops1", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="Schertech GmbH"))
    session.commit()
    login_as(client, "devops1")

    response = client.post("/clients/1/toggle-active", follow_redirects=False)
    assert response.status_code == 303
    target = session.get(Client, 1)
    session.refresh(target)
    assert target.is_active is False

    response = client.post("/clients/1/toggle-active", follow_redirects=False)
    assert response.status_code == 303
    session.refresh(target)
    assert target.is_active is True


def test_developer_cannot_toggle_client_active(web):
    client, session = web
    make_user(session, id=1, name="Some Developer", role=UserRole.developer, username="dev", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="Schertech GmbH"))
    session.commit()
    login_as(client, "dev")

    response = client.post("/clients/1/toggle-active")

    assert response.status_code == 403
    target = session.get(Client, 1)
    session.refresh(target)
    assert target.is_active is True


def test_clients_page_renders_filter_input_and_row_name_attributes(web):
    client, session = web
    make_user(session, id=1, name="Some Developer", role=UserRole.developer, username="dev", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="Intercable (ICT)"))
    session.add(Client(id=2, name="Schertech GmbH"))
    session.commit()
    login_as(client, "dev")

    response = client.get("/clients")

    assert response.status_code == 200
    assert 'id="client-name-filter"' in response.text
    assert 'data-client-name="intercable (ict)"' in response.text
    assert 'data-client-name="schertech gmbh"' in response.text


def test_clients_page_shows_status_and_toggle_button_for_admin_devops(web):
    client, session = web
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    session.add(Client(id=1, name="Active Co", is_active=True))
    session.add(Client(id=2, name="Inactive Co", is_active=False))
    session.commit()
    login_as(client, "root")

    response = client.get("/clients")

    assert response.status_code == 200
    assert "/clients/1/toggle-active" in response.text
    assert "Deactivate" in response.text
    assert "/clients/2/toggle-active" in response.text
    assert "Activate" in response.text
