"""Management tab: a hub for management tools, gated by require_management.

Users (/admin/users) stays admin-only and keeps its URL; the hub only links to it.
See docs/superpowers/specs/2026-09-30-request-checklists-design.md.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from app.auth import require_management
from app.models.user import User, UserRole
from app.static_version import STATIC_VERSION

router = APIRouter(prefix="/management")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION


@router.get("")
def management_hub(request: Request, current_user: User = Depends(require_management)):
    return templates.TemplateResponse(
        request,
        "management.html",
        {"current_user": current_user, "is_admin": current_user.role == UserRole.admin},
    )
