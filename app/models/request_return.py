from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.deployment_request import RequestStatus


class RequestReturn(Base):
    """One occasion on which DevOps handed a request back to its requester.

    A table rather than columns on the request because the history is the point:
    a team lead looking at their developer's request needs to see that it came
    back three times and why each time, not just the most recent sentence. See
    docs/superpowers/specs/2026-09-17-return-request-design.md.
    """

    __tablename__ = "request_returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Not unique, unlike DeploymentExecution.request_id — a request can be returned
    # any number of times, and each one is kept.
    request_id: Mapped[int] = mapped_column(ForeignKey("deployment_requests.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    returned_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    returned_at: Mapped[datetime] = mapped_column(DateTime)
    # `approved` or `in_progress`. Returning from in_progress deletes the
    # DeploymentExecution row (see the return route), so without this column the
    # fact that someone had already claimed and started the deployment would be
    # lost entirely.
    returned_from: Mapped[RequestStatus] = mapped_column(Enum(RequestStatus))

    request = relationship("DeploymentRequest", back_populates="returns")
    returner = relationship("User")
