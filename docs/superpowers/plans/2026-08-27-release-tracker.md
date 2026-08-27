# Release Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Release Tracker" tab showing, per client + system, the current
deployed version, the previous version, a snapshot of the latest version on the
`shopfloor-suite` repo's `main` branch (plus its latest merged PR number), and when
each record was made — populated by a popup DevOps fills in when marking a Standard
Deployment request as deployed.

**Architecture:** Two new tables (`client_version_records` — full history, one row
per deploy confirmation; `bitbucket_main_branch_status` — a single-row cache
refreshed every 5 minutes). A new Bitbucket Cloud REST API adapter
(`app/services/bitbucket_source.py`) mirrors the existing CRM adapter's shape
(`app/services/task_source.py`) but is simpler (static bearer token, no login
step). The existing `deploy_request` route gains a required `current_version` form
field and, on success, inserts a `client_version_records` row using a snapshot of
the cached Bitbucket status. A new router/template pair serves the tab itself.

**Tech Stack:** FastAPI + Jinja2 (server-rendered, no frontend build), SQLAlchemy +
Alembic, httpx for the Bitbucket API, pytest with `httpx.MockTransport` for adapter
tests and an in-memory SQLite `Base.metadata.create_all()` fixture for
model/service/route tests — all matching this repo's existing patterns exactly
(see `app/services/task_source.py`, `tests/test_task_source.py`,
`tests/test_sync.py`, `tests/test_dashboard.py`).

**Spec:** `docs/superpowers/specs/2026-08-27-release-tracker-design.md`

## Global Constraints

- Bitbucket workspace: `SCT`, repo slug: `shopfloor-suite`, branch: `main`, file
  path: `frontend-sap/src/assets/release.json` (confirmed against the real repo URL
  `https://bitbucket.org/SCT/shopfloor-suite/src/main/`) — these are config
  defaults, overridable via `.env`, never hardcoded as the *only* option.
- Auth: a Bitbucket Repository or Workspace Access Token (bearer token, no
  username) — confirmed with the user. Never logged, never committed; lives only
  in `.env` (gitignored) via `Settings.bitbucket_api_token`.
- Scope: Standard Deployment requests only. `db_dump_restore`/`test_local` request
  types are completely unaffected — their "Mark Deployed" stays a bare submit
  button, no popup, no version tracking.
- `client_version_records` is a full history table (a new row every deploy
  confirmation, never overwritten). `bitbucket_main_branch_status` is the opposite
  — a single-row cache, overwritten in place every sync.
- The popup only requires/collects `current_version`; `previous_version` is always
  auto-filled server-side from history, never typed by the user.
- Row correction (edit) touches only `current_version` — `previous_version`,
  `main_version`, `main_pr_number` are permanent historical snapshots, never
  editable.
- Release Tracker tab is visible to every logged-in user, no role gating (matches
  Dashboard/History/Requests).
- Follow existing patterns exactly rather than inventing new ones: adapter shape
  (`task_source.py`), sync/upsert shape (`sync.py`), CLI subcommand shape
  (`cli.py`), dialog/modal conventions (`base.html`'s `#changes-modal`), filter-bar
  conventions (`_deployment_filter_bar.html`), auth-permission-function shape
  (`can_delete_request`/`can_edit_request` in `app/auth.py`).

---

## File Structure

**New files:**
- `app/models/client_version_record.py` — `ClientVersionRecord` model
- `app/models/bitbucket_main_branch_status.py` — `BitbucketMainBranchStatus` model
- `alembic/versions/<rev>_add_client_version_records_and_bitbucket_status.py` — migration creating both tables
- `app/services/bitbucket_source.py` — `BitbucketCloudProvider` adapter + `BitbucketMainStatusInfo` dataclass
- `app/services/release_tracker.py` — query helpers behind the new tab
- `app/routers/release_tracker.py` — `GET /release-tracker`, `GET /release-tracker/export.xlsx`, `GET`/`POST /release-tracker/{id}/edit`
- `app/templates/release_tracker.html`
- `app/templates/release_tracker_edit.html`
- `tests/test_bitbucket_source.py`
- `tests/test_release_tracker.py`

**Modified files:**
- `app/config.py` — new `bitbucket_*` Settings fields
- `.env.example` — documents the new keys (blank placeholders)
- `app/models/__init__.py` — register the two new models
- `app/services/sync.py` — new `sync_bitbucket_main_status()`
- `app/services/export.py` — new `RELEASE_TRACKER_COLUMNS` + `release_tracker_rows_to_xlsx()`
- `app/cli.py` — new `sync-bitbucket-main` subcommand
- `app/auth.py` — new `can_edit_client_version_record()`
- `app/routers/dashboard.py` — `deploy_request()` extended; `list_requests()` gains a `previous_versions` lookup for the popup
- `app/templates/request_list.html` — deploy-confirmation dialog + button change (Standard Deployment rows only)
- `app/templates/base.html` — new nav link
- `app/main.py` — register the new router
- `README.md` — crontab entry + `.env` docs for the new sync job
- `tests/test_sync.py` — new tests for `sync_bitbucket_main_status`
- `tests/test_dashboard.py` — new tests for extended `deploy_request` and `previous_versions`

---

### Task 1: Data model — `ClientVersionRecord` + `BitbucketMainBranchStatus`

**Files:**
- Create: `app/models/client_version_record.py`
- Create: `app/models/bitbucket_main_branch_status.py`
- Create: `alembic/versions/d4e5f6a7b8c9_add_client_version_records_and_bitbucket_status.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_client_version_record_model.py`

**Interfaces:**
- Produces: `ClientVersionRecord` (fields: `id`, `client_id`, `environment`,
  `current_version`, `previous_version`, `main_version`, `main_pr_number`,
  `deployment_request_id`, `recorded_by`, `created_at`, `updated_at`, plus
  relationships `client`, `deployment_request`, `recorder`).
- Produces: `BitbucketMainBranchStatus` (fields: `id`, `version`, `pr_number`,
  `last_synced_at`).
- Consumed by every later task in this plan.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_client_version_record_model.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models on Base.metadata
from app.database import Base
from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.user import User, UserRole


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_client_version_record_round_trips(db_session):
    client = Client(id=1, name="CRM")
    user = User(id=1, name="Deployer", role=UserRole.developer)
    request = DeploymentRequest(
        id=1,
        request_type=RequestType.standard,
        client_id=1,
        environment=DeploymentEnvironment.live,
        status=RequestStatus.completed,
        created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    db_session.add_all([client, user, request])
    db_session.flush()

    record = ClientVersionRecord(
        client_id=1,
        environment=DeploymentEnvironment.live,
        current_version="2026.34.34",
        previous_version="2026.34.30",
        main_version="2026.34.40",
        main_pr_number=1234,
        deployment_request_id=1,
        recorded_by=1,
        created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    db_session.add(record)
    db_session.commit()

    fetched = db_session.get(ClientVersionRecord, record.id)
    assert fetched.current_version == "2026.34.34"
    assert fetched.previous_version == "2026.34.30"
    assert fetched.main_version == "2026.34.40"
    assert fetched.main_pr_number == 1234
    assert fetched.client.name == "CRM"
    assert fetched.recorder.name == "Deployer"
    assert fetched.deployment_request.id == 1


def test_bitbucket_main_branch_status_round_trips(db_session):
    status = BitbucketMainBranchStatus(id=1, version="2026.34.40", pr_number=1234)
    db_session.add(status)
    db_session.commit()

    fetched = db_session.get(BitbucketMainBranchStatus, 1)
    assert fetched.version == "2026.34.40"
    assert fetched.pr_number == 1234
    assert fetched.last_synced_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_client_version_record_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.client_version_record'`

- [ ] **Step 3: Write the models**

```python
# app/models/client_version_record.py
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.deployment_request import DeploymentEnvironment


class ClientVersionRecord(Base):
    """One row per DevOps deploy-confirmation on a `standard` DeploymentRequest — see
    docs/superpowers/specs/2026-08-27-release-tracker-design.md ("Release Tracker").

    Full history, never overwritten: each Mark Deployed confirmation inserts a new
    row rather than updating an existing one (confirmed with the user — a
    client+environment's full version timeline, not just latest state).
    previous_version/main_version/main_pr_number are snapshots of what was true at
    the moment this row was created; editing a row afterward (see
    can_edit_client_version_record in app/auth.py) only ever corrects
    current_version, never those historical snapshots.
    """

    __tablename__ = "client_version_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    environment: Mapped[DeploymentEnvironment] = mapped_column(Enum(DeploymentEnvironment))
    current_version: Mapped[str] = mapped_column(String(100))
    # Auto-filled at insert time from the previous row's current_version for this
    # same (client_id, environment) — null only for the very first record ever made
    # for that pair.
    previous_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Snapshotted from BitbucketMainBranchStatus at insert time — see that model.
    # Both null if the very first Bitbucket sync hasn't run yet when this row is
    # created (e.g. the first 5 minutes after this feature ships).
    main_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    main_pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deployment_request_id: Mapped[int] = mapped_column(ForeignKey("deployment_requests.id"))
    recorded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    # Bumped when current_version is corrected after the fact (see
    # can_edit_client_version_record) — equal to created_at until then.
    updated_at: Mapped[datetime] = mapped_column(DateTime)

    client = relationship("Client")
    deployment_request = relationship("DeploymentRequest")
    recorder = relationship("User")
```

```python
# app/models/bitbucket_main_branch_status.py
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BitbucketMainBranchStatus(Base):
    """A single-row cache (id is always 1) of the shopfloor-suite repo's main
    branch release.json version + latest merged PR number, refreshed every 5
    minutes by `python -m app.cli sync-bitbucket-main` — see
    docs/superpowers/specs/2026-08-27-release-tracker-design.md. NOT a history
    table (contrast ClientVersionRecord) — each sync overwrites this same row in
    place.
    """

    __tablename__ = "bitbucket_main_branch_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

Update `app/models/__init__.py`:

```python
from app.models.approval import Approval
from app.models.audit_log import AuditLog
from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
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
    "ClientVersionRecord",
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

- [ ] **Step 5: Write the Alembic migration**

Check the current head first: `grep -rL "$(basename -a alembic/versions/*.py | sed 's/\.py$//' | sed 's/^[a-f0-9]*_//')" alembic/versions/*.py` is unreliable — instead just confirm no file has `down_revision` pointing at a revision that itself has no children:

```bash
grep -rl "down_revision.*'c3d4e5f6a7b8'" alembic/versions/*.py; echo "(empty output above = c3d4e5f6a7b8 is still head)"
```

```python
# alembic/versions/d4e5f6a7b8c9_add_client_version_records_and_bitbucket_status.py
"""add client_version_records and bitbucket_main_branch_status tables

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'bitbucket_main_branch_status',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('version', sa.String(length=100), nullable=True),
        sa.Column('pr_number', sa.Integer(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'client_version_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('environment', sa.Enum('test', 'live', name='deploymentenvironment'), nullable=False),
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


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('client_version_records')
    op.drop_table('bitbucket_main_branch_status')
```

Note: the `environment` column reuses the existing Postgres enum type
`deploymentenvironment` (created by an earlier migration for
`deployment_requests.environment`) — do NOT let Alembic re-`CREATE TYPE` it, or
the migration will fail with `type "deploymentenvironment" already exists`. Since
`sa.Enum(...)` in a *second* table's column defaults to `create_type=True`, add
`create_type=False` explicitly:

```python
sa.Column(
    'environment',
    sa.Enum('test', 'live', name='deploymentenvironment', create_type=False),
    nullable=False,
),
```

- [ ] **Step 6: Run the migration against the real Postgres DB and verify**

```bash
docker exec deployment_status-app-1 alembic upgrade head
docker exec deployment_status-db-1 psql -U deploy_tracker -d deploy_tracker -c "\d client_version_records"
docker exec deployment_status-db-1 psql -U deploy_tracker -d deploy_tracker -c "\d bitbucket_main_branch_status"
```
Expected: both tables exist with the columns above; `alembic_version` now shows `d4e5f6a7b8c9`.

- [ ] **Step 7: Commit**

```bash
git add app/models/client_version_record.py app/models/bitbucket_main_branch_status.py app/models/__init__.py alembic/versions/d4e5f6a7b8c9_add_client_version_records_and_bitbucket_status.py tests/test_client_version_record_model.py
git commit -m "Add ClientVersionRecord and BitbucketMainBranchStatus models"
```

---

### Task 2: Bitbucket config

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `Settings.bitbucket_api_token`, `Settings.bitbucket_workspace`,
  `Settings.bitbucket_repo_slug`, `Settings.bitbucket_release_path`,
  `Settings.bitbucket_branch` — consumed by Task 3's `BitbucketCloudProvider`.

No test-first cycle here — this is a plain declarative config addition with no
behavior of its own; it's exercised (and actually tested) via Task 3's adapter
tests, which construct a `Settings(...)` with these fields.

- [ ] **Step 1: Add the fields to `Settings`**

```python
# app/config.py — insert after task_api_deployable_machine_group_id (line 34)

    # Bitbucket Cloud REST API — backs the Release Tracker's "current version at
    # main" snapshot (docs/superpowers/specs/2026-08-27-release-tracker-design.md).
    # A Repository or Workspace Access Token (bearer, no username) — confirmed
    # with the user. Real value only ever lives in .env (gitignored), never here.
    bitbucket_api_token: str | None = None
    # Confirmed against the real repo URL: https://bitbucket.org/SCT/shopfloor-suite/src/main/
    bitbucket_workspace: str = "SCT"
    bitbucket_repo_slug: str = "shopfloor-suite"
    bitbucket_release_path: str = "frontend-sap/src/assets/release.json"
    bitbucket_branch: str = "main"
```

- [ ] **Step 2: Document the new keys in `.env.example`**

```bash
# .env.example — insert after the TASK_API_DEPLOYABLE_MACHINE_GROUP_ID line

# Bitbucket Cloud REST API — Release Tracker's "current version at main" snapshot.
# Repository or Workspace Access Token (bearer auth, no username).
BITBUCKET_API_TOKEN=
BITBUCKET_WORKSPACE=SCT
BITBUCKET_REPO_SLUG=shopfloor-suite
BITBUCKET_RELEASE_PATH=frontend-sap/src/assets/release.json
BITBUCKET_BRANCH=main
```

- [ ] **Step 3: Verify the app still boots**

```bash
docker compose up -d --build app
sleep 3
docker logs deployment_status-app-1 --tail 15
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8010/login
```
Expected: `200`, no errors in logs (new fields are all optional/defaulted, so
existing `.env` files without them still work).

- [ ] **Step 4: Commit**

```bash
git add app/config.py .env.example
git commit -m "Add Bitbucket API config settings"
```

---

### Task 3: `BitbucketCloudProvider` adapter

**Files:**
- Create: `app/services/bitbucket_source.py`
- Test: `tests/test_bitbucket_source.py`

**Interfaces:**
- Consumes: `Settings.bitbucket_*` (Task 2).
- Produces: `BitbucketMainStatusInfo` (dataclass: `version: str | None`,
  `pr_number: int | None`), `BitbucketCloudProvider` with method
  `get_main_branch_status(self) -> BitbucketMainStatusInfo`. Consumed by Task 4's
  `sync_bitbucket_main_status()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bitbucket_source.py
import httpx
import pytest

from app.config import Settings
from app.services.bitbucket_source import BitbucketCloudProvider


def _make_provider(handler):
    settings = Settings(
        bitbucket_api_token="fake-token",
        bitbucket_workspace="SCT",
        bitbucket_repo_slug="shopfloor-suite",
        bitbucket_release_path="frontend-sap/src/assets/release.json",
        bitbucket_branch="main",
    )
    client = httpx.Client(
        base_url="https://api.bitbucket.org/2.0",
        transport=httpx.MockTransport(handler),
    )
    return BitbucketCloudProvider(settings, client=client)


def test_get_main_branch_status_parses_release_and_latest_merged_pr():
    def handler(request):
        assert request.headers["authorization"] == "Bearer fake-token"
        if request.url.path == (
            "/2.0/repositories/SCT/shopfloor-suite/src/main/"
            "frontend-sap/src/assets/release.json"
        ):
            return httpx.Response(200, json={"release": "2026.34.34"})
        if request.url.path == "/2.0/repositories/SCT/shopfloor-suite/pullrequests":
            assert request.url.params["state"] == "MERGED"
            assert request.url.params["q"] == 'destination.branch.name="main"'
            assert request.url.params["sort"] == "-updated_on"
            return httpx.Response(200, json={"values": [{"id": 1234}, {"id": 1200}]})
        raise AssertionError(f"unexpected path {request.url.path}")

    provider = _make_provider(handler)
    status = provider.get_main_branch_status()

    assert status.version == "2026.34.34"
    assert status.pr_number == 1234


def test_get_main_branch_status_handles_no_merged_prs_yet():
    def handler(request):
        if "pullrequests" in request.url.path:
            return httpx.Response(200, json={"values": []})
        return httpx.Response(200, json={"release": "2026.34.34"})

    provider = _make_provider(handler)
    status = provider.get_main_branch_status()

    assert status.version == "2026.34.34"
    assert status.pr_number is None


def test_requires_configured_token():
    settings = Settings(bitbucket_api_token=None)
    with pytest.raises(RuntimeError, match="bitbucket_api_token is not configured"):
        BitbucketCloudProvider(settings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_bitbucket_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.bitbucket_source'`

- [ ] **Step 3: Write the adapter**

```python
# app/services/bitbucket_source.py
"""Adapter for the Bitbucket Cloud REST API — backs the Release Tracker's "current
version at main" snapshot (docs/superpowers/specs/2026-08-27-release-tracker-design.md).

Only ever reads from ONE fixed repo (settings.bitbucket_workspace/bitbucket_repo_slug,
"SCT/shopfloor-suite" by default) and ONE fixed branch (bitbucket_branch, "main") — this
is deliberately not a per-client lookup (confirmed with the user: clients are
differentiated by DeploymentRequest, not by any Bitbucket branch/repo mapping).
"""

from dataclasses import dataclass

import httpx

from app.config import Settings


@dataclass
class BitbucketMainStatusInfo:
    version: str | None
    pr_number: int | None


class BitbucketCloudProvider:
    """Talks to api.bitbucket.org/2.0.

    Auth: a single Repository or Workspace Access Token, sent as
    `Authorization: Bearer <token>` on every call — unlike InHouseTaskSourceProvider
    (app/services/task_source.py), there's no login step; the token is static and
    doesn't expire mid-run the way the CRM's does, so there's no 401-retry logic
    here either.
    """

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        if not settings.bitbucket_api_token:
            raise RuntimeError(
                "bitbucket_api_token is not configured. Set it in .env (see .env.example)."
            )
        self._workspace = settings.bitbucket_workspace
        self._repo_slug = settings.bitbucket_repo_slug
        self._path = settings.bitbucket_release_path
        self._branch = settings.bitbucket_branch
        self._token = settings.bitbucket_api_token
        self._client = client or httpx.Client(base_url="https://api.bitbucket.org/2.0", timeout=10.0)

    def _auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def get_main_branch_status(self) -> BitbucketMainStatusInfo:
        return BitbucketMainStatusInfo(
            version=self._fetch_release_version(),
            pr_number=self._fetch_latest_merged_pr_number(),
        )

    def _fetch_release_version(self) -> str | None:
        path = f"/repositories/{self._workspace}/{self._repo_slug}/src/{self._branch}/{self._path}"
        response = self._client.get(path, headers=self._auth_header())
        response.raise_for_status()
        return response.json().get("release")

    def _fetch_latest_merged_pr_number(self) -> int | None:
        # Most recently merged PR into `main` overall, regardless of what it
        # touched — confirmed with the user, not specifically the PR that last
        # changed release.json.
        path = f"/repositories/{self._workspace}/{self._repo_slug}/pullrequests"
        params = {
            "state": "MERGED",
            "q": f'destination.branch.name="{self._branch}"',
            "sort": "-updated_on",
        }
        response = self._client.get(path, headers=self._auth_header(), params=params)
        response.raise_for_status()
        values = response.json().get("values", [])
        return values[0]["id"] if values else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2.
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/bitbucket_source.py tests/test_bitbucket_source.py
git commit -m "Add BitbucketCloudProvider adapter"
```

---

### Task 4: `sync_bitbucket_main_status()` + CLI subcommand + README

**Files:**
- Modify: `app/services/sync.py`
- Modify: `app/cli.py`
- Modify: `README.md`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `BitbucketCloudProvider`/`BitbucketMainStatusInfo` (Task 3),
  `BitbucketMainBranchStatus` (Task 1).
- Produces: `sync_bitbucket_main_status(db: Session, provider) -> None`. Consumed
  by Task 8's `deploy_request()` (reads the row this writes) and by the new CLI
  subcommand.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_sync.py` (alongside the existing `FakeProvider`):

```python
# tests/test_sync.py — add near the top, after the existing imports
from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.services.sync import sync_bitbucket_main_status


class FakeBitbucketProvider:
    def __init__(self, version, pr_number):
        self._version = version
        self._pr_number = pr_number

    def get_main_branch_status(self):
        from app.services.bitbucket_source import BitbucketMainStatusInfo
        return BitbucketMainStatusInfo(version=self._version, pr_number=self._pr_number)


def test_sync_bitbucket_main_status_creates_row_on_first_sync(db_session):
    sync_bitbucket_main_status(db_session, FakeBitbucketProvider("2026.34.34", 1234))

    status = db_session.get(BitbucketMainBranchStatus, 1)
    assert status is not None
    assert status.version == "2026.34.34"
    assert status.pr_number == 1234
    assert status.last_synced_at is not None


def test_sync_bitbucket_main_status_updates_in_place_not_duplicates(db_session):
    sync_bitbucket_main_status(db_session, FakeBitbucketProvider("2026.34.34", 1234))
    sync_bitbucket_main_status(db_session, FakeBitbucketProvider("2026.34.40", 1300))

    assert db_session.query(BitbucketMainBranchStatus).count() == 1
    status = db_session.get(BitbucketMainBranchStatus, 1)
    assert status.version == "2026.34.40"
    assert status.pr_number == 1300
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_sync.py -k bitbucket -v`
Expected: FAIL — `ImportError: cannot import name 'sync_bitbucket_main_status'`

- [ ] **Step 3: Write the service function**

```python
# app/services/sync.py — add near sync_deployable_tasks, with the other imports
from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus


def sync_bitbucket_main_status(db: Session, provider) -> None:
    """Upserts the single BitbucketMainBranchStatus row (id=1) — a cache, not a
    history table (contrast sync_deployable_tasks' never-delete upsert onto many
    rows). See docs/superpowers/specs/2026-08-27-release-tracker-design.md.
    """
    status_info = provider.get_main_branch_status()

    row = db.get(BitbucketMainBranchStatus, 1)
    if row is None:
        row = BitbucketMainBranchStatus(id=1)
        db.add(row)

    row.version = status_info.version
    row.pr_number = status_info.pr_number
    row.last_synced_at = datetime.now(timezone.utc)
    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2.
Expected: PASS (2 tests)

- [ ] **Step 5: Add the CLI subcommand**

```python
# app/cli.py — add to the imports
from app.services.bitbucket_source import BitbucketCloudProvider
from app.services.sync import sync_bitbucket_main_status  # add to the existing sync import block


def cmd_sync_bitbucket_main(_args: argparse.Namespace) -> None:
    # Meant to be run every 5 minutes via cron, same as deployable-tasks — see
    # README's crontab section. Refreshes the single cached row deploy_request()
    # snapshots from when marking a Standard Deployment request as deployed.
    settings = get_settings()
    provider = BitbucketCloudProvider(settings)
    db = SessionLocal()
    try:
        sync_bitbucket_main_status(db, provider)
        status = db.query(BitbucketMainBranchStatus).first()
        print(f"Synced main branch status: version={status.version} pr={status.pr_number}")
    finally:
        db.close()
```

```python
# app/cli.py — add to the imports
from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
```

```python
# app/cli.py — add inside main(), alongside the other subparsers
    sync_bitbucket_main_parser = subparsers.add_parser(
        "sync-bitbucket-main",
        help="Refresh the cached shopfloor-suite main-branch release version + latest merged PR",
    )
    sync_bitbucket_main_parser.set_defaults(func=cmd_sync_bitbucket_main)
```

- [ ] **Step 6: Verify the CLI command runs against the real Bitbucket API (once the token is set)**

```bash
docker exec deployment_status-app-1 python -m app.cli sync-bitbucket-main
```
Expected (once `BITBUCKET_API_TOKEN` is set in `.env` and the app rebuilt): prints
`Synced main branch status: version=... pr=...`. If the token isn't set yet, this
will raise the `RuntimeError` from Task 3 — that's expected and fine; note it in
the commit message and move on, this doesn't block the rest of the plan (tests use
a mock, not the real API).

- [ ] **Step 7: Document the cron entry in README.md**

Find the existing `deployable-tasks` crontab section in README.md (`README.md`
around line 388, per the earlier `*/5 * * * * ... deployable-tasks` entry) and add
a sibling entry directly below it:

```markdown
*/5 * * * * cd /path/to/Deployment_status && .venv/bin/python -m app.cli sync-bitbucket-main >> /var/log/bitbucket-main-sync.log 2>&1
```

Also add a short note near it (matching the existing style) that this pulls
`shopfloor-suite`'s `main` branch `release.json` + latest merged PR into a single
cached row, consumed by the Release Tracker tab when a Standard Deployment is
marked deployed.

- [ ] **Step 8: Commit**

```bash
git add app/services/sync.py app/cli.py README.md tests/test_sync.py
git commit -m "Add sync_bitbucket_main_status service + sync-bitbucket-main CLI command"
```

---

### Task 5: `release_tracker.py` service — query helpers

**Files:**
- Create: `app/services/release_tracker.py`
- Test: `tests/test_release_tracker_service.py`

**Interfaces:**
- Consumes: `ClientVersionRecord`, `Client`, `DeploymentEnvironment` (Task 1).
- Produces:
  - `latest_current_version(db: Session, client_id: int, environment: DeploymentEnvironment) -> str | None`
  - `release_tracker_rows(db: Session, client_id: int | None, environment: DeploymentEnvironment | None) -> list[ClientVersionRecord]`
  - `clients_with_version_records(db: Session) -> list[Client]`
  
  Consumed by: Task 8 (`deploy_request` uses `latest_current_version`), Task 9
  (`list_requests`/the popup use `latest_current_version` for every distinct
  client+env), Task 10 (`release_tracker` route uses `release_tracker_rows` +
  `clients_with_version_records`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_release_tracker_service.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.user import User, UserRole
from app.services.release_tracker import (
    clients_with_version_records,
    latest_current_version,
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
    db_session.add(Client(id=client_id, name=client_name))
    db_session.add(User(id=1, name="Deployer", role=UserRole.developer))
    db_session.add(
        DeploymentRequest(
            id=client_id,
            request_type=RequestType.standard,
            client_id=client_id,
            environment=DeploymentEnvironment.live,
            status=RequestStatus.completed,
            created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )
    )
    db_session.flush()


def _add_record(db_session, *, client_id, environment, current_version, created_at):
    record = ClientVersionRecord(
        client_id=client_id,
        environment=environment,
        current_version=current_version,
        deployment_request_id=client_id,
        recorded_by=1,
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(record)
    db_session.commit()
    return record


def test_latest_current_version_returns_none_when_no_history(db_session):
    _seed(db_session)
    assert latest_current_version(db_session, 1, DeploymentEnvironment.live) is None


def test_latest_current_version_returns_most_recent(db_session):
    _seed(db_session)
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.30", created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    assert latest_current_version(db_session, 1, DeploymentEnvironment.live) == "2026.34.34"


def test_latest_current_version_scoped_by_environment(db_session):
    _seed(db_session)
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.test,
        current_version="2026.34.10", created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    assert latest_current_version(db_session, 1, DeploymentEnvironment.live) is None


def test_release_tracker_rows_newest_first(db_session):
    _seed(db_session)
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.30", created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    rows = release_tracker_rows(db_session, None, None)
    assert [r.current_version for r in rows] == ["2026.34.34", "2026.34.30"]


def test_release_tracker_rows_filters_by_client_and_environment(db_session):
    _seed(db_session, client_id=1, client_name="CRM")
    _seed(db_session, client_id=2, client_name="Acme")
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    _add_record(
        db_session, client_id=2, environment=DeploymentEnvironment.test,
        current_version="9.9.9", created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    rows = release_tracker_rows(db_session, 1, None)
    assert [r.client_id for r in rows] == [1]

    rows = release_tracker_rows(db_session, None, DeploymentEnvironment.test)
    assert [r.client_id for r in rows] == [2]


def test_clients_with_version_records_only_lists_clients_with_history(db_session):
    _seed(db_session, client_id=1, client_name="CRM")
    _seed(db_session, client_id=2, client_name="Acme")
    _add_record(
        db_session, client_id=1, environment=DeploymentEnvironment.live,
        current_version="2026.34.34", created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    clients = clients_with_version_records(db_session)
    assert [c.name for c in clients] == ["CRM"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_release_tracker_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.release_tracker'`

- [ ] **Step 3: Write the service**

```python
# app/services/release_tracker.py
"""Read-only queries behind the Release Tracker tab (docs/superpowers/specs/
2026-08-27-release-tracker-design.md) and the deploy-confirmation popup that feeds
it (app/routers/dashboard.py's deploy_request/list_requests).
"""

from sqlalchemy.orm import Session, joinedload

from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
from app.models.deployment_request import DeploymentEnvironment


def latest_current_version(db: Session, client_id: int, environment: DeploymentEnvironment) -> str | None:
    """The most recent current_version recorded for this client+environment, or
    None if there's no history yet — this is what the deploy-confirmation popup
    shows as "Previous version" (see Task 8/9), and what a new record's own
    previous_version gets set to."""
    record = (
        db.query(ClientVersionRecord)
        .filter(
            ClientVersionRecord.client_id == client_id,
            ClientVersionRecord.environment == environment,
        )
        .order_by(ClientVersionRecord.created_at.desc())
        .first()
    )
    return record.current_version if record else None


def release_tracker_rows(
    db: Session, client_id: int | None, environment: DeploymentEnvironment | None
) -> list[ClientVersionRecord]:
    """Full history, newest first — the Release Tracker tab's primary listing."""
    query = db.query(ClientVersionRecord).options(
        joinedload(ClientVersionRecord.client),
        joinedload(ClientVersionRecord.recorder),
    )
    if client_id is not None:
        query = query.filter(ClientVersionRecord.client_id == client_id)
    if environment is not None:
        query = query.filter(ClientVersionRecord.environment == environment)
    return query.order_by(ClientVersionRecord.created_at.desc()).all()


def clients_with_version_records(db: Session) -> list[Client]:
    """Clients to populate the filter dropdown with — only ones that actually have
    at least one ClientVersionRecord, mirroring clients_with_deployments() in
    app/services/dashboard.py."""
    return (
        db.query(Client)
        .join(ClientVersionRecord, ClientVersionRecord.client_id == Client.id)
        .distinct()
        .order_by(Client.name)
        .all()
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2.
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/release_tracker.py tests/test_release_tracker_service.py
git commit -m "Add release_tracker service query helpers"
```

---

### Task 6: `can_edit_client_version_record()` permission

**Files:**
- Modify: `app/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `ClientVersionRecord` (Task 1).
- Produces: `can_edit_client_version_record(current_user: User, record: ClientVersionRecord) -> bool`. Consumed by Task 10's tab template (Edit button visibility) and Task 11's edit route (actual enforcement).

- [ ] **Step 1: Write the failing tests**

`test_auth.py`'s existing tests all use the `web` fixture (from `tests/conftest.py`
— `client, session = web`) plus the `make_user` helper, not a raw `db_session`
fixture — match that convention rather than introducing a new one:

```python
# tests/test_auth.py — add near the bottom
from app.auth import can_edit_client_version_record
from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.user import UserRole


def _make_client_version_record(session, *, recorded_by):
    session.add(Client(id=1, name="CRM"))
    session.add(
        DeploymentRequest(
            id=1, request_type=RequestType.standard, client_id=1,
            environment=DeploymentEnvironment.live, status=RequestStatus.completed,
        )
    )
    session.flush()
    record = ClientVersionRecord(
        client_id=1, environment=DeploymentEnvironment.live, current_version="1.0",
        deployment_request_id=1, recorded_by=recorded_by,
    )
    session.add(record)
    session.flush()
    return record


def test_recorder_can_edit_their_own_client_version_record(web):
    _, session = web
    user = make_user(session, id=5, name="Deployer", username="deployer")
    record = _make_client_version_record(session, recorded_by=5)
    assert can_edit_client_version_record(user, record) is True


def test_other_user_cannot_edit_someone_elses_client_version_record(web):
    _, session = web
    other = make_user(session, id=6, name="Someone Else", username="someone-else")
    record = _make_client_version_record(session, recorded_by=5)
    assert can_edit_client_version_record(other, record) is False


def test_admin_can_edit_any_client_version_record(web):
    _, session = web
    admin = make_user(session, id=7, name="Root Admin", role=UserRole.admin, username="root")
    record = _make_client_version_record(session, recorded_by=5)
    assert can_edit_client_version_record(admin, record) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_auth.py -k client_version -v`
Expected: FAIL — `ImportError: cannot import name 'can_edit_client_version_record'`

- [ ] **Step 3: Write the permission function**

```python
# app/auth.py — add after can_edit_request

def can_edit_client_version_record(current_user: User, record) -> bool:
    """Whether current_user may correct this ClientVersionRecord's current_version:
    an admin, or whoever originally recorded it. No status-window restriction (see
    can_delete_request/can_edit_request for that pattern) — there's no approval
    workflow on these rows, just a plain typo correction, so it stays correctable
    indefinitely."""
    if current_user.role == UserRole.admin:
        return True
    return current_user.id == record.recorded_by
```

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2.
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "Add can_edit_client_version_record permission"
```

---

### Task 7: Excel export columns for Release Tracker rows

**Files:**
- Modify: `app/services/export.py`
- Test: `tests/test_export.py` (create if it doesn't already exist — check first with `ls tests/test_export.py`)

**Interfaces:**
- Consumes: `ClientVersionRecord` (Task 1).
- Produces: `release_tracker_rows_to_xlsx(rows: list[ClientVersionRecord], sheet_title: str) -> bytes`. Consumed by Task 10's export route.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_export.py
from datetime import datetime, timezone
from io import BytesIO

from openpyxl import load_workbook

from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
from app.models.deployment_request import DeploymentEnvironment
from app.models.user import User
from app.services.export import release_tracker_rows_to_xlsx


def test_release_tracker_rows_to_xlsx_writes_expected_columns():
    record = ClientVersionRecord(
        id=1,
        client_id=1,
        environment=DeploymentEnvironment.live,
        current_version="2026.34.34",
        previous_version="2026.34.30",
        main_version="2026.34.40",
        main_pr_number=1234,
        created_at=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
    )
    record.client = Client(name="CRM")
    record.recorder = User(name="Deployer")

    content = release_tracker_rows_to_xlsx([record], "Release Tracker")
    workbook = load_workbook(BytesIO(content))
    sheet = workbook.active

    header_row = [cell.value for cell in sheet[1]]
    assert header_row == [
        "Client", "System", "Current Version", "Previous Version",
        "Current Version at Main", "Recorded By", "Updated At",
    ]
    data_row = [cell.value for cell in sheet[2]]
    assert data_row == [
        "CRM", "Live", "2026.34.34", "2026.34.30",
        "2026.34.40 (PR #1234)", "Deployer", "2026-08-27 10:00 UTC",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_export.py -v`
Expected: FAIL — `ImportError: cannot import name 'release_tracker_rows_to_xlsx'`

- [ ] **Step 3: Write the export function**

```python
# app/services/export.py — add at the bottom, alongside the existing COLUMNS/rows_to_xlsx
from app.models.client_version_record import ClientVersionRecord

RELEASE_TRACKER_COLUMNS = [
    ("Client", lambda r: r.client.name if r.client else ""),
    ("System", lambda r: r.environment.value.capitalize() if r.environment else ""),
    ("Current Version", lambda r: r.current_version or ""),
    ("Previous Version", lambda r: r.previous_version or ""),
    (
        "Current Version at Main",
        lambda r: f"{r.main_version} (PR #{r.main_pr_number})" if r.main_version and r.main_pr_number
        else (r.main_version or ""),
    ),
    ("Recorded By", lambda r: r.recorder.name if r.recorder else ""),
    ("Updated At", lambda r: r.updated_at.strftime("%Y-%m-%d %H:%M UTC") if r.updated_at else ""),
]


def release_tracker_rows_to_xlsx(rows: list[ClientVersionRecord], sheet_title: str) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title[:31]

    headers = [label for label, _ in RELEASE_TRACKER_COLUMNS]
    sheet.append(headers)
    for row in rows:
        sheet.append([getter(row) for _, getter in RELEASE_TRACKER_COLUMNS])

    for index, (header, getter) in enumerate(RELEASE_TRACKER_COLUMNS, start=1):
        widest = max([len(header)] + [len(str(getter(row))) for row in rows]) if rows else len(header)
        sheet.column_dimensions[get_column_letter(index)].width = min(widest + 2, 40)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: same command as Step 2.
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/export.py tests/test_export.py
git commit -m "Add release_tracker_rows_to_xlsx export"
```

---

### Task 8: Extend `deploy_request` with `current_version` popup submission

**Files:**
- Modify: `app/routers/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `latest_current_version()` (Task 5), `BitbucketMainBranchStatus`
  (Task 1), `ClientVersionRecord` (Task 1).
- Produces: `POST /requests/{id}/deploy` now requires `current_version` for
  `standard`-type requests and inserts a `ClientVersionRecord`. Consumed by Task 10 (visible in the tab it feeds), and by Task 9/Task 11 (the frontend popup and the edit route's tests).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py`, near the existing deploy-related tests (search
for `test_.*deploy` to find them):

```python
# tests/test_dashboard.py — add
from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.client_version_record import ClientVersionRecord


def _seed_in_progress_standard_request(session, *, client_id=1, environment=DeploymentEnvironment.live):
    session.add(Client(id=client_id, name="CRM"))
    make_user(session, id=1, name="Requester", username="requester", password=DEFAULT_TEST_PASSWORD)
    make_user(
        session, id=3, name="Deployer", username="deployer", password=DEFAULT_TEST_PASSWORD,
        machine_group_id=DEPLOY_TEAM_MACHINE_GROUP_ID,
    )
    session.commit()
    request = DeploymentRequest(
        id=1, request_type=RequestType.standard, client_id=client_id, environment=environment,
        git_branch="release/v12", commit_hash="a1b2c3d", version="V12", requested_by=1,
        status=RequestStatus.in_progress, created_at=datetime.now(timezone.utc),
    )
    session.add(request)
    session.add(
        DeploymentExecution(
            request_id=1, executed_by=3, claimed_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc), status=ExecutionStatus.in_progress,
        )
    )
    session.commit()
    return request


def test_deploy_request_requires_current_version_for_standard_requests(web):
    client, session = web
    _seed_in_progress_standard_request(session)
    login_as(client, "deployer")

    response = client.post("/requests/1/deploy", data={"current_version": ""})

    assert response.status_code == 400
    assert session.get(DeploymentRequest, 1).status == RequestStatus.in_progress
    assert session.query(ClientVersionRecord).count() == 0


def test_deploy_request_creates_client_version_record(web):
    client, session = web
    _seed_in_progress_standard_request(session)
    session.add(BitbucketMainBranchStatus(id=1, version="2026.34.40", pr_number=1234))
    session.commit()
    login_as(client, "deployer")

    response = client.post("/requests/1/deploy", data={"current_version": "2026.34.34"}, follow_redirects=False)

    assert response.status_code == 303
    assert session.get(DeploymentRequest, 1).status == RequestStatus.completed
    record = session.query(ClientVersionRecord).one()
    assert record.client_id == 1
    assert record.environment == DeploymentEnvironment.live
    assert record.current_version == "2026.34.34"
    assert record.previous_version is None
    assert record.main_version == "2026.34.40"
    assert record.main_pr_number == 1234
    assert record.deployment_request_id == 1
    assert record.recorded_by == 3


def test_deploy_request_fills_previous_version_from_prior_record(web):
    client, session = web
    _seed_in_progress_standard_request(session)
    session.add(
        ClientVersionRecord(
            client_id=1, environment=DeploymentEnvironment.live, current_version="2026.34.30",
            deployment_request_id=1, recorded_by=3,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    login_as(client, "deployer")

    client.post("/requests/1/deploy", data={"current_version": "2026.34.34"})

    latest = session.query(ClientVersionRecord).order_by(ClientVersionRecord.id.desc()).first()
    assert latest.previous_version == "2026.34.30"
    assert latest.current_version == "2026.34.34"


def test_deploy_request_works_without_a_bitbucket_sync_yet(web):
    # No BitbucketMainBranchStatus row at all — main_version/main_pr_number just
    # come through null rather than erroring.
    client, session = web
    _seed_in_progress_standard_request(session)
    login_as(client, "deployer")

    response = client.post("/requests/1/deploy", data={"current_version": "2026.34.34"}, follow_redirects=False)

    assert response.status_code == 303
    record = session.query(ClientVersionRecord).one()
    assert record.main_version is None
    assert record.main_pr_number is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_dashboard.py -k "deploy_request_requires_current or deploy_request_creates or deploy_request_fills or deploy_request_works_without" -v`
Expected: FAIL — first test likely fails with 422 (unexpected extra form data
rejected or field required error differs from 400), and the rest fail because
no `ClientVersionRecord` row gets created at all yet.

- [ ] **Step 3: Extend `deploy_request`**

```python
# app/routers/dashboard.py — imports, add:
from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.client_version_record import ClientVersionRecord
from app.services.release_tracker import latest_current_version
```

Replace the existing `deploy_request` function body:

```python
@router.post("/requests/{request_id}/deploy")
def deploy_request(
    request_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_deploy_team_member),
    # Only required/used for `standard` requests — see the Release Tracker design
    # doc. db_dump_restore/test_local requests don't carry a client+environment
    # pair, so this field is simply ignored for them (Form(None), not Form(...),
    # so their bare-submit button — which never sends this field — still works).
    current_version: str | None = Form(None),
):
    deployment_request = _get_request_or_404(db, request_id)
    if deployment_request.status != RequestStatus.in_progress:
        raise HTTPException(status_code=409, detail="Request has not been started yet")

    if deployment_request.request_type == RequestType.standard:
        current_version = (current_version or "").strip()
        if not current_version:
            return templates.TemplateResponse(
                request,
                "request_list.html",
                {"current_user": current_user, "requests": [], "error": "Current version is required."},
                status_code=400,
            )

    # Updates the row start_request() above created — DeploymentExecution.request_id is
    # unique-per-request (app/models/deployment_execution.py), so this is always exactly
    # one row, not a new insert. Deliberately not restricted to whoever ran start_request:
    # any deploy-team member may mark it deployed, same "membership, not a personal claim
    # lock" model the rest of this router already uses (see require_deploy_team_member).
    execution = db.query(DeploymentExecution).filter_by(request_id=request_id).one()
    execution.completed_at = datetime.now(timezone.utc)
    execution.status = ExecutionStatus.completed
    deployment_request.status = RequestStatus.completed

    if deployment_request.request_type == RequestType.standard:
        bitbucket_status = db.get(BitbucketMainBranchStatus, 1)
        db.add(
            ClientVersionRecord(
                client_id=deployment_request.client_id,
                environment=deployment_request.environment,
                current_version=current_version,
                previous_version=latest_current_version(
                    db, deployment_request.client_id, deployment_request.environment
                ),
                main_version=bitbucket_status.version if bitbucket_status else None,
                main_pr_number=bitbucket_status.pr_number if bitbucket_status else None,
                deployment_request_id=deployment_request.id,
                recorded_by=current_user.id,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )

    db.commit()
    manager.notify()
    return RedirectResponse(url="/requests", status_code=303)
```

Note the 400 branch above deliberately re-renders a minimal `request_list.html`
context rather than the full one `list_requests()` builds — that's acceptable
because Task 9's frontend dialog validates `current_version` is non-empty
client-side too (see `required` on the input), so this server-side 400 is a
backstop that should rarely actually render for a real user; it mirrors the
"friendlier error" pattern used elsewhere in this router (e.g.
`create_request`'s `rerender()`), just simplified since this route doesn't
already build a `_request_form_context()`-style dict. If this feels fragile
during implementation, an acceptable alternative is a plain
`HTTPException(400, "Current version is required.")` instead — pick whichever
reads cleaner once you see it next to the real code; either satisfies the tests
above (`response.status_code == 400`).

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2.
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest -q`
Expected: all pass (the pre-existing unrelated `test_requests_queue_branch_commit_has_view_button_for_full_text` failure, if still present on `master`, is not something this task touches — confirm it's the same failure as before, not a new one).

- [ ] **Step 6: Commit**

```bash
git add app/routers/dashboard.py tests/test_dashboard.py
git commit -m "Record ClientVersionRecord when a Standard Deployment is marked deployed"
```

---

### Task 9: Deploy-confirmation dialog — `previous_versions` context + `request_list.html`

**Files:**
- Modify: `app/routers/dashboard.py`
- Modify: `app/templates/request_list.html`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `latest_current_version()` (Task 5), `POST /requests/{id}/deploy` with `current_version` (Task 8).
- Produces: `list_requests()`'s template context gains `"previous_versions": dict[str, str | None]`, keyed by `f"{client_id}:{environment.value}"`; `request_list.html` renders the actual popup. This is one deliverable (the context computation is only meaningful once the template consumes it), tested end-to-end at the HTTP level like the rest of this router's tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard.py — add
def test_requests_queue_renders_deploy_version_dialog_for_standard_in_progress_row(web):
    client, session = web
    _seed_in_progress_standard_request(session)
    session.add(
        ClientVersionRecord(
            client_id=1, environment=DeploymentEnvironment.live, current_version="2026.34.30",
            deployment_request_id=1, recorded_by=3,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    login_as(client, "deployer")

    response = client.get("/requests")

    assert response.status_code == 200
    assert 'id="deploy-version-modal"' in response.text
    assert 'data-deploy-request-id="1"' in response.text
    assert 'data-deploy-previous-version="2026.34.30"' in response.text
    # The button itself is no longer a bare submit for standard rows:
    assert 'type="button" class="deploy" data-deploy-request-id="1"' in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_dashboard.py -k renders_deploy_version_dialog -v`
Expected: FAIL — `KeyError` or `AssertionError` (neither the context key nor the template markup exist yet).

- [ ] **Step 3: Add the `previous_versions` lookup to `list_requests`**

```python
# app/routers/dashboard.py — add to the imports
from app.services.release_tracker import latest_current_version
```

```python
# app/routers/dashboard.py — inside list_requests(), after `requests_ = (...)` is built,
# before the `active_requests = (...)` block:

    # Feeds the deploy-confirmation popup's read-only "Previous version" field
    # (request_list.html) — only meaningful for standard, in_progress rows, but
    # cheap enough to just compute for every distinct (client_id, environment)
    # pair actually on this page rather than filtering further.
    previous_versions: dict[str, str | None] = {}
    for r in requests_:
        if r.request_type == RequestType.standard and r.status == RequestStatus.in_progress and r.client_id and r.environment:
            key = f"{r.client_id}:{r.environment.value}"
            if key not in previous_versions:
                previous_versions[key] = latest_current_version(db, r.client_id, r.environment)
```

Add `"previous_versions": previous_versions,` to the context dict returned by
`list_requests()`, alongside the existing `"can_edit_request"` key etc.

- [ ] **Step 4: Update the template**

Replace the `in_progress` branch of the Action column (`request_list.html`, the
block containing the current bare `Mark Deployed` submit):

```html
              {% elif r.status == RequestStatus.in_progress %}
                {% if can_deploy %}
                  {% if r.request_type == RequestType.standard %}
                    {% set version_key = (r.client_id ~ ":" ~ r.environment.value) if r.client_id and r.environment else "" %}
                    <button
                      type="button"
                      class="deploy"
                      data-deploy-request-id="{{ r.id }}"
                      data-deploy-client="{{ r.client.name if r.client else '' }}"
                      data-deploy-system="{{ r.environment.value if r.environment else '' }}"
                      data-deploy-previous-version="{{ previous_versions.get(version_key) or '' }}"
                    >Mark Deployed</button>
                  {% else %}
                    <form method="post" action="/requests/{{ r.id }}/deploy" class="inline-form">
                      <button type="submit" class="deploy">Mark Deployed</button>
                    </form>
                  {% endif %}
                {% else %}
                  <span class="muted">In progress — awaiting completion</span>
                {% endif %}
```

Add the dialog markup right before the closing `{% endblock %}`'s final
`</script>` block (i.e. after the pagination `<nav>`, alongside where the
existing page script starts) — reusing `.changes-modal`'s visual chrome class
directly (same border/padding/shadow/backdrop, just a distinct id and real form
inputs instead of read-only text):

```html
  <dialog id="deploy-version-modal" class="changes-modal">
    <h2>Confirm Deployment</h2>
    <form method="post" id="deploy-version-form" class="form">
      <div class="field">
        <label>Client</label>
        <p class="static-field" id="deploy-version-client"></p>
      </div>
      <div class="field">
        <label>System</label>
        <p class="static-field" id="deploy-version-system"></p>
      </div>
      <div class="field">
        <label>Previous version</label>
        <p class="static-field" id="deploy-version-previous"></p>
      </div>
      <div class="field">
        <label for="deploy-version-current">Current version</label>
        <input type="text" id="deploy-version-current" name="current_version" required placeholder="e.g. 2026.34.34">
      </div>
      <div class="changes-modal-actions">
        <button type="submit" class="deploy">Confirm Deployment</button>
        <button type="button" class="button-secondary" id="deploy-version-cancel">Cancel</button>
      </div>
    </form>
  </dialog>
```

Add the wiring script — inside the existing bottom `<script>` block's IIFE (right
after the `connectLiveUpdateSocket();`/`setTimeout(...)` lines, still inside the
same `(function () { ... })();`):

```js
      // --- Deploy-confirmation popup (Standard Deployment only) ---------------------
      var deployDialog = document.getElementById("deploy-version-modal");
      if (deployDialog) {
        var deployForm = document.getElementById("deploy-version-form");
        var deployClientEl = document.getElementById("deploy-version-client");
        var deploySystemEl = document.getElementById("deploy-version-system");
        var deployPreviousEl = document.getElementById("deploy-version-previous");
        var deployCurrentInput = document.getElementById("deploy-version-current");

        document.querySelectorAll("button.deploy[data-deploy-request-id]").forEach(function (button) {
          button.addEventListener("click", function () {
            var requestId = button.getAttribute("data-deploy-request-id");
            deployForm.action = "/requests/" + requestId + "/deploy";
            deployClientEl.textContent = button.getAttribute("data-deploy-client") || "—";
            deploySystemEl.textContent = button.getAttribute("data-deploy-system") || "—";
            deployPreviousEl.textContent = button.getAttribute("data-deploy-previous-version") || "—";
            deployCurrentInput.value = "";
            deployDialog.showModal();
          });
        });

        document.getElementById("deploy-version-cancel").addEventListener("click", function () {
          deployDialog.close();
        });
        deployDialog.addEventListener("click", function (event) {
          if (event.target === deployDialog) deployDialog.close();
        });
      }
```

- [ ] **Step 5: Run test to verify it passes**

Run: same command as Step 2.
Expected: PASS

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest -q`
Expected: all pass except the known pre-existing unrelated failure.

- [ ] **Step 7: Manually verify in a real browser**

```bash
docker compose up -d --build app
```
Log in, get a `standard` request to `in_progress` (Start Deployment), open
`/requests`, click "Mark Deployed" — confirm the dialog opens, shows client/
system/previous-version, requires a current version, and submitting actually
completes the deploy and inserts a `client_version_records` row (check via
`docker exec deployment_status-db-1 psql -U deploy_tracker -d deploy_tracker -c "select * from client_version_records;"`).
Also confirm a `db_dump_restore`/`test_local` row's "Mark Deployed" still works as
a bare submit, unaffected.

- [ ] **Step 8: Commit**

```bash
git add app/routers/dashboard.py app/templates/request_list.html tests/test_dashboard.py
git commit -m "Add deploy-confirmation popup for Standard Deployment requests"
```

---

### Task 10: Release Tracker tab — route, template, nav link, export

**Files:**
- Create: `app/templates/release_tracker.html`
- Modify: `app/routers/dashboard.py` (add `_release_tracker_filters` helper) — or
  create `app/routers/release_tracker.py` (decide per the note below)
- Modify: `app/main.py`
- Modify: `app/templates/base.html`
- Test: `tests/test_release_tracker.py`

**Note on router placement:** `app/routers/dashboard.py` is already ~900 lines.
Put the new routes in a **new file** `app/routers/release_tracker.py` instead of
growing `dashboard.py` further — cleaner boundary, and this feature's routes
don't share any request-scoped helpers with `dashboard.py` except
`_parse_filters`-style query parsing, which is small enough to duplicate locally
(3 lines) rather than import across router files.

**Interfaces:**
- Consumes: `release_tracker_rows()`, `clients_with_version_records()` (Task 5),
  `release_tracker_rows_to_xlsx()` (Task 7), `can_edit_client_version_record()`
  (Task 6, wired here but exercised fully in Task 11).
- Produces: `GET /release-tracker`, `GET /release-tracker/export.xlsx`. Nav link.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_release_tracker.py
from datetime import datetime, timezone

from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def _seed_release_tracker_row(session, *, client_id=1, client_name="CRM", current_version="2026.34.34"):
    session.add(Client(id=client_id, name=client_name))
    make_user(session, id=1, name="Deployer", username="deployer", password=DEFAULT_TEST_PASSWORD)
    session.add(
        DeploymentRequest(
            id=client_id, request_type=RequestType.standard, client_id=client_id,
            environment=DeploymentEnvironment.live, status=RequestStatus.completed,
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    record = ClientVersionRecord(
        client_id=client_id, environment=DeploymentEnvironment.live, current_version=current_version,
        deployment_request_id=client_id, recorded_by=1,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    session.add(record)
    session.commit()
    return record


def test_release_tracker_page_renders_for_any_logged_in_user(web):
    client, session = web
    _seed_release_tracker_row(session)
    login_as(client, "deployer")

    response = client.get("/release-tracker")

    assert response.status_code == 200
    assert "2026.34.34" in response.text
    assert "CRM" in response.text


def test_release_tracker_requires_login(web):
    client, session = web
    _seed_release_tracker_row(session)

    response = client.get("/release-tracker", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_release_tracker_filters_by_client(web):
    client, session = web
    _seed_release_tracker_row(session, client_id=1, client_name="CRM", current_version="1.0")
    _seed_release_tracker_row(session, client_id=2, client_name="Acme", current_version="2.0")
    login_as(client, "deployer")

    response = client.get("/release-tracker", params={"client_id": "1"})

    assert "1.0" in response.text
    assert "2.0" not in response.text


def test_release_tracker_export_xlsx(web):
    client, session = web
    _seed_release_tracker_row(session)
    login_as(client, "deployer")

    response = client.get("/release-tracker/export.xlsx")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_nav_shows_release_tracker_link_for_logged_in_user(web):
    client, session = web
    _seed_release_tracker_row(session)
    login_as(client, "deployer")

    response = client.get("/requests")

    assert 'href="/release-tracker"' in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_release_tracker.py -v`
Expected: FAIL — `404 Not Found` on `/release-tracker` (route doesn't exist yet).

- [ ] **Step 3: Write the router**

```python
# app/routers/release_tracker.py
"""Web UI for the Release Tracker tab — per-client/system version history, fed by
the deploy-confirmation popup in app/routers/dashboard.py's deploy_request(). See
docs/superpowers/specs/2026-08-27-release-tracker-design.md.
"""

from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import can_edit_client_version_record, require_login
from app.database import get_db
from app.models.client import Client
from app.models.client_version_record import ClientVersionRecord
from app.models.deployment_request import DeploymentEnvironment
from app.models.user import User
from app.services.export import release_tracker_rows_to_xlsx
from app.services.release_tracker import clients_with_version_records, release_tracker_rows
from app.static_version import STATIC_VERSION

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION


def _parse_release_tracker_filters(client_id: str | None, environment: str | None):
    parsed_client_id = int(client_id) if client_id else None
    parsed_environment = DeploymentEnvironment(environment) if environment else None
    return parsed_client_id, parsed_environment


def _filter_context(db: Session, client_id: int | None, environment: DeploymentEnvironment | None) -> dict:
    return {
        "filter_clients": clients_with_version_records(db),
        "filter_environments": list(DeploymentEnvironment),
        "selected_client_id": client_id,
        "selected_environment": environment,
    }


@router.get("/release-tracker")
def release_tracker_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    client_id: str | None = None,
    environment: str | None = None,
):
    parsed_client_id, parsed_environment = _parse_release_tracker_filters(client_id, environment)
    rows = release_tracker_rows(db, parsed_client_id, parsed_environment)
    context = {
        "current_user": current_user,
        "rows": rows,
        "can_edit_record": lambda r: can_edit_client_version_record(current_user, r),
    }
    context.update(_filter_context(db, parsed_client_id, parsed_environment))
    return templates.TemplateResponse(request, "release_tracker.html", context)


@router.get("/release-tracker/export.xlsx")
def release_tracker_export_xlsx(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    client_id: str | None = None,
    environment: str | None = None,
):
    parsed_client_id, parsed_environment = _parse_release_tracker_filters(client_id, environment)
    rows = release_tracker_rows(db, parsed_client_id, parsed_environment)
    content = release_tracker_rows_to_xlsx(rows, "Release Tracker")
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=release-tracker.xlsx"},
    )
```

(Task 11 adds the `GET`/`POST /release-tracker/{id}/edit` routes to this same
file.)

Register it in `app/main.py`:

```python
from app.routers.release_tracker import router as release_tracker_router
# ...
app.include_router(release_tracker_router)
```

- [ ] **Step 4: Write the template**

```html
{# app/templates/release_tracker.html #}
{% extends "base.html" %}
{% block title %}Release Tracker — Deployment Tracker{% endblock %}
{% block content %}
  <p class="eyebrow">Versions</p>
  <h1>Release Tracker</h1>
  <p class="subtitle">Current + previous deployed version per client and system, and
    what's on <code>main</code> at the moment each deployment was confirmed.</p>

  <form method="get" action="/release-tracker" class="filter-bar">
    <div class="filter-field">
      <label for="client_id">Client</label>
      <select name="client_id" id="client_id">
        <option value="">All clients</option>
        {% for c in filter_clients %}
          <option value="{{ c.id }}" {% if selected_client_id == c.id %}selected{% endif %}>{{ c.name }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="filter-field">
      <label for="environment">System</label>
      <select name="environment" id="environment">
        <option value="">All systems</option>
        {% for env in filter_environments %}
          <option value="{{ env.value }}" {% if selected_environment == env %}selected{% endif %}>{{ env.value | capitalize }}</option>
        {% endfor %}
      </select>
    </div>
    <button type="submit" class="button-primary">Filter</button>
    <a href="/release-tracker" class="button-secondary">Reset</a>
    <a
      href="/release-tracker/export.xlsx?client_id={{ selected_client_id or '' }}&environment={{ selected_environment.value if selected_environment else '' }}"
      class="button-secondary"
    >Export to Excel</a>
  </form>

  {% if rows %}
    <div class="table-scroll">
    <table class="status-table release-tracker-table">
      <thead>
        <tr>
          <th>Client</th>
          <th>System</th>
          <th>Current Version</th>
          <th>Previous Version</th>
          <th>Current Version at Main</th>
          <th>Recorded By</th>
          <th>Updated At</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        {% for row in rows %}
        <tr>
          <td>{{ row.client.name if row.client else "—" }}</td>
          <td><span class="badge badge-{{ row.environment.value }}">{{ row.environment.value | capitalize }}</span></td>
          <td>{{ row.current_version }}</td>
          <td>{{ row.previous_version or "—" }}</td>
          <td>
            {% if row.main_version and row.main_pr_number %}
              {{ row.main_version }} (PR #{{ row.main_pr_number }})
            {% elif row.main_version %}
              {{ row.main_version }}
            {% else %}
              —
            {% endif %}
          </td>
          <td>{{ row.recorder.name if row.recorder else "—" }}</td>
          <td>{{ row.updated_at.strftime("%Y-%m-%d %H:%M UTC") if row.updated_at else "—" }}</td>
          <td>
            {% if can_edit_record(row) %}
              <a href="/release-tracker/{{ row.id }}/edit" class="action-link edit">Edit</a>
            {% else %}
              <span class="muted">—</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    </div>
  {% else %}
    <p class="empty-state">No deployments have been confirmed with a version yet.</p>
  {% endif %}
{% endblock %}
```

- [ ] **Step 5: Add the nav link**

```html
{# app/templates/base.html — inside the nav block, after the Requests link and
   before the Admin-role conditional (or after it — order matches the user's
   mental model of Dashboard/History/Requests/Release Tracker/Admin) #}
    <a href="/release-tracker">Release Tracker</a>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: same command as Step 2.
Expected: PASS (5 tests)

- [ ] **Step 7: Run the full test suite**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest -q`
Expected: all pass except the known pre-existing unrelated failure.

- [ ] **Step 8: Commit**

```bash
git add app/routers/release_tracker.py app/templates/release_tracker.html app/templates/base.html app/main.py tests/test_release_tracker.py
git commit -m "Add Release Tracker tab (route, template, nav link, Excel export)"
```

---

### Task 11: Row correction (edit `current_version`)

**Files:**
- Modify: `app/routers/release_tracker.py`
- Create: `app/templates/release_tracker_edit.html`
- Test: `tests/test_release_tracker.py`

**Interfaces:**
- Consumes: `can_edit_client_version_record()` (Task 6).
- Produces: `GET`/`POST /release-tracker/{id}/edit`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_release_tracker.py — add
def test_recorder_can_edit_their_own_record(web):
    client, session = web
    record = _seed_release_tracker_row(session, current_version="2026.34.34")
    login_as(client, "deployer")

    response = client.post(
        f"/release-tracker/{record.id}/edit",
        data={"current_version": "2026.34.35"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(record)
    assert record.current_version == "2026.34.35"


def test_other_user_cannot_edit_someone_elses_record(web):
    client, session = web
    record = _seed_release_tracker_row(session, current_version="2026.34.34")
    make_user(session, id=2, name="Other Dev", username="otherdev", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    login_as(client, "otherdev")

    response = client.post(f"/release-tracker/{record.id}/edit", data={"current_version": "9.9.9"})

    assert response.status_code == 403
    session.refresh(record)
    assert record.current_version == "2026.34.34"


def test_edit_rejects_blank_current_version(web):
    client, session = web
    record = _seed_release_tracker_row(session, current_version="2026.34.34")
    login_as(client, "deployer")

    response = client.post(f"/release-tracker/{record.id}/edit", data={"current_version": "  "})

    assert response.status_code == 400
    session.refresh(record)
    assert record.current_version == "2026.34.34"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest tests/test_release_tracker.py -k edit -v`
Expected: FAIL — `404 Not Found`.

- [ ] **Step 3: Add the edit routes**

```python
# app/routers/release_tracker.py — add
from fastapi import Form


def _get_record_or_404(db: Session, record_id: int) -> ClientVersionRecord:
    record = db.get(ClientVersionRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Client version record not found")
    return record


@router.get("/release-tracker/{record_id}/edit")
def release_tracker_edit_form(
    record_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
):
    record = _get_record_or_404(db, record_id)
    if not can_edit_client_version_record(current_user, record):
        raise HTTPException(status_code=403, detail="You don't have permission to edit this record")
    return templates.TemplateResponse(
        request, "release_tracker_edit.html", {"current_user": current_user, "record": record}
    )


@router.post("/release-tracker/{record_id}/edit")
def release_tracker_edit(
    record_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
    current_version: str = Form(...),
):
    from datetime import datetime, timezone

    record = _get_record_or_404(db, record_id)
    if not can_edit_client_version_record(current_user, record):
        raise HTTPException(status_code=403, detail="You don't have permission to edit this record")

    current_version = current_version.strip()
    if not current_version:
        return templates.TemplateResponse(
            request,
            "release_tracker_edit.html",
            {"current_user": current_user, "record": record, "error": "Current version is required."},
            status_code=400,
        )

    record.current_version = current_version
    record.updated_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url="/release-tracker", status_code=303)
```

(Move the `from datetime import datetime, timezone` to the top of the file
alongside the other imports rather than inline — shown inline above only to keep
this diff-sized snippet self-contained.)

- [ ] **Step 4: Write the edit template**

```html
{# app/templates/release_tracker_edit.html #}
{% extends "base.html" %}
{% block title %}Edit Version Record — Deployment Tracker{% endblock %}
{% block content %}
  <h1>Edit Version Record — {{ record.client.name if record.client else "—" }} ({{ record.environment.value | capitalize }})</h1>

  {% if error %}
    <p class="error">{{ error }}</p>
  {% endif %}

  <form method="post" action="/release-tracker/{{ record.id }}/edit" class="form">
    <div class="field">
      <label>Previous version <span class="optional">(not editable)</span></label>
      <p class="static-field">{{ record.previous_version or "—" }}</p>
    </div>
    <div class="field">
      <label>Current version at main <span class="optional">(not editable)</span></label>
      <p class="static-field">
        {% if record.main_version and record.main_pr_number %}
          {{ record.main_version }} (PR #{{ record.main_pr_number }})
        {% else %}
          {{ record.main_version or "—" }}
        {% endif %}
      </p>
    </div>
    <div class="field">
      <label for="current_version">Current version</label>
      <input type="text" id="current_version" name="current_version" required value="{{ record.current_version }}">
    </div>
    <button type="submit">Save Changes</button>
    <a href="/release-tracker" class="link-button">Cancel</a>
  </form>
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: same command as Step 2.
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full test suite**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest -q`
Expected: all pass except the known pre-existing unrelated failure.

- [ ] **Step 7: Manually verify in a real browser**

```bash
docker compose up -d --build app
docker exec deployment_status-app-1 alembic upgrade head
```
Visit `/release-tracker`, confirm the table renders, filters work, Export to
Excel downloads a real file, and Edit → change current_version → Save actually
persists (check via `docker exec deployment_status-db-1 psql -U deploy_tracker -d deploy_tracker -c "select current_version, updated_at from client_version_records;"`).

- [ ] **Step 8: Commit**

```bash
git add app/routers/release_tracker.py app/templates/release_tracker_edit.html tests/test_release_tracker.py
git commit -m "Add Release Tracker row correction (edit current_version)"
```

---

### Task 12: Senior-developer cleanup pass

**Files:** All files touched by Tasks 1-12 (see File Structure above).

**Interfaces:** None — this task changes no behavior, only removes cruft. Every
test from Tasks 1-12 must still pass unchanged afterward.

Confirmed with the user: once all the above is implemented, do an explicit pass
as an experienced developer reviewing this feature's diff specifically for:

- **Dead code** — any function, import, template block, or CSS rule added during
  Tasks 1-12 that ended up unused (e.g. if the alternate 400-handling approach
  noted in Task 8/Step 3 was chosen, make sure the other approach's leftover
  scaffolding, if any, isn't still sitting around).
- **Unnecessary comments** — this codebase's existing style leans heavily on
  explanatory comments for *non-obvious* decisions (see `task_source.py`,
  `auth.py`), which is worth matching, but comments that just restate what the
  next line already says plainly should go. Read each new comment and ask "would
  a skilled developer new to this file actually need this, or is the code already
  self-explanatory?"
- **Duplication that crept in across tasks** — e.g. compare `list_requests()`'s
  version-lookup loop (Task 9) against `release_tracker_rows()` (Task 5) for any
  logic that should have been shared but wasn't.

- [ ] **Step 1: Review the full diff since branching off `master`**

```bash
git diff master...HEAD -- app/ tests/ | less
```

- [ ] **Step 2: Fix anything found, inline**

- [ ] **Step 3: Re-run the full test suite to confirm nothing broke**

Run: `docker run --rm -v "$(pwd)":/srv/app -w /srv/app -e DATABASE_URL="sqlite:///:memory:" deployment_status-app python -m pytest -q`
Expected: all pass except the known pre-existing unrelated failure.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Clean up dead code and unnecessary comments from the Release Tracker feature"
```

---

## Post-plan follow-up (not part of this plan's tasks)

- The real `BITBUCKET_API_TOKEN` still needs to be added to the deployment's
  `.env` by the user before `sync-bitbucket-main` can run against the real API —
  every task above is fully testable via mocks without it.
- Once the token is set, add the crontab entry from Task 4/Step 7 to the actual
  host's crontab (the plan only documents it in README.md; it doesn't install it).
