"""add client_system_urls, drop client mes url columns

Revision ID: 794fd9e979da
Revises: 1e089cafea76
Create Date: 2026-09-03 15:55:52.088628

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '794fd9e979da'
down_revision: Union[str, Sequence[str], None] = '1e089cafea76'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Reuses the 'deploymentenvironment' Postgres enum type, already created for
# deployment_requests.environment (8cc094ec27e9) — create_type=False so create_table
# below doesn't try (and fail) to CREATE TYPE a second time. The dialect-specific
# postgresql.ENUM (not the generic sa.Enum) is what actually honors create_type=False
# on CREATE TABLE's own DDL, per SQLAlchemy's Postgres ENUM docs.
deployment_environment = postgresql.ENUM("test", "live", name="deploymentenvironment", create_type=False)

clients = sa.table(
    "clients", sa.column("id", sa.Integer), sa.column("mes_test_url", sa.String), sa.column("mes_live_url", sa.String)
)
client_system_urls = sa.table(
    "client_system_urls",
    sa.column("client_id", sa.Integer),
    sa.column("environment", deployment_environment),
    sa.column("label", sa.String),
    sa.column("url", sa.String),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "client_system_urls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("environment", deployment_environment, nullable=False),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Carry forward any URLs already entered under the old single-URL-per-system
    # columns as the first row of the new one-to-many table, before those columns are
    # dropped below — a client with both set gets two rows (one test, one live), a
    # client with neither is skipped entirely.
    bind = op.get_bind()
    rows = bind.execute(sa.select(clients.c.id, clients.c.mes_test_url, clients.c.mes_live_url)).fetchall()
    to_insert = []
    for client_id, test_url, live_url in rows:
        if test_url:
            to_insert.append({"client_id": client_id, "environment": "test", "label": None, "url": test_url})
        if live_url:
            to_insert.append({"client_id": client_id, "environment": "live", "label": None, "url": live_url})
    if to_insert:
        bind.execute(client_system_urls.insert(), to_insert)

    with op.batch_alter_table("clients", schema=None) as batch_op:
        batch_op.drop_column("mes_live_url")
        batch_op.drop_column("mes_test_url")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("clients", schema=None) as batch_op:
        batch_op.add_column(sa.Column("mes_test_url", sa.VARCHAR(length=500), autoincrement=False, nullable=True))
        batch_op.add_column(sa.Column("mes_live_url", sa.VARCHAR(length=500), autoincrement=False, nullable=True))

    bind = op.get_bind()
    rows = bind.execute(
        sa.select(client_system_urls.c.client_id, client_system_urls.c.environment, client_system_urls.c.url)
    ).fetchall()
    # Downgrade is lossy by nature here (multiple URLs per client+system collapse back
    # to one column) — last-one-wins per client+system, same as any other "widen then
    # narrow" schema downgrade.
    for client_id, environment, url in rows:
        column = clients.c.mes_test_url if environment == "test" else clients.c.mes_live_url
        bind.execute(clients.update().where(clients.c.id == client_id).values({column.name: url}))

    op.drop_table("client_system_urls")
