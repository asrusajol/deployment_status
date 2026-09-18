"""add withdrawal columns to deployment_requests

Revision ID: d3a1c9e7b0f2
Revises: b7c3d9e1f2a4
Create Date: 2026-09-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3a1c9e7b0f2"
down_revision: Union[str, Sequence[str], None] = "b7c3d9e1f2a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A new migration, not an edit of b7c3d9e1f2a4 (which added request_returns) —
    # that revision is already applied against the local dev database, so amending
    # it in place would leave that database inconsistent with the file.
    #
    # Columns, not a request_withdrawals table: unlike a return, a withdrawal is
    # terminal and happens at most once per request, so there is nothing for a
    # separate table's row-per-occasion shape to buy here (see the comment on
    # DeploymentRequest.withdrawn_at). All three are nullable — every existing row
    # predates withdrawal and has none of this.
    with op.batch_alter_table("deployment_requests", schema=None) as batch_op:
        batch_op.add_column(sa.Column("withdrawn_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("withdrawn_by", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("withdrawn_note", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_deployment_requests_withdrawn_by_users",
            "users",
            ["withdrawn_by"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("deployment_requests", schema=None) as batch_op:
        batch_op.drop_constraint("fk_deployment_requests_withdrawn_by_users", type_="foreignkey")
        batch_op.drop_column("withdrawn_note")
        batch_op.drop_column("withdrawn_by")
        batch_op.drop_column("withdrawn_at")
