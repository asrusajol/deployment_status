"""add leader_user_id to teams

Revision ID: 4c8ecdac1eb0
Revises: daf5992b3642
Create Date: 2026-07-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4c8ecdac1eb0'
down_revision: Union[str, Sequence[str], None] = 'daf5992b3642'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('teams', schema=None) as batch_op:
        batch_op.add_column(sa.Column('leader_user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_teams_leader_user_id_users', 'users', ['leader_user_id'], ['id']
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('teams', schema=None) as batch_op:
        batch_op.drop_constraint('fk_teams_leader_user_id_users', type_='foreignkey')
        batch_op.drop_column('leader_user_id')
