from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    # Mirrored from the in-house API's daily client-roster sync (project_plan.md, Section 6).
    source_system_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Local-only toggle (admin/devops, /clients page) — never touched by sync_clients(),
    # same "manual override survives the next sync" rule as User.role/email
    # (README's "Importing data from the CRM API" section). Deactivating hides the
    # client from the New Request form's Client dropdown (app/routers/dashboard.py)
    # without deleting anything it's already linked to — existing requests/deployments
    # keep referencing the row, and re-activating brings it back.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    # Multiple MES server URLs per client, split by Test/Live system — see
    # app/models/client_system_url.py. Replaces the old single mes_test_url/
    # mes_live_url columns.
    system_urls = relationship(
        "ClientSystemUrl", back_populates="client", cascade="all, delete-orphan", order_by="ClientSystemUrl.id"
    )
