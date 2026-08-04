"""Web UI: submit/approve/start/deploy requests and view current per-client/environment status.

Server-rendered with Jinja2 directly inside the FastAPI app (project_plan.md, Section 5) —
no separate frontend build. Five-stage flow: Submit -> Pending Team Lead Approval ->
Pending Deployment -> In Progress -> Deployed. A `standard` (RequestType) request lands in
`pending_approval` the moment it's created (the bare `submitted` status is left for the
older intake-skill stopgap format only). "Start Deployment" (start_request) moves
Approved -> In Progress so the requester can see a deploy-team member has picked it up,
not just that it's approved and waiting; "Mark Deployed" (deploy_request) then only
accepts In Progress. Both write to the same DeploymentExecution row (claim/start
timestamps, then the completion timestamp) rather than the bare claim/start/complete/fail
lifecycle DeploymentExecution's own model supports in full.

Two other RequestTypes — `db_dump_restore` and `test_local` — skip the approval stage
entirely by design (create_db_dump_restore_request/create_test_local_request below land
straight in `approved`), but still go through the same Start/Mark Deployed steps as
everything else, so who executed them and when is still tracked.

Every route here requires login (require_login) — this whole router is the app.
"""

from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import can_approve_deployment_request, require_deploy_team_member, require_login
from app.config import Settings, get_settings
from app.database import get_db
from app.models.approval import Approval, ApprovalDecision
from app.models.client import Client
from app.models.deployable_task import DeployableTask
from app.models.deployment_execution import DeploymentExecution, ExecutionStatus
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.user import User, UserRole
from app.services.dashboard import clients_with_deployments, current_deployment_status, deployment_history
from app.services.export import rows_to_xlsx
from app.services.sync import sync_deployable_tasks
from app.services.task_source import InHouseTaskSourceProvider
from app.static_version import STATIC_VERSION

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION

# Shown instead of the raw enum value everywhere the status is displayed to a person.
STATUS_LABELS = {
    RequestStatus.pending_intake: "Pending intake",
    RequestStatus.submitted: "Submitted",
    RequestStatus.pending_approval: "Pending Team Lead Approval",
    RequestStatus.approved: "Pending Deployment",
    RequestStatus.rejected: "Rejected",
    RequestStatus.claimed: "Claimed",
    RequestStatus.in_progress: "In Progress",
    RequestStatus.completed: "Deployed",
    RequestStatus.failed: "Failed",
    RequestStatus.rolled_back: "Rolled Back",
}

# Sentinel option value for "create a new client from the text field below" in the
# request form's client <select> — picked because it can never collide with a real
# Client.id (those are ints).
NEW_CLIENT_VALUE = "__new__"

REQUEST_TYPE_LABELS = {
    RequestType.standard: "Standard Deployment",
    RequestType.db_dump_restore: "Database Dump & Restore",
    RequestType.test_local: "Test.local Deployment",
}

# Known *.test.local boxes, offered as quick-pick suggestions on the "Test.local
# Deployment" tab — a convenience only. Any *.test.local host can still be typed in;
# see create_test_local_request()'s validation below.
TEST_LOCAL_SERVER_SUGGESTIONS = ["crm.test.local", "tmp.test.local", "vop.test.local"]


class _Rail:
    """Four dots standing for the request's own four real lifecycle stages — Submitted ->
    Approved -> Started -> Deployed (project_plan.md, Section 4; the Started stage is what
    start_request() below newly surfaces). `dots` is a color per stage
    ("empty"/"amber"/"teal"/"green"/"red"); `pulse` marks the one dot still in motion (None
    once the request has settled into a terminal state)."""

    __slots__ = ("dots", "pulse")

    def __init__(self, dots: tuple[str, str, str, str], pulse: int | None):
        self.dots = dots
        self.pulse = pulse


# Rendered by request_list.html next to every request's status label — see RequestStatus
# for what each value means. Not applicable to the dashboard/history tables, which only
# ever show already-`completed` rows (fully lit every time), so the rail would add
# nothing there.
RAIL_STAGES = {
    RequestStatus.pending_intake: _Rail(("amber", "empty", "empty", "empty"), 0),
    RequestStatus.submitted: _Rail(("amber", "empty", "empty", "empty"), 0),
    RequestStatus.pending_approval: _Rail(("amber", "empty", "empty", "empty"), 0),
    RequestStatus.approved: _Rail(("teal", "teal", "empty", "empty"), 1),
    RequestStatus.claimed: _Rail(("teal", "teal", "empty", "empty"), 1),
    RequestStatus.in_progress: _Rail(("teal", "teal", "teal", "empty"), 2),
    RequestStatus.completed: _Rail(("teal", "teal", "teal", "green"), None),
    RequestStatus.rejected: _Rail(("red", "empty", "empty", "empty"), None),
    RequestStatus.failed: _Rail(("teal", "teal", "red", "empty"), None),
    RequestStatus.rolled_back: _Rail(("teal", "teal", "teal", "red"), None),
}


@router.get("/")
def home() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


def _parse_filters(
    client_id: str | None, environment: str | None, task_id: str | None
) -> tuple[int | None, DeploymentEnvironment | None, str | None]:
    """Shared query-string parsing for the dashboard/history pages and their exports —
    an empty string (the filter form's "All clients"/"All systems" option) means "no
    filter", same as the param being absent entirely."""
    parsed_client_id = int(client_id) if client_id else None
    parsed_environment = DeploymentEnvironment(environment) if environment else None
    parsed_task_id = task_id.strip() if task_id and task_id.strip() else None
    return parsed_client_id, parsed_environment, parsed_task_id


def _filter_context(db: Session, client_id: int | None, environment: DeploymentEnvironment | None, task_id: str | None) -> dict:
    return {
        "filter_clients": clients_with_deployments(db),
        "filter_environments": list(DeploymentEnvironment),
        "selected_client_id": client_id,
        "selected_environment": environment,
        "selected_task_id": task_id or "",
    }


@router.get("/dashboard")
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    client_id: str | None = None,
    environment: str | None = None,
    task_id: str | None = None,
):
    parsed_client_id, parsed_environment, parsed_task_id = _parse_filters(client_id, environment, task_id)
    rows = current_deployment_status(db, parsed_client_id, parsed_environment, parsed_task_id)
    context = {"rows": rows, "current_user": current_user}
    context.update(_filter_context(db, parsed_client_id, parsed_environment, parsed_task_id))
    return templates.TemplateResponse(request, "dashboard.html", context)


@router.get("/dashboard/export.xlsx")
def export_dashboard_xlsx(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    client_id: str | None = None,
    environment: str | None = None,
    task_id: str | None = None,
):
    parsed_client_id, parsed_environment, parsed_task_id = _parse_filters(client_id, environment, task_id)
    rows = current_deployment_status(db, parsed_client_id, parsed_environment, parsed_task_id)
    content = rows_to_xlsx(rows, "Current Status")
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=current-deployment-status.xlsx"},
    )


@router.get("/dashboard/history")
def dashboard_history(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    client_id: str | None = None,
    environment: str | None = None,
    task_id: str | None = None,
):
    parsed_client_id, parsed_environment, parsed_task_id = _parse_filters(client_id, environment, task_id)
    rows = deployment_history(db, parsed_client_id, parsed_environment, parsed_task_id)
    context = {"rows": rows, "current_user": current_user}
    context.update(_filter_context(db, parsed_client_id, parsed_environment, parsed_task_id))
    return templates.TemplateResponse(request, "dashboard_history.html", context)


@router.get("/dashboard/history/export.xlsx")
def export_dashboard_history_xlsx(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    client_id: str | None = None,
    environment: str | None = None,
    task_id: str | None = None,
):
    parsed_client_id, parsed_environment, parsed_task_id = _parse_filters(client_id, environment, task_id)
    rows = deployment_history(db, parsed_client_id, parsed_environment, parsed_task_id)
    content = rows_to_xlsx(rows, "Deployment History")
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=deployment-history.xlsx"},
    )


def _request_form_context(
    db: Session,
    current_user: User,
    error: str | None = None,
    active_tab: str = "standard",
    notice: str | None = None,
) -> dict:
    return {
        "current_user": current_user,
        "clients": db.query(Client).order_by(Client.name).all(),
        # Task ID is picked from here, not typed — see create_request() below. Only
        # currently-PLANNED tasks, matching the same convention `deployable-tasks` (the
        # CLI command) already uses.
        "deployable_tasks": (
            db.query(DeployableTask)
            .filter(DeployableTask.target_status == "PLANNED")
            .order_by(DeployableTask.due_date)
            .all()
        ),
        "new_client_value": NEW_CLIENT_VALUE,
        "environments": list(DeploymentEnvironment),
        "test_local_server_suggestions": TEST_LOCAL_SERVER_SUGGESTIONS,
        "active_tab": active_tab,
        "error": error,
        "notice": notice,
    }


@router.get("/requests/new")
def new_request_form(
    request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_login)
):
    # "synced"/"sync_error" arrive as query params after a redirect from
    # sync_deployable_tasks_now() below — a plain query-string flash message rather than a
    # session-backed one, since there's nothing else on this page worth persisting further.
    synced = request.query_params.get("synced")
    notice = f"Synced {synced} deployable task(s) from the CRM." if synced is not None else None
    error = request.query_params.get("sync_error")
    return templates.TemplateResponse(
        request, "request_form.html", _request_form_context(db, current_user, error=error, notice=notice)
    )


@router.post("/requests/new/sync-deployable-tasks")
def sync_deployable_tasks_now(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    settings: Settings = Depends(get_settings),
):
    """Manual trigger for the same sync the `deployable-tasks` CLI command already runs
    on its own 5-minute cron schedule (README.md) — lets someone pull in an order they
    *just* created in the CRM immediately, instead of waiting for the next scheduled run.
    """
    try:
        result = sync_deployable_tasks(db, InHouseTaskSourceProvider(settings))
    except Exception as exc:
        return RedirectResponse(
            url=f"/requests/new?sync_error={quote(f'Could not sync from the CRM: {exc}')}",
            status_code=303,
        )
    return RedirectResponse(url=f"/requests/new?synced={result.total}", status_code=303)


def _parse_deployable_task_ids(raw: str | None) -> list[int]:
    """The hidden deployable_task_ids field is a comma-separated list of DeployableTask.id
    values, built client-side (request_form.html) as the requester adds tasks one at a
    time from the search box — never typed directly. Silently drops anything that isn't
    a plain int (a raw POST bypassing the JS) rather than 500ing; create_request() below
    treats an empty result the same as "nothing selected"."""
    if not raw:
        return []
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        task_id = int(part)
        if task_id not in ids:  # dedupe, preserve selection order
            ids.append(task_id)
    return ids


@router.post("/requests")
def create_request(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    # Comma-separated DeployableTask.id values — see _parse_deployable_task_ids() above.
    # A plain str (not list[int] = Form(...)) so a blank/missing field re-renders the
    # friendlier "select a task" error below instead of a raw 422.
    deployable_task_ids: str | None = Form(None),
    # Same reasoning: an empty-string value (e.g. a raw POST bypassing the <select>'s
    # `required` attribute) is parsed as None by Starlette's form handling, which would
    # otherwise 422 before ever reaching the friendlier "select a client" error below.
    client_id: str | None = Form(None),
    new_client_name: str = Form(""),
    environment: DeploymentEnvironment = Form(...),
    git_branch: str = Form(...),
    commit_hash: str = Form(...),
    version: str = Form(...),
    changes_description: str = Form(""),
):
    def rerender(error: str):
        context = _request_form_context(db, current_user, error)
        return templates.TemplateResponse(request, "request_form.html", context, status_code=400)

    task_id_error = (
        "Select at least one Task ID from the list below — type to search, then pick a "
        "suggestion; typed text that doesn't match one isn't a valid selection."
    )
    task_ids = _parse_deployable_task_ids(deployable_task_ids)
    if not task_ids:
        return rerender(task_id_error)

    # Looked up by DeployableTask.id (the CRM's own operation id, always unique) rather
    # than the task's own task_id string — that string is NOT guaranteed unique across
    # orders (see DeployableTask's docstring), so it can't safely be the lookup key here.
    deployable_tasks = [db.get(DeployableTask, task_id) for task_id in task_ids]
    if any(task is None or task.target_status != "PLANNED" for task in deployable_tasks):
        return rerender(task_id_error)

    # Multiple orders can be deployed together in one request, but only if they're all
    # for the same client AND the same target (test/live) — a request has exactly one
    # `environment`, so mixing a Test order with a Live one into it would silently deploy
    # one of them to the wrong system. (request_form.html's JS already blocks both of
    # these client-side; this is the server-side backstop.)
    if len({task.client_name for task in deployable_tasks}) > 1:
        return rerender("All selected Task IDs must belong to the same client.")
    if len({task.target for task in deployable_tasks}) > 1:
        return rerender("All selected Task IDs must be for the same system (Test or Live).")

    combined_task_id = ", ".join(task.task_id for task in deployable_tasks)
    combined_module_name = ", ".join(task.item_name or "?" for task in deployable_tasks)

    git_branch = git_branch.strip()
    commit_hash = commit_hash.strip()
    version = version.strip()
    if not git_branch or not commit_hash or not version:
        return rerender("Git branch, commit hash, and version are all required.")

    if client_id == NEW_CLIENT_VALUE:
        new_client_name = new_client_name.strip()
        if not new_client_name:
            return rerender("Enter a name for the new client.")
        client = db.query(Client).filter(Client.name == new_client_name).one_or_none()
        if client is None:
            client = Client(name=new_client_name)
            db.add(client)
            db.flush()  # populate client.id before using it below
    elif client_id:
        client = db.get(Client, int(client_id))
        if client is None:
            return rerender("Selected client no longer exists.")
    else:
        return rerender('Select a client, or choose "+ Add new client".')

    db.add(
        DeploymentRequest(
            task_id=combined_task_id,
            module_name=combined_module_name,
            client_id=client.id,
            environment=environment,
            git_branch=git_branch,
            commit_hash=commit_hash,
            version=version,
            changes_description=changes_description.strip() or None,
            requested_by=current_user.id,
            status=RequestStatus.pending_approval,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return RedirectResponse(url="/requests", status_code=303)


@router.post("/requests/db-dump-restore")
def create_db_dump_restore_request(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    dump_source: str = Form(...),
    version: str = Form(...),
    restore_source: str = Form(""),
    # Checkbox: absent from the POST body entirely when unchecked, so a plain bool
    # default (rather than Form(...)) is what lets "unchecked" parse cleanly.
    share_with_requestor: bool = Form(False),
):
    def rerender(error: str):
        context = _request_form_context(db, current_user, error, active_tab="db_dump_restore")
        return templates.TemplateResponse(request, "request_form.html", context, status_code=400)

    dump_source = dump_source.strip()
    version = version.strip()
    restore_source = restore_source.strip()
    if not dump_source:
        return rerender("Dump source is required.")
    if not version:
        return rerender("Application version is required.")
    # Mutually exclusive by design — a dump is either restored somewhere else, or just
    # handed back to the requester, never both and never neither.
    if share_with_requestor and restore_source:
        return rerender("Choose either a restore source or “share with requestor”, not both.")
    if not share_with_requestor and not restore_source:
        return rerender("Provide a restore source, or check “share with requestor”.")

    db.add(
        DeploymentRequest(
            request_type=RequestType.db_dump_restore,
            dump_source=dump_source,
            version=version,
            restore_source=restore_source or None,
            share_with_requestor=share_with_requestor,
            requested_by=current_user.id,
            # No approval required for this request type — lands straight in the
            # deploy team's "Pending Deployment" queue, same as an approved standard
            # request, so who-executed-it-and-when is still tracked.
            status=RequestStatus.approved,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return RedirectResponse(url="/requests", status_code=303)


@router.post("/requests/test-local")
def create_test_local_request(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    server: str = Form(...),
    git_branch: str = Form(...),
    version: str = Form(...),
    changes_description: str = Form(""),
):
    def rerender(error: str):
        context = _request_form_context(db, current_user, error, active_tab="test_local")
        return templates.TemplateResponse(request, "request_form.html", context, status_code=400)

    server = server.strip()
    git_branch = git_branch.strip()
    version = version.strip()
    if not server:
        return rerender("Server name is required.")
    if not server.endswith(".test.local"):
        return rerender("Server must be a *.test.local host.")
    if not git_branch:
        return rerender("Branch name is required.")
    if not version:
        return rerender("Application version is required.")

    db.add(
        DeploymentRequest(
            request_type=RequestType.test_local,
            server=server,
            git_branch=git_branch,
            version=version,
            changes_description=changes_description.strip() or None,
            requested_by=current_user.id,
            # No approval required for this request type — see create_db_dump_restore_request above.
            status=RequestStatus.approved,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return RedirectResponse(url="/requests", status_code=303)


@router.get("/requests")
def list_requests(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    settings: Settings = Depends(get_settings),
):
    requests_ = db.query(DeploymentRequest).order_by(DeploymentRequest.created_at.desc()).all()
    # can_deploy is the same for every row (deploy-team membership doesn't depend on
    # which request it is — require_deploy_team_member() in app/auth.py). can_approve
    # does depend on the row (each request's own requester's team lead may differ), so
    # it's a function the template calls per-row rather than a single boolean — mirrors
    # can_approve_deployment_request() so the template hides buttons a click would 403 on.
    can_deploy = (
        current_user.role == UserRole.admin
        or current_user.machine_group_id == settings.task_api_deployable_machine_group_id
    )
    return templates.TemplateResponse(
        request,
        "request_list.html",
        {
            "current_user": current_user,
            "requests": requests_,
            "status_labels": STATUS_LABELS,
            "RequestStatus": RequestStatus,
            "RequestType": RequestType,
            "request_type_labels": REQUEST_TYPE_LABELS,
            "rail_stages": RAIL_STAGES,
            "can_approve_request": lambda r: can_approve_deployment_request(current_user, r),
            "can_deploy": can_deploy,
        },
    )


def _get_request_or_404(db: Session, request_id: int) -> DeploymentRequest:
    deployment_request = db.get(DeploymentRequest, request_id)
    if deployment_request is None:
        raise HTTPException(status_code=404, detail="Deployment request not found")
    return deployment_request


def _require_can_approve(current_user: User, deployment_request: DeploymentRequest) -> None:
    if not can_approve_deployment_request(current_user, deployment_request):
        raise HTTPException(
            status_code=403,
            detail="Only the requester's own team lead (or an admin) can approve/reject this request",
        )


@router.post("/requests/{request_id}/approve")
def approve_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    comment: str = Form(""),
):
    deployment_request = _get_request_or_404(db, request_id)
    _require_can_approve(current_user, deployment_request)
    if deployment_request.status != RequestStatus.pending_approval:
        raise HTTPException(status_code=409, detail="Request is not pending approval")

    db.add(
        Approval(
            request_id=request_id,
            approver_id=current_user.id,
            decision=ApprovalDecision.approved,
            decided_at=datetime.now(timezone.utc),
            comment=comment.strip() or None,
        )
    )
    deployment_request.status = RequestStatus.approved
    db.commit()
    return RedirectResponse(url="/requests", status_code=303)


@router.post("/requests/{request_id}/reject")
def reject_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    comment: str = Form(""),
):
    deployment_request = _get_request_or_404(db, request_id)
    _require_can_approve(current_user, deployment_request)
    if deployment_request.status != RequestStatus.pending_approval:
        raise HTTPException(status_code=409, detail="Request is not pending approval")

    db.add(
        Approval(
            request_id=request_id,
            approver_id=current_user.id,
            decision=ApprovalDecision.rejected,
            decided_at=datetime.now(timezone.utc),
            comment=comment.strip() or None,
        )
    )
    deployment_request.status = RequestStatus.rejected
    db.commit()
    return RedirectResponse(url="/requests", status_code=303)


@router.post("/requests/{request_id}/start")
def start_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_deploy_team_member),
):
    """The first half of execution tracking (project_plan.md Section 4/Phase 2, previously
    unsurfaced): a deploy-team member marks a request as picked up, moving it to
    `in_progress` so the requester sees "In Progress" instead of it just sitting at
    "Pending Deployment" with no sign anyone's working on it. Creates the
    DeploymentExecution row that deploy_request() below later completes."""
    deployment_request = _get_request_or_404(db, request_id)
    if deployment_request.status != RequestStatus.approved:
        raise HTTPException(status_code=409, detail="Request is not pending deployment")

    now = datetime.now(timezone.utc)
    db.add(
        DeploymentExecution(
            request_id=request_id,
            executed_by=current_user.id,
            claimed_at=now,
            started_at=now,
            status=ExecutionStatus.in_progress,
        )
    )
    deployment_request.status = RequestStatus.in_progress
    db.commit()
    return RedirectResponse(url="/requests", status_code=303)


@router.post("/requests/{request_id}/deploy")
def deploy_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_deploy_team_member),
):
    deployment_request = _get_request_or_404(db, request_id)
    if deployment_request.status != RequestStatus.in_progress:
        raise HTTPException(status_code=409, detail="Request has not been started yet")

    # Updates the row start_request() above created — DeploymentExecution.request_id is
    # unique-per-request (app/models/deployment_execution.py), so this is always exactly
    # one row, not a new insert. Deliberately not restricted to whoever ran start_request:
    # any deploy-team member may mark it deployed, same "membership, not a personal claim
    # lock" model the rest of this router already uses (see require_deploy_team_member).
    execution = db.query(DeploymentExecution).filter_by(request_id=request_id).one()
    execution.completed_at = datetime.now(timezone.utc)
    execution.status = ExecutionStatus.completed
    deployment_request.status = RequestStatus.completed
    db.commit()
    return RedirectResponse(url="/requests", status_code=303)
