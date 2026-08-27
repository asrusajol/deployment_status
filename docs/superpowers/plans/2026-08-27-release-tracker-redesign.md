# Release Tracker Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Release Tracker's per-deploy history table with a one-row-
per-client latest-state table (Test/Live version pairs as columns, plus a
per-client snapshot of the shared Bitbucket main-branch version), matching
the user's mockup, while migrating the real, already-in-use history data
forward and dropping the old table.

**Architecture:** A new `client_version_status` table (one row per client,
unique `client_id`) replaces `client_version_records` (was one row per
deploy). `test_*`/`live_*` column pairs are updated independently by the
existing deploy-confirmation popup — a Live deploy only ever touches
`live_*`. `main_version`/`main_pr_number`/`main_updated_at` are a per-client
snapshot taken from the existing single-row `bitbucket_main_branch_status`
cache at that client's own deploy time — the periodic 5-minute sync still
only ever writes to that one cache row, never to any client row. The cache
gains a `version_changed_at` column that only advances when the fetched
version actually differs from what's stored, so a client's main-version
snapshot reflects "main has been at this version since Y", not "I happened
to sync at time Y".

**Tech Stack:** Same as before — FastAPI + Jinja2, SQLAlchemy + Alembic,
pytest with an in-memory SQLite fixture. This plan builds on infrastructure
Release Tracker v1 already established (the Bitbucket adapter/sync job, the
deploy-confirmation popup's dialog, the router/template split) — most tasks
here are *replacing* existing code, not building from nothing.

**Spec:** `docs/superpowers/specs/2026-08-27-release-tracker-redesign.md`
(supersedes the data-model/tab/popup sections of
`docs/superpowers/specs/2026-08-27-release-tracker-design.md`)

## Global Constraints

- One row per client in `client_version_status` (`client_id` unique) —
  never a second row for the same client.
- A deploy to one environment never touches the other environment's columns
  on the same row (`live_*` untouched by a Test deploy, and vice versa).
- `test_updated_at`/`live_updated_at` bump on EVERY deploy confirmation for
  that environment, even a redeploy of the identical version string
  (confirmed with the user — "always bump on deploy").
- `main_version`/`main_pr_number`/`main_updated_at` are written ONLY at a
  client's own deploy time (either environment), copied from the
  `bitbucket_main_branch_status` cache at that moment. The periodic sync job
  (`sync_bitbucket_main_status`) NEVER writes to `client_version_status` —
  confirmed with the user, "no need to update the whole clients table" on
  every poll.
- `main_updated_at` is set to the cache's `version_changed_at`, NOT to
  `datetime.now()` at deploy time — it answers "main has been at this
  version since Y", not "when did I look".
- `version_changed_at` on `bitbucket_main_branch_status` only advances when
  the newly-fetched version differs from what's already stored;
  `last_synced_at` keeps bumping every sync regardless (kept for ops
  diagnostics, not shown in the Release Tracker UI).
- `test_previous_version`/`live_previous_version` are tracked in the backend
  but NOT rendered anywhere in the Release Tracker UI (per the user: "keep
  it in backend... use it later if needed").
- Row correction (edit) is permission-checked PER COLUMN: editing
  `test_current_version` requires `current_user.id == test_recorded_by` (or
  admin); editing `live_current_version` requires
  `current_user.id == live_recorded_by` (or admin) — independently. A user
  can be allowed to fix one and not the other on the same row.
- The Release Tracker filter bar drops the System/environment filter
  entirely (every row always shows both Test and Live) — Client filter only.
- Colors: reuse existing design tokens — `--amber-dim`/`--amber` for the
  Test column block, `--violet-dim`/`--violet` for Live, `--green-dim`/
  `--green` for Main Version — matching this app's existing test/live badge
  convention, no new colors invented.
- The real data currently in `client_version_records` (a teammate's actual
  deploys) must be migrated forward into `client_version_status` before that
  table is dropped — not just discarded.

---

## File Structure

**New files:**
- `app/models/client_version_status.py` — `ClientVersionStatus` model
- `alembic/versions/<rev>_replace_client_version_records_with_status.py` —
  migration: create new table, add `version_changed_at`, migrate data
  forward, drop old table
- `tests/test_client_version_status_model.py`

**Deleted files:**
- `app/models/client_version_record.py`
- `tests/test_client_version_record_model.py`

**Modified files:**
- `app/models/__init__.py` — swap the model registration
- `app/models/bitbucket_main_branch_status.py` — add `version_changed_at`
- `app/services/sync.py` — `sync_bitbucket_main_status` sets
  `version_changed_at` only on real change
- `app/services/release_tracker.py` — rewritten for the new shape
  (`record_client_deploy`, `current_version_for`, `release_tracker_rows`,
  `clients_with_version_records`)
- `app/auth.py` — `can_edit_client_version_record` →
  `can_edit_client_version_status` (per-column)
- `app/services/export.py` — `RELEASE_TRACKER_COLUMNS` rewritten for the new
  row shape
- `app/routers/dashboard.py` — `deploy_request`/`list_requests` wired to the
  new service functions
- `app/routers/release_tracker.py` — environment filter removed, edit routes
  rewritten for per-column editing
- `app/templates/release_tracker.html` — new column layout, colored blocks
- `app/templates/release_tracker_edit.html` — two independent optional
  fields, each gated by its own permission
- `app/templates/_deployment_filter_bar.html` — System filter made
  conditional (`show_system_filter`, default `true` — Dashboard/History
  unaffected)
- `app/static/style.css` — new column-block background classes
- `tests/test_sync.py`, `tests/test_dashboard.py`,
  `tests/test_release_tracker_service.py`, `tests/test_release_tracker.py`,
  `tests/test_export.py`, `tests/test_auth.py` — updated for the new shape

---

### Task 1: Data model — `ClientVersionStatus` + migration + real-data backfill

**Files:**
- Create: `app/models/client_version_status.py`
- Create: `alembic/versions/e1f2a3b4c5d6_replace_client_version_records_with_status.py`
- Delete: `app/models/client_version_record.py`, `tests/test_client_version_record_model.py`
- Modify: `app/models/__init__.py`, `app/models/bitbucket_main_branch_status.py`
- Test: `tests/test_client_version_status_model.py`

**Interfaces:**
- Produces: `ClientVersionStatus` (fields: `id`, `client_id` [unique],
  `test_current_version`, `test_previous_version`, `test_updated_at`,
  `test_recorded_by`, `test_deployment_request_id`, `live_current_version`,
  `live_previous_version`, `live_updated_at`, `live_recorded_by`,
  `live_deployment_request_id`, `main_version`, `main_pr_number`,
  `main_updated_at`, plus relationships `client`, `test_recorder`,
  `test_deployment_request`, `live_recorder`, `live_deployment_request`).
- Produces: `BitbucketMainBranchStatus.version_changed_at` (new column).
- Consumed by every later task in this plan.

This task is the highest-risk one in the plan — it drops a table holding
real production data. Run the migration against the real dev/staging DB
(Step 8 below) and manually verify the migrated rows before moving to Task 2.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_client_version_status_model.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models on Base.metadata
from app.database import Base
from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.user import User, UserRole


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_client_version_status_round_trips(db_session):
    client = Client(id=1, name="CRM")
    user = User(id=1, name="Deployer", role=UserRole.developer)
    request = DeploymentRequest(
        id=1, request_type=RequestType.standard, client_id=1,
        environment=DeploymentEnvironment.live, status=RequestStatus.completed,
        created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    db_session.add_all([client, user, request])
    db_session.flush()

    row = ClientVersionStatus(
        client_id=1,
        test_current_version="2026.34.30", test_previous_version="2026.34.20",
        test_updated_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        test_recorded_by=1, test_deployment_request_id=1,
        live_current_version="2026.34.34", live_previous_version="2026.34.30",
        live_updated_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        live_recorded_by=1, live_deployment_request_id=1,
        main_version="2026.34.40", main_pr_number=15009,
        main_updated_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    db_session.add(row)
    db_session.commit()

    fetched = db_session.get(ClientVersionStatus, row.id)
    assert fetched.client.name == "CRM"
    assert fetched.test_current_version == "2026.34.30"
    assert fetched.live_current_version == "2026.34.34"
    assert fetched.main_version == "2026.34.40"
    assert fetched.test_recorder.name == "Deployer"
    assert fetched.live_deployment_request.id == 1


def test_client_version_status_client_id_is_unique(db_session):
    db_session.add(Client(id=1, name="CRM"))
    db_session.flush()
    db_session.add(ClientVersionStatus(client_id=1))
    db_session.commit()
    db_session.add(ClientVersionStatus(client_id=1))
    with pytest.raises(Exception):  # IntegrityError, dialect-specific
        db_session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_client_version_status_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.client_version_status'`

- [ ] **Step 3: Write the model**

```python
# app/models/client_version_status.py
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ClientVersionStatus(Base):
    """One row per client — latest-state Test/Live version tracking. See
    docs/superpowers/specs/2026-08-27-release-tracker-redesign.md. Replaces
    v1's per-deploy history table (ClientVersionRecord).

    test_*/live_* fields are written independently by
    app.services.release_tracker.record_client_deploy(): confirming a Live
    deploy only ever touches live_*, confirming a Test deploy only ever
    touches test_*. Each also carries a *_previous_version (the value it's
    overwriting) kept for potential future use — not currently rendered
    anywhere in the Release Tracker UI.

    main_version/main_pr_number/main_updated_at are a per-client SNAPSHOT,
    not a live shared value — written only when THIS client deploys (either
    environment), copied from BitbucketMainBranchStatus at that moment. The
    periodic 5-minute sync (app.services.sync.sync_bitbucket_main_status)
    never writes to this table at all — two different clients can
    legitimately show two different Main Version values, each reflecting
    their own last deploy moment.
    """

    __tablename__ = "client_version_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), unique=True)

    test_current_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    test_previous_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    test_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    test_recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    test_deployment_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("deployment_requests.id"), nullable=True
    )

    live_current_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    live_previous_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    live_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    live_recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    live_deployment_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("deployment_requests.id"), nullable=True
    )

    main_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    main_pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    main_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    client = relationship("Client")
    test_recorder = relationship("User", foreign_keys=[test_recorded_by])
    test_deployment_request = relationship("DeploymentRequest", foreign_keys=[test_deployment_request_id])
    live_recorder = relationship("User", foreign_keys=[live_recorded_by])
    live_deployment_request = relationship("DeploymentRequest", foreign_keys=[live_deployment_request_id])
```

Add `version_changed_at` to the cache model:

```python
# app/models/bitbucket_main_branch_status.py — replace the whole file
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BitbucketMainBranchStatus(Base):
    """A single-row cache (id is always 1) of the shopfloor-suite repo's main
    branch release.json version + latest merged PR number, refreshed every 5
    minutes by `python -m app.cli sync-bitbucket-main` — see
    docs/superpowers/specs/2026-08-27-release-tracker-redesign.md. NOT a
    history table — each sync overwrites this same row in place, and it's
    the only table that sync ever writes to (client_version_status rows
    snapshot from here at each client's own deploy time, but the sync job
    itself never touches client_version_status).
    """

    __tablename__ = "bitbucket_main_branch_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Bumped on every successful sync regardless of whether the value
    # changed — ops/liveness diagnostic only ("is the cron job still
    # running"), not shown anywhere in the Release Tracker UI.
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Bumped ONLY when `version` actually differs from what was already
    # stored — this is what client rows snapshot into their own
    # main_updated_at at deploy time, so it answers "main has been at this
    # version since Y", not "when did the sync job last run".
    version_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

Delete `app/models/client_version_record.py` and `tests/test_client_version_record_model.py`.

Update `app/models/__init__.py`:

```python
from app.models.approval import Approval
from app.models.audit_log import AuditLog
from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from app.models.deployable_task import DeployableTask
from app.models.deployment_execution import DeploymentExecution
from app.models.deployment_request import DeploymentRequest
from app.models.team import Team
from app.models.user import User

__all__ = [
    "Approval",
    "AuditLog",
    "BitbucketMainBranchStatus",
    "Client",
    "ClientVersionStatus",
    "DeployableTask",
    "DeploymentExecution",
    "DeploymentRequest",
    "Team",
    "User",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: same command as Step 2.
Expected: PASS (2 tests)

- [ ] **Step 5: Confirm the current Alembic head**

```bash
grep -rl "down_revision.*'d4e5f6a7b8c9'" alembic/versions/*.py; echo "(empty output above = d4e5f6a7b8c9 is still head)"
```

- [ ] **Step 6: Write the migration, including the real-data backfill**

```python
# alembic/versions/e1f2a3b4c5d6_replace_client_version_records_with_status.py
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
```

Note on Step 6's `sa.Column('environment', sa.Enum(..., create_type=False))`
in `downgrade()`: Task 1 of the original Release Tracker plan discovered
`create_type=False` alone does NOT suppress `CREATE TYPE` on a fresh
`op.create_table()` in this SQLAlchemy version (confirmed by reproducing the
failure against the real Postgres DB) — if you actually need to run this
downgrade for real, add the same `ALTER TABLE ... USING` cast workaround
that migration `d4e5f6a7b8c9` uses, applied to this table instead. Flagging
this rather than duplicating the workaround here since `downgrade()` is not
expected to run against real data (see its docstring) — if you do need it,
copy the pattern from `d4e5f6a7b8c9`'s `upgrade()`.

- [ ] **Step 7: Run the SQLite test suite**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_client_version_status_model.py -v`
Expected: PASS (this only exercises `Base.metadata.create_all`, not the
Alembic migration file itself — Step 8 exercises the real migration.)

- [ ] **Step 8: Run the migration against the real Postgres DB and verify the real data migrated correctly**

Before running: capture what's currently in the old table so you can compare
after.

```bash
docker exec deployment_status-db-1 psql -U deploy_tracker -d deploy_tracker -c "select client_id, environment, current_version, previous_version, created_at from client_version_records order by client_id, environment, created_at;"
```

Run the migration:

```bash
docker compose up -d --build app
docker exec deployment_status-app-1 alembic upgrade head
```

Verify:

```bash
docker exec deployment_status-db-1 psql -U deploy_tracker -d deploy_tracker -c "\d client_version_status"
docker exec deployment_status-db-1 psql -U deploy_tracker -d deploy_tracker -c "select * from client_version_status;"
docker exec deployment_status-db-1 psql -U deploy_tracker -d deploy_tracker -c "\d bitbucket_main_branch_status"
docker exec deployment_status-db-1 psql -U deploy_tracker -d deploy_tracker -c "select id, version, pr_number, last_synced_at, version_changed_at from bitbucket_main_branch_status;"
docker exec deployment_status-db-1 psql -U deploy_tracker -d deploy_tracker -c "select tablename from pg_tables where tablename = 'client_version_records';"
```

Confirm: every client that had rows in the old table now has exactly one
row in `client_version_status`, with `test_current_version`/
`live_current_version` matching that client's latest row per environment
from the "before" query above, `test_previous_version`/`live_previous_version`
matching the second-latest, and the old table is gone (last query returns 0
rows).

- [ ] **Step 9: Commit**

```bash
git add app/models/client_version_status.py app/models/bitbucket_main_branch_status.py app/models/__init__.py alembic/versions/e1f2a3b4c5d6_replace_client_version_records_with_status.py tests/test_client_version_status_model.py
git rm app/models/client_version_record.py tests/test_client_version_record_model.py
git commit -m "Replace ClientVersionRecord history table with ClientVersionStatus (one row per client)"
```

---

### Task 2: Service layer — `record_client_deploy` + query functions + sync's `version_changed_at`

**Files:**
- Modify: `app/services/release_tracker.py` (full rewrite)
- Modify: `app/services/sync.py`
- Test: `tests/test_release_tracker_service.py` (full rewrite), `tests/test_sync.py`

**Interfaces:**
- Consumes: `ClientVersionStatus` (Task 1).
- Produces:
  - `record_client_deploy(db, *, client_id, environment, current_version, recorded_by, deployment_request_id) -> ClientVersionStatus`
  - `current_version_for(db, client_id, environment) -> str | None`
  - `release_tracker_rows(db, client_id: int | None) -> list[ClientVersionStatus]`
  - `clients_with_version_records(db) -> list[Client]`
  
  Consumed by: Task 4 (`deploy_request`/`list_requests` use
  `record_client_deploy`/`current_version_for`), Task 5 (`release_tracker`
  route uses `release_tracker_rows`/`clients_with_version_records`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_release_tracker_service.py — replace the whole file
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.user import User, UserRole
from app.services.release_tracker import (
    clients_with_version_records,
    current_version_for,
    record_client_deploy,
    release_tracker_rows,
)


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed(db_session, *, client_id=1, client_name="CRM"):
    if db_session.get(Client, client_id) is None:
        db_session.add(Client(id=client_id, name=client_name))
    if db_session.get(User, 1) is None:
        db_session.add(User(id=1, name="Deployer", role=UserRole.developer))
    db_session.add(
        DeploymentRequest(
            id=client_id, request_type=RequestType.standard, client_id=client_id,
            environment=DeploymentEnvironment.live, status=RequestStatus.completed,
            created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )
    )
    db_session.flush()


def test_record_client_deploy_creates_row_on_first_deploy(db_session):
    _seed(db_session)

    row = record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    assert db_session.query(ClientVersionStatus).count() == 1
    assert row.live_current_version == "2026.34.34"
    assert row.live_previous_version is None
    assert row.test_current_version is None


def test_record_client_deploy_live_does_not_touch_test_columns(db_session):
    _seed(db_session)
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.test,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    row = record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    assert row.test_current_version == "1.0"  # untouched by the Live deploy
    assert row.live_current_version == "2026.34.34"
    assert db_session.query(ClientVersionStatus).count() == 1  # still one row


def test_record_client_deploy_captures_previous_version(db_session):
    _seed(db_session)
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.30", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    row = record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    assert row.live_current_version == "2026.34.34"
    assert row.live_previous_version == "2026.34.30"


def test_record_client_deploy_snapshots_main_from_cache(db_session):
    _seed(db_session)
    changed_at = datetime(2026, 8, 20, tzinfo=timezone.utc)
    db_session.add(
        BitbucketMainBranchStatus(id=1, version="2026.34.40", pr_number=15009, version_changed_at=changed_at)
    )
    db_session.commit()

    row = record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    assert row.main_version == "2026.34.40"
    assert row.main_pr_number == 15009
    assert row.main_updated_at == changed_at  # the cache's version_changed_at, not now()


def test_record_client_deploy_main_snapshot_is_null_without_a_sync_yet(db_session):
    _seed(db_session)
    row = record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()
    assert row.main_version is None
    assert row.main_pr_number is None
    assert row.main_updated_at is None


def test_record_client_deploy_does_not_touch_other_clients(db_session):
    _seed(db_session, client_id=1, client_name="CRM")
    _seed(db_session, client_id=2, client_name="Acme")
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    record_client_deploy(
        db_session, client_id=2, environment=DeploymentEnvironment.live,
        current_version="2.0", recorded_by=1, deployment_request_id=2,
    )
    db_session.commit()

    row1 = db_session.query(ClientVersionStatus).filter_by(client_id=1).one()
    assert row1.live_current_version == "1.0"  # untouched by client 2's deploy


def test_current_version_for_returns_none_when_no_row(db_session):
    _seed(db_session)
    assert current_version_for(db_session, 1, DeploymentEnvironment.live) is None


def test_current_version_for_returns_the_right_environment(db_session):
    _seed(db_session)
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.test,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()
    assert current_version_for(db_session, 1, DeploymentEnvironment.test) == "1.0"
    assert current_version_for(db_session, 1, DeploymentEnvironment.live) is None


def test_release_tracker_rows_one_per_client_ordered_by_name(db_session):
    _seed(db_session, client_id=1, client_name="Zebra Corp")
    _seed(db_session, client_id=2, client_name="Acme")
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    record_client_deploy(
        db_session, client_id=2, environment=DeploymentEnvironment.live,
        current_version="2.0", recorded_by=1, deployment_request_id=2,
    )
    db_session.commit()

    rows = release_tracker_rows(db_session, None)
    assert [r.client.name for r in rows] == ["Acme", "Zebra Corp"]


def test_release_tracker_rows_filters_by_client(db_session):
    _seed(db_session, client_id=1, client_name="CRM")
    _seed(db_session, client_id=2, client_name="Acme")
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    record_client_deploy(
        db_session, client_id=2, environment=DeploymentEnvironment.live,
        current_version="2.0", recorded_by=1, deployment_request_id=2,
    )
    db_session.commit()

    rows = release_tracker_rows(db_session, 1)
    assert [r.client_id for r in rows] == [1]


def test_clients_with_version_records_only_lists_clients_with_a_status_row(db_session):
    _seed(db_session, client_id=1, client_name="CRM")
    _seed(db_session, client_id=2, client_name="Acme")
    record_client_deploy(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="1.0", recorded_by=1, deployment_request_id=1,
    )
    db_session.commit()

    clients = clients_with_version_records(db_session)
    assert [c.name for c in clients] == ["CRM"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_release_tracker_service.py -v`
Expected: FAIL — `ImportError` (the old function names no longer exist / the
new ones aren't written yet).

- [ ] **Step 3: Rewrite the service**

```python
# app/services/release_tracker.py — replace the whole file
"""Read-only queries and the deploy-time write behind the Release Tracker tab
(docs/superpowers/specs/2026-08-27-release-tracker-redesign.md) and the
deploy-confirmation popup that feeds it (app/routers/dashboard.py's
deploy_request/list_requests).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_request import DeploymentEnvironment


def record_client_deploy(
    db: Session,
    *,
    client_id: int,
    environment: DeploymentEnvironment,
    current_version: str,
    recorded_by: int,
    deployment_request_id: int,
) -> ClientVersionStatus:
    """Get-or-create this client's ClientVersionStatus row, then update only
    the columns for `environment` — the other environment's columns are left
    completely untouched, and no other client's row is touched either way.

    Also snapshots main_version/main_pr_number/main_updated_at from the
    current BitbucketMainBranchStatus cache (all None if no sync has run
    yet). main_updated_at is set to the cache's version_changed_at, not
    datetime.now() — see BitbucketMainBranchStatus's docstring for why.
    """
    row = db.query(ClientVersionStatus).filter_by(client_id=client_id).one_or_none()
    if row is None:
        row = ClientVersionStatus(client_id=client_id)
        db.add(row)
        db.flush()

    prefix = environment.value  # "test" or "live" — matches the column prefixes exactly
    setattr(row, f"{prefix}_previous_version", getattr(row, f"{prefix}_current_version"))
    setattr(row, f"{prefix}_current_version", current_version)
    setattr(row, f"{prefix}_updated_at", datetime.now(timezone.utc))
    setattr(row, f"{prefix}_recorded_by", recorded_by)
    setattr(row, f"{prefix}_deployment_request_id", deployment_request_id)

    cache = db.get(BitbucketMainBranchStatus, 1)
    row.main_version = cache.version if cache else None
    row.main_pr_number = cache.pr_number if cache else None
    row.main_updated_at = cache.version_changed_at if cache else None

    return row


def current_version_for(db: Session, client_id: int, environment: DeploymentEnvironment) -> str | None:
    """The client's current version for this environment right now — what
    the deploy-confirmation popup shows as "Previous version" (it's about to
    become the previous value the moment this deploy is confirmed). None if
    this client has no ClientVersionStatus row yet, or hasn't deployed to
    this environment yet."""
    row = db.query(ClientVersionStatus).filter_by(client_id=client_id).one_or_none()
    if row is None:
        return None
    return getattr(row, f"{environment.value}_current_version")


def release_tracker_rows(db: Session, client_id: int | None) -> list[ClientVersionStatus]:
    """One row per client, ordered by client name — the Release Tracker
    tab's primary listing."""
    query = (
        db.query(ClientVersionStatus)
        .join(Client, ClientVersionStatus.client_id == Client.id)
        .options(joinedload(ClientVersionStatus.client))
    )
    if client_id is not None:
        query = query.filter(ClientVersionStatus.client_id == client_id)
    return query.order_by(Client.name).all()


def clients_with_version_records(db: Session) -> list[Client]:
    """Clients to populate the filter dropdown with — only ones that
    actually have a ClientVersionStatus row."""
    return (
        db.query(Client)
        .join(ClientVersionStatus, ClientVersionStatus.client_id == Client.id)
        .distinct()
        .order_by(Client.name)
        .all()
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2.
Expected: PASS (11 tests)

- [ ] **Step 5: Write the failing test for `sync_bitbucket_main_status`'s new behavior**

```python
# tests/test_sync.py — add (near the existing sync_bitbucket_main_status tests)
def test_sync_bitbucket_main_status_bumps_version_changed_at_when_version_differs(db_session):
    sync_bitbucket_main_status(db_session, FakeBitbucketProvider("2026.34.34", 1234))
    first = db_session.get(BitbucketMainBranchStatus, 1)
    first_changed_at = first.version_changed_at
    assert first_changed_at is not None

    sync_bitbucket_main_status(db_session, FakeBitbucketProvider("2026.34.40", 1300))
    second = db_session.get(BitbucketMainBranchStatus, 1)

    assert second.version_changed_at > first_changed_at
    assert second.version == "2026.34.40"


def test_sync_bitbucket_main_status_leaves_version_changed_at_when_version_is_the_same(db_session):
    sync_bitbucket_main_status(db_session, FakeBitbucketProvider("2026.34.34", 1234))
    first = db_session.get(BitbucketMainBranchStatus, 1)
    first_changed_at = first.version_changed_at
    first_synced_at = first.last_synced_at

    sync_bitbucket_main_status(db_session, FakeBitbucketProvider("2026.34.34", 1234))
    second = db_session.get(BitbucketMainBranchStatus, 1)

    assert second.version_changed_at == first_changed_at  # unchanged
    assert second.last_synced_at > first_synced_at  # this DOES bump every time
```

- [ ] **Step 6: Run test to verify it fails**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_sync.py -k version_changed_at -v`
Expected: FAIL — `AttributeError` (the column/logic don't exist in the old
`sync_bitbucket_main_status` yet).

- [ ] **Step 7: Update `sync_bitbucket_main_status`**

```python
# app/services/sync.py — replace the existing sync_bitbucket_main_status function
def sync_bitbucket_main_status(db: Session, provider) -> None:
    """Upserts the single BitbucketMainBranchStatus row (id=1) — a cache, not
    a history table. version_changed_at only advances when the fetched
    version actually differs from what's stored; last_synced_at bumps every
    time regardless (ops/liveness diagnostic only). See
    docs/superpowers/specs/2026-08-27-release-tracker-redesign.md.
    """
    status_info = provider.get_main_branch_status()
    now = datetime.now(timezone.utc)

    row = db.get(BitbucketMainBranchStatus, 1)
    if row is None:
        row = BitbucketMainBranchStatus(id=1)
        db.add(row)
        version_changed = status_info.version is not None
    else:
        version_changed = status_info.version != row.version

    row.version = status_info.version
    row.pr_number = status_info.pr_number
    row.last_synced_at = now
    if version_changed:
        row.version_changed_at = now
    db.commit()
```

- [ ] **Step 8: Run tests to verify they pass**

Run: same command as Step 6, then the full `test_sync.py` file:
`docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_sync.py -v`
Expected: PASS (all tests, including the 2 new ones)

- [ ] **Step 9: Commit**

```bash
git add app/services/release_tracker.py app/services/sync.py tests/test_release_tracker_service.py tests/test_sync.py
git commit -m "Rewrite release_tracker service for one-row-per-client status; sync tracks version_changed_at"
```

---

### Task 3: Per-column edit permission + Excel export columns

**Files:**
- Modify: `app/auth.py`
- Modify: `app/services/export.py`
- Test: `tests/test_auth.py`, `tests/test_export.py`

**Interfaces:**
- Consumes: `ClientVersionStatus` (Task 1).
- Produces: `can_edit_client_version_status(current_user, row, environment) -> bool`. Consumed by Task 5's edit routes/template.
- Produces: `release_tracker_rows_to_xlsx(rows: list[ClientVersionStatus], sheet_title) -> bytes`, `RELEASE_TRACKER_COLUMNS`. Consumed by Task 5's export route.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth.py — remove the old can_edit_client_version_record tests
# and _make_client_version_record helper, replace with:
from app.auth import can_edit_client_version_status
from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_request import DeploymentEnvironment


def _make_client_version_status(session, *, test_recorded_by=None, live_recorded_by=None):
    session.add(Client(id=1, name="CRM"))
    session.flush()
    row = ClientVersionStatus(
        client_id=1, test_current_version="1.0", test_recorded_by=test_recorded_by,
        live_current_version="2.0", live_recorded_by=live_recorded_by,
    )
    session.add(row)
    session.flush()
    return row


def test_recorder_can_edit_the_environment_they_recorded(web):
    _, session = web
    user = make_user(session, id=5, name="Deployer", username="deployer")
    row = _make_client_version_status(session, test_recorded_by=5)
    assert can_edit_client_version_status(user, row, DeploymentEnvironment.test) is True


def test_recorder_cannot_edit_the_other_environment_on_the_same_row(web):
    _, session = web
    user = make_user(session, id=5, name="Deployer", username="deployer")
    row = _make_client_version_status(session, test_recorded_by=5, live_recorded_by=6)
    assert can_edit_client_version_status(user, row, DeploymentEnvironment.live) is False


def test_admin_can_edit_either_environment(web):
    _, session = web
    admin = make_user(session, id=7, name="Root Admin", role=UserRole.admin, username="root")
    row = _make_client_version_status(session, test_recorded_by=5, live_recorded_by=6)
    assert can_edit_client_version_status(admin, row, DeploymentEnvironment.test) is True
    assert can_edit_client_version_status(admin, row, DeploymentEnvironment.live) is True
```

```python
# tests/test_export.py — replace the release-tracker test
from app.models.client_version_status import ClientVersionStatus


def test_release_tracker_rows_to_xlsx_writes_expected_columns():
    row = ClientVersionStatus(
        id=1, client_id=1,
        test_current_version="1.0", test_updated_at=datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc),
        live_current_version="2026.34.34", live_updated_at=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
        main_version="2026.34.40", main_pr_number=15009,
        main_updated_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    row.client = Client(name="CRM")

    content = release_tracker_rows_to_xlsx([row], "Release Tracker")
    workbook = load_workbook(BytesIO(content))
    sheet = workbook.active

    header_row = [cell.value for cell in sheet[1]]
    assert header_row == [
        "Client", "Test Current Version", "Test Updated At",
        "Live Current Version", "Live Updated At",
        "Main Version", "Main Updated At",
    ]
    data_row = [cell.value for cell in sheet[2]]
    assert data_row == [
        "CRM", "1.0", "2026-08-27 09:00 UTC",
        "2026.34.34", "2026-08-27 10:00 UTC",
        "2026.34.40 (PR #15009)", "2026-08-20 00:00 UTC",
    ]
```

(Keep the file's existing `rows_to_xlsx` test for `DeploymentStatusRow`
completely untouched — only the release-tracker test changes.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_auth.py -k client_version_status tests/test_export.py -v`
Expected: FAIL — `ImportError`/`AttributeError`.

- [ ] **Step 3: Write the permission function**

```python
# app/auth.py — replace can_edit_client_version_record with:
def can_edit_client_version_status(current_user: User, row, environment) -> bool:
    """Whether current_user may correct row's `{environment}_current_version`
    — checked PER COLUMN, not per row: a user who only ever confirmed this
    client's Test deploy can fix Test but not Live on the same row, and vice
    versa. Admins can edit either."""
    if current_user.role == UserRole.admin:
        return True
    recorded_by = getattr(row, f"{environment.value}_recorded_by")
    return current_user.id == recorded_by
```

- [ ] **Step 4: Update the export columns**

```python
# app/services/export.py — replace RELEASE_TRACKER_COLUMNS and its import
from app.models.client_version_status import ClientVersionStatus  # replaces the ClientVersionRecord import

RELEASE_TRACKER_COLUMNS = [
    ("Client", lambda r: r.client.name if r.client else ""),
    ("Test Current Version", lambda r: r.test_current_version or ""),
    ("Test Updated At", lambda r: r.test_updated_at.strftime("%Y-%m-%d %H:%M UTC") if r.test_updated_at else ""),
    ("Live Current Version", lambda r: r.live_current_version or ""),
    ("Live Updated At", lambda r: r.live_updated_at.strftime("%Y-%m-%d %H:%M UTC") if r.live_updated_at else ""),
    (
        "Main Version",
        lambda r: f"{r.main_version} (PR #{r.main_pr_number})" if r.main_version and r.main_pr_number
        else (r.main_version or ""),
    ),
    ("Main Updated At", lambda r: r.main_updated_at.strftime("%Y-%m-%d %H:%M UTC") if r.main_updated_at else ""),
]


def release_tracker_rows_to_xlsx(rows: list[ClientVersionStatus], sheet_title: str) -> bytes:
    return _columns_to_xlsx(rows, RELEASE_TRACKER_COLUMNS, sheet_title)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: same command as Step 2, then the full files:
`docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_auth.py tests/test_export.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/auth.py app/services/export.py tests/test_auth.py tests/test_export.py
git commit -m "Add per-column edit permission and rewrite Release Tracker export columns"
```

---

### Task 4: Wire `deploy_request`/`list_requests` to the new service

**Files:**
- Modify: `app/routers/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `record_client_deploy`, `current_version_for` (Task 2).
- Produces: `deploy_request`/`list_requests` behavior updated — same
  external behavior (popup, validation, redirect), different backend write
  target.

- [ ] **Step 1: Update imports and the tests that reference the old model**

Search `tests/test_dashboard.py` for every use of `ClientVersionRecord`/
`latest_current_version` and replace: `from app.models.client_version_status
import ClientVersionStatus` replaces the `ClientVersionRecord` import;
assertions that queried `ClientVersionRecord` rows now query
`ClientVersionStatus` instead, checking `test_current_version`/
`live_current_version` fields rather than a fresh row's `current_version`/
`environment`. Concretely, update these existing tests (search for their
names):

```python
# tests/test_dashboard.py — replace test_deploy_request_creates_client_version_record
def test_deploy_request_creates_client_version_status(web):
    client, session = web
    _seed_in_progress_standard_request(session)
    session.add(BitbucketMainBranchStatus(id=1, version="2026.34.40", pr_number=1234))
    session.commit()
    login_as(client, "deployer")

    response = client.post("/requests/1/deploy", data={"current_version": "2026.34.34"}, follow_redirects=False)

    assert response.status_code == 303
    assert session.get(DeploymentRequest, 1).status == RequestStatus.completed
    row = session.query(ClientVersionStatus).one()
    assert row.client_id == 1
    assert row.live_current_version == "2026.34.34"  # _seed_in_progress_standard_request uses live
    assert row.live_previous_version is None
    assert row.main_version == "2026.34.40"
    assert row.main_pr_number == 1234
    assert row.live_deployment_request_id == 1
    assert row.live_recorded_by == 3


# tests/test_dashboard.py — replace test_deploy_request_fills_previous_version_from_prior_record
def test_deploy_request_fills_previous_version_from_prior_status(web):
    client, session = web
    _seed_in_progress_standard_request(session)
    session.add(
        ClientVersionStatus(
            client_id=1, live_current_version="2026.34.30",
            live_deployment_request_id=1, live_recorded_by=3,
            live_updated_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    login_as(client, "deployer")

    client.post("/requests/1/deploy", data={"current_version": "2026.34.34"})

    row = session.query(ClientVersionStatus).one()
    assert row.live_previous_version == "2026.34.30"
    assert row.live_current_version == "2026.34.34"


# tests/test_dashboard.py — replace test_deploy_request_works_without_a_bitbucket_sync_yet
def test_deploy_request_works_without_a_bitbucket_sync_yet(web):
    client, session = web
    _seed_in_progress_standard_request(session)
    login_as(client, "deployer")

    response = client.post("/requests/1/deploy", data={"current_version": "2026.34.34"}, follow_redirects=False)

    assert response.status_code == 303
    row = session.query(ClientVersionStatus).one()
    assert row.main_version is None
    assert row.main_pr_number is None
    assert row.main_updated_at is None


# tests/test_dashboard.py — replace test_deploy_request_succeeds_for_standard_request_with_null_client_id
# and _with_null_environment (from the final-review fix wave): update their
# ClientVersionRecord-counting assertion to ClientVersionStatus:
#   assert session.query(ClientVersionStatus).count() == 0
```

Also update the two remaining count-zero assertions in
`test_deploy_request_requires_current_version_for_standard_requests` and any
other test asserting `session.query(ClientVersionRecord).count() == 0` to
use `ClientVersionStatus` instead.

Add a new test proving one client's deploy doesn't touch another's row, and
one proving a Test deploy leaves a prior Live value alone:

```python
def test_deploy_request_live_does_not_touch_existing_test_value(web):
    client, session = web
    _seed_in_progress_standard_request(session)  # environment=live by default
    session.add(
        ClientVersionStatus(
            client_id=1, test_current_version="1.0",
            test_deployment_request_id=99, test_recorded_by=3,
            test_updated_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    login_as(client, "deployer")

    client.post("/requests/1/deploy", data={"current_version": "2026.34.34"})

    row = session.query(ClientVersionStatus).one()
    assert row.test_current_version == "1.0"  # untouched
    assert row.live_current_version == "2026.34.34"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_dashboard.py -k "client_version_status or deploy_request" -v`
Expected: FAIL — `ImportError` (router still imports/uses the old model/function names).

- [ ] **Step 3: Update `deploy_request`**

```python
# app/routers/dashboard.py — imports: replace
#   from app.models.client_version_record import ClientVersionRecord
#   from app.services.release_tracker import latest_current_version
# with:
from app.models.client_version_status import ClientVersionStatus
from app.services.release_tracker import current_version_for, record_client_deploy
```

Replace the body of `deploy_request` from the `if deployment_request.request_type == RequestType.standard and has_client_and_environment:` block (the second occurrence, right before `db.commit()`) with:

```python
    if deployment_request.request_type == RequestType.standard and has_client_and_environment:
        record_client_deploy(
            db,
            client_id=deployment_request.client_id,
            environment=deployment_request.environment,
            current_version=current_version,
            recorded_by=current_user.id,
            deployment_request_id=deployment_request.id,
        )
```

(This replaces the old inline `ClientVersionRecord(...)`/`BitbucketMainBranchStatus`
lookup block entirely — `record_client_deploy` now does that internally.)

- [ ] **Step 4: Update `list_requests`'s `previous_versions` lookup**

```python
# app/routers/dashboard.py — inside list_requests(), change:
#   previous_versions[key] = latest_current_version(db, r.client_id, r.environment)
# to:
                previous_versions[key] = current_version_for(db, r.client_id, r.environment)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: same command as Step 2, then the full file:
`docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_dashboard.py -v`
Expected: all pass except the one known pre-existing unrelated failure
(`test_requests_queue_branch_commit_has_view_button_for_full_text`).

- [ ] **Step 6: Commit**

```bash
git add app/routers/dashboard.py tests/test_dashboard.py
git commit -m "Wire deploy_request/list_requests to record_client_deploy/current_version_for"
```

---

### Task 5: Release Tracker router — drop System filter, per-column edit routes

**Files:**
- Modify: `app/routers/release_tracker.py`
- Test: `tests/test_release_tracker.py` (largely rewritten)

**Interfaces:**
- Consumes: `release_tracker_rows`, `clients_with_version_records` (Task 2),
  `can_edit_client_version_status` (Task 3), `release_tracker_rows_to_xlsx`
  (Task 3).
- Produces: updated `GET /release-tracker`, `GET /release-tracker/export.xlsx`,
  `GET`/`POST /release-tracker/{status_id}/edit`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_release_tracker.py — replace the whole file
from datetime import datetime, timezone

from app.models.client import Client
from app.models.client_version_status import ClientVersionStatus
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def _seed_release_tracker_row(session, *, client_id=1, client_name="CRM", recorded_by=1, **overrides):
    if session.get(Client, client_id) is None:
        session.add(Client(id=client_id, name=client_name))
    make_user(session, id=recorded_by, name="Deployer", username=f"deployer{recorded_by}", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    defaults = dict(
        client_id=client_id,
        test_current_version="1.0", test_recorded_by=recorded_by, test_updated_at=datetime.now(timezone.utc),
        live_current_version="2.0", live_recorded_by=recorded_by, live_updated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    row = ClientVersionStatus(**defaults)
    session.add(row)
    session.commit()
    return row


def test_release_tracker_page_renders_one_row_per_client(web):
    client, session = web
    _seed_release_tracker_row(session)
    login_as(client, "deployer1")

    response = client.get("/release-tracker")

    assert response.status_code == 200
    assert "1.0" in response.text
    assert "2.0" in response.text
    assert "CRM" in response.text
    assert 'name="environment"' not in response.text  # System filter is gone


def test_release_tracker_requires_login(web):
    client, session = web
    _seed_release_tracker_row(session)

    response = client.get("/release-tracker", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_release_tracker_filters_by_client(web):
    client, session = web
    _seed_release_tracker_row(session, client_id=1, client_name="CRM", test_current_version="1.0")
    _seed_release_tracker_row(session, client_id=2, client_name="Acme", recorded_by=2, test_current_version="9.0")
    login_as(client, "deployer1")

    response = client.get("/release-tracker", params={"client_id": "1"})

    assert "1.0" in response.text
    assert "9.0" not in response.text


def test_release_tracker_export_xlsx(web):
    client, session = web
    _seed_release_tracker_row(session)
    login_as(client, "deployer1")

    response = client.get("/release-tracker/export.xlsx")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_recorder_can_edit_the_environment_they_recorded(web):
    client, session = web
    row = _seed_release_tracker_row(session, test_current_version="1.0")
    login_as(client, "deployer1")

    response = client.post(
        f"/release-tracker/{row.id}/edit", data={"test_current_version": "1.1"}, follow_redirects=False
    )

    assert response.status_code == 303
    session.refresh(row)
    assert row.test_current_version == "1.1"
    assert row.live_current_version == "2.0"  # untouched — no live_current_version in the POST


def test_recorder_cannot_edit_the_other_environment(web):
    client, session = web
    row = _seed_release_tracker_row(session, test_recorded_by=1, live_recorded_by=1)
    make_user(session, id=2, name="Other Dev", username="otherdev", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    # Give live to a different recorder so deployer2 has no permission over it
    row.live_recorded_by = 2
    session.commit()
    login_as(client, "deployer1")

    response = client.post(f"/release-tracker/{row.id}/edit", data={"live_current_version": "9.9"})

    assert response.status_code == 403
    session.refresh(row)
    assert row.live_current_version == "2.0"


def test_edit_rejects_blank_current_version(web):
    client, session = web
    row = _seed_release_tracker_row(session, test_current_version="1.0")
    login_as(client, "deployer1")

    response = client.post(f"/release-tracker/{row.id}/edit", data={"test_current_version": "   "})

    assert response.status_code == 400
    session.refresh(row)
    assert row.test_current_version == "1.0"


def test_edit_no_op_does_not_bump_updated_at(web):
    client, session = web
    row = _seed_release_tracker_row(session, test_current_version="1.0")
    original_updated_at = row.test_updated_at
    session.commit()
    login_as(client, "deployer1")

    client.post(f"/release-tracker/{row.id}/edit", data={"test_current_version": "1.0"})  # same value

    session.refresh(row)
    assert row.test_updated_at == original_updated_at  # unchanged, since nothing actually differed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_release_tracker.py -v`
Expected: FAIL — mix of `AssertionError` (System filter still present, wrong
model queried) and `TypeError` (route signature mismatches).

- [ ] **Step 3: Rewrite the router**

```python
# app/routers/release_tracker.py — replace the whole file
"""Web UI for the Release Tracker tab — one row per client, fed by the
deploy-confirmation popup in app/routers/dashboard.py's deploy_request(). See
docs/superpowers/specs/2026-08-27-release-tracker-redesign.md.
"""

from datetime import datetime, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import can_edit_client_version_status, require_login
from app.database import get_db
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_request import DeploymentEnvironment
from app.models.user import User
from app.services.export import release_tracker_rows_to_xlsx
from app.services.release_tracker import clients_with_version_records, release_tracker_rows
from app.static_version import STATIC_VERSION

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION


def _parse_release_tracker_filters(client_id: str | None) -> int | None:
    return int(client_id) if client_id else None


def _filter_context(db: Session, client_id: int | None) -> dict:
    return {
        "filter_clients": clients_with_version_records(db),
        "show_system_filter": False,
        "selected_client_id": client_id,
    }


@router.get("/release-tracker")
def release_tracker_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    client_id: str | None = None,
):
    parsed_client_id = _parse_release_tracker_filters(client_id)
    rows = release_tracker_rows(db, parsed_client_id)
    context = {
        "current_user": current_user,
        "rows": rows,
        "can_edit_test": lambda r: can_edit_client_version_status(current_user, r, DeploymentEnvironment.test),
        "can_edit_live": lambda r: can_edit_client_version_status(current_user, r, DeploymentEnvironment.live),
    }
    context.update(_filter_context(db, parsed_client_id))
    return templates.TemplateResponse(request, "release_tracker.html", context)


@router.get("/release-tracker/export.xlsx")
def release_tracker_export_xlsx(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    client_id: str | None = None,
):
    parsed_client_id = _parse_release_tracker_filters(client_id)
    rows = release_tracker_rows(db, parsed_client_id)
    content = release_tracker_rows_to_xlsx(rows, "Release Tracker")
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=release-tracker.xlsx"},
    )


def _get_status_or_404(db: Session, status_id: int) -> ClientVersionStatus:
    row = db.get(ClientVersionStatus, status_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Client version status not found")
    return row


@router.get("/release-tracker/{status_id}/edit")
def release_tracker_edit_form(
    status_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
):
    row = _get_status_or_404(db, status_id)
    can_edit_test = can_edit_client_version_status(current_user, row, DeploymentEnvironment.test)
    can_edit_live = can_edit_client_version_status(current_user, row, DeploymentEnvironment.live)
    if not can_edit_test and not can_edit_live:
        raise HTTPException(status_code=403, detail="You don't have permission to edit this record")
    return templates.TemplateResponse(
        request, "release_tracker_edit.html",
        {"current_user": current_user, "record": row, "can_edit_test": can_edit_test, "can_edit_live": can_edit_live},
    )


@router.post("/release-tracker/{status_id}/edit")
def release_tracker_edit(
    status_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    test_current_version: str | None = Form(None),
    live_current_version: str | None = Form(None),
):
    row = _get_status_or_404(db, status_id)
    can_edit_test = can_edit_client_version_status(current_user, row, DeploymentEnvironment.test)
    can_edit_live = can_edit_client_version_status(current_user, row, DeploymentEnvironment.live)

    if test_current_version is not None and not can_edit_test:
        raise HTTPException(status_code=403, detail="You don't have permission to edit the Test version")
    if live_current_version is not None and not can_edit_live:
        raise HTTPException(status_code=403, detail="You don't have permission to edit the Live version")
    if test_current_version is None and live_current_version is None:
        raise HTTPException(status_code=403, detail="You don't have permission to edit this record")

    errors = []
    now = datetime.now(timezone.utc)
    if test_current_version is not None:
        stripped = test_current_version.strip()
        if not stripped:
            errors.append("Test current version cannot be blank.")
        elif stripped != row.test_current_version:
            row.test_current_version = stripped
            row.test_updated_at = now
    if live_current_version is not None:
        stripped = live_current_version.strip()
        if not stripped:
            errors.append("Live current version cannot be blank.")
        elif stripped != row.live_current_version:
            row.live_current_version = stripped
            row.live_updated_at = now

    if errors:
        return templates.TemplateResponse(
            request, "release_tracker_edit.html",
            {
                "current_user": current_user, "record": row, "error": " ".join(errors),
                "can_edit_test": can_edit_test, "can_edit_live": can_edit_live,
            },
            status_code=400,
        )

    db.commit()
    return RedirectResponse(url="/release-tracker", status_code=303)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2.
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add app/routers/release_tracker.py tests/test_release_tracker.py
git commit -m "Drop System filter, add per-column edit routes to Release Tracker router"
```

---

### Task 6: Templates — colored column blocks + conditional System filter

**Files:**
- Modify: `app/templates/release_tracker.html`
- Modify: `app/templates/_deployment_filter_bar.html`
- Modify: `app/static/style.css`
- Test: covered by Task 5's `test_release_tracker_page_renders_one_row_per_client`
  (already asserts the System filter is gone) — this task's own verification
  is primarily a manual browser check (Step 3 below), since color/layout
  isn't meaningfully unit-testable.

**Interfaces:**
- Consumes: `rows` (list of `ClientVersionStatus`), `can_edit_test`/
  `can_edit_live` (Task 5's route context), `show_system_filter` (Task 5's
  `_filter_context`).
- Produces: the actual rendered tab. Nothing downstream depends on this
  task's internals.

- [ ] **Step 1: Make the System filter conditional in the shared partial**

```html
{# app/templates/_deployment_filter_bar.html — replace the whole file #}
<form method="get" action="{{ filter_action }}" class="filter-bar">
  <div class="filter-field">
    <label for="client_id">Client</label>
    <select name="client_id" id="client_id">
      <option value="">All clients</option>
      {% for c in filter_clients %}
        <option value="{{ c.id }}" {% if selected_client_id == c.id %}selected{% endif %}>{{ c.name }}</option>
      {% endfor %}
    </select>
  </div>
  {% if show_system_filter | default(true) %}
  <div class="filter-field">
    <label for="environment">System</label>
    <select name="environment" id="environment">
      <option value="">All systems</option>
      {% for env in filter_environments %}
        <option value="{{ env.value }}" {% if selected_environment == env %}selected{% endif %}>{{ env.value | capitalize }}</option>
      {% endfor %}
    </select>
  </div>
  {% endif %}
  {% if show_task_id_filter | default(true) %}
  <div class="filter-field">
    <label for="task_id">Task ID</label>
    <input type="text" name="task_id" id="task_id" value="{{ selected_task_id }}" placeholder="Search Task ID">
  </div>
  {% endif %}
  <button type="submit" class="button-primary">Filter</button>
  <a href="{{ filter_action }}" class="button-secondary">Reset</a>
  <a
    href="{{ export_action }}?client_id={{ selected_client_id or '' }}{% if show_system_filter | default(true) %}&environment={{ selected_environment.value if selected_environment else '' }}{% endif %}{% if show_task_id_filter | default(true) %}&task_id={{ selected_task_id | urlencode }}{% endif %}"
    class="button-secondary"
  >Export to Excel</a>
</form>
```

`dashboard.html`/`dashboard_history.html` set neither `show_system_filter`
nor `show_task_id_filter`, so `default(true)` preserves their current
rendering unchanged — no changes needed to those two templates or their
routes.

- [ ] **Step 2: Rewrite the Release Tracker table + colors**

```html
{# app/templates/release_tracker.html — replace the whole file #}
{% extends "base.html" %}
{% block title %}Release Tracker — Deployment Tracker{% endblock %}
{% block content %}
  <p class="eyebrow">Versions</p>
  <h1>Release Tracker</h1>
  <p class="subtitle">Current Test/Live version per client, and what was on
    <code>main</code> the last time that client deployed.</p>

  {% set filter_action = "/release-tracker" %}
  {% set export_action = "/release-tracker/export.xlsx" %}
  {% include "_deployment_filter_bar.html" %}

  {% if rows %}
    <div class="table-scroll">
    <table class="status-table release-tracker-table">
      <thead>
        <tr>
          <th rowspan="2">Client</th>
          <th colspan="2" class="col-test">Test</th>
          <th colspan="2" class="col-live">Live</th>
          <th colspan="2" class="col-main">Main Version</th>
          <th rowspan="2">Action</th>
        </tr>
        <tr>
          <th class="col-test">Current Version</th>
          <th class="col-test">Updated At</th>
          <th class="col-live">Current Version</th>
          <th class="col-live">Updated At</th>
          <th class="col-main">Version</th>
          <th class="col-main">Updated At</th>
        </tr>
      </thead>
      <tbody>
        {% for row in rows %}
        <tr>
          <td>{{ row.client.name if row.client else "—" }}</td>
          <td class="col-test">{{ row.test_current_version or "—" }}</td>
          <td class="col-test">{{ row.test_updated_at.strftime("%Y-%m-%d %H:%M UTC") if row.test_updated_at else "—" }}</td>
          <td class="col-live">{{ row.live_current_version or "—" }}</td>
          <td class="col-live">{{ row.live_updated_at.strftime("%Y-%m-%d %H:%M UTC") if row.live_updated_at else "—" }}</td>
          <td class="col-main">
            {% if row.main_version and row.main_pr_number %}
              {{ row.main_version }} (PR #{{ row.main_pr_number }})
            {% else %}
              {{ row.main_version or "—" }}
            {% endif %}
          </td>
          <td class="col-main">{{ row.main_updated_at.strftime("%Y-%m-%d %H:%M UTC") if row.main_updated_at else "—" }}</td>
          <td>
            <div class="actions-inner">
              {% if can_edit_test(row) or can_edit_live(row) %}
                <a href="/release-tracker/{{ row.id }}/edit" class="action-link edit">Edit</a>
              {% else %}
                <span class="muted">—</span>
              {% endif %}
            </div>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    </div>
  {% else %}
    <p class="empty-state">No clients have a recorded deploy yet.</p>
  {% endif %}
{% endblock %}
```

Add the color-block CSS:

```css
/* app/static/style.css — add near the existing .badge-test/.badge-live rules */

/* Release Tracker's Test/Live/Main column blocks — same hues as the
   test/live badges elsewhere in the app (amber/violet), plus green for
   Main Version, matching the app's existing "shipped/complete" signal
   color. Applied to both the two-row <thead> and every <td> in that
   column so the tinted block reads as one visual group per row. */
.release-tracker-table th.col-test,
.release-tracker-table td.col-test { background: var(--amber-dim); }
.release-tracker-table th.col-live,
.release-tracker-table td.col-live { background: var(--violet-dim); }
.release-tracker-table th.col-main,
.release-tracker-table td.col-main { background: var(--green-dim); }
```

- [ ] **Step 3: Manually verify in a real browser**

```bash
cd /home/abubakkar/Desktop/Versions/codebase/deployment_status
docker compose up -d --build app
```

Log in, visit `/release-tracker`. Confirm: the table shows one row per
client with Test (amber-tinted), Live (violet-tinted), Main Version
(green-tinted) column blocks; the filter bar shows only Client (no System
dropdown); Export to Excel downloads a file with the new columns. If real
client data already exists from the migration (Task 1, Step 8), confirm it
renders correctly — the values should match what you verified there.

- [ ] **Step 4: Run the full test suite**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest -q`
Expected: all pass except the one known pre-existing unrelated failure.

- [ ] **Step 5: Commit**

```bash
git add app/templates/release_tracker.html app/templates/_deployment_filter_bar.html app/static/style.css
git commit -m "Colored Test/Live/Main column blocks on Release Tracker; drop System filter from the shared bar"
```

---

### Task 7: Edit template — two independently-gated fields

**Files:**
- Modify: `app/templates/release_tracker_edit.html`

**Interfaces:**
- Consumes: `record` (a `ClientVersionStatus`), `can_edit_test`/
  `can_edit_live` (booleans, from Task 5's route context), `error`
  (optional string).
- Produces: the edit form. Covered by Task 5's route-level tests
  (`test_recorder_can_edit_the_environment_they_recorded`,
  `test_recorder_cannot_edit_the_other_environment`,
  `test_edit_rejects_blank_current_version`) — this task's own verification
  is the manual browser check in Step 2.

- [ ] **Step 1: Rewrite the template**

```html
{# app/templates/release_tracker_edit.html — replace the whole file #}
{% extends "base.html" %}
{% block title %}Edit Version Status — Deployment Tracker{% endblock %}
{% block content %}
  <h1>Edit Version Status — {{ record.client.name if record.client else "—" }}</h1>

  {% if error %}
    <p class="error">{{ error }}</p>
  {% endif %}

  <form method="post" action="/release-tracker/{{ record.id }}/edit" class="form">
    {% if can_edit_test %}
      <div class="field">
        <label for="test_current_version">Test current version</label>
        <input type="text" id="test_current_version" name="test_current_version" value="{{ record.test_current_version or '' }}">
      </div>
    {% else %}
      <div class="field">
        <label>Test current version <span class="optional">(not editable by you)</span></label>
        <p class="static-field">{{ record.test_current_version or "—" }}</p>
      </div>
    {% endif %}

    {% if can_edit_live %}
      <div class="field">
        <label for="live_current_version">Live current version</label>
        <input type="text" id="live_current_version" name="live_current_version" value="{{ record.live_current_version or '' }}">
      </div>
    {% else %}
      <div class="field">
        <label>Live current version <span class="optional">(not editable by you)</span></label>
        <p class="static-field">{{ record.live_current_version or "—" }}</p>
      </div>
    {% endif %}

    <button type="submit">Save Changes</button>
    <a href="/release-tracker" class="link-button">Cancel</a>
  </form>
{% endblock %}
```

Note: a field rendered as a plain `<p class="static-field">` (the
not-editable case) has no `<input>`, so it's simply absent from the POSTed
form data — the router's `Form(None)` default already treats that as "not
attempting to edit this field" (see Task 5), so no hidden-input trickery is
needed here.

- [ ] **Step 2: Manually verify in a real browser**

```bash
docker compose up -d --build app
```

Log in as a user who recorded only one environment on some client's row
(or create one via the popup flow), visit that row's Edit link. Confirm:
the environment they recorded shows an editable input pre-filled with the
current value; the other environment shows read-only text with "(not
editable by you)"; submitting a change to the editable one redirects back
to `/release-tracker` with the new value showing.

- [ ] **Step 3: Run the full test suite**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest -q`
Expected: all pass except the one known pre-existing unrelated failure.

- [ ] **Step 4: Commit**

```bash
git add app/templates/release_tracker_edit.html
git commit -m "Two independently-gated fields in the Release Tracker edit form"
```

---

### Task 8: Senior-developer cleanup pass

**Files:** All files touched by Tasks 1-7.

**Interfaces:** None — this task changes no behavior, only removes cruft.
Every test from Tasks 1-7 must still pass unchanged.

Same spirit as the original Release Tracker plan's final task (the user
asked for this explicitly, again): read the full diff since branching off
`master`, and look specifically for:

- **Dead code**: any leftover reference to `ClientVersionRecord`,
  `latest_current_version`, or the old per-environment history concept
  anywhere in the codebase (grep for all three across `app/` and `tests/` —
  there should be zero hits after Tasks 1-7). Any unused import left behind
  by a search-and-replace across the many files this redesign touched.
- **Unnecessary comments**: comments that just restate the next line, or
  that still describe v1's history-table behavior after the code beneath
  them no longer does that.
- **Duplication that crept in**: compare `record_client_deploy`'s
  `setattr`/`getattr`-with-prefix pattern against
  `can_edit_client_version_status`'s `getattr(row, f"{environment.value}_recorded_by")`
  — both do the same "prefix by environment.value" trick; leave them as-is
  (each is a single line, extracting a shared helper for one line each way
  would be over-abstraction) but confirm neither duplicated the OTHER's
  logic by accident.

- [ ] **Step 1: Grep for dead references to the old model/functions**

```bash
grep -rn "ClientVersionRecord\|latest_current_version" app/ tests/ docs/superpowers/plans/2026-08-27-release-tracker.md docs/superpowers/specs/2026-08-27-release-tracker-design.md
```

Expected: hits only in `docs/superpowers/plans/2026-08-27-release-tracker.md`
and `docs/superpowers/specs/2026-08-27-release-tracker-design.md` (the v1
plan/spec documents — leave those alone, they're a historical record of what
v1 was, superseded by this plan/spec, not live code). Any hit inside `app/`
or `tests/` is a real leftover — fix it.

- [ ] **Step 2: Review the full diff**

```bash
git diff master...HEAD -- app/ tests/ | less
```

- [ ] **Step 3: Fix anything found, inline**

- [ ] **Step 4: Re-run the full test suite to confirm nothing broke**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest -q`
Expected: all pass except the one known pre-existing unrelated failure.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Clean up dead code and unnecessary comments from the Release Tracker redesign"
```

---

## Post-plan follow-up (not part of this plan's tasks)

- Task 1's migration was already run against the real dev DB during Task 1
  itself (that task's Step 8) — no separate production migration step is
  needed beyond whatever this branch's normal deploy process already does.
- If the real client data migrated in Task 1 looks wrong once the full
  feature is live (Task 6's manual check), that's a signal to revisit Task
  1's fold-forward logic before merging further — don't paper over it in a
  later task.
