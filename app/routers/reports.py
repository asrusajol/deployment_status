"""The Reports tab.

A landing page over a registry of report types, so adding a report later means one
REPORTS entry plus its own router module — not another top-level nav item.
"""

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from app.auth import require_reports_access
from app.models.user import User
from app.static_version import STATIC_VERSION

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION


@dataclass(frozen=True)
class ReportLink:
    slug: str
    title: str
    description: str


REPORTS = [
    ReportLink(
        slug="order-task-dependency",
        title="Order Task Dependency",
        description="Per order, which tasks are blocked by an unfinished predecessor and which are overdue.",
    ),
]


@router.get("/reports")
def reports_index(request: Request, current_user: User = Depends(require_reports_access)):
    return templates.TemplateResponse(
        request, "reports_index.html", {"current_user": current_user, "reports": REPORTS}
    )
