from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BitbucketMainBranchStatus(Base):
    """A single-row cache (id is always 1) of the shopfloor-suite repo's main
    branch release.json version + latest merged PR number, refreshed every 5
    minutes by `python -m app.cli sync-bitbucket-main` — see
    docs/superpowers/specs/2026-08-27-release-tracker-design.md. NOT a history
    table (contrast ClientVersionRecord) — each sync overwrites this same row in
    place.
    """

    __tablename__ = "bitbucket_main_branch_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
