"""Management tab: a hub for management tools, gated by require_management.

Users (/admin/users) stays admin-only and keeps its URL; the hub only links to it.
Checklists: anyone with Management access can view and add terms; editing, reordering
and retiring are admin-only (require_admin). See
docs/superpowers/specs/2026-09-30-request-checklists-design.md.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import require_admin, require_management
from app.database import get_db
from app.models.checklist import LABEL_MAX_LENGTH, ChecklistItem
from app.models.deployment_request import RequestType
from app.models.user import User, UserRole
from app.routers.dashboard import REQUEST_TYPE_LABELS
from app.static_version import STATIC_VERSION

router = APIRouter(prefix="/management")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION

CHECKLISTS_URL = "/management/checklists"


@router.get("")
def management_hub(request: Request, current_user: User = Depends(require_management)):
    return templates.TemplateResponse(
        request,
        "management.html",
        {"current_user": current_user, "is_admin": current_user.role == UserRole.admin},
    )


def _clean_label(label: str) -> str:
    label = label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="The term can't be blank.")
    if len(label) > LABEL_MAX_LENGTH:
        raise HTTPException(status_code=400, detail=f"Keep the term under {LABEL_MAX_LENGTH} characters.")
    return label


def _get_item_or_404(db: Session, item_id: int) -> ChecklistItem:
    item = db.get(ChecklistItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Checklist term not found")
    return item


def _touch(item: ChecklistItem, user: User) -> None:
    item.updated_by = user.id
    item.updated_at = datetime.now(timezone.utc)


@router.get("/checklists")
def list_checklists(request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_management)):
    items = db.query(ChecklistItem).order_by(ChecklistItem.position, ChecklistItem.id).all()
    # Grouped in Python, in RequestType declaration order: Postgres sorts an enum column
    # by declaration and SQLite by string, so ORDER BY request_type would differ by DB.
    sections = [
        (request_type, [i for i in items if i.request_type == request_type and i.is_active])
        for request_type in RequestType
    ]
    # Retired terms are folded away below the list rather than mixed into it — they no
    # longer gate anything, and interleaved they read as live steps.
    retired = [i for request_type in RequestType for i in items if i.request_type == request_type and not i.is_active]
    return templates.TemplateResponse(
        request,
        "management_checklists.html",
        {
            "current_user": current_user,
            "sections": sections,
            "retired": retired,
            "type_labels": REQUEST_TYPE_LABELS,
            "is_admin": current_user.role == UserRole.admin,
        },
    )


@router.post("/checklists")
def add_checklist_item(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_management),
    request_type: str = Form(...),
    label: str = Form(""),
):
    try:
        parsed_type = RequestType(request_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unknown request type")
    label = _clean_label(label)
    last = db.query(func.max(ChecklistItem.position)).filter(ChecklistItem.request_type == parsed_type).scalar()
    db.add(ChecklistItem(
        request_type=parsed_type, label=label, position=(last or 0) + 1, is_active=True,
        created_by=current_user.id, created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    return RedirectResponse(url=CHECKLISTS_URL, status_code=303)


def _same_wording(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


@router.post("/checklists/{item_id}/edit")
def edit_checklist_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    label: str = Form(""),
    # Optional so a wording-only edit keeps the term's type. Reassigning is how a term
    # is reused on another type — still one type per term (decision D2).
    request_type: str | None = Form(None),
):
    # Safe to reword or reassign: ChecklistConfirmation snapshots the label, and each
    # confirmation's request carries its own type, so history is unaffected either way.
    item = _get_item_or_404(db, item_id)
    new_label = _clean_label(label)
    new_type = item.request_type
    if request_type:
        try:
            new_type = RequestType(request_type)
        except ValueError:
            raise HTTPException(status_code=400, detail="Unknown request type")

    # "Assign only if not assigned before": an active term with the same wording on the
    # target type would make DevOps tick the same step twice. Retired ones don't count.
    others = db.query(ChecklistItem).filter(
        ChecklistItem.request_type == new_type, ChecklistItem.is_active.is_(True), ChecklistItem.id != item.id
    )
    if any(_same_wording(other.label, new_label) for other in others):
        raise HTTPException(status_code=400, detail="That term is already assigned to this request type.")

    if new_type != item.request_type:
        last = db.query(func.max(ChecklistItem.position)).filter(ChecklistItem.request_type == new_type).scalar()
        item.request_type = new_type
        item.position = (last or 0) + 1
    item.label = new_label
    _touch(item, current_user)
    db.commit()
    return RedirectResponse(url=CHECKLISTS_URL, status_code=303)


@router.post("/checklists/{item_id}/move")
def move_checklist_item(
    item_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin), direction: str = Form(...),
):
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be up or down")
    item = _get_item_or_404(db, item_id)
    same_type = db.query(ChecklistItem).filter(ChecklistItem.request_type == item.request_type, ChecklistItem.id != item.id)
    if direction == "up":
        neighbour = same_type.filter(ChecklistItem.position < item.position).order_by(ChecklistItem.position.desc()).first()
    else:
        neighbour = same_type.filter(ChecklistItem.position > item.position).order_by(ChecklistItem.position.asc()).first()
    if neighbour is not None:
        item.position, neighbour.position = neighbour.position, item.position
        _touch(item, current_user)
        db.commit()
    return RedirectResponse(url=CHECKLISTS_URL, status_code=303)


def _set_active(db: Session, item_id: int, user: User, active: bool) -> RedirectResponse:
    item = _get_item_or_404(db, item_id)
    item.is_active = active
    _touch(item, user)
    db.commit()
    return RedirectResponse(url=CHECKLISTS_URL, status_code=303)


@router.post("/checklists/{item_id}/deactivate")
def deactivate_checklist_item(item_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    # Retire, never delete: confirmation rows reference this term.
    return _set_active(db, item_id, current_user, False)


@router.post("/checklists/{item_id}/activate")
def activate_checklist_item(item_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    return _set_active(db, item_id, current_user, True)
