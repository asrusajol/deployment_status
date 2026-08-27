"""Web UI for the Release Tracker tab — per-client/system version history, fed by
the deploy-confirmation popup in app/routers/dashboard.py's deploy_request(). See
docs/superpowers/specs/2026-08-27-release-tracker-design.md.
"""

from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import can_edit_client_version_record, require_login
from app.database import get_db
from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
from app.models.deployment_request import DeploymentEnvironment
from app.models.user import User
from app.services.export import release_tracker_rows_to_xlsx
from app.services.release_tracker import clients_with_version_records, release_tracker_rows
from app.static_version import STATIC_VERSION

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION


def _parse_release_tracker_filters(client_id: str | None, environment: str | None):
    parsed_client_id = int(client_id) if client_id else None
    parsed_environment = DeploymentEnvironment(environment) if environment else None
    return parsed_client_id, parsed_environment


def _filter_context(db: Session, client_id: int | None, environment: DeploymentEnvironment | None) -> dict:
    return {
        "filter_clients": clients_with_version_records(db),
        "filter_environments": list(DeploymentEnvironment),
        "selected_client_id": client_id,
        "selected_environment": environment,
    }


@router.get("/release-tracker")
def release_tracker_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    client_id: str | None = None,
    environment: str | None = None,
):
    parsed_client_id, parsed_environment = _parse_release_tracker_filters(client_id, environment)
    rows = release_tracker_rows(db, parsed_client_id, parsed_environment)
    context = {
        "current_user": current_user,
        "rows": rows,
        "can_edit_record": lambda r: can_edit_client_version_record(current_user, r),
    }
    context.update(_filter_context(db, parsed_client_id, parsed_environment))
    return templates.TemplateResponse(request, "release_tracker.html", context)


@router.get("/release-tracker/export.xlsx")
def release_tracker_export_xlsx(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    client_id: str | None = None,
    environment: str | None = None,
):
    parsed_client_id, parsed_environment = _parse_release_tracker_filters(client_id, environment)
    rows = release_tracker_rows(db, parsed_client_id, parsed_environment)
    content = release_tracker_rows_to_xlsx(rows, "Release Tracker")
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=release-tracker.xlsx"},
    )
