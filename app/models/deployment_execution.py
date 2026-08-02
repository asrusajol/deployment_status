import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ExecutionStatus(str, enum.Enum):
    claimed = "claimed"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    rolled_back = "rolled_back"


class DeploymentExecution(Base):
    __tablename__ = "deployment_executions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique=True is the DB-level enforcement of project_plan.md Section 7/5: a request can only
    # ever be claimed once, so a second executor's claim attempt fails on insert rather than racing
    # in application code. A retried deployment after failure should open a new DeploymentRequest,
    # not reuse this row.
    request_id: Mapped[int] = mapped_column(ForeignKey("deployment_requests.id"), unique=True)
    executed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    claimed_at: Mapped[datetime] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[ExecutionStatus] = mapped_column(Enum(ExecutionStatus), default=ExecutionStatus.claimed)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    request = relationship("DeploymentRequest", back_populates="executions")
    executor = relationship("User")
