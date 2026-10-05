from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.deployment_request import RequestType

LABEL_MAX_LENGTH = 500

# When a term is due. A plain string column (not a Postgres enum) so adding a stage later
# needs no ALTER TYPE; validated in app/routers/management.py.
DUE_BEFORE_START = "before_start"
DUE_BEFORE_COMPLETE = "before_complete"
DUE_LABELS = {DUE_BEFORE_START: "Before start", DUE_BEFORE_COMPLETE: "Before Mark Deployed"}


class ChecklistItem(Base):
    """One term DevOps must confirm before starting a request of any type it is assigned
    to (ChecklistItemType). A term can apply to several types and each type orders its
    own list, so position lives on the assignment, not here. Never hard-deleted —
    ChecklistConfirmation rows point here — `is_active=False` retires a term everywhere.
    See docs/superpowers/specs/2026-09-30-request-checklists-design.md.
    """

    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(LABEL_MAX_LENGTH))
    # Before start: gates Start Deployment. Before complete: done during the work (after
    # a restore / deploy) and gates Mark Deployed instead.
    due: Mapped[str] = mapped_column(String(16), default=DUE_BEFORE_START, server_default=DUE_BEFORE_START)
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
    """Assigns a checklist term to one request type, at a place in that type's list."""

    __tablename__ = "checklist_item_types"

    item_id: Mapped[int] = mapped_column(ForeignKey("checklist_items.id"), primary_key=True)
    request_type: Mapped[RequestType] = mapped_column(Enum(RequestType), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)

    item = relationship("ChecklistItem", back_populates="types")


class ChecklistConfirmation(Base):
    """One term ticked by one person for one attempt (`round`) at a request.

    Saved the moment it's ticked, so closing the pop-up loses nothing; unticking deletes
    it while its stage is still open. Keyed on the request, not the DeploymentExecution:
    Return deletes the execution row, and the audit must outlive it. `round` is how many
    times the request had been returned when it was ticked, so each attempt is ticked
    afresh and earlier attempts stay in the audit. `item_label` is a snapshot — a tick
    only counts while it still matches the term's wording.
    """

    __tablename__ = "checklist_confirmations"
    __table_args__ = (
        UniqueConstraint("request_id", "checklist_item_id", "round", name="uq_checklist_confirmations_request_item_round"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("deployment_requests.id"), index=True)
    checklist_item_id: Mapped[int] = mapped_column(ForeignKey("checklist_items.id"))
    item_label: Mapped[str] = mapped_column(String(LABEL_MAX_LENGTH))
    round: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    confirmed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime)

    request = relationship("DeploymentRequest", back_populates="checklist_confirmations")
    item = relationship("ChecklistItem")
    confirmer = relationship("User")
