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
