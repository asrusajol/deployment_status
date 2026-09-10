"""The Order Task Dependency report page.

Reads live from the CRM on each request — there is no local table behind this report,
matching the source report's own behaviour (pick filters, view results). The HTML view
and both exports share _parse_report_filters()/_load_groups() so they can never disagree
about what is currently filtered.
"""

from datetime import date, datetime, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_reports_access
from app.config import Settings, get_settings
from app.database import get_db
from app.models.team import Team
from app.models.user import User
from app.services.export import order_task_dependency_rows_to_xlsx
from app.services.order_task_dependency import (
    InvalidFilters,
    ReportFilters,
    flatten,
    load_report,
)
from app.services.report_pdf import render_order_task_dependency_pdf
from app.services.task_source import InHouseTaskSourceProvider
from app.static_version import STATIC_VERSION

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION

CRM_FAILURE_MESSAGE = "The report could not be loaded from the CRM. Try again, or check that the CRM is reachable."


def build_provider(settings: Settings) -> InHouseTaskSourceProvider:
    """Seam for tests — patched out so no test ever needs a live CRM."""
    return InHouseTaskSourceProvider(settings)


def _machine_groups(db: Session) -> list[Team]:
    # The already-synced local mirror of the CRM's MachineGroups; no second CRM call
    # just to fill a dropdown.
    return list(db.scalars(select(Team).order_by(Team.name)))


def _parse_report_filters(
    start: str | None, end: str | None, machine_group_id: list[int], overdue_only: bool
) -> ReportFilters:
    return ReportFilters.parse(
        start=start or None,
        end=end or None,
        machine_group_ids=list(machine_group_id),
        overdue_only=overdue_only,
        today=date.today(),
    )


def _group_names(db: Session) -> dict[int, str]:
    return {team.id: team.name for team in _machine_groups(db)}


def _load_groups(db: Session, settings: Settings, filters: ReportFilters):
    return load_report(
        build_provider(settings),
        filters,
        today=date.today(),
        machine_group_names=_group_names(db),
    )


@router.get("/reports/order-task-dependency")
def order_task_dependency_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_reports_access),
    settings: Settings = Depends(get_settings),
    start: str | None = None,
    end: str | None = None,
    machine_group_id: list[int] = Query(default=[]),
    overdue_only: bool = False,
):
    groups: list = []
    error = None
    filters = None
    try:
        filters = _parse_report_filters(start, end, machine_group_id, overdue_only)
        groups = _load_groups(db, settings, filters)
    except InvalidFilters as exc:
        error = str(exc)
    except Exception:  # noqa: BLE001 — any CRM/transport failure degrades to a banner
        error = CRM_FAILURE_MESSAGE

    return templates.TemplateResponse(
        request,
        "reports/order_task_dependency.html",
        {
            "current_user": current_user,
            "groups": groups,
            "tasks": flatten(groups),
            "error": error,
            "filters": filters,
            "machine_groups": _machine_groups(db),
            "selected_machine_group_ids": list(machine_group_id),
            "start": start or "",
            "end": end or "",
            "overdue_only": overdue_only,
            "generated_at": datetime.now(timezone.utc),
        },
    )


@router.get("/reports/order-task-dependency/export.xlsx")
def order_task_dependency_export_xlsx(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_reports_access),
    settings: Settings = Depends(get_settings),
    start: str | None = None,
    end: str | None = None,
    machine_group_id: list[int] = Query(default=[]),
    overdue_only: bool = False,
):
    try:
        filters = _parse_report_filters(start, end, machine_group_id, overdue_only)
        groups = _load_groups(db, settings, filters)
    except InvalidFilters as exc:
        return PlainTextResponse(str(exc), status_code=400)
    except Exception:  # noqa: BLE001 — never hand back a corrupt workbook
        return PlainTextResponse(CRM_FAILURE_MESSAGE, status_code=502)

    content = order_task_dependency_rows_to_xlsx(flatten(groups), "Order Task Dependency")
    filename = f"order-task-dependency-{filters.start.isoformat()}-to-{filters.end.isoformat()}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/reports/order-task-dependency/export.pdf")
def order_task_dependency_export_pdf(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_reports_access),
    settings: Settings = Depends(get_settings),
    start: str | None = None,
    end: str | None = None,
    machine_group_id: list[int] = Query(default=[]),
    overdue_only: bool = False,
):
    try:
        filters = _parse_report_filters(start, end, machine_group_id, overdue_only)
        groups = _load_groups(db, settings, filters)
    except InvalidFilters as exc:
        return PlainTextResponse(str(exc), status_code=400)
    except Exception:  # noqa: BLE001
        return PlainTextResponse(CRM_FAILURE_MESSAGE, status_code=502)

    content = render_order_task_dependency_pdf(
        groups, summary=filters.describe(_group_names(db)), generated_at=datetime.now(timezone.utc)
    )
    filename = f"order-task-dependency-{filters.start.isoformat()}-to-{filters.end.isoformat()}.pdf"
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
