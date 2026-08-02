from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DeployableTask(Base):
    """Mirrors one currently-PLANNED "Deployment Test system"/"Deployment Live System"
    operation from /get-orders.

    Keyed by the CRM's own operation id (like Team.id — see that model for the same
    reasoning). See task_source.py's DeployableTaskInfo/list_deployable_tasks() for how
    this is extracted, and sync.py's sync_deployable_tasks() for the upsert (project_plan.md,
    Section 12). Flat "currently planned deploy operations" list — no readiness-gate concept.
    """

    __tablename__ = "deployable_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)  # CRM's operation id
    # task_id (custom_id) is NOT guaranteed unique across orders — two distinct orders
    # can share the same order number. order_id (the CRM's own internal order id) is
    # what actually disambiguates them; see the DeployableTaskInfo docstring for why.
    order_id: Mapped[int] = mapped_column(BigInteger, index=True)
    task_id: Mapped[str] = mapped_column(String(255), index=True)  # order's custom_id, e.g. "PR-03045"
    order_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    item_custom_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    item_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pos_id: Mapped[str] = mapped_column(String(10))  # the CRM's raw pos code, e.g. "0040"/"0060"
    target: Mapped[str] = mapped_column(String(10))  # "test" or "live" — derived from pos_id
    target_status: Mapped[str] = mapped_column(String(50))
    # Not a DB-level ForeignKey to User, deliberately — same reasoning as
    # User.machine_group_id: this is the CRM's raw custom_id, resolved at query time
    # rather than enforced, so a dangling reference never breaks the sync.
    assigned_developer_custom_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assigned_developer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
