"""Which checklist terms a Start needs, and recording that they were confirmed.

start_request() (app/routers/dashboard.py) calls record_start_confirmations() before it
adds the DeploymentExecution row and commits once, so a start and its confirmations are
saved together or not at all.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.checklist import ChecklistConfirmation, ChecklistItem
from app.models.deployment_request import DeploymentRequest, RequestType
from app.models.user import User


class ChecklistIncomplete(Exception):
    def __init__(self, missing: list[ChecklistItem]):
        super().__init__("checklist incomplete")
        self.missing = missing


def active_items_by_type(db: Session) -> dict[RequestType, list[ChecklistItem]]:
    """Active terms grouped by request type, in display order. One query for the whole
    request listing — never per row."""
    items = (
        db.query(ChecklistItem)
        .filter(ChecklistItem.is_active.is_(True))
        .order_by(ChecklistItem.position, ChecklistItem.id)
        .all()
    )
    grouped: dict[RequestType, list[ChecklistItem]] = {}
    for item in items:
        grouped.setdefault(item.request_type, []).append(item)
    return grouped


def record_start_confirmations(
    db: Session, deployment_request: DeploymentRequest, submitted_ids: list[str], user: User, now: datetime
) -> None:
    """Adds one confirmation per required term, or raises ChecklistIncomplete.

    Required is re-read here, not trusted from the page: a term added while the pop-up
    was open must block the start. Submitted ids that aren't required (a term retired
    meanwhile, junk) are ignored. Does not commit — the caller's commit covers this and
    the execution row together.
    """
    required = active_items_by_type(db).get(deployment_request.request_type, [])
    submitted = set(submitted_ids)
    missing = [item for item in required if str(item.id) not in submitted]
    if missing:
        raise ChecklistIncomplete(missing)
    for item in required:
        db.add(ChecklistConfirmation(
            request_id=deployment_request.id, checklist_item_id=item.id, item_label=item.label,
            confirmed_by=user.id, confirmed_at=now,
        ))
