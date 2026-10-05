"""add checklist tables, seed the DB dump terms

Replaces the hard-coded DB_DUMP_START_CHECKLIST (commit 7356a1e) with terms admins
manage from /management/checklists. A term can apply to several request types
(checklist_item_types, which also holds each type's order), and every confirmed term is
kept as an audit row. Seeds the four existing db_dump_restore terms so behaviour is
identical straight after upgrade.

Revision ID: c2e8f5a1d6b7
Revises: a7c1e4d2f9b3
Create Date: 2026-09-30
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c2e8f5a1d6b7"
down_revision: Union[str, Sequence[str], None] = "a7c1e4d2f9b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The type already exists (a1b2c3d4e5f6); creating it again would fail the upgrade.
request_type = postgresql.ENUM("standard", "db_dump_restore", "test_local", name="requesttype", create_type=False)

SEED_DB_DUMP_TERMS = (
    "Close cronjobs / scheduled jobs",
    "Restart workers to apply the change",
    "Check .env for anything that can trigger emails",
    "After restoration, remove email settings from the settings table and the web UI (basevisu module)",
)


def upgrade() -> None:
    op.create_table(
        "checklist_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "checklist_item_types",
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("checklist_items.id"), primary_key=True),
        sa.Column("request_type", request_type, primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
    )
    op.create_table(
        "checklist_confirmations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("deployment_requests.id"), nullable=False),
        sa.Column("checklist_item_id", sa.Integer(), sa.ForeignKey("checklist_items.id"), nullable=False),
        sa.Column("item_label", sa.String(500), nullable=False),
        sa.Column("confirmed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_checklist_confirmations_request_id", "checklist_confirmations", ["request_id"])

    items = sa.table(
        "checklist_items",
        sa.column("id", sa.Integer),
        sa.column("label", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
    )
    item_types = sa.table(
        "checklist_item_types",
        sa.column("item_id", sa.Integer),
        sa.column("request_type", request_type),
        sa.column("position", sa.Integer),
    )
    now = datetime.now(timezone.utc)
    conn = op.get_bind()
    for position, label in enumerate(SEED_DB_DUMP_TERMS, start=1):
        item_id = conn.execute(
            items.insert().values(label=label, is_active=True, created_at=now).returning(items.c.id)
        ).scalar_one()
        conn.execute(item_types.insert().values(item_id=item_id, request_type="db_dump_restore", position=position))


def downgrade() -> None:
    op.drop_index("ix_checklist_confirmations_request_id", table_name="checklist_confirmations")
    op.drop_table("checklist_confirmations")
    op.drop_table("checklist_item_types")
    op.drop_table("checklist_items")
