"""Read-only queries behind the Release Tracker tab (docs/superpowers/specs/
2026-08-27-release-tracker-design.md) and the deploy-confirmation popup that feeds
it (app/routers/dashboard.py's deploy_request/list_requests).
"""

from sqlalchemy.orm import Session, joinedload

from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
from app.models.deployment_request import DeploymentEnvironment


def latest_current_version(db: Session, client_id: int, environment: DeploymentEnvironment) -> str | None:
    """The most recent current_version recorded for this client+environment, or
    None if there's no history yet — this is what the deploy-confirmation popup
    shows as "Previous version" (see Task 8/9), and what a new record's own
    previous_version gets set to."""
    record = (
        db.query(ClientVersionRecord)
        .filter(
            ClientVersionRecord.client_id == client_id,
            ClientVersionRecord.environment == environment,
        )
        .order_by(ClientVersionRecord.created_at.desc())
        .first()
    )
    return record.current_version if record else None


def release_tracker_rows(
    db: Session, client_id: int | None, environment: DeploymentEnvironment | None
) -> list[ClientVersionRecord]:
    """Full history, newest first — the Release Tracker tab's primary listing."""
    query = db.query(ClientVersionRecord).options(
        joinedload(ClientVersionRecord.client),
        joinedload(ClientVersionRecord.recorder),
    )
    if client_id is not None:
        query = query.filter(ClientVersionRecord.client_id == client_id)
    if environment is not None:
        query = query.filter(ClientVersionRecord.environment == environment)
    return query.order_by(ClientVersionRecord.created_at.desc()).all()


def clients_with_version_records(db: Session) -> list[Client]:
    """Clients to populate the filter dropdown with — only ones that actually have
    at least one ClientVersionRecord, mirroring clients_with_deployments() in
    app/services/dashboard.py."""
    return (
        db.query(Client)
        .join(ClientVersionRecord, ClientVersionRecord.client_id == Client.id)
        .distinct()
        .order_by(Client.name)
        .all()
    )
