"""Read-only queries and the deploy-time write behind the Release Tracker tab
(docs/superpowers/specs/2026-08-27-release-tracker-redesign.md) and the
deploy-confirmation popup that feeds it (app/routers/dashboard.py's
deploy_request/list_requests).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_request import DeploymentEnvironment


def record_client_deploy(
    db: Session,
    *,
    client_id: int,
    environment: DeploymentEnvironment,
    current_version: str,
    recorded_by: int,
    deployment_request_id: int,
) -> ClientVersionStatus:
    """Get-or-create this client's ClientVersionStatus row, then update only
    the columns for `environment` — the other environment's columns are left
    completely untouched, and no other client's row is touched either way.

    Does not touch BitbucketMainBranchStatus at all: Main Version is a live
    read at render time (current_main_branch_status(), below), not a
    per-client snapshot taken at deploy time — see ClientVersionStatus's
    docstring for why that changed.
    """
    row = db.query(ClientVersionStatus).filter_by(client_id=client_id).one_or_none()
    if row is None:
        row = ClientVersionStatus(client_id=client_id)
        db.add(row)
        db.flush()

    prefix = environment.value  # "test" or "live" — matches the column prefixes exactly
    setattr(row, f"{prefix}_previous_version", getattr(row, f"{prefix}_current_version"))
    setattr(row, f"{prefix}_current_version", current_version)
    setattr(row, f"{prefix}_updated_at", datetime.now(timezone.utc))
    setattr(row, f"{prefix}_recorded_by", recorded_by)
    setattr(row, f"{prefix}_deployment_request_id", deployment_request_id)

    return row


def current_main_branch_status(db: Session) -> BitbucketMainBranchStatus | None:
    """The single cached row of shopfloor-suite's main-branch status
    (id=1), refreshed every 5 minutes by `python -m app.cli
    sync-bitbucket-main` — None if that sync has never run. This is what
    the Release Tracker tab's Main Version block reads for every client
    row, live, at render time (see release_tracker_rows() — this is
    deliberately not folded into that function, since it's one shared row,
    not per-client)."""
    return db.get(BitbucketMainBranchStatus, 1)


def current_version_for(db: Session, client_id: int, environment: DeploymentEnvironment) -> str | None:
    """The client's current version for this environment right now — what
    the deploy-confirmation popup shows as "Previous version" (it's about to
    become the previous value the moment this deploy is confirmed). None if
    this client has no ClientVersionStatus row yet, or hasn't deployed to
    this environment yet."""
    row = db.query(ClientVersionStatus).filter_by(client_id=client_id).one_or_none()
    if row is None:
        return None
    return getattr(row, f"{environment.value}_current_version")


def release_tracker_rows(db: Session, client_id: int | None) -> list[ClientVersionStatus]:
    """One row per client, ordered by client name — the Release Tracker
    tab's primary listing."""
    query = (
        db.query(ClientVersionStatus)
        .join(Client, ClientVersionStatus.client_id == Client.id)
        .options(joinedload(ClientVersionStatus.client))
    )
    if client_id is not None:
        query = query.filter(ClientVersionStatus.client_id == client_id)
    return query.order_by(Client.name).all()


def clients_with_version_records(db: Session) -> list[Client]:
    """Clients to populate the filter dropdown with — only ones that
    actually have a ClientVersionStatus row."""
    return (
        db.query(Client)
        .join(ClientVersionStatus, ClientVersionStatus.client_id == Client.id)
        .distinct()
        .order_by(Client.name)
        .all()
    )
