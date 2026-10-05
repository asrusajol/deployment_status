"""two-stage checklists: when each term is due, and ticks saved per attempt

c2e8f5a1d6b7 is already applied on live, so this is a forward migration on top of it
rather than an edit to it.

- checklist_items.due: before_start (gates Start Deployment, as every term did until
  now) or before_complete (gates Mark Deployed). Existing terms stay before_start so
  live behaves as before, except the one seeded "After restoration…" term, if it is
  still exactly as seeded — it can only be done once the restore has run.
- checklist_confirmations.round: the attempt a tick belongs to (how many times the
  request had been returned when it was ticked). Backfilled from request_returns, so
  every existing tick lands in the attempt it was made for and requests already in
  progress keep counting their ticks.
- one tick per (request, term, attempt): ticks are now saved one at a time and a
  re-tick updates the row instead of adding another.

Revision ID: b9d3f6a2e8c4
Revises: c2e8f5a1d6b7
Create Date: 2026-10-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9d3f6a2e8c4"
down_revision: Union[str, Sequence[str], None] = "c2e8f5a1d6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SEEDED_AFTER_RESTORE_TERM = (
    "After restoration, remove email settings from the settings table and the web UI (basevisu module)"
)
UNIQUE_TICK = "uq_checklist_confirmations_request_item_round"


def upgrade() -> None:
    op.add_column(
        "checklist_items",
        sa.Column("due", sa.String(16), nullable=False, server_default="before_start"),
    )
    # Only the untouched seed row (created_by is NULL for seeded terms): a term an admin
    # created or reworded keeps today's behaviour until an admin changes it in the UI.
    op.execute(
        sa.text(
            "UPDATE checklist_items SET due = 'before_complete' "
            "WHERE created_by IS NULL AND label = :label"
        ).bindparams(label=SEEDED_AFTER_RESTORE_TERM)
    )

    op.add_column(
        "checklist_confirmations",
        sa.Column("round", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE checklist_confirmations c SET round = ("
        " SELECT count(*) FROM request_returns r"
        " WHERE r.request_id = c.request_id AND r.returned_at < c.confirmed_at"
        ")"
    )
    op.create_unique_constraint(UNIQUE_TICK, "checklist_confirmations", ["request_id", "checklist_item_id", "round"])


def downgrade() -> None:
    op.drop_constraint(UNIQUE_TICK, "checklist_confirmations", type_="unique")
    op.drop_column("checklist_confirmations", "round")
    op.drop_column("checklist_items", "due")
