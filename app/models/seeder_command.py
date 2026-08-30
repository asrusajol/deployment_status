# app/models/seeder_command.py
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SeederCommand(Base):
    """One saved seeder/artisan command per client — the Seeder Collection
    tab (devops/admin only). See
    docs/superpowers/specs/2026-08-30-seeder-collection-design.md.

    Deliberately one row per client, not per environment: the same command
    serves Test and Live for a given client (confirmed with the user), so
    there's no environment column here, unlike ClientVersionStatus.
    """

    __tablename__ = "seeder_commands"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), unique=True)

    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    command: Mapped[str] = mapped_column(Text)

    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = relationship("Client")
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])
