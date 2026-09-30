"""add management-access and checklist-audit switches to users

Both default off: straight after deploy only admins can open the Management tab or
see checklist audits, until an admin grants either on /admin/users. Switches rather
than a role because a user holds exactly one role.

Revision ID: a7c1e4d2f9b3
Revises: f4a9c2e1b3d5
Create Date: 2026-09-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c1e4d2f9b3"
down_revision: Union[str, Sequence[str], None] = "f4a9c2e1b3d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("can_access_management", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("can_view_checklist_audit", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "can_view_checklist_audit")
    op.drop_column("users", "can_access_management")
