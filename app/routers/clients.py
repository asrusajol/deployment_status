"""Web UI for viewing/editing each client's MES Test/Live server URLs — a client can
have several per system (app/models/client_system_url.py).

Every client here comes from the CRM sync (app/services/sync.py:sync_clients) — this
page never creates a new client, only adds URLs the CRM has no concept of. Visible to
every logged-in user (like Release Tracker); editing is restricted to admin/devops
(app/auth.py:require_admin_or_devops), same roles that can edit ClientVersionStatus
rows on the Release Tracker page.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_admin_or_devops, require_login
from app.database import get_db
from app.models.client import Client
from app.models.client_system_url import ClientSystemUrl
from app.models.deployment_request import DeploymentEnvironment
from app.models.user import User
from app.static_version import STATIC_VERSION

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION


@router.get("/clients")
def clients_page(request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_login)):
    clients = db.query(Client).order_by(Client.name).all()
    return templates.TemplateResponse(
        request, "clients.html", {"current_user": current_user, "clients": clients, "error": None}
    )


def _get_client_or_404(db: Session, client_id: int) -> Client:
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.post("/clients/{client_id}/urls")
def add_client_url(
    client_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_admin_or_devops),
    environment: DeploymentEnvironment = Form(...),
    label: str = Form(""),
    url: str = Form(...),
):
    client = _get_client_or_404(db, client_id)
    url = url.strip()
    if not url:
        # No inline error rendering for this one — same "just don't submit a blank"
        # tolerance as the rest of this page; the browser's own `required` attribute on
        # the url input already stops this in practice.
        return RedirectResponse(url="/clients", status_code=303)
    db.add(
        ClientSystemUrl(client_id=client.id, environment=environment, label=label.strip() or None, url=url)
    )
    db.commit()
    return RedirectResponse(url="/clients", status_code=303)


@router.post("/clients/{client_id}/urls/{url_id}/delete")
def delete_client_url(
    client_id: int,
    url_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_admin_or_devops),
):
    row = db.get(ClientSystemUrl, url_id)
    if row is None or row.client_id != client_id:
        raise HTTPException(status_code=404, detail="URL not found")
    db.delete(row)
    db.commit()
    return RedirectResponse(url="/clients", status_code=303)


@router.post("/clients/{client_id}/toggle-active")
def toggle_client_active(
    client_id: int, db: Session = Depends(get_db), _current_user: User = Depends(require_admin_or_devops)
):
    # A soft hide, not a delete — see Client.is_active. Nothing referencing this client
    # (requests, deployments, ClientVersionStatus, ClientSystemUrl) is touched.
    client = _get_client_or_404(db, client_id)
    client.is_active = not client.is_active
    db.commit()
    return RedirectResponse(url="/clients", status_code=303)
