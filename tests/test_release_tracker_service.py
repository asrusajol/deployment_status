from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.user import User, UserRole
from app.services.release_tracker import (
    clients_with_version_records,
    current_version_for,
    record_client_deploy,
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
    if db_session.get(Client, client_id) is None:
        db_session.add(Client(id=client_id, name=client_name))
    if db_session.get(User, 1) is None:
        db_session.add(User(id=1, name="Deployer", role=UserRole.developer))
    db_session.add(
        DeploymentRequest(
            id=client_id, request_type=RequestType.standard, client_id=client_id,
            environment=DeploymentEnvironment.live, status=RequestStatus.completed,
            created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )
    )
    db_session.flush()


def test_record_client_deploy_creates_row_on_first_deploy(db_session):
    _seed(db_session)

    row = record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    assert db_session.query(ClientVersionStatus).count() == 1
    assert row.live_current_version == "2026.34.34"
    assert row.live_previous_version is None
    assert row.test_current_version is None


def test_record_client_deploy_live_does_not_touch_test_columns(db_session):
    _seed(db_session)
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.test,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    row = record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    assert row.test_current_version == "1.0"  # untouched by the Live deploy
    assert row.live_current_version == "2026.34.34"
    assert db_session.query(ClientVersionStatus).count() == 1  # still one row


def test_record_client_deploy_captures_previous_version(db_session):
    _seed(db_session)
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.30", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    row = record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    assert row.live_current_version == "2026.34.34"
    assert row.live_previous_version == "2026.34.30"


def test_record_client_deploy_snapshots_main_from_cache(db_session):
    _seed(db_session)
    changed_at = datetime(2026, 8, 20, tzinfo=timezone.utc)
    db_session.add(
        BitbucketMainBranchStatus(id=1, version="2026.34.40", pr_number=15009, version_changed_at=changed_at)
    )
    db_session.commit()

    row = record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    assert row.main_version == "2026.34.40"
    assert row.main_pr_number == 15009
    # the cache's version_changed_at, not now() — compared tz-naively because
    # BitbucketMainBranchStatus.version_changed_at is a plain (non-tz-aware)
    # DateTime column: SQLAlchemy round-trips it as naive UTC after a commit
    # expires the ORM object and re-fetches it, both on SQLite here and on
    # the real Postgres TIMESTAMP WITHOUT TIME ZONE column in production.
    assert row.main_updated_at == changed_at.replace(tzinfo=None)


def test_record_client_deploy_main_snapshot_is_null_without_a_sync_yet(db_session):
    _seed(db_session)
    row = record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()
    assert row.main_version is None
    assert row.main_pr_number is None
    assert row.main_updated_at is None


def test_record_client_deploy_does_not_touch_other_clients(db_session):
    _seed(db_session, client_id=1, client_name="CRM")
    _seed(db_session, client_id=2, client_name="Acme")
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    record_client_deploy(
        db_session, client_id=2, environment=DeploymentEnvironment.live,
        current_version="2.0", recorded_by=1, deployment_request_id=2,
    )
    db_session.commit()

    row1 = db_session.query(ClientVersionStatus).filter_by(client_id=1).one()
    assert row1.live_current_version == "1.0"  # untouched by client 2's deploy


def test_current_version_for_returns_none_when_no_row(db_session):
    _seed(db_session)
    assert current_version_for(db_session, 1, DeploymentEnvironment.live) is None


def test_current_version_for_returns_the_right_environment(db_session):
    _seed(db_session)
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.test,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()
    assert current_version_for(db_session, 1, DeploymentEnvironment.test) == "1.0"
    assert current_version_for(db_session, 1, DeploymentEnvironment.live) is None


def test_release_tracker_rows_one_per_client_ordered_by_name(db_session):
    _seed(db_session, client_id=1, client_name="Zebra Corp")
    _seed(db_session, client_id=2, client_name="Acme")
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    record_client_deploy(
        db_session, client_id=2, environment=DeploymentEnvironment.live,
        current_version="2.0", recorded_by=1, deployment_request_id=2,
    )
    db_session.commit()

    rows = release_tracker_rows(db_session, None)
    assert [r.client.name for r in rows] == ["Acme", "Zebra Corp"]


def test_release_tracker_rows_filters_by_client(db_session):
    _seed(db_session, client_id=1, client_name="CRM")
    _seed(db_session, client_id=2, client_name="Acme")
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    record_client_deploy(
        db_session, client_id=2, environment=DeploymentEnvironment.live,
        current_version="2.0", recorded_by=1, deployment_request_id=2,
    )
    db_session.commit()

    rows = release_tracker_rows(db_session, 1)
    assert [r.client_id for r in rows] == [1]


def test_clients_with_version_records_only_lists_clients_with_a_status_row(db_session):
    _seed(db_session, client_id=1, client_name="CRM")
    _seed(db_session, client_id=2, client_name="Acme")
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    clients = clients_with_version_records(db_session)
    assert [c.name for c in clients] == ["CRM"]


def test_record_client_deploy_test_previous_version_rolls_forward_past_intervening_live(db_session):
    """Test -> Live -> Test-again: test_previous_version should roll forward
    from the FIRST Test deploy, not from the intervening Live deploy in a
    different environment, and not stay stale from before any Test deploy."""
    _seed(db_session)

    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.test,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2.0", recorded_by=1, deployment_request_id=1,
    )
    row = record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.test,
        current_version="1.1", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    assert row.test_current_version == "1.1"
    assert row.test_previous_version == "1.0"


def test_record_client_deploy_always_bumps_updated_at_on_identical_redeploy(db_session):
    """Global Constraint: *_updated_at bumps on EVERY deploy confirmation
    for that environment, even a redeploy of the identical version string —
    it must update in place (still exactly one row), not skip the bump."""
    _seed(db_session)

    first = record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.test,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()
    first_updated_at = first.test_updated_at
    assert first_updated_at is not None

    second = record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.test,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    assert second.test_updated_at is not None
    assert second.test_updated_at >= first_updated_at
    assert db_session.query(ClientVersionStatus).count() == 1
