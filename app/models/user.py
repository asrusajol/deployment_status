import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserRole(str, enum.Enum):
    developer = "developer"
    team_lead = "team_lead"
    devops = "devops"
    admin = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    # The CRM/Machines feed (project_plan.md, Section 6) doesn't carry an email address or
    # username — both nullable, keyed by source_system_id instead. Backfilled from the
    # separate CRM Users feed via sync_team_leads()/sync_user_contacts(); still null for
    # everyone else until/unless another feed can provide them.
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole))

    # Login access is opt-in per user, granted by an admin (app/routers/admin.py) — not
    # every CRM-synced employee automatically gets an account. NULL means "can't log in
    # yet." Whenever an admin (re)sets this — including the very first, via the
    # `create-admin` CLI command — must_change_password is forced True, so a temporary
    # password an admin knows is never left as someone's permanent one.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    # Mirrored from the in-house API's daily user-roster sync (project_plan.md, Section 6):
    # employees are represented as "Machine" records in that system, hence machine_group_id.
    source_system_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    # Not a DB-level ForeignKey deliberately: this is the CRM's raw MachineGroups.id, and
    # the CRM's own referential integrity is outside our control — a team could be renamed,
    # deactivated, or (rarely) deleted between a user's last sync and a team's, which would
    # turn a hard FK into an outage. `team` below resolves it at the ORM level instead, so a
    # dangling id just means `user.team is None`, not a crashed sync.
    machine_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    team = relationship(
        "Team",
        primaryjoin="foreign(User.machine_group_id) == Team.id",
        viewonly=True,
        uselist=False,
    )
