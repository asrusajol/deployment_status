"""add approve-all-requests switch to users

Defaults off: straight after deploy nobody but admins and each requester's own team
lead can approve, until an admin grants it on /admin/users. A switch rather than a role
because a user holds exactly one role and the grantee stays a team_lead.

Revision ID: a3e8d5c1f7b2
Revises: b9d3f6a2e8c4
Create Date: 2026-10-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3e8d5c1f7b2"
down_revision: Union[str, Sequence[str], None] = "b9d3f6a2e8c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("can_approve_all_requests", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "can_approve_all_requests")
