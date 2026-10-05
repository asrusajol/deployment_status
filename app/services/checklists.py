"""Checklist terms per request: which are due when, and saving ticks one at a time.

A term is due either before Start Deployment or before Mark Deployed (ChecklistItem.due).
DevOps tick terms as they do them — each tick is saved immediately by set_tick(), so a
closed pop-up or a page reload loses nothing. start_request() and deploy_request() in
app/routers/dashboard.py refuse while missing_items() for their stage is non-empty.
"""

from datetime import datetime

from sqlalchemy.orm import Session, contains_eager

from app.models.checklist import (
    DUE_BEFORE_COMPLETE,
    DUE_BEFORE_START,
    ChecklistConfirmation,
    ChecklistItem,
    ChecklistItemType,
)
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from app.models.user import User

# When each stage's ticks may change. Once a stage has passed its ticks are a record:
# before-start ticks freeze at Start, before-complete ticks at Mark Deployed.
TICK_WINDOWS = {
    DUE_BEFORE_START: (RequestStatus.approved,),
    DUE_BEFORE_COMPLETE: (RequestStatus.in_progress,),
}


class ChecklistError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def checklist_applies(deployment_request: DeploymentRequest) -> bool:
    """False for a DB dump that is only handed back to the requester: nothing is restored
    anywhere, so there are no schedules, workers or email settings to neutralise."""
    return not (
        deployment_request.request_type == RequestType.db_dump_restore and deployment_request.share_with_requestor
    )


def current_round(deployment_request: DeploymentRequest) -> int:
    """Which attempt this is: 0 until the first Return, then one more per Return. Each
    attempt is ticked afresh; earlier attempts' ticks stay for the audit."""
    return len(deployment_request.returns)


def active_items_by_type(db: Session, due: str) -> dict[RequestType, list[ChecklistItem]]:
    """Active terms due at `due`, grouped by every request type they're assigned to, in
    each type's own order. One query for the whole request listing — never per row."""
    assignments = (
        db.query(ChecklistItemType)
        .join(ChecklistItemType.item)
        .filter(ChecklistItem.is_active.is_(True), ChecklistItem.due == due)
        .options(contains_eager(ChecklistItemType.item))
        .order_by(ChecklistItemType.position, ChecklistItemType.item_id)
        .all()
    )
    grouped: dict[RequestType, list[ChecklistItem]] = {}
    for assignment in assignments:
        grouped.setdefault(assignment.request_type, []).append(assignment.item)
    return grouped


def required_items(db: Session, deployment_request: DeploymentRequest, due: str) -> list[ChecklistItem]:
    if not checklist_applies(deployment_request):
        return []
    return active_items_by_type(db, due).get(deployment_request.request_type, [])


def counted_item_ids(deployment_request: DeploymentRequest) -> set[int]:
    """Terms ticked in the current round whose wording hasn't changed since — a tick on
    reworded wording doesn't count, so the audit only ever holds what was actually seen.
    Reads the loaded relationships, so the request listing can call it per row without a
    query when confirmations (and their items) are eager-loaded."""
    round_ = current_round(deployment_request)
    return {
        c.checklist_item_id
        for c in deployment_request.checklist_confirmations
        if c.round == round_ and c.item is not None and c.item_label == c.item.label
    }


def missing_items(db: Session, deployment_request: DeploymentRequest, due: str) -> list[ChecklistItem]:
    ticked = counted_item_ids(deployment_request)
    return [item for item in required_items(db, deployment_request, due) if item.id not in ticked]


def set_tick(
    db: Session, deployment_request: DeploymentRequest, item: ChecklistItem, user: User, checked: bool, now: datetime
) -> None:
    """Saves (or removes) one tick for the current round. Does not commit."""
    if item not in required_items(db, deployment_request, item.due):
        raise ChecklistError(400, "That term isn't on this request's checklist.")
    if deployment_request.status not in TICK_WINDOWS[item.due]:
        raise ChecklistError(409, "This part of the checklist can't be changed at this stage.")

    round_ = current_round(deployment_request)
    existing = (
        db.query(ChecklistConfirmation)
        .filter_by(request_id=deployment_request.id, checklist_item_id=item.id, round=round_)
        .one_or_none()
    )
    if checked:
        if existing is None:
            db.add(ChecklistConfirmation(
                request_id=deployment_request.id, checklist_item_id=item.id, item_label=item.label,
                round=round_, confirmed_by=user.id, confirmed_at=now,
            ))
        else:
            # Re-ticking after the term was reworded records the new wording and who saw it.
            existing.item_label = item.label
            existing.confirmed_by = user.id
            existing.confirmed_at = now
    elif existing is not None:
        db.delete(existing)
    db.flush()
    db.expire(deployment_request, ["checklist_confirmations"])
