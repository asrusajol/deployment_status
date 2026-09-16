"""drop host from seeder_commands

The servers a seeder runs against are the client's own Test/Live URLs, already
stored in client_system_urls and editable on the /clients page. The Seeder
Collection cards now read those live, so a per-row host string was a second,
silently-stale copy of the same fact — see
docs/superpowers/specs/2026-08-30-seeder-collection-design.md.

Revision ID: c9d8e7f6a5b4
Revises: ede1c9608c88
Create Date: 2026-09-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d8e7f6a5b4"
down_revision: Union[str, Sequence[str], None] = "ede1c9608c88"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("seeder_commands", "host")


def downgrade() -> None:
    # Nullable on the way back: the values are gone, and the client's own URLs
    # are the source of truth now anyway.
    op.add_column("seeder_commands", sa.Column("host", sa.String(length=255), nullable=True))
