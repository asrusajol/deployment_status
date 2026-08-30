"""Web UI for the Seeder Collection tab — one saved seeder/artisan command
per client, devops/admin only. See
docs/superpowers/specs/2026-08-30-seeder-collection-design.md.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_devops
from app.database import get_db
from app.models.seeder_command import SeederCommand
from app.models.user import User
from app.services.seeder_collection import (
    ClientAlreadyHasSeederCommandError,
    clients_without_seeder_command,
    create_seeder_command,
    delete_seeder_command,
    seeder_collection_rows,
    update_seeder_command,
)
from app.static_version import STATIC_VERSION

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION


def _get_seeder_command_or_404(db: Session, seeder_id: int) -> SeederCommand:
    row = db.get(SeederCommand, seeder_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Seeder command not found")
    return row


def _blank_fields(host: str | None, title: str, command: str) -> list[str]:
    errors = []
    if not title.strip():
        errors.append("Title cannot be blank.")
    if not command.strip():
        errors.append("Command cannot be blank.")
    return errors


@router.get("/seeder-collection")
def seeder_collection_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_devops),
):
    rows = seeder_collection_rows(db)
    return templates.TemplateResponse(
        request, "seeder_collection.html", {"current_user": current_user, "rows": rows}
    )


@router.get("/seeder-collection/new")
def seeder_collection_new_form(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_devops),
):
    clients = clients_without_seeder_command(db)
    return templates.TemplateResponse(
        request, "seeder_collection_form.html",
        {"current_user": current_user, "clients": clients, "record": None},
    )


@router.post("/seeder-collection/new")
def seeder_collection_create(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_devops),
    client_id: int = Form(...),
    host: str = Form(""),
    title: str = Form(...),
    command: str = Form(...),
):
    errors = _blank_fields(host, title, command)
    if not errors:
        try:
            create_seeder_command(
                db, client_id=client_id, host=host.strip() or None, title=title.strip(),
                command=command.strip(), created_by=current_user.id,
            )
        except ClientAlreadyHasSeederCommandError:
            errors.append("This client already has a saved seeder command — edit it instead.")

    if errors:
        clients = clients_without_seeder_command(db)
        return templates.TemplateResponse(
            request, "seeder_collection_form.html",
            {
                "current_user": current_user, "clients": clients, "record": None, "error": " ".join(errors),
                "submitted": {"client_id": client_id, "host": host, "title": title, "command": command},
            },
            status_code=400,
        )

    db.commit()
    return RedirectResponse(url="/seeder-collection", status_code=303)


@router.get("/seeder-collection/{seeder_id}/edit")
def seeder_collection_edit_form(
    seeder_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_devops),
):
    row = _get_seeder_command_or_404(db, seeder_id)
    return templates.TemplateResponse(
        request, "seeder_collection_form.html",
        {"current_user": current_user, "clients": None, "record": row},
    )


@router.post("/seeder-collection/{seeder_id}/edit")
def seeder_collection_edit(
    seeder_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_devops),
    host: str = Form(""),
    title: str = Form(...),
    command: str = Form(...),
):
    row = _get_seeder_command_or_404(db, seeder_id)
    errors = _blank_fields(host, title, command)

    if errors:
        return templates.TemplateResponse(
            request, "seeder_collection_form.html",
            {"current_user": current_user, "clients": None, "record": row, "error": " ".join(errors)},
            status_code=400,
        )

    update_seeder_command(
        db, row, host=host.strip() or None, title=title.strip(), command=command.strip(),
        updated_by=current_user.id,
    )
    db.commit()
    return RedirectResponse(url="/seeder-collection", status_code=303)


@router.post("/seeder-collection/{seeder_id}/delete")
def seeder_collection_delete(
    seeder_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_devops),
):
    row = _get_seeder_command_or_404(db, seeder_id)
    delete_seeder_command(db, row)
    db.commit()
    return RedirectResponse(url="/seeder-collection", status_code=303)
