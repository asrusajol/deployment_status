"""Which checklist terms a Start needs, and recording that they were confirmed.

start_request() (app/routers/dashboard.py) calls record_start_confirmations() before it
adds the DeploymentExecution row and commits once, so a start and its confirmations are
saved together or not at all.
"""

import hashlib
from datetime import datetime

from sqlalchemy.orm import Session, contains_eager

from app.models.checklist import ChecklistConfirmation, ChecklistItem, ChecklistItemType
from app.models.deployment_request import DeploymentRequest, RequestType
from app.models.user import User


class ChecklistIncomplete(Exception):
    def __init__(self, missing: list[ChecklistItem]):
        super().__init__("checklist incomplete")
        self.missing = missing


def checklist_token(item: ChecklistItem) -> str:
    """The value a Start pop-up checkbox posts: the term id plus a fingerprint of the
    wording shown. A term reworded while the pop-up was open no longer matches, so the
    confirmation snapshot can only ever record wording the deployer actually saw."""
    digest = hashlib.sha256(item.label.encode("utf-8")).hexdigest()[:16]
    return f"{item.id}:{digest}"


def active_items_by_type(db: Session) -> dict[RequestType, list[ChecklistItem]]:
    """Active terms grouped by every request type they're assigned to, in each type's own
    order. One query for the whole request listing — never per row."""
    assignments = (
        db.query(ChecklistItemType)
        .join(ChecklistItemType.item)
        .filter(ChecklistItem.is_active.is_(True))
        .options(contains_eager(ChecklistItemType.item))
        .order_by(ChecklistItemType.position, ChecklistItemType.item_id)
        .all()
    )
    grouped: dict[RequestType, list[ChecklistItem]] = {}
    for assignment in assignments:
        grouped.setdefault(assignment.request_type, []).append(assignment.item)
    return grouped


def record_start_confirmations(
    db: Session, deployment_request: DeploymentRequest, submitted_ids: list[str], user: User, now: datetime
) -> None:
    """Adds one confirmation per required term, or raises ChecklistIncomplete.

    Required is re-read here, not trusted from the page: a term added or reworded while
    the pop-up was open must block the start (see checklist_token). Submitted values that
    aren't required (a term retired meanwhile, junk) are ignored. Does not commit — the
    caller's commit covers this and the execution row together.
    """
    required = active_items_by_type(db).get(deployment_request.request_type, [])
    submitted = set(submitted_ids)
    missing = [item for item in required if checklist_token(item) not in submitted]
    if missing:
        raise ChecklistIncomplete(missing)
    for item in required:
        db.add(ChecklistConfirmation(
            request_id=deployment_request.id, checklist_item_id=item.id, item_label=item.label,
            confirmed_by=user.id, confirmed_at=now,
        ))
