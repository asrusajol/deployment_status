import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models on Base.metadata
from app.database import Base
from app.models.client import Client
from app.models.seeder_command import SeederCommand
from app.models.user import User, UserRole
from app.services.seeder_collection import (
    ClientAlreadyHasSeederCommandError,
    clients_without_seeder_command,
    create_seeder_command,
    delete_seeder_command,
    seeder_collection_rows,
    update_seeder_command,
)


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_client(db_session, *, client_id=1, name="CRM"):
    db_session.add(Client(id=client_id, name=name))
    db_session.flush()


def _seed_user(db_session, *, user_id=1, name="Devops One"):
    db_session.add(User(id=user_id, name=name, role=UserRole.devops))
    db_session.flush()


def test_create_seeder_command(db_session):
    _seed_client(db_session)
    _seed_user(db_session)

    row = create_seeder_command(
        db_session, client_id=1, host="10.10.2.103", title="Dynamic Permission Seeder",
        command="php8.2 artisan seed:permissions --modules=BASEVISU", created_by=1,
    )
    db_session.commit()

    assert row.id is not None
    assert row.client_id == 1
    assert row.host == "10.10.2.103"
    assert row.title == "Dynamic Permission Seeder"
    assert row.command == "php8.2 artisan seed:permissions --modules=BASEVISU"
    assert row.created_by == 1
    assert row.updated_by == 1


def test_create_seeder_command_rejects_a_second_row_for_the_same_client(db_session):
    _seed_client(db_session)
    _seed_user(db_session)
    create_seeder_command(db_session, client_id=1, host="h", title="t", command="c", created_by=1)
    db_session.commit()

    with pytest.raises(ClientAlreadyHasSeederCommandError):
        create_seeder_command(db_session, client_id=1, host="h2", title="t2", command="c2", created_by=1)


def test_update_seeder_command(db_session):
    _seed_client(db_session)
    _seed_user(db_session)
    _seed_user(db_session, user_id=2, name="Devops Two")
    row = create_seeder_command(db_session, client_id=1, host="h", title="t", command="c", created_by=1)
    db_session.commit()

    updated = update_seeder_command(db_session, row, host="h2", title="t2", command="c2", updated_by=2)
    db_session.commit()

    assert updated.host == "h2"
    assert updated.title == "t2"
    assert updated.command == "c2"
    assert updated.updated_by == 2
    assert updated.created_by == 1  # unchanged


def test_delete_seeder_command(db_session):
    _seed_client(db_session)
    _seed_user(db_session)
    row = create_seeder_command(db_session, client_id=1, host="h", title="t", command="c", created_by=1)
    db_session.commit()

    delete_seeder_command(db_session, row)
    db_session.commit()

    assert db_session.query(SeederCommand).count() == 0


def test_seeder_collection_rows_ordered_by_client_name(db_session):
    _seed_client(db_session, client_id=1, name="Zebra Corp")
    _seed_client(db_session, client_id=2, name="Acme")
    _seed_user(db_session)
    create_seeder_command(db_session, client_id=1, host="h", title="t", command="c", created_by=1)
    create_seeder_command(db_session, client_id=2, host="h", title="t", command="c", created_by=1)
    db_session.commit()

    rows = seeder_collection_rows(db_session)
    assert [r.client.name for r in rows] == ["Acme", "Zebra Corp"]


def test_clients_without_seeder_command_excludes_clients_that_already_have_one(db_session):
    _seed_client(db_session, client_id=1, name="CRM")
    _seed_client(db_session, client_id=2, name="Acme")
    _seed_user(db_session)
    create_seeder_command(db_session, client_id=1, host="h", title="t", command="c", created_by=1)
    db_session.commit()

    clients = clients_without_seeder_command(db_session)
    assert [c.name for c in clients] == ["Acme"]
