from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.user import User, UserRole
from app.services.release_tracker import (
    clients_with_version_records,
    latest_current_version,
    release_tracker_rows,
)


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed(db_session, *, client_id=1, client_name="CRM"):
    db_session.add(Client(id=client_id, name=client_name))
    # _seed() is called once per client in some tests, so guard against a duplicate
    # user id=1 insert.
    if not db_session.query(User).filter(User.id == 1).first():
        db_session.add(User(id=1, name="Deployer", role=UserRole.developer))
    db_session.add(
        DeploymentRequest(
            id=client_id,
            request_type=RequestType.standard,
            client_id=client_id,
            environment=DeploymentEnvironment.live,
            status=RequestStatus.completed,
            created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )
    )
    db_session.flush()


def _add_record(db_session, *, client_id, environment, current_version, created_at):
    record = ClientVersionRecord(
        client_id=client_id,
        environment=environment,
        current_version=current_version,
        deployment_request_id=client_id,
        recorded_by=1,
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(record)
    db_session.commit()
    return record


def test_latest_current_version_returns_none_when_no_history(db_session):
    _seed(db_session)
    assert latest_current_version(db_session, 1, DeploymentEnvironment.live) is None


def test_latest_current_version_returns_most_recent(db_session):
    _seed(db_session)
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.30", created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    assert latest_current_version(db_session, 1, DeploymentEnvironment.live) == "2026.34.34"


def test_latest_current_version_scoped_by_environment(db_session):
    _seed(db_session)
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.test,
        current_version="2026.34.10", created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    assert latest_current_version(db_session, 1, DeploymentEnvironment.live) is None


def test_release_tracker_rows_newest_first(db_session):
    _seed(db_session)
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.30", created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    rows = release_tracker_rows(db_session, None, None)
    assert [r.current_version for r in rows] == ["2026.34.34", "2026.34.30"]


def test_release_tracker_rows_filters_by_client_and_environment(db_session):
    _seed(db_session, client_id=1, client_name="CRM")
    _seed(db_session, client_id=2, client_name="Acme")
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    _add_record(
        db_session, client_id=2, environment=DeploymentEnvironment.test,
        current_version="9.9.9", created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    rows = release_tracker_rows(db_session, 1, None)
    assert [r.client_id for r in rows] == [1]

    rows = release_tracker_rows(db_session, None, DeploymentEnvironment.test)
    assert [r.client_id for r in rows] == [2]


def test_clients_with_version_records_only_lists_clients_with_history(db_session):
    _seed(db_session, client_id=1, client_name="CRM")
    _seed(db_session, client_id=2, client_name="Acme")
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    clients = clients_with_version_records(db_session)
    assert [c.name for c in clients] == ["CRM"]
