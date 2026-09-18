from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models on Base.metadata
from app.database import Base
from app.models.deployment_request import DeploymentRequest, RequestStatus
from app.models.request_return import RequestReturn
from app.models.user import User, UserRole


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _request(db_session):
    db_session.add(User(id=1, name="Rajib Ahamad", role=UserRole.devops))
    request = DeploymentRequest(
        task_id="PR-1", requested_by=1, status=RequestStatus.returned,
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    db_session.add(request)
    db_session.flush()
    return request


def test_a_return_round_trips(db_session):
    request = _request(db_session)
    db_session.add(
        RequestReturn(
            request_id=request.id, reason="branch client/foo was deleted", returned_by=1,
            returned_at=datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc),
            returned_from=RequestStatus.approved,
        )
    )
    db_session.commit()

    stored = db_session.query(RequestReturn).one()
    assert stored.reason == "branch client/foo was deleted"
    assert stored.returned_from == RequestStatus.approved
    assert stored.returner.name == "Rajib Ahamad"


def test_a_request_keeps_every_return_newest_first(db_session):
    """The whole point of a log: "returned three times" is a different problem
    from "returned once", and a team lead needs to see both."""
    request = _request(db_session)
    for day, reason in ((15, "branch deleted"), (16, "wrong commit"), (17, "migration error on live")):
        db_session.add(
            RequestReturn(
                request_id=request.id, reason=reason, returned_by=1,
                returned_at=datetime(2026, 9, day, tzinfo=timezone.utc),
                returned_from=RequestStatus.approved,
            )
        )
    db_session.commit()
    db_session.refresh(request)

    assert [r.reason for r in request.returns] == [
        "migration error on live", "wrong commit", "branch deleted",
    ]
    assert request.latest_return.reason == "migration error on live"


def test_latest_return_is_none_when_never_returned(db_session):
    request = _request(db_session)
    db_session.commit()

    assert request.latest_return is None
