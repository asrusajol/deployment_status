from datetime import datetime, timedelta

from app.models.checklist import ChecklistConfirmation, ChecklistItemType
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from tests.conftest import make_checklist_item, make_user
from tests.test_dashboard import db_session  # noqa: F401 — reuse the in-memory session fixture


def test_item_defaults_to_active(db_session):
    item = make_checklist_item(db_session, label="Close cronjobs")
    assert item.is_active is True


def test_one_term_can_be_assigned_to_several_types(db_session):
    item = make_checklist_item(
        db_session, label="Close cronjobs", request_types=(RequestType.db_dump_restore, RequestType.test_local)
    )
    db_session.refresh(item)
    assert sorted(t.request_type.value for t in item.types) == ["db_dump_restore", "test_local"]


def test_unassigning_a_type_removes_only_that_link(db_session):
    item = make_checklist_item(
        db_session, label="Close cronjobs", request_types=(RequestType.db_dump_restore, RequestType.test_local)
    )
    item.types = [t for t in item.types if t.request_type != RequestType.test_local]
    db_session.commit()

    assert db_session.query(ChecklistItemType).count() == 1
    assert db_session.get(type(item), item.id) is not None


def test_request_lists_confirmations_newest_first(db_session):
    make_user(db_session, id=1, name="Zunayed")
    request = DeploymentRequest(request_type=RequestType.db_dump_restore, status=RequestStatus.approved,
                                created_at=datetime(2026, 9, 1))
    db_session.add(request)
    item = make_checklist_item(db_session, label="Close cronjobs")
    older = datetime(2026, 9, 1, 10, 0)
    # One tick per term per attempt (unique key), so the two ticks are two attempts.
    for round_, at in enumerate((older, older + timedelta(hours=1))):
        db_session.add(ChecklistConfirmation(request_id=request.id, checklist_item_id=item.id, round=round_,
                                             item_label=item.label, confirmed_by=1, confirmed_at=at))
    db_session.commit()
    db_session.refresh(request)

    times = [c.confirmed_at for c in request.checklist_confirmations]
    assert times == sorted(times, reverse=True)
    assert request.checklist_confirmations[0].confirmer.name == "Zunayed"


def test_one_tick_per_term_per_attempt(db_session):
    import pytest
    from sqlalchemy.exc import IntegrityError

    make_user(db_session, id=1, name="Zunayed")
    request = DeploymentRequest(request_type=RequestType.db_dump_restore, status=RequestStatus.approved,
                                created_at=datetime(2026, 9, 1))
    db_session.add(request)
    item = make_checklist_item(db_session, label="Close cronjobs")
    for _ in range(2):
        db_session.add(ChecklistConfirmation(request_id=request.id, checklist_item_id=item.id, round=0,
                                             item_label=item.label, confirmed_by=1, confirmed_at=datetime(2026, 9, 1)))
    with pytest.raises(IntegrityError):
        db_session.commit()
