"""add module_name to deployment_requests

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('deployment_requests', schema=None) as batch_op:
        batch_op.add_column(sa.Column('module_name', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('deployment_requests', schema=None) as batch_op:
        batch_op.drop_column('module_name')
