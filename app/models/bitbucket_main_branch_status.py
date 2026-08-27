# app/models/bitbucket_main_branch_status.py
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BitbucketMainBranchStatus(Base):
    """A single-row cache (id is always 1) of the shopfloor-suite repo's main
    branch release.json version + latest merged PR number, refreshed every 5
    minutes by `python -m app.cli sync-bitbucket-main` — see
    docs/superpowers/specs/2026-08-27-release-tracker-redesign.md. NOT a
    history table — each sync overwrites this same row in place, and it's
    the only table that sync ever writes to (client_version_status rows
    snapshot from here at each client's own deploy time, but the sync job
    itself never touches client_version_status).
    """

    __tablename__ = "bitbucket_main_branch_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Bumped on every successful sync regardless of whether the value
    # changed — ops/liveness diagnostic only ("is the cron job still
    # running"), not shown anywhere in the Release Tracker UI.
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Bumped ONLY when `version` actually differs from what was already
    # stored — this is what client rows snapshot into their own
    # main_updated_at at deploy time, so it answers "main has been at this
    # version since Y", not "when did the sync job last run".
    version_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
