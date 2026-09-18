"""add request_returns and the returned/withdrawn statuses

Revision ID: b7c3d9e1f2a4
Revises: c9d8e7f6a5b4
Create Date: 2026-09-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7c3d9e1f2a4"
down_revision: Union[str, Sequence[str], None] = "c9d8e7f6a5b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres refuses ALTER TYPE ... ADD VALUE inside a transaction block, and
    # Alembic wraps migrations in one by default — hence the autocommit block.
    # Getting this wrong fails at deploy time and never in CI: the test suite
    # builds its schema from Base.metadata and never runs migrations.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE requeststatus ADD VALUE IF NOT EXISTS 'returned'")
        op.execute("ALTER TYPE requeststatus ADD VALUE IF NOT EXISTS 'withdrawn'")

    op.create_table(
        "request_returns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("returned_by", sa.Integer(), nullable=False),
        sa.Column("returned_at", sa.DateTime(), nullable=False),
        # Generic sa.Enum ignores create_type=False (it's a postgresql-dialect-only
        # kwarg) and tries to re-issue CREATE TYPE requeststatus AS ENUM (), which
        # blows up with "type already exists" on a real deploy. It passed CI and a
        # single fresh-DB "upgrade head" because SQLAlchemy caches "already
        # created" state within one process and skips the CREATE there — the
        # failure only shows up when this revision runs in its own process, which
        # is every real deploy (upgrading from the previous head) and never a
        # from-scratch test run. The test suite can't catch this either: it
        # builds its schema from Base.metadata.create_all and never runs
        # migrations. Use postgresql.ENUM, which actually honours create_type.
        sa.Column(
            "returned_from",
            postgresql.ENUM(name="requeststatus", create_type=False),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["request_id"], ["deployment_requests.id"]),
        sa.ForeignKeyConstraint(["returned_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_request_returns_request_id", "request_returns", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_request_returns_request_id", table_name="request_returns")
    op.drop_table("request_returns")
    # The enum values are left in place: Postgres cannot drop one cleanly, and a
    # spare unused value is harmless.
