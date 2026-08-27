"""Web UI for the Release Tracker tab — one row per client, fed by the
deploy-confirmation popup in app/routers/dashboard.py's deploy_request(). See
docs/superpowers/specs/2026-08-27-release-tracker-redesign.md.
"""

from datetime import datetime, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import can_edit_client_version_status, require_login
from app.database import get_db
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_request import DeploymentEnvironment
from app.models.user import User
from app.services.export import release_tracker_rows_to_xlsx
from app.services.release_tracker import clients_with_version_records, release_tracker_rows
from app.static_version import STATIC_VERSION

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION


def _parse_release_tracker_filters(client_id: str | None) -> int | None:
    return int(client_id) if client_id else None


def _filter_context(db: Session, client_id: int | None) -> dict:
    return {
        "filter_clients": clients_with_version_records(db),
        "selected_client_id": client_id,
    }


@router.get("/release-tracker")
def release_tracker_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    client_id: str | None = None,
):
    parsed_client_id = _parse_release_tracker_filters(client_id)
    rows = release_tracker_rows(db, parsed_client_id)
    context = {
        "current_user": current_user,
        "rows": rows,
        "can_edit_test": lambda r: can_edit_client_version_status(current_user, r, DeploymentEnvironment.test),
        "can_edit_live": lambda r: can_edit_client_version_status(current_user, r, DeploymentEnvironment.live),
    }
    context.update(_filter_context(db, parsed_client_id))
    return templates.TemplateResponse(request, "release_tracker.html", context)


@router.get("/release-tracker/export.xlsx")
def release_tracker_export_xlsx(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    client_id: str | None = None,
):
    parsed_client_id = _parse_release_tracker_filters(client_id)
    rows = release_tracker_rows(db, parsed_client_id)
    content = release_tracker_rows_to_xlsx(rows, "Release Tracker")
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=release-tracker.xlsx"},
    )


def _get_status_or_404(db: Session, status_id: int) -> ClientVersionStatus:
    row = db.get(ClientVersionStatus, status_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Client version status not found")
    return row


@router.get("/release-tracker/{status_id}/edit")
def release_tracker_edit_form(
    status_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
):
    row = _get_status_or_404(db, status_id)
    can_edit_test = can_edit_client_version_status(current_user, row, DeploymentEnvironment.test)
    can_edit_live = can_edit_client_version_status(current_user, row, DeploymentEnvironment.live)
    if not can_edit_test and not can_edit_live:
        raise HTTPException(status_code=403, detail="You don't have permission to edit this record")
    return templates.TemplateResponse(
        request, "release_tracker_edit.html",
        {"current_user": current_user, "record": row, "can_edit_test": can_edit_test, "can_edit_live": can_edit_live},
    )


@router.post("/release-tracker/{status_id}/edit")
def release_tracker_edit(
    status_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    test_current_version: str | None = Form(None),
    live_current_version: str | None = Form(None),
):
    row = _get_status_or_404(db, status_id)
    can_edit_test = can_edit_client_version_status(current_user, row, DeploymentEnvironment.test)
    can_edit_live = can_edit_client_version_status(current_user, row, DeploymentEnvironment.live)

    if test_current_version is not None and not can_edit_test:
        raise HTTPException(status_code=403, detail="You don't have permission to edit the Test version")
    if live_current_version is not None and not can_edit_live:
        raise HTTPException(status_code=403, detail="You don't have permission to edit the Live version")
    if test_current_version is None and live_current_version is None:
        raise HTTPException(status_code=403, detail="You don't have permission to edit this record")

    errors = []
    now = datetime.now(timezone.utc)
    if test_current_version is not None:
        stripped = test_current_version.strip()
        if not stripped:
            errors.append("Test current version cannot be blank.")
        elif stripped != row.test_current_version:
            row.test_current_version = stripped
            row.test_updated_at = now
    if live_current_version is not None:
        stripped = live_current_version.strip()
        if not stripped:
            errors.append("Live current version cannot be blank.")
        elif stripped != row.live_current_version:
            row.live_current_version = stripped
            row.live_updated_at = now

    if errors:
        return templates.TemplateResponse(
            request, "release_tracker_edit.html",
            {
                "current_user": current_user, "record": row, "error": " ".join(errors),
                "can_edit_test": can_edit_test, "can_edit_live": can_edit_live,
            },
            status_code=400,
        )

    db.commit()
    return RedirectResponse(url="/release-tracker", status_code=303)
