"""replace client_version_records with client_version_status

Revision ID: e1f2a3b4c5d6
Revises: d4e5f6a7b8c9
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # 1. New one-row-per-client table.
    op.create_table(
        'client_version_status',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('test_current_version', sa.String(length=100), nullable=True),
        sa.Column('test_previous_version', sa.String(length=100), nullable=True),
        sa.Column('test_updated_at', sa.DateTime(), nullable=True),
        sa.Column('test_recorded_by', sa.Integer(), nullable=True),
        sa.Column('test_deployment_request_id', sa.Integer(), nullable=True),
        sa.Column('live_current_version', sa.String(length=100), nullable=True),
        sa.Column('live_previous_version', sa.String(length=100), nullable=True),
        sa.Column('live_updated_at', sa.DateTime(), nullable=True),
        sa.Column('live_recorded_by', sa.Integer(), nullable=True),
        sa.Column('live_deployment_request_id', sa.Integer(), nullable=True),
        sa.Column('main_version', sa.String(length=100), nullable=True),
        sa.Column('main_pr_number', sa.Integer(), nullable=True),
        sa.Column('main_updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.ForeignKeyConstraint(['test_recorded_by'], ['users.id']),
        sa.ForeignKeyConstraint(['test_deployment_request_id'], ['deployment_requests.id']),
        sa.ForeignKeyConstraint(['live_recorded_by'], ['users.id']),
        sa.ForeignKeyConstraint(['live_deployment_request_id'], ['deployment_requests.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('client_id', name='uq_client_version_status_client_id'),
    )

    # 2. version_changed_at on the cache table — backfill to last_synced_at
    # as the best available approximation (the exact moment the current
    # value first appeared isn't recoverable from what v1 stored).
    op.add_column('bitbucket_main_branch_status', sa.Column('version_changed_at', sa.DateTime(), nullable=True))
    op.execute(
        "UPDATE bitbucket_main_branch_status SET version_changed_at = last_synced_at WHERE version IS NOT NULL"
    )

    # 3. Fold the real history in client_version_records forward into the
    # new wide table, one client at a time, before dropping it. Uses plain
    # SQLAlchemy Core table objects (not the ORM models, which won't exist
    # for the old table after this migration) — this is how this repo's
    # existing migrations already do cross-table work in Alembic.
    old = sa.table(
        'client_version_records',
        sa.column('client_id', sa.Integer),
        sa.column('environment', sa.String),
        sa.column('current_version', sa.String),
        sa.column('main_version', sa.String),
        sa.column('main_pr_number', sa.Integer),
        sa.column('deployment_request_id', sa.Integer),
        sa.column('recorded_by', sa.Integer),
        sa.column('created_at', sa.DateTime),
        sa.column('updated_at', sa.DateTime),
    )
    new = sa.table(
        'client_version_status',
        sa.column('client_id', sa.Integer),
        sa.column('test_current_version', sa.String),
        sa.column('test_previous_version', sa.String),
        sa.column('test_updated_at', sa.DateTime),
        sa.column('test_recorded_by', sa.Integer),
        sa.column('test_deployment_request_id', sa.Integer),
        sa.column('live_current_version', sa.String),
        sa.column('live_previous_version', sa.String),
        sa.column('live_updated_at', sa.DateTime),
        sa.column('live_recorded_by', sa.Integer),
        sa.column('live_deployment_request_id', sa.Integer),
        sa.column('main_version', sa.String),
        sa.column('main_pr_number', sa.Integer),
        sa.column('main_updated_at', sa.DateTime),
    )

    cached_version_changed_at = bind.execute(
        sa.text("SELECT version_changed_at FROM bitbucket_main_branch_status WHERE id = 1")
    ).scalar()

    client_ids = [
        row[0] for row in bind.execute(sa.text("SELECT DISTINCT client_id FROM client_version_records"))
    ]
    for client_id in client_ids:
        values = {'client_id': client_id}
        latest_overall_updated_at = None
        latest_overall_main_version = None
        latest_overall_main_pr = None

        for env, prefix in (('test', 'test'), ('live', 'live')):
            records = bind.execute(
                sa.select(old)
                .where(old.c.client_id == client_id, old.c.environment == env)
                .order_by(old.c.created_at)
            ).fetchall()
            if not records:
                continue
            last = records[-1]
            values[f'{prefix}_current_version'] = last.current_version
            values[f'{prefix}_previous_version'] = records[-2].current_version if len(records) > 1 else None
            values[f'{prefix}_updated_at'] = last.updated_at
            values[f'{prefix}_recorded_by'] = last.recorded_by
            values[f'{prefix}_deployment_request_id'] = last.deployment_request_id
            if latest_overall_updated_at is None or last.updated_at > latest_overall_updated_at:
                latest_overall_updated_at = last.updated_at
                latest_overall_main_version = last.main_version
                latest_overall_main_pr = last.main_pr_number

        values['main_version'] = latest_overall_main_version
        values['main_pr_number'] = latest_overall_main_pr
        values['main_updated_at'] = cached_version_changed_at
        bind.execute(sa.insert(new).values(**values))

    # 4. Drop the old history table — its real data has been folded forward
    # into client_version_status above.
    op.drop_table('client_version_records')


def downgrade() -> None:
    """Downgrade schema.

    NOTE: this downgrade recreates client_version_records EMPTY — the fold-
    forward in upgrade() is lossy by design (many-rows-to-one-row loses
    everything before "current" and "previous"), so there is no way to
    reconstruct the original history. If you need to roll back after real
    data has flowed through the new table post-migration, restore from a
    database backup instead of relying on this downgrade.
    """
    op.create_table(
        'client_version_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('environment', sa.Enum('test', 'live', name='deploymentenvironment', create_type=False), nullable=False),
        sa.Column('current_version', sa.String(length=100), nullable=False),
        sa.Column('previous_version', sa.String(length=100), nullable=True),
        sa.Column('main_version', sa.String(length=100), nullable=True),
        sa.Column('main_pr_number', sa.Integer(), nullable=True),
        sa.Column('deployment_request_id', sa.Integer(), nullable=False),
        sa.Column('recorded_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.ForeignKeyConstraint(['deployment_request_id'], ['deployment_requests.id']),
        sa.ForeignKeyConstraint(['recorded_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.drop_column('bitbucket_main_branch_status', 'version_changed_at')
    op.drop_table('client_version_status')
