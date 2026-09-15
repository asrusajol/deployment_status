"""drop main_version/main_pr_number/main_updated_at from client_version_status

Main Version is now a live read of bitbucket_main_branch_status at render
time (app.services.release_tracker.current_main_branch_status()), not a
per-client snapshot taken at deploy time — see
docs/superpowers/specs/2026-08-27-release-tracker-redesign.md's Main
Version section, superseded by this fix.

Revision ID: a2b3c4d5e6f7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('client_version_status', 'main_version')
    op.drop_column('client_version_status', 'main_pr_number')
    op.drop_column('client_version_status', 'main_updated_at')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('client_version_status', sa.Column('main_version', sa.String(length=100), nullable=True))
    op.add_column('client_version_status', sa.Column('main_pr_number', sa.Integer(), nullable=True))
    op.add_column('client_version_status', sa.Column('main_updated_at', sa.DateTime(), nullable=True))
