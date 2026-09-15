# app/models/client_version_status.py
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ClientVersionStatus(Base):
    """One row per client — latest-state Test/Live version tracking. See
    docs/superpowers/specs/2026-08-27-release-tracker-redesign.md. Replaces
    v1's per-deploy history table (ClientVersionRecord).

    test_*/live_* fields are written independently by
    app.services.release_tracker.record_client_deploy(): confirming a Live
    deploy only ever touches live_*, confirming a Test deploy only ever
    touches test_*. Each also carries a *_previous_version (the value it's
    overwriting) kept for potential future use — not currently rendered
    anywhere in the Release Tracker UI.

    Main Version is NOT stored here (as of the 2026-08-30 fix) — it used to
    be a per-client snapshot copied from BitbucketMainBranchStatus at that
    client's own deploy time, but that meant a client's displayed Main
    Version only updated on that client's next deploy, not when main
    actually changed. It's now always a live read of the single
    BitbucketMainBranchStatus cache row at render time — see
    app.services.release_tracker.current_main_branch_status() — so every
    client row shows the same, current value.
    """

    __tablename__ = "client_version_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), unique=True)

    test_current_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    test_previous_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    test_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    test_recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    test_deployment_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("deployment_requests.id"), nullable=True
    )

    live_current_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    live_previous_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    live_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    live_recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    live_deployment_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("deployment_requests.id"), nullable=True
    )

    client = relationship("Client")
    test_recorder = relationship("User", foreign_keys=[test_recorded_by])
    test_deployment_request = relationship("DeploymentRequest", foreign_keys=[test_deployment_request_id])
    live_recorder = relationship("User", foreign_keys=[live_recorded_by])
    live_deployment_request = relationship("DeploymentRequest", foreign_keys=[live_deployment_request_id])
