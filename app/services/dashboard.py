"""Read-only queries behind the two deployment dashboards (project_plan.md, Section 5):
current per-client/system status, and the full filterable history both are drawn from.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Query, Session, joinedload

from app.models.approval import Approval, ApprovalDecision
from app.models.client import Client
from app.models.deployment_execution import DeploymentExecution, ExecutionStatus
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus


@dataclass
class DeploymentStatusRow:
    client_name: str
    environment: str
    git_branch: str | None
    commit_hash: str | None
    version: str | None
    task_id: str | None
    changes_description: str | None
    requested_by: str | None
    approved_by: str | None
    deployed_by: str | None
    requested_at: datetime | None
    deployed_at: datetime | None
    request_id: int
    # The MES server URL picked on the request (app/routers/dashboard.py's
    # create_request/edit_request — the same DeploymentRequest.server column
    # db_dump_restore/test_local requests already used). Optional: only Standard
    # requests get it from the Server URL dropdown, and only when one was configured.
    server: str | None = None


def _completed_executions_query(
    db: Session,
    client_id: int | None,
    environment: DeploymentEnvironment | None,
    task_id: str | None,
) -> Query:
    query = (
        db.query(DeploymentExecution)
        .join(DeploymentRequest, DeploymentExecution.request_id == DeploymentRequest.id)
        .filter(DeploymentExecution.status == ExecutionStatus.completed)
        .filter(DeploymentRequest.status == RequestStatus.completed)
        .filter(DeploymentRequest.client_id.isnot(None))
        .filter(DeploymentRequest.environment.isnot(None))
        .options(joinedload(DeploymentExecution.request).joinedload(DeploymentRequest.client))
        .options(joinedload(DeploymentExecution.request).joinedload(DeploymentRequest.requester))
        .options(
            joinedload(DeploymentExecution.request)
            .joinedload(DeploymentRequest.approvals)
            .joinedload(Approval.approver)
        )
        .options(joinedload(DeploymentExecution.executor))
    )
    if client_id is not None:
        query = query.filter(DeploymentRequest.client_id == client_id)
    if environment is not None:
        query = query.filter(DeploymentRequest.environment == environment)
    if task_id:
        query = query.filter(DeploymentRequest.task_id.ilike(f"%{task_id}%"))
    return query


def _approved_by(request: DeploymentRequest) -> str | None:
    approval = next((a for a in request.approvals if a.decision == ApprovalDecision.approved), None)
    return approval.approver.name if approval and approval.approver else None


def _row_from_execution(execution: DeploymentExecution) -> DeploymentStatusRow:
    request = execution.request
    return DeploymentStatusRow(
        client_name=request.client.name,
        environment=request.environment.value,
        git_branch=request.git_branch,
        commit_hash=request.commit_hash,
        version=request.version,
        task_id=request.task_id,
        changes_description=request.changes_description,
        requested_by=request.requester.name if request.requester else None,
        approved_by=_approved_by(request),
        deployed_by=execution.executor.name if execution.executor else None,
        requested_at=request.created_at,
        deployed_at=execution.completed_at,
        request_id=execution.request_id,
        server=request.server,
    )


def current_deployment_status(
    db: Session,
    client_id: int | None = None,
    environment: DeploymentEnvironment | None = None,
    task_id: str | None = None,
) -> list[DeploymentStatusRow]:
    """For every (client, environment) pair that's ever had a completed deployment matching
    the given filters, return the most recently completed one — this is what "which
    branch/commit is currently running" resolves to on the dashboard.

    Computed on read rather than kept in its own synced table: this is a small internal
    tool with a handful of clients/environments, so re-scanning completed executions per
    dashboard load is cheap, and it avoids a second place for "current status" to drift
    out of sync with the request/execution rows it's derived from.
    """
    completed_executions = (
        _completed_executions_query(db, client_id, environment, task_id)
        .order_by(DeploymentExecution.completed_at.desc())
        .all()
    )

    latest_by_key: dict[tuple[int, str], DeploymentExecution] = {}
    for execution in completed_executions:
        key = (execution.request.client_id, execution.request.environment)
        latest_by_key.setdefault(key, execution)  # already newest-first, so first write wins

    rows = [_row_from_execution(execution) for execution in latest_by_key.values()]
    return sorted(rows, key=lambda r: r.deployed_at or datetime.min, reverse=True)


def deployment_history(
    db: Session,
    client_id: int | None = None,
    environment: DeploymentEnvironment | None = None,
    task_id: str | None = None,
) -> list[DeploymentStatusRow]:
    """Every completed deployment matching the given filters, newest first — unlike
    current_deployment_status() above, this is NOT deduped to one row per client/system;
    it's the full audit trail behind that "current" view.
    """
    completed_executions = (
        _completed_executions_query(db, client_id, environment, task_id)
        .order_by(DeploymentExecution.completed_at.desc())
        .all()
    )
    return [_row_from_execution(execution) for execution in completed_executions]


def clients_with_deployments(db: Session) -> list[Client]:
    """Clients to populate the filter dropdown with — only ones that actually have at
    least one completed deployment, so the list isn't cluttered with every CRM client."""
    return (
        db.query(Client)
        .join(DeploymentRequest, DeploymentRequest.client_id == Client.id)
        .filter(DeploymentRequest.status == RequestStatus.completed)
        .distinct()
        .order_by(Client.name)
        .all()
    )
