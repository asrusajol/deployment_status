"""Service layer for the Seeder Collection tab (devops/admin only). See
docs/superpowers/specs/2026-08-30-seeder-collection-design.md.
"""

from sqlalchemy.orm import Session, joinedload

from app.models.client import Client
from app.models.seeder_command import SeederCommand


class ClientAlreadyHasSeederCommandError(Exception):
    """Raised by create_seeder_command() when the client already has a row —
    the DB's unique constraint on client_id is the hard backstop, this is
    the friendlier, catchable check ahead of it."""


def seeder_collection_rows(db: Session) -> list[SeederCommand]:
    """Every saved seeder command, one row per client, ordered by client
    name — the Seeder Collection tab's primary listing."""
    return (
        db.query(SeederCommand)
        .join(Client, SeederCommand.client_id == Client.id)
        .options(joinedload(SeederCommand.client))
        .order_by(Client.name)
        .all()
    )


def clients_without_seeder_command(db: Session) -> list[Client]:
    """Clients that don't already have a SeederCommand row — populates the
    "add new" form's client picker, since it's one row per client."""
    taken_client_ids = db.query(SeederCommand.client_id)
    return db.query(Client).filter(Client.id.notin_(taken_client_ids)).order_by(Client.name).all()


def create_seeder_command(
    db: Session, *, client_id: int, host: str | None, title: str, command: str, created_by: int
) -> SeederCommand:
    if db.query(SeederCommand).filter_by(client_id=client_id).first() is not None:
        raise ClientAlreadyHasSeederCommandError(f"Client {client_id} already has a saved seeder command")
    row = SeederCommand(
        client_id=client_id, host=host, title=title, command=command,
        created_by=created_by, updated_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def update_seeder_command(
    db: Session, row: SeederCommand, *, host: str | None, title: str, command: str, updated_by: int
) -> SeederCommand:
    row.host = host
    row.title = title
    row.command = command
    row.updated_by = updated_by
    db.flush()
    return row


def delete_seeder_command(db: Session, row: SeederCommand) -> None:
    db.delete(row)
    db.flush()
