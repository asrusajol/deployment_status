from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.deployment_request import RequestType

LABEL_MAX_LENGTH = 500


class ChecklistItem(Base):
    """One term DevOps must confirm before starting a request of any type it is assigned
    to (ChecklistItemType). There is one shared order across all terms (`position`);
    each type's Start pop-up shows its terms in that order. Never hard-deleted —
    ChecklistConfirmation rows point here — `is_active=False` retires a term everywhere.
    See docs/superpowers/specs/2026-09-30-request-checklists-design.md.
    """

    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
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
    # delete-orphan: unassigning a type is removing its row from this list. Nothing else
    # points at an assignment, so dropping it loses no history.
    types = relationship(
        "ChecklistItemType", back_populates="item", cascade="all, delete-orphan",
        order_by="ChecklistItemType.request_type",
    )


class ChecklistItemType(Base):
    """Assigns a checklist term to one request type."""

    __tablename__ = "checklist_item_types"

    item_id: Mapped[int] = mapped_column(ForeignKey("checklist_items.id"), primary_key=True)
    request_type: Mapped[RequestType] = mapped_column(Enum(RequestType), primary_key=True)

    item = relationship("ChecklistItem", back_populates="types")


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
