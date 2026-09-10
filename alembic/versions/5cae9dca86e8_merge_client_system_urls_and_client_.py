"""merge client_system_urls and client_version_status heads

Empty on purpose — this changes no schema. Two branches each added a migration off the
same parent (794fd9e979da added client_system_urls; a2b3c4d5e6f7 dropped the main-version
snapshot from client_version_status) and the merge that brought them together never
joined their lineages, so the history had two heads. `alembic upgrade head` then refused
outright ("Multiple head revisions are present"), which meant any fresh deploy — the
Docker app container included — died on startup. This revision exists solely to give the
two lineages a single descendant again.

Revision ID: 5cae9dca86e8
Revises: 794fd9e979da, a2b3c4d5e6f7
Create Date: 2026-09-10 20:32:23.098005

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5cae9dca86e8'
down_revision: Union[str, Sequence[str], None] = ('794fd9e979da', 'a2b3c4d5e6f7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
