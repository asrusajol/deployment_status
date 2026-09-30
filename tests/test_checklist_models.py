from datetime import datetime, timedelta, timezone

from app.models.checklist import ChecklistConfirmation, ChecklistItem
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from tests.conftest import make_user
from tests.test_dashboard import db_session  # noqa: F401 — reuse the in-memory session fixture


def test_item_defaults_to_active(db_session):
    item = ChecklistItem(request_type=RequestType.db_dump_restore, label="Close cronjobs", position=1,
                         created_at=datetime.now(timezone.utc))
    db_session.add(item)
    db_session.commit()
    assert item.is_active is True


def test_request_lists_confirmations_newest_first(db_session):
    make_user(db_session, id=1, name="Zunayed")
    request = DeploymentRequest(request_type=RequestType.db_dump_restore, status=RequestStatus.approved,
                                created_at=datetime.now(timezone.utc))
    item = ChecklistItem(request_type=RequestType.db_dump_restore, label="Close cronjobs", position=1,
                         created_at=datetime.now(timezone.utc))
    db_session.add_all([request, item])
    db_session.flush()
    older = datetime(2026, 9, 1, 10, 0)
    for at in (older, older + timedelta(hours=1)):
        db_session.add(ChecklistConfirmation(request_id=request.id, checklist_item_id=item.id,
                                             item_label=item.label, confirmed_by=1, confirmed_at=at))
    db_session.commit()
    db_session.refresh(request)

    times = [c.confirmed_at for c in request.checklist_confirmations]
    assert times == sorted(times, reverse=True)
    assert request.checklist_confirmations[0].confirmer.name == "Zunayed"
