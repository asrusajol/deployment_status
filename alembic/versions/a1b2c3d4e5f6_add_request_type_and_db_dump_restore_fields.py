"""add request_type and db dump/restore fields to deployment_requests

Revision ID: a1b2c3d4e5f6
Revises: 339b1dfe4e60
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '339b1dfe4e60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


request_type = sa.Enum('standard', 'db_dump_restore', 'test_local', name='requesttype')


def upgrade() -> None:
    """Upgrade schema."""
    request_type.create(op.get_bind(), checkfirst=True)
    with op.batch_alter_table('deployment_requests', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('request_type', request_type, nullable=False, server_default='standard')
        )
        batch_op.add_column(sa.Column('dump_source', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('restore_source', sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column(
                'share_with_requestor', sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('deployment_requests', schema=None) as batch_op:
        batch_op.drop_column('share_with_requestor')
        batch_op.drop_column('restore_source')
        batch_op.drop_column('dump_source')
        batch_op.drop_column('request_type')
    request_type.drop(op.get_bind(), checkfirst=True)
