from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models on Base.metadata
from app.database import Base
from app.models.client import Client
from app.models.client_system_url import ClientSystemUrl
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_request import (
    DeploymentEnvironment,
    DeploymentRequest,
    RequestStatus,
    RequestType,
)
from app.services.client_merge import ClientMergeError, merge_clients

SYNCED, TYPED = 10, 20  # the CRM-synced client, and the hand-created duplicate


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add(Client(id=SYNCED, name="A.W. Schumacher GmbH", source_system_id="857"))
    session.add(Client(id=TYPED, name="A.W. Schumacher", source_system_id=None))
    session.commit()
    yield session
    session.close()


def _request(session, client_id, task_id="PR-1"):
    session.add(
        DeploymentRequest(
            request_type=RequestType.standard,
            task_id=task_id,
            client_id=client_id,
            environment=DeploymentEnvironment.test,
            git_branch="main",
            commit_hash="abc1234",
            version="V12",
            status=RequestStatus.pending_approval,
            created_at=datetime(2026, 1, 1),
        )
    )
    session.commit()


def test_requests_are_repointed_at_the_surviving_client(db_session):
    _request(db_session, TYPED, "PR-OLD")
    _request(db_session, SYNCED, "PR-NEW")

    plan = merge_clients(db_session, keep_id=SYNCED, remove_id=TYPED)
    db_session.commit()

    assert plan.requests_moved == 1
    assert db_session.query(DeploymentRequest).filter_by(client_id=SYNCED).count() == 2
    assert db_session.get(Client, TYPED) is None


def test_urls_move_across_and_exact_duplicates_are_dropped(db_session):
    db_session.add(ClientSystemUrl(client_id=SYNCED, environment=DeploymentEnvironment.test, url="http://a.local"))
    db_session.add(ClientSystemUrl(client_id=TYPED, environment=DeploymentEnvironment.test, url="http://a.local"))
    db_session.add(ClientSystemUrl(client_id=TYPED, environment=DeploymentEnvironment.live, url="http://b.local"))
    db_session.commit()

    plan = merge_clients(db_session, keep_id=SYNCED, remove_id=TYPED)
    db_session.commit()

    assert plan.urls_moved == 1
    assert plan.urls_dropped_as_duplicate == ["http://a.local"]
    urls = {u.url for u in db_session.query(ClientSystemUrl).filter_by(client_id=SYNCED)}
    assert urls == {"http://a.local", "http://b.local"}  # not listed twice


def test_version_status_moves_when_the_keeper_has_none(db_session):
    db_session.add(ClientVersionStatus(client_id=TYPED, test_current_version="1.0"))
    db_session.commit()

    plan = merge_clients(db_session, keep_id=SYNCED, remove_id=TYPED)
    db_session.commit()

    assert "moved" in plan.version_status
    row = db_session.query(ClientVersionStatus).one()
    assert row.client_id == SYNCED
    assert row.test_current_version == "1.0"


def test_version_status_duplicate_is_discarded_not_repointed(db_session):
    """client_version_status.client_id is UNIQUE — repointing a second row onto the
    keeper would violate it, so the duplicate's row is dropped instead."""
    db_session.add(ClientVersionStatus(client_id=SYNCED, test_current_version="keep-me"))
    db_session.add(ClientVersionStatus(client_id=TYPED, test_current_version="discard-me"))
    db_session.commit()

    plan = merge_clients(db_session, keep_id=SYNCED, remove_id=TYPED)
    db_session.commit()

    assert "discarded" in plan.version_status
    rows = db_session.query(ClientVersionStatus).all()
    assert len(rows) == 1
    assert rows[0].client_id == SYNCED
    assert rows[0].test_current_version == "keep-me"


def test_refuses_to_keep_the_client_without_a_source_system_id(db_session):
    with pytest.raises(ClientMergeError, match="source_system_id"):
        merge_clients(db_session, keep_id=TYPED, remove_id=SYNCED)

    # Nothing touched — both clients still there.
    assert db_session.get(Client, SYNCED) is not None
    assert db_session.get(Client, TYPED) is not None


def test_force_allows_keeping_the_unsynced_client(db_session):
    merge_clients(db_session, keep_id=TYPED, remove_id=SYNCED, force=True)
    db_session.commit()

    assert db_session.get(Client, SYNCED) is None
    assert db_session.get(Client, TYPED) is not None


def test_rename_gives_the_keeper_the_removed_clients_name(db_session):
    """clients.name is UNIQUE, so this is only possible once the duplicate is deleted."""
    plan = merge_clients(db_session, keep_id=SYNCED, remove_id=TYPED, rename_to_removed=True)
    db_session.commit()

    assert plan.renamed_to == "A.W. Schumacher"
    assert db_session.get(Client, SYNCED).name == "A.W. Schumacher"


def test_rejects_merging_a_client_into_itself(db_session):
    with pytest.raises(ClientMergeError, match="same client"):
        merge_clients(db_session, keep_id=SYNCED, remove_id=SYNCED)


def test_rejects_an_unknown_client_id(db_session):
    with pytest.raises(ClientMergeError, match="9999"):
        merge_clients(db_session, keep_id=SYNCED, remove_id=9999)


def test_describe_lists_what_would_change(db_session):
    _request(db_session, TYPED)

    plan = merge_clients(db_session, keep_id=SYNCED, remove_id=TYPED)

    text = "\n".join(plan.describe())
    assert "A.W. Schumacher" in text
    assert "deployment_requests repointed : 1" in text
