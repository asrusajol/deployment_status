"""merge seeder-collection and master heads

Revision ID: ede1c9608c88
Revises: 5cae9dca86e8, f7a8b9c0d1e2
Create Date: 2026-09-15 14:23:16.891958

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ede1c9608c88'
down_revision: Union[str, Sequence[str], None] = ('5cae9dca86e8', 'f7a8b9c0d1e2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
