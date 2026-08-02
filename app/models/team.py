from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Team(Base):
    """Mirrors the CRM's MachineGroups entity (project_plan.md, Section 6)."""

    __tablename__ = "teams"

    # Deliberately the CRM's own MachineGroups.id, not a locally-generated value.
    # Machines.machine_group_id — already mirrored onto users.machine_group_id — references
    # this exact numeric id in the source system, so reusing it here lets the two tables
    # join directly with no extra lookup column.
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    source_system_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)  # e.g. "MG-00001"
    name: Mapped[str] = mapped_column(String(255))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Unlike machine_group_id (a raw CRM id, no DB-level FK — see app/models/user.py),
    # this points at our own locally-controlled users.id, so a real FK is safe here.
    # Set by sync_team_leads() (app/services/sync.py): the user in the CRM's "Team Leads"
    # userGroup (custom_id UG-00002) whose own machine_group_id resolves to this team.
    leader_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    leader = relationship("User", foreign_keys=[leader_user_id])
