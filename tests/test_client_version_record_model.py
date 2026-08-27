from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models on Base.metadata
from app.database import Base
from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.user import User, UserRole


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_client_version_record_round_trips(db_session):
    client = Client(id=1, name="CRM")
    user = User(id=1, name="Deployer", role=UserRole.developer)
    request = DeploymentRequest(
        id=1,
        request_type=RequestType.standard,
        client_id=1,
        environment=DeploymentEnvironment.live,
        status=RequestStatus.completed,
        created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    db_session.add_all([client, user, request])
    db_session.flush()

    record = ClientVersionRecord(
        client_id=1,
        environment=DeploymentEnvironment.live,
        current_version="2026.34.34",
        previous_version="2026.34.30",
        main_version="2026.34.40",
        main_pr_number=1234,
        deployment_request_id=1,
        recorded_by=1,
        created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    db_session.add(record)
    db_session.commit()

    fetched = db_session.get(ClientVersionRecord, record.id)
    assert fetched.current_version == "2026.34.34"
    assert fetched.previous_version == "2026.34.30"
    assert fetched.main_version == "2026.34.40"
    assert fetched.main_pr_number == 1234
    assert fetched.client.name == "CRM"
    assert fetched.recorder.name == "Deployer"
    assert fetched.deployment_request.id == 1


def test_bitbucket_main_branch_status_round_trips(db_session):
    status = BitbucketMainBranchStatus(id=1, version="2026.34.40", pr_number=1234)
    db_session.add(status)
    db_session.commit()

    fetched = db_session.get(BitbucketMainBranchStatus, 1)
    assert fetched.version == "2026.34.40"
    assert fetched.pr_number == 1234
    assert fetched.last_synced_at is None
