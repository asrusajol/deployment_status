"""switch approve-all-requests on for existing admins

can_approve_deployment_request() used to let every admin approve any team's request
by role alone. Now the can_approve_all_requests switch governs admins too, so without
this every admin would lose cross-team approval the moment the code deployed. Admins
made later start with it off, like anyone else.

Revision ID: c6f2a9d4e1b7
Revises: a3e8d5c1f7b2
Create Date: 2026-10-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c6f2a9d4e1b7"
down_revision: Union[str, Sequence[str], None] = "a3e8d5c1f7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE users SET can_approve_all_requests = true WHERE role = 'admin'")


def downgrade() -> None:
    # Nothing to undo: the old code ignored the switch for admins, and which admins had
    # it on before the upgrade isn't recorded.
    pass
