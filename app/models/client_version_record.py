from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.deployment_request import DeploymentEnvironment


class ClientVersionRecord(Base):
    """One row per DevOps deploy-confirmation on a `standard` DeploymentRequest — see
    docs/superpowers/specs/2026-08-27-release-tracker-design.md ("Release Tracker").

    Full history, never overwritten: each Mark Deployed confirmation inserts a new
    row rather than updating an existing one (confirmed with the user — a
    client+environment's full version timeline, not just latest state).
    previous_version/main_version/main_pr_number are snapshots of what was true at
    the moment this row was created; editing a row afterward (see
    can_edit_client_version_record in app/auth.py) only ever corrects
    current_version, never those historical snapshots.
    """

    __tablename__ = "client_version_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    environment: Mapped[DeploymentEnvironment] = mapped_column(Enum(DeploymentEnvironment, create_type=False))
    current_version: Mapped[str] = mapped_column(String(100))
    # Auto-filled at insert time from the previous row's current_version for this
    # same (client_id, environment) — null only for the very first record ever made
    # for that pair.
    previous_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Snapshotted from BitbucketMainBranchStatus at insert time — see that model.
    # Both null if the very first Bitbucket sync hasn't run yet when this row is
    # created (e.g. the first 5 minutes after this feature ships).
    main_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    main_pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deployment_request_id: Mapped[int] = mapped_column(ForeignKey("deployment_requests.id"))
    recorded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    # Bumped when current_version is corrected after the fact (see
    # can_edit_client_version_record) — equal to created_at until then.
    updated_at: Mapped[datetime] = mapped_column(DateTime)

    client = relationship("Client")
    deployment_request = relationship("DeploymentRequest")
    recorder = relationship("User")
