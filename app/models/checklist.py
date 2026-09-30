from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.deployment_request import RequestType

LABEL_MAX_LENGTH = 500


class ChecklistItem(Base):
    """One term DevOps must confirm before starting a request of `request_type`.

    One type per row, deliberately: the same wording on two types is two rows, so
    editing one type's checklist can never change another's. Never hard-deleted —
    ChecklistConfirmation rows point here — `is_active=False` retires a term. See
    docs/superpowers/specs/2026-09-30-request-checklists-design.md.
    """

    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_type: Mapped[RequestType] = mapped_column(Enum(RequestType), index=True)
    label: Mapped[str] = mapped_column(String(LABEL_MAX_LENGTH))
    position: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Null only for the terms seeded by migration c2e8f5a1d6b7.
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])


class ChecklistConfirmation(Base):
    """One term confirmed by one person when starting one request.

    Keyed on the request, not the DeploymentExecution: Return deletes the execution
    row, and the audit must outlive it. No unique constraint — a request started,
    returned and started again keeps both sets. `item_label` is a snapshot, so
    rewording a term later never changes what someone agreed to.
    """

    __tablename__ = "checklist_confirmations"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("deployment_requests.id"), index=True)
    checklist_item_id: Mapped[int] = mapped_column(ForeignKey("checklist_items.id"))
    item_label: Mapped[str] = mapped_column(String(LABEL_MAX_LENGTH))
    confirmed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime)

    request = relationship("DeploymentRequest", back_populates="checklist_confirmations")
    item = relationship("ChecklistItem")
    confirmer = relationship("User")
