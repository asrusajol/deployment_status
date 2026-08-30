from app.models.client import Client
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
        client_id=client_id, host="10.10.2.103", title="Dynamic Permission Seeder",
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
    assert "10.10.2.103" in response.text
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
        data={"client_id": "1", "host": "10.10.2.103", "title": "Dynamic Permission Seeder", "command": "php artisan seed"},
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
        data={"client_id": "1", "host": "h", "title": "t", "command": "c"},
    )

    assert response.status_code == 400


def test_edit_seeder_command(web):
    client, session = web
    _login_devops(client, session)
    row = _seed_seeder_command(session)

    response = client.post(
        f"/seeder-collection/{row.id}/edit",
        data={"host": "10.10.2.200", "title": "New Title", "command": "new command"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(row)
    assert row.host == "10.10.2.200"
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
        f"/seeder-collection/{row.id}/edit", data={"host": "x", "title": "x", "command": "x"}
    )

    assert response.status_code == 403


def test_non_devops_cannot_delete(web):
    client, session = web
    _login_developer(client, session)
    row = _seed_seeder_command(session)

    response = client.post(f"/seeder-collection/{row.id}/delete")

    assert response.status_code == 403
