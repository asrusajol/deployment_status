from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.deployment_request import DeploymentEnvironment


class ClientSystemUrl(Base):
    """One MES server URL for one client's Test or Live system. A client can have
    several per system (e.g. multiple production lines each with their own server) —
    see the /clients page (app/routers/clients.py). Replaces Client.mes_test_url/
    mes_live_url, which only ever supported one URL per system.

    Reuses DeploymentEnvironment (test/live) rather than defining a second enum for the
    same two values — this is the same Test/Live split the rest of the app already uses
    (DeploymentRequest.environment, ClientVersionStatus's test_*/live_* columns)."""

    __tablename__ = "client_system_urls"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    environment: Mapped[DeploymentEnvironment] = mapped_column(Enum(DeploymentEnvironment))
    # Optional — lets admin/devops tell apart multiple URLs on the same client+system
    # (e.g. "Line 1" / "Line 2"). A single unlabeled URL is the common case and reads
    # fine on its own in the request form's dropdown (app/templates/request_form.html).
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    url: Mapped[str] = mapped_column(String(500))

    client = relationship("Client", back_populates="system_urls")
