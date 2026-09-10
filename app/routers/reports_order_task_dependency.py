"""The Order Task Dependency report page.

Reads live from the CRM on each request — there is no local table behind this report,
matching the source report's own behaviour (pick filters, view results). The HTML view
and both exports share _parse_report_filters()/_load_groups() so they can never disagree
about what is currently filtered.
"""

from datetime import date, datetime, timezone
from io import BytesIO

import httpx
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
    InvalidCrmTimestamp,
    InvalidFilters,
    ReportFilters,
    flatten,
    load_report,
)
from app.services.report_pdf import render_order_task_dependency_pdf
from app.services.task_source import InHouseTaskSourceProvider, OdataError
from app.static_version import STATIC_VERSION

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION

CRM_FAILURE_MESSAGE = "The report could not be loaded from the CRM. Try again, or check that the CRM is reachable."

# Failures that mean "the CRM did not give us usable data" — these degrade to a banner.
# Anything else (TypeError, KeyError, a bug in the computation) is a real defect and must
# surface as a 500 rather than be disguised as a connectivity problem.
CRM_FAILURES = (httpx.HTTPError, OSError, OdataError, InvalidCrmTimestamp)


def build_provider(settings: Settings) -> InHouseTaskSourceProvider:
    """Seam for tests — patched out so no test ever needs a live CRM."""
    return InHouseTaskSourceProvider(settings)


def _machine_groups(db: Session) -> list[Team]:
    # The already-synced local mirror of the CRM's MachineGroups; no second CRM call
    # just to fill a dropdown.
    return list(db.scalars(select(Team).order_by(Team.name)))


def _parse_report_filters(
    start: str | None,
    end: str | None,
    machine_group_id: list[int],
    overdue_only: bool,
    today: date,
    show_closed: bool = False,
) -> ReportFilters:
    return ReportFilters.parse(
        start=start or None,
        end=end or None,
        machine_group_ids=list(machine_group_id),
        overdue_only=overdue_only,
        today=today,
        show_closed=show_closed,
    )


def _group_names(machine_groups: list[Team]) -> dict[int, str]:
    return {team.id: team.name for team in machine_groups}


def _load_groups(db: Session, settings: Settings, filters: ReportFilters, today: date, machine_groups: list[Team]):
    return load_report(
        build_provider(settings),
        filters,
        today=today,
        machine_group_names=_group_names(machine_groups),
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
    show_closed: bool = False,
):
    today = datetime.now(timezone.utc).date()
    machine_groups = _machine_groups(db)
    groups: list = []
    error = None
    filters = None
    try:
        filters = _parse_report_filters(start, end, machine_group_id, overdue_only, today, show_closed)
        groups = _load_groups(db, settings, filters, today, machine_groups)
    except InvalidFilters as exc:
        error = str(exc)
    except CRM_FAILURES as exc:  # any CRM/transport failure degrades to a banner
        error = f"{CRM_FAILURE_MESSAGE} ({exc})"

    return templates.TemplateResponse(
        request,
        "reports/order_task_dependency.html",
        {
            "current_user": current_user,
            "groups": groups,
            "tasks": flatten(groups),
            "error": error,
            "filters": filters,
            "machine_groups": machine_groups,
            "selected_machine_group_ids": list(machine_group_id),
            "start": start or "",
            "end": end or "",
            "overdue_only": overdue_only,
            "show_closed": show_closed,
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
    show_closed: bool = False,
):
    today = datetime.now(timezone.utc).date()
    machine_groups = _machine_groups(db)
    try:
        filters = _parse_report_filters(start, end, machine_group_id, overdue_only, today, show_closed)
        groups = _load_groups(db, settings, filters, today, machine_groups)
        content = order_task_dependency_rows_to_xlsx(flatten(groups), "Order Task Dependency")
    except InvalidFilters as exc:
        return PlainTextResponse(str(exc), status_code=400)
    except CRM_FAILURES as exc:  # never hand back a corrupt workbook
        return PlainTextResponse(f"{CRM_FAILURE_MESSAGE} ({exc})", status_code=502)

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
    show_closed: bool = False,
):
    today = datetime.now(timezone.utc).date()
    machine_groups = _machine_groups(db)
    try:
        filters = _parse_report_filters(start, end, machine_group_id, overdue_only, today, show_closed)
        groups = _load_groups(db, settings, filters, today, machine_groups)
        content = render_order_task_dependency_pdf(
            groups, summary=filters.describe(_group_names(machine_groups)), generated_at=datetime.now(timezone.utc)
        )
    except InvalidFilters as exc:
        return PlainTextResponse(str(exc), status_code=400)
    except CRM_FAILURES as exc:
        return PlainTextResponse(f"{CRM_FAILURE_MESSAGE} ({exc})", status_code=502)

    filename = f"order-task-dependency-{filters.start.isoformat()}-to-{filters.end.isoformat()}.pdf"
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
