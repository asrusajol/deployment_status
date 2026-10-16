"""Management: the checklist terms, gated by require_management.

Settings in the nav holds Users (/admin/users, admin-only) and this page. Anyone with
Management access can view and add terms; editing (wording, types, when, sort order)
and retiring are admin-only (require_admin). See
docs/superpowers/specs/2026-09-30-request-checklists-design.md.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.auth import require_admin, require_management
from app.database import get_db
from app.models.checklist import DUE_BEFORE_START, DUE_LABELS, LABEL_MAX_LENGTH, ChecklistItem, ChecklistItemType
from app.models.deployment_request import RequestType
from app.models.user import User, UserRole
from app.routers.dashboard import REQUEST_TYPE_LABELS
from app.static_version import STATIC_VERSION

router = APIRouter(prefix="/management")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION

CHECKLISTS_URL = "/management/checklists"
VIEWS = ("all", *(t.value for t in RequestType))


def _view(view: str | None) -> str:
    return view if view in VIEWS else "all"


def _back(view: str | None) -> RedirectResponse:
    """Back to the view the change was made from — redirecting to the bare page dropped
    every save, move and retire back to All."""
    view = _view(view)
    url = CHECKLISTS_URL if view == "all" else f"{CHECKLISTS_URL}?view={view}"
    return RedirectResponse(url=url, status_code=303)


@router.get("")
def management_hub(current_user: User = Depends(require_management)):
    """The old Management hub. Settings now opens in the nav (Users, Management
    Checklist), so the URL — still in bookmarks — goes to the first one this user
    can open."""
    url = "/admin/users" if current_user.role == UserRole.admin else CHECKLISTS_URL
    return RedirectResponse(url=url, status_code=303)


def _clean_label(label: str) -> str:
    label = label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="The term can't be blank.")
    if len(label) > LABEL_MAX_LENGTH:
        raise HTTPException(status_code=400, detail=f"Keep the term under {LABEL_MAX_LENGTH} characters.")
    return label


def _parse_types(values: list[str]) -> list[RequestType]:
    try:
        return list(dict.fromkeys(RequestType(v) for v in values))
    except ValueError:
        raise HTTPException(status_code=400, detail="Unknown request type")


def _parse_due(due: str | None, default: str) -> str:
    if due is None:
        return default
    if due not in DUE_LABELS:
        raise HTTPException(status_code=400, detail="Unknown 'when' for a checklist term")
    return due


def _get_item_or_404(db: Session, item_id: int) -> ChecklistItem:
    item = db.get(ChecklistItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Checklist term not found")
    return item


def _touch(item: ChecklistItem, user: User) -> None:
    item.updated_by = user.id
    item.updated_at = datetime.now(timezone.utc)


def _same_wording(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _refuse_duplicates(db: Session, label: str, request_types: list[RequestType], exclude_id: int | None) -> None:
    """"Assign only if not assigned before": another active term with the same wording on
    a target type would make DevOps tick the same step twice. Retired ones don't count."""
    if not request_types:
        return
    others = (
        db.query(ChecklistItemType)
        .join(ChecklistItemType.item)
        .filter(ChecklistItemType.request_type.in_(request_types), ChecklistItem.is_active.is_(True))
        .all()
    )
    for other in others:
        if other.item_id != exclude_id and _same_wording(other.item.label, label):
            raise HTTPException(
                status_code=400,
                detail=f"That term is already assigned to {REQUEST_TYPE_LABELS[other.request_type]}.",
            )


def _next_position(db: Session, request_type: RequestType) -> int:
    last = db.query(func.max(ChecklistItemType.position)).filter(ChecklistItemType.request_type == request_type).scalar()
    return (last or 0) + 1


@router.get("/checklists")
def list_checklists(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_management),
    view: str | None = None,
):
    items = db.query(ChecklistItem).options(selectinload(ChecklistItem.types)).order_by(ChecklistItem.id).all()
    active = [i for i in items if i.is_active]
    # A term appears under every type it's assigned to, in that type's own order.
    sections = [
        (
            request_type,
            sorted(
                ((i, t.position) for i in active for t in i.types if t.request_type == request_type),
                key=lambda pair: (pair[1], pair[0].id),
            ),
        )
        for request_type in RequestType
    ]
    return templates.TemplateResponse(
        request,
        "management_checklists.html",
        {
            "current_user": current_user,
            "sections": sections,
            # The All view: every active term once, assigned or not, with all its types.
            "all_items": active,
            # Retired terms are folded away below the list rather than mixed into it —
            # they no longer gate anything, and interleaved they read as live steps.
            "retired": [i for i in items if not i.is_active],
            "type_labels": REQUEST_TYPE_LABELS,
            "due_labels": DUE_LABELS,
            "is_admin": current_user.role == UserRole.admin,
            "view": _view(view),
        },
    )


@router.post("/checklists")
def add_checklist_item(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_management),
    label: str = Form(""),
    request_types: list[str] = Form([]),
    due: str | None = Form(None),
    view: str | None = Form(None),
):
    label = _clean_label(label)
    due = _parse_due(due, DUE_BEFORE_START)
    types = _parse_types(request_types)
    if not types:
        raise HTTPException(status_code=400, detail="Pick at least one request type.")
    _refuse_duplicates(db, label, types, exclude_id=None)
    item = ChecklistItem(
        label=label, due=due, is_active=True, created_by=current_user.id, created_at=datetime.now(timezone.utc),
    )
    item.types = [ChecklistItemType(request_type=t, position=_next_position(db, t)) for t in types]
    db.add(item)
    db.commit()
    return _back(view)


@router.post("/checklists/{item_id}/edit")
def edit_checklist_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    label: str = Form(""),
    request_types: list[str] = Form([]),
    # The edit form always sends this, so "no boxes ticked" means unassign everything;
    # without it (a wording-only POST) the assignments are left alone.
    types_submitted: str | None = Form(None),
    # Before start / Before Mark Deployed; left as it is when absent.
    due: str | None = Form(None),
    # The design's Sort Order, which here is the term's place in one type's list (order
    # is per type); set from that type's tab. Left alone when absent.
    position: str | None = Form(None),
    position_type: str | None = Form(None),
    view: str | None = Form(None),
):
    # Safe to reword or reassign: ChecklistConfirmation snapshots the label, and each
    # confirmation's request carries its own type, so history is unaffected either way.
    item = _get_item_or_404(db, item_id)
    new_label = _clean_label(label)
    current = [t.request_type for t in item.types]
    wanted = _parse_types(request_types) if types_submitted else current
    _refuse_duplicates(db, new_label, wanted, exclude_id=item.id)

    kept = [t for t in item.types if t.request_type in wanted]
    added = [ChecklistItemType(request_type=t, position=_next_position(db, t)) for t in wanted if t not in current]
    item.types = kept + added
    item.label = new_label
    item.due = _parse_due(due, item.due)
    if position not in (None, ""):
        _place(db, item, position, position_type)
    _touch(item, current_user)
    db.commit()
    return _back(view)


def _place(db: Session, item: ChecklistItem, position: str, position_type: str | None) -> None:
    """Puts the term at `position` (1 = first; past the end = last) in one type's list,
    renumbering that type 1..n. Only that type moves — the others keep their order."""
    if not position.strip().isdigit() or int(position) < 1:
        raise HTTPException(status_code=400, detail="Sort order must be a whole number from 1.")
    (request_type,) = _parse_types([position_type or ""])
    link = next((t for t in item.types if t.request_type == request_type), None)
    if link is None:
        raise HTTPException(status_code=400, detail="That term isn't assigned to this request type.")
    db.flush()
    others = (
        db.query(ChecklistItemType)
        .join(ChecklistItemType.item)
        .filter(ChecklistItemType.request_type == request_type, ChecklistItemType.item_id != item.id)
        .order_by(ChecklistItemType.position, ChecklistItemType.item_id)
        .all()
    )
    # The number is the one the tab shows — counted over active terms only, so retired
    # ones (still holding positions) go after them rather than shifting the count.
    active = [t for t in others if t.item.is_active]
    active.insert(min(int(position), len(active) + 1) - 1, link)
    for number, row in enumerate(active + [t for t in others if not t.item.is_active], start=1):
        row.position = number


def _set_active(db: Session, item_id: int, user: User, active: bool, view: str | None) -> RedirectResponse:
    item = _get_item_or_404(db, item_id)
    item.is_active = active
    _touch(item, user)
    db.commit()
    return _back(view)


@router.post("/checklists/{item_id}/deactivate")
def deactivate_checklist_item(
    item_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin),
    view: str | None = Form(None),
):
    # Retire, never delete: confirmation rows reference this term.
    return _set_active(db, item_id, current_user, False, view)


@router.post("/checklists/{item_id}/activate")
def activate_checklist_item(
    item_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin),
    view: str | None = Form(None),
):
    return _set_active(db, item_id, current_user, True, view)
