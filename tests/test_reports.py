import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models on Base.metadata
from app.database import Base
from app.models.team import Team
from app.models.user import User, UserRole
from app.services.reports import UNASSIGNED_LABEL, users_by_team


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _add_user(db, name, machine_group_id):
    db.add(User(name=name, role=UserRole.developer, machine_group_id=machine_group_id))


def test_users_by_team_groups_by_team_name(db_session):
    db_session.add(Team(id=1, source_system_id="MG-00001", name="Team QA"))
    db_session.add(Team(id=3, source_system_id="MG-00003", name="Developer"))
    _add_user(db_session, "Alice", machine_group_id=1)
    _add_user(db_session, "Bob", machine_group_id=3)
    _add_user(db_session, "Carol", machine_group_id=1)
    db_session.commit()

    grouped = users_by_team(db_session)

    assert grouped == {"Developer": ["Bob"], "Team QA": ["Alice", "Carol"]}


def test_users_by_team_puts_null_machine_group_under_unassigned(db_session):
    _add_user(db_session, "Dave", machine_group_id=None)
    db_session.commit()

    grouped = users_by_team(db_session)

    assert grouped == {UNASSIGNED_LABEL: ["Dave"]}


def test_users_by_team_puts_dangling_machine_group_id_under_unassigned(db_session):
    # machine_group_id=999 references no Team row — this is the exact scenario the
    # non-FK relationship on User.team (app/models/user.py) is designed to handle
    # gracefully instead of raising or dropping the user from the report.
    _add_user(db_session, "Erin", machine_group_id=999)
    db_session.commit()

    grouped = users_by_team(db_session)

    assert grouped == {UNASSIGNED_LABEL: ["Erin"]}


def test_users_by_team_sorts_unassigned_last(db_session):
    db_session.add(Team(id=1, source_system_id="MG-00001", name="Zebra Team"))
    _add_user(db_session, "Alice", machine_group_id=None)
    _add_user(db_session, "Bob", machine_group_id=1)
    db_session.commit()

    grouped = users_by_team(db_session)

    assert list(grouped.keys()) == ["Zebra Team", UNASSIGNED_LABEL]


# Tests for the Reports tab web interface
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


@pytest.mark.parametrize("role", [UserRole.admin, UserRole.devops, UserRole.team_lead])
def test_allowed_roles_can_open_the_reports_tab(web, role):
    client, session = web
    make_user(session, id=1, name="U", role=role, username="u", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "u")

    response = client.get("/reports")

    assert response.status_code == 200
    assert "Order Task Dependency" in response.text


def test_a_developer_is_refused(web):
    client, session = web
    make_user(session, id=1, name="D", role=UserRole.developer, username="d", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "d")

    assert client.get("/reports").status_code == 403


def test_the_nav_link_is_hidden_from_a_developer(web):
    client, session = web
    make_user(session, id=1, name="D", role=UserRole.developer, username="d", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "d")

    assert 'href="/reports"' not in client.get("/dashboard").text


def test_the_nav_link_is_shown_to_devops(web):
    client, session = web
    make_user(session, id=1, name="O", role=UserRole.devops, username="o", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "o")

    assert 'href="/reports"' in client.get("/dashboard").text


def test_anonymous_access_redirects_to_login(client):
    response = client.get("/reports", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
