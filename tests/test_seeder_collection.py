from app.models.client import Client
from app.models.client_system_url import ClientSystemUrl
from app.models.deployment_request import DeploymentEnvironment
from app.models.seeder_command import SeederCommand
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def _seed_client(session, *, client_id=1, name="CRM"):
    if session.get(Client, client_id) is None:
        session.add(Client(id=client_id, name=name))
        session.commit()


def _seed_seeder_command(session, *, client_id=1, client_name="CRM", created_by=1, **overrides):
    _seed_client(session, client_id=client_id, name=client_name)
    defaults = dict(
        client_id=client_id, title="Dynamic Permission Seeder",
        command="php8.2 artisan seed:permissions --modules=BASEVISU", created_by=created_by, updated_by=created_by,
    )
    defaults.update(overrides)
    row = SeederCommand(**defaults)
    session.add(row)
    session.commit()
    return row


def _login_devops(client, session, *, user_id=1, username="devopsone"):
    make_user(session, id=user_id, name="Devops One", role=UserRole.devops, username=username, password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, username)


def _login_developer(client, session, *, user_id=1, username="devone"):
    make_user(session, id=user_id, name="Dev One", role=UserRole.developer, username=username, password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, username)


def test_seeder_collection_page_lists_saved_commands(web):
    client, session = web
    _login_devops(client, session)
    _seed_seeder_command(session)

    response = client.get("/seeder-collection")

    assert response.status_code == 200
    assert "CRM" in response.text
    assert "Dynamic Permission Seeder" in response.text
    assert "php8.2 artisan seed:permissions --modules=BASEVISU" in response.text


def test_seeder_collection_requires_login(web):
    client, session = web
    _seed_seeder_command(session)

    response = client.get("/seeder-collection", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_seeder_collection_forbidden_for_non_devops(web):
    client, session = web
    _login_developer(client, session)
    _seed_seeder_command(session)

    response = client.get("/seeder-collection")

    assert response.status_code == 403


def test_seeder_collection_nav_link_hidden_for_non_devops(web):
    client, session = web
    _login_developer(client, session)

    response = client.get("/dashboard")

    assert "Seeder Collection" not in response.text


def test_seeder_collection_nav_link_shown_for_devops(web):
    client, session = web
    _login_devops(client, session)

    response = client.get("/dashboard")

    assert "Seeder Collection" in response.text


def test_create_seeder_command_via_form(web):
    client, session = web
    _login_devops(client, session)
    _seed_client(session)

    response = client.post(
        "/seeder-collection/new",
        data={"client_id": "1", "title": "Dynamic Permission Seeder", "command": "php artisan seed"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    row = session.query(SeederCommand).filter_by(client_id=1).one()
    assert row.title == "Dynamic Permission Seeder"
    assert row.created_by == 1


def test_create_rejects_second_command_for_same_client(web):
    client, session = web
    _login_devops(client, session)
    _seed_seeder_command(session)

    response = client.post(
        "/seeder-collection/new",
        data={"client_id": "1", "title": "t", "command": "c"},
    )

    assert response.status_code == 400


def test_edit_seeder_command(web):
    client, session = web
    _login_devops(client, session)
    row = _seed_seeder_command(session)

    response = client.post(
        f"/seeder-collection/{row.id}/edit",
        data={"title": "New Title", "command": "new command"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(row)
    assert row.title == "New Title"
    assert row.command == "new command"
    assert row.updated_by == 1


def test_delete_seeder_command(web):
    client, session = web
    _login_devops(client, session)
    row = _seed_seeder_command(session)

    response = client.post(f"/seeder-collection/{row.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert session.query(SeederCommand).count() == 0


def test_non_devops_cannot_edit(web):
    client, session = web
    _login_developer(client, session)
    row = _seed_seeder_command(session)

    response = client.post(
        f"/seeder-collection/{row.id}/edit", data={"title": "x", "command": "x"}
    )

    assert response.status_code == 403


def test_non_devops_cannot_delete(web):
    client, session = web
    _login_developer(client, session)
    row = _seed_seeder_command(session)

    response = client.post(f"/seeder-collection/{row.id}/delete")

    assert response.status_code == 403


def _seed_url(session, *, client_id=1, environment=DeploymentEnvironment.test, url="http://crm-test.local", label=None):
    session.add(ClientSystemUrl(client_id=client_id, environment=environment, url=url, label=label))
    session.commit()


def test_card_shows_the_clients_test_and_live_urls(web):
    """The same seeder serves both environments, so the card shows both."""
    client, session = web
    _login_devops(client, session)
    _seed_seeder_command(session)
    _seed_url(session, url="http://crm-test.local")
    _seed_url(session, environment=DeploymentEnvironment.live, url="http://crm-live.local")

    response = client.get("/seeder-collection")

    assert "http://crm-test.local" in response.text
    assert "http://crm-live.local" in response.text


def test_card_shows_every_url_for_an_environment_with_its_label(web):
    """A client with two Live servers shows both, told apart by label."""
    client, session = web
    _login_devops(client, session)
    _seed_seeder_command(session)
    _seed_url(session, environment=DeploymentEnvironment.live, url="http://crm-live-1.local", label="Line 1")
    _seed_url(session, environment=DeploymentEnvironment.live, url="http://crm-live-2.local", label="Line 2")

    response = client.get("/seeder-collection")

    assert "http://crm-live-1.local" in response.text
    assert "http://crm-live-2.local" in response.text
    assert "Line 1" in response.text
    assert "Line 2" in response.text


def test_card_prompts_to_add_urls_when_the_client_has_none(web):
    client, session = web
    _login_devops(client, session)
    _seed_seeder_command(session)

    response = client.get("/seeder-collection")

    assert "No server URLs on record" in response.text
    assert 'href="/clients"' in response.text


def test_card_is_filterable_by_url(web):
    """The filter box searches the URLs now that host is gone, so typing an
    IP still finds the card."""
    client, session = web
    _login_devops(client, session)
    _seed_seeder_command(session)
    _seed_url(session, url="http://10.10.2.103")

    response = client.get("/seeder-collection")

    card = response.text.split('data-search="')[1].split('"')[0]
    assert "http://10.10.2.103" in card


def test_new_form_embeds_client_urls_so_selecting_a_client_shows_them(web):
    client, session = web
    _login_devops(client, session)
    _seed_client(session)
    _seed_url(session, url="http://crm-test.local")
    _seed_url(session, environment=DeploymentEnvironment.live, url="http://crm-live.local")

    response = client.get("/seeder-collection/new")

    assert 'data-client-id="1"' in response.text
    assert 'data-environment="test"' in response.text
    assert 'data-url="http://crm-test.local"' in response.text
    assert 'data-url="http://crm-live.local"' in response.text


def test_edit_form_shows_the_clients_urls(web):
    client, session = web
    _login_devops(client, session)
    row = _seed_seeder_command(session)
    _seed_url(session, url="http://crm-test.local")

    response = client.get(f"/seeder-collection/{row.id}/edit")

    assert "http://crm-test.local" in response.text


def test_form_has_no_host_field(web):
    client, session = web
    _login_devops(client, session)
    _seed_client(session)

    response = client.get("/seeder-collection/new")

    assert 'name="host"' not in response.text


def test_new_form_client_picker_is_a_searchable_combobox(web):
    """Same type-to-filter picker as the dashboard/requests filter bar, not a
    raw <select> — there are 55 clients to scroll past otherwise."""
    client, session = web
    _login_devops(client, session)
    _seed_client(session, client_id=1, name="Scherer GmbH")

    response = client.get("/seeder-collection/new")

    assert 'class="combobox-options"' in response.text
    assert 'class="combobox-option"' in response.text
    assert "Scherer GmbH" in response.text
    assert '<select name="client_id"' not in response.text
    assert '<select id="client_id"' not in response.text


def test_create_without_choosing_a_client_shows_an_error(web):
    """A typed client name that matches nothing leaves the hidden field empty;
    that must be a readable error, not a 422."""
    client, session = web
    _login_devops(client, session)
    _seed_client(session)

    response = client.post(
        "/seeder-collection/new",
        data={"client_id": "", "title": "t", "command": "c"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Choose a client" in response.text
