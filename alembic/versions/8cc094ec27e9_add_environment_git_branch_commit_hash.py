"""add environment, git_branch, commit_hash to deployment_requests

Revision ID: 8cc094ec27e9
Revises: 4c8ecdac1eb0
Create Date: 2026-07-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8cc094ec27e9'
down_revision: Union[str, Sequence[str], None] = '4c8ecdac1eb0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


deployment_environment = sa.Enum('test', 'live', name='deploymentenvironment')


def upgrade() -> None:
    """Upgrade schema."""
    deployment_environment.create(op.get_bind(), checkfirst=True)
    with op.batch_alter_table('deployment_requests', schema=None) as batch_op:
        batch_op.add_column(sa.Column('environment', deployment_environment, nullable=True))
        batch_op.add_column(sa.Column('git_branch', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('commit_hash', sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('deployment_requests', schema=None) as batch_op:
        batch_op.drop_column('commit_hash')
        batch_op.drop_column('git_branch')
        batch_op.drop_column('environment')
    deployment_environment.drop(op.get_bind(), checkfirst=True)
