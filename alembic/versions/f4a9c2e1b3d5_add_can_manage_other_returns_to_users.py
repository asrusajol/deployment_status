"""add can_manage_other_returns to users

Every admin used to see every OTHER user's returned request pinned at the top of
their queue regardless of whether they had anything to do with it — reported as
pure noise for admins who manage the deploy queue but never touch returns. This
adds a per-admin checkbox (set from /admin/users) that opts a specific admin back
into seeing other people's returns; default is off, so a returned request an
admin does not own is excluded from their main queue unless they turn it on.

Purely a queue display preference — it does not touch can_edit_request()/
can_resubmit_request() in app/auth.py, which are unaffected by this column.

Revision ID: f4a9c2e1b3d5
Revises: d3a1c9e7b0f2
Create Date: 2026-09-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4a9c2e1b3d5"
down_revision: Union[str, Sequence[str], None] = "d3a1c9e7b0f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("can_manage_other_returns", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "can_manage_other_returns")
