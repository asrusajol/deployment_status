"""Service layer for the Seeder Collection tab (devops/admin only). See
docs/superpowers/specs/2026-08-30-seeder-collection-design.md.
"""

from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.client import Client
from app.models.client_system_url import ClientSystemUrl
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
        # system_urls eagerly too: every card renders its client's Test/Live
        # URLs, so lazy-loading them would be one extra query per card.
        .options(joinedload(SeederCommand.client).selectinload(Client.system_urls))
        .order_by(Client.name)
        .all()
    )


def client_system_urls_for_form(db: Session) -> list[ClientSystemUrl]:
    """Every client's server URLs, for the add form to show the picked
    client's URLs without a round trip — same approach as the deployment
    request form (app/templates/request_form.html)."""
    return db.query(ClientSystemUrl).order_by(ClientSystemUrl.id).all()


def clients_without_seeder_command(db: Session) -> list[Client]:
    """Clients that don't already have a SeederCommand row — populates the
    "add new" form's client picker, since it's one row per client."""
    taken_client_ids = db.query(SeederCommand.client_id)
    return db.query(Client).filter(Client.id.notin_(taken_client_ids)).order_by(Client.name).all()


def create_seeder_command(
    db: Session, *, client_id: int, title: str, command: str, created_by: int
) -> SeederCommand:
    if db.query(SeederCommand).filter_by(client_id=client_id).first() is not None:
        raise ClientAlreadyHasSeederCommandError(f"Client {client_id} already has a saved seeder command")
    row = SeederCommand(
        client_id=client_id, title=title, command=command,
        created_by=created_by, updated_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def update_seeder_command(
    db: Session, row: SeederCommand, *, title: str, command: str, updated_by: int
) -> SeederCommand:
    row.title = title
    row.command = command
    row.updated_by = updated_by
    db.flush()
    return row


def delete_seeder_command(db: Session, row: SeederCommand) -> None:
    db.delete(row)
    db.flush()
