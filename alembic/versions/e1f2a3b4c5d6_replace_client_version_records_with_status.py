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


def _fold_client_history(records_by_env: dict, cached_version, cached_version_changed_at) -> dict:
    """Pure fold-forward logic for one client: given each environment's ordered
    list of old client_version_records rows (oldest first), produce the dict of
    column values for that client's single client_version_status row.

    Extracted out of upgrade() so it's testable without a live Alembic/DB
    context — see tests/test_migration_e1f2a3b4c5d6.py.

    `records_by_env` maps 'test'/'live' -> list of rows (each needs
    .current_version, .main_version, .main_pr_number, .recorded_by,
    .deployment_request_id, .created_at, .updated_at attributes; a plain
    SQLAlchemy Core RowMapping/Row satisfies this).
    """
    values = {}
    latest_overall_created_at = None
    latest_overall_main_version = None
    latest_overall_main_pr = None

    for env in ('test', 'live'):
        records = records_by_env.get(env) or []
        if not records:
            continue
        last = records[-1]
        values[f'{env}_current_version'] = last.current_version
        values[f'{env}_previous_version'] = records[-2].current_version if len(records) > 1 else None
        values[f'{env}_updated_at'] = last.updated_at
        values[f'{env}_recorded_by'] = last.recorded_by
        values[f'{env}_deployment_request_id'] = last.deployment_request_id
        if latest_overall_created_at is None or last.created_at > latest_overall_created_at:
            latest_overall_created_at = last.created_at
            latest_overall_main_version = last.main_version
            latest_overall_main_pr = last.main_pr_number

    values['main_version'] = latest_overall_main_version
    values['main_pr_number'] = latest_overall_main_pr
    # main_updated_at has no recoverable "changed at" timestamp per
    # client in v1's data — the only timestamp available is the cache's
    # current version_changed_at. That's only a safe stand-in for a
    # client whose folded-forward main_version still matches what's
    # currently cached; if the client's last-recorded snapshot is stale
    # (main branch has moved on since), pairing it with "now" would be
    # a plausible-but-wrong timestamp. Leave it NULL in that case,
    # consistent with how test/live_previous_version are left NULL
    # when unrecoverable.
    if latest_overall_main_version is not None and latest_overall_main_version == cached_version:
        values['main_updated_at'] = cached_version_changed_at
    else:
        values['main_updated_at'] = None
    return values


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

    # .first() (not .one()) — bitbucket_main_branch_status has zero rows on
    # any fresh/never-synced database (that row is only ever created by
    # sync_bitbucket_main_status(), never by a migration or app-startup
    # path), so .one() would raise NoResultFound and hard-abort the entire
    # upgrade on an empty schema. cached_version/cached_version_changed_at
    # are both None in that case, which _fold_client_history() already
    # handles correctly (the `latest_overall_main_version == cached_version`
    # comparison below is only ever True when a client's own folded value is
    # also None, and the `latest_overall_main_version is not None` guard
    # prevents that from matching).
    cached_row = bind.execute(
        sa.text("SELECT version, version_changed_at FROM bitbucket_main_branch_status WHERE id = 1")
    ).first()
    cached_version, cached_version_changed_at = cached_row or (None, None)

    client_ids = [
        row[0] for row in bind.execute(sa.text("SELECT DISTINCT client_id FROM client_version_records"))
    ]
    for client_id in client_ids:
        records_by_env = {
            env: bind.execute(
                sa.select(old)
                .where(old.c.client_id == client_id, old.c.environment == env)
                .order_by(old.c.created_at)
            ).fetchall()
            for env in ('test', 'live')
        }
        values = {'client_id': client_id}
        values.update(_fold_client_history(records_by_env, cached_version, cached_version_changed_at))
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
        # Plain String here, then cast in place below — a generic sa.Enum(...,
        # create_type=False) does NOT suppress CREATE TYPE (only
        # sqlalchemy.dialects.postgresql.ENUM respects that kwarg), and
        # deploymentenvironment already exists (shared with
        # deployment_requests.environment), so creating the column directly
        # as that enum type raises DuplicateObject on Postgres. Same
        # two-step pattern d4e5f6a7b8c9's upgrade() already uses for this.
        sa.Column('environment', sa.String(length=10), nullable=False),
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
    # Cast the environment column to the deploymentenvironment enum type
    op.execute('ALTER TABLE client_version_records ALTER COLUMN environment TYPE deploymentenvironment USING environment::deploymentenvironment')
    op.drop_column('bitbucket_main_branch_status', 'version_changed_at')
    op.drop_table('client_version_status')
