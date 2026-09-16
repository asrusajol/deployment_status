import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models on Base.metadata
from app.database import Base
from app.models.client import Client
from app.models.client_system_url import ClientSystemUrl
from app.models.deployment_request import DeploymentEnvironment
from app.models.seeder_command import SeederCommand
from app.models.user import User, UserRole
from app.services.seeder_collection import (
    ClientAlreadyHasSeederCommandError,
    client_system_urls_for_form,
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
        db_session, client_id=1, title="Dynamic Permission Seeder",
        command="php8.2 artisan seed:permissions --modules=BASEVISU", created_by=1,
    )
    db_session.commit()

    assert row.id is not None
    assert row.client_id == 1
    assert row.title == "Dynamic Permission Seeder"
    assert row.command == "php8.2 artisan seed:permissions --modules=BASEVISU"
    assert row.created_by == 1
    assert row.updated_by == 1


def test_create_seeder_command_rejects_a_second_row_for_the_same_client(db_session):
    _seed_client(db_session)
    _seed_user(db_session)
    create_seeder_command(db_session, client_id=1, title="t", command="c", created_by=1)
    db_session.commit()

    with pytest.raises(ClientAlreadyHasSeederCommandError):
        create_seeder_command(db_session, client_id=1, title="t2", command="c2", created_by=1)


def test_update_seeder_command(db_session):
    _seed_client(db_session)
    _seed_user(db_session)
    _seed_user(db_session, user_id=2, name="Devops Two")
    row = create_seeder_command(db_session, client_id=1, title="t", command="c", created_by=1)
    db_session.commit()

    updated = update_seeder_command(db_session, row, title="t2", command="c2", updated_by=2)
    db_session.commit()

    assert updated.title == "t2"
    assert updated.command == "c2"
    assert updated.updated_by == 2
    assert updated.created_by == 1  # unchanged


def test_delete_seeder_command(db_session):
    _seed_client(db_session)
    _seed_user(db_session)
    row = create_seeder_command(db_session, client_id=1, title="t", command="c", created_by=1)
    db_session.commit()

    delete_seeder_command(db_session, row)
    db_session.commit()

    assert db_session.query(SeederCommand).count() == 0


def test_seeder_collection_rows_ordered_by_client_name(db_session):
    _seed_client(db_session, client_id=1, name="Zebra Corp")
    _seed_client(db_session, client_id=2, name="Acme")
    _seed_user(db_session)
    create_seeder_command(db_session, client_id=1, title="t", command="c", created_by=1)
    create_seeder_command(db_session, client_id=2, title="t", command="c", created_by=1)
    db_session.commit()

    rows = seeder_collection_rows(db_session)
    assert [r.client.name for r in rows] == ["Acme", "Zebra Corp"]


def test_clients_without_seeder_command_excludes_clients_that_already_have_one(db_session):
    _seed_client(db_session, client_id=1, name="CRM")
    _seed_client(db_session, client_id=2, name="Acme")
    _seed_user(db_session)
    create_seeder_command(db_session, client_id=1, title="t", command="c", created_by=1)
    db_session.commit()

    clients = clients_without_seeder_command(db_session)
    assert [c.name for c in clients] == ["Acme"]


def _seed_url(db_session, *, client_id=1, environment=DeploymentEnvironment.test, url="http://crm-test.local", label=None):
    db_session.add(ClientSystemUrl(client_id=client_id, environment=environment, url=url, label=label))
    db_session.flush()


def test_seeder_collection_rows_eager_loads_client_system_urls(db_session):
    """The listing renders every client's Test/Live URLs, so it must load them
    up front — one lazy load per card would be an N+1 across 55 clients."""
    _seed_user(db_session)
    for client_id, name in ((1, "Alpha"), (2, "Beta")):
        _seed_client(db_session, client_id=client_id, name=name)
        _seed_url(db_session, client_id=client_id, url=f"http://{name}-test.local")
        _seed_url(db_session, client_id=client_id, environment=DeploymentEnvironment.live, url=f"http://{name}-live.local")
        create_seeder_command(db_session, client_id=client_id, title="Seeder", command="php artisan x", created_by=1)
    db_session.commit()
    db_session.expire_all()

    rows = seeder_collection_rows(db_session)

    queries = []

    def record(conn, cursor, statement, *rest):
        queries.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", record)
    try:
        urls = [[u.url for u in row.client.system_urls] for row in rows]
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", record)

    assert urls == [
        ["http://Alpha-test.local", "http://Alpha-live.local"],
        ["http://Beta-test.local", "http://Beta-live.local"],
    ]
    assert queries == [], f"system_urls lazy-loaded after the fact: {queries}"


def test_client_system_urls_for_form_returns_every_url(db_session):
    """Feeds the add form's client picker, so selecting a client can show its
    URLs without a round trip."""
    _seed_client(db_session, client_id=1, name="Alpha")
    _seed_url(db_session, client_id=1, url="http://alpha-test.local")
    _seed_url(db_session, client_id=1, environment=DeploymentEnvironment.live, url="http://alpha-live.local", label="Line 2")
    db_session.commit()

    urls = client_system_urls_for_form(db_session)

    assert [(u.client_id, u.environment, u.url, u.label) for u in urls] == [
        (1, DeploymentEnvironment.test, "http://alpha-test.local", None),
        (1, DeploymentEnvironment.live, "http://alpha-live.local", "Line 2"),
    ]


def test_create_seeder_command_does_not_take_a_host(db_session):
    """Host is gone — the client's own URLs are the single source of truth."""
    _seed_client(db_session)
    _seed_user(db_session)

    with pytest.raises(TypeError):
        create_seeder_command(
            db_session, client_id=1, host="10.0.0.1", title="Seeder", command="php artisan x", created_by=1
        )
