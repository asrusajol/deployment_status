import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ApprovalDecision(str, enum.Enum):
    approved = "approved"
    rejected = "rejected"


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("deployment_requests.id"))
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[ApprovalDecision] = mapped_column(Enum(ApprovalDecision))
    decided_at: Mapped[datetime] = mapped_column(DateTime)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    request = relationship("DeploymentRequest", back_populates="approvals")
    approver = relationship("User")
