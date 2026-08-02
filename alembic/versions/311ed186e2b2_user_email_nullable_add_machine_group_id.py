"""user email nullable, add machine_group_id

Revision ID: 311ed186e2b2
Revises: 57c0dcdd667b
Create Date: 2026-07-28 14:08:03.719981

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '311ed186e2b2'
down_revision: Union[str, Sequence[str], None] = '57c0dcdd667b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('machine_group_id', sa.Integer(), nullable=True))
    # batch_alter_table: SQLite can't ALTER COLUMN directly, so this needs the
    # copy-and-swap approach batch mode provides. Harmless no-op wrapper on Postgres.
    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column('email', existing_type=sa.VARCHAR(length=255), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column('email', existing_type=sa.VARCHAR(length=255), nullable=False)
    op.drop_column('users', 'machine_group_id')
