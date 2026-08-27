# app/models/client_version_status.py
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
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

    main_version/main_pr_number/main_updated_at are a per-client SNAPSHOT,
    not a live shared value — written only when THIS client deploys (either
    environment), copied from BitbucketMainBranchStatus at that moment. The
    periodic 5-minute sync (app.services.sync.sync_bitbucket_main_status)
    never writes to this table at all — two different clients can
    legitimately show two different Main Version values, each reflecting
    their own last deploy moment.
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

    main_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    main_pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    main_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    client = relationship("Client")
    test_recorder = relationship("User", foreign_keys=[test_recorded_by])
    test_deployment_request = relationship("DeploymentRequest", foreign_keys=[test_deployment_request_id])
    live_recorder = relationship("User", foreign_keys=[live_recorded_by])
    live_deployment_request = relationship("DeploymentRequest", foreign_keys=[live_deployment_request_id])
