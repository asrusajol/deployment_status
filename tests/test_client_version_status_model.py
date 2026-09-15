# tests/test_client_version_status_model.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models on Base.metadata
from app.database import Base
from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.user import User, UserRole


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_client_version_status_round_trips(db_session):
    client = Client(id=1, name="CRM")
    user = User(id=1, name="Deployer", role=UserRole.developer)
    request = DeploymentRequest(
        id=1, request_type=RequestType.standard, client_id=1,
        environment=DeploymentEnvironment.live, status=RequestStatus.completed,
        created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    db_session.add_all([client, user, request])
    db_session.flush()

    row = ClientVersionStatus(
        client_id=1,
        test_current_version="2026.34.30", test_previous_version="2026.34.20",
        test_updated_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        test_recorded_by=1, test_deployment_request_id=1,
        live_current_version="2026.34.34", live_previous_version="2026.34.30",
        live_updated_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        live_recorded_by=1, live_deployment_request_id=1,
    )
    db_session.add(row)
    db_session.commit()

    fetched = db_session.get(ClientVersionStatus, row.id)
    assert fetched.client.name == "CRM"
    assert fetched.test_current_version == "2026.34.30"
    assert fetched.live_current_version == "2026.34.34"
    assert fetched.test_recorder.name == "Deployer"
    assert fetched.live_deployment_request.id == 1


def test_client_version_status_client_id_is_unique(db_session):
    db_session.add(Client(id=1, name="CRM"))
    db_session.flush()
    db_session.add(ClientVersionStatus(client_id=1))
    db_session.commit()
    db_session.add(ClientVersionStatus(client_id=1))
    with pytest.raises(Exception):  # IntegrityError, dialect-specific
        db_session.commit()
