# Return a Request to Its Requester — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let DevOps hand a request back to its requester with a reason instead of leaving it stuck in Pending Deployment, keeping every return as a permanent log.

**Architecture:** Two new `RequestStatus` values (`returned`, `withdrawn`) and one new table (`request_returns`, one row per return). Three new POST routes — return, resubmit, withdraw — following the existing approve/reject route shape. Because adding a status means touching five separate constants, Task 1 lands the status values together with exhaustiveness tests that iterate `RequestStatus` itself, so every later task and every future status is protected before any feature code exists.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Alembic, Jinja2, Postgres 16, pytest.

**Spec:** `docs/superpowers/specs/2026-09-17-return-request-design.md`

## Global Constraints

- Run tests with `.venv/bin/python -m pytest -q -p no:cacheprovider`. **`python` is not on PATH** (CLAUDE.md).
- The **whole** suite must pass before every commit, not just the touched file.
- Write the failing test first and *watch it fail*. A test that passes when first written has proved nothing.
- Never run migrations against `deploy_tracker`; use a throwaway database (CLAUDE.md).
- Alembic must end on exactly **one head**. Check a revision id is free before using it: `grep -rl "<id>" alembic/versions/`.
- Tests build their schema from `Base.metadata.create_all` and never run migrations — a broken migration cannot fail the suite.
- CSS must use `:root` tokens (`var(--amber)`, `var(--slate-chip)`, …). **Never hardcode hex.**
- Comments explain *why*, naming the incident that motivated the code, matching the surrounding style.

---

### Task 1: The two statuses, and defences so a status can never half-exist

Lands `returned` and `withdrawn` plus every constant they must appear in, guarded by tests that iterate the enum. Do this first: until it exists, any later task that adds a status value 500s the Requests page.

**Files:**
- Modify: `app/models/deployment_request.py` (enum values; delete dead constant)
- Modify: `app/routers/dashboard.py` (`STATUS_LABELS`, `RAIL_STAGES`, `OPEN_REQUEST_STATUS_ORDER`, `FINISHED_REQUEST_STATUSES`)
- Modify: `app/templates/request_list.html:162` (bare lookup → `.get`)
- Test: `tests/test_request_status_coverage.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `RequestStatus.returned`, `RequestStatus.withdrawn`; `NEUTRAL_RAIL` in `app/routers/dashboard.py`.

- [ ] **Step 1: Write the failing exhaustiveness tests**

Create `tests/test_request_status_coverage.py`:

```python
"""Every RequestStatus must be fully wired up.

Adding a status to this app means touching five separate constants and each
miss fails differently — RAIL_STAGES is a bare dict lookup inside
request_list.html's row loop, so a missing entry 500s the whole Requests
page, while a missing sort-set entry just silently sinks the row into
history (which is how the pending_intake ordering regression shipped with a
green suite). These tests iterate the enum itself, so a status added later
fails them without anyone remembering this file exists.
"""

import pytest

from app.models.deployment_request import (
    DeploymentRequest,
    RequestStatus,
)
from app.routers.dashboard import (
    FINISHED_REQUEST_STATUSES,
    OPEN_REQUEST_STATUS_ORDER,
    RAIL_STAGES,
    STATUS_LABELS,
)


@pytest.mark.parametrize("status", list(RequestStatus), ids=lambda s: s.value)
def test_every_status_has_a_rail(status):
    assert status in RAIL_STAGES


@pytest.mark.parametrize("status", list(RequestStatus), ids=lambda s: s.value)
def test_every_status_has_a_label(status):
    assert status in STATUS_LABELS


@pytest.mark.parametrize("status", list(RequestStatus), ids=lambda s: s.value)
def test_every_status_is_either_open_or_finished(status):
    in_open = status in OPEN_REQUEST_STATUS_ORDER
    in_finished = status in FINISHED_REQUEST_STATUSES
    assert in_open != in_finished, (
        f"{status.value} is in "
        f"{'both' if in_open else 'neither'} of OPEN_REQUEST_STATUS_ORDER / "
        "FINISHED_REQUEST_STATUSES — it must be in exactly one, or it sorts wrong silently"
    )


def test_new_statuses_exist():
    assert RequestStatus.returned.value == "returned"
    assert RequestStatus.withdrawn.value == "withdrawn"
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_request_status_coverage.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: returned` on the import-time parametrize is fine too; the point is it does not pass.

- [ ] **Step 3: Add the enum values**

In `app/models/deployment_request.py`, inside `class RequestStatus`, after `rolled_back`:

```python
    # DevOps could not deploy this through no fault of their own — most often the
    # git branch named on it was deleted — so it goes back to the requester to fix
    # and resubmit. Distinct from `rejected`, which is the approval gate's verdict
    # on whether the work should happen at all: returned means "not yet, and here
    # is what to fix". See docs/superpowers/specs/2026-09-17-return-request-design.md.
    returned = "returned"
    # The requester abandoned a returned request. Terminal, and deliberately not a
    # delete: the request carries a return log by this point, and deleting the row
    # would destroy exactly the history a team lead goes looking for.
    withdrawn = "withdrawn"
```

- [ ] **Step 4: Delete the dead constant in the same file**

Remove line 13's `ACTIVE_REQUEST_STATUSES = ("submitted", ...)`. It is a tuple of *strings* referenced nowhere in `app/`, `tests/` or `alembic/` — verify with `grep -rn "ACTIVE_REQUEST_STATUSES" app tests alembic | grep -v FOR_NOTIFICATIONS` (only its own definition should match). It is dead, and it reads exactly like the list someone would update while adding a status, believing they had done something.

- [ ] **Step 5: Wire up the four constants in `app/routers/dashboard.py`**

`STATUS_LABELS` — add:

```python
    RequestStatus.returned: "Returned",
    RequestStatus.withdrawn: "Withdrawn",
```

`RAIL_STAGES` — add, and define the fallback just above the dict:

```python
# Used when a status has no rail of its own. request_list.html looks rails up
# through .get() with this default, so a status someone forgets to add here
# renders a plain row instead of raising KeyError inside the row loop and
# taking the entire Requests page down with it.
NEUTRAL_RAIL = _Rail(("empty", "empty", "empty", "empty"), None)
```

```python
    # Back to the first dot, amber, and pulsing — same shape as pending_approval,
    # because the request really has gone back to the start and really is waiting
    # on a person.
    RequestStatus.returned: _Rail(("amber", "empty", "empty", "empty"), 0),
    # Slate, not red: withdrawing is not a failure or a rejection, it is a decision
    # not to proceed. No pulse — nothing is waiting on anyone.
    RequestStatus.withdrawn: _Rail(("slate", "empty", "empty", "empty"), None),
```

`OPEN_REQUEST_STATUS_ORDER` — put `returned` **first**, before `pending_approval`:

```python
OPEN_REQUEST_STATUS_ORDER = (
    # First on purpose: a returned request is the only one that has moved
    # *backwards*, and it waits on someone who is not watching the deploy queue,
    # so it is the easiest thing on the page to forget.
    RequestStatus.returned,
    RequestStatus.pending_approval,
    RequestStatus.approved,
    RequestStatus.claimed,
    RequestStatus.in_progress,
    RequestStatus.pending_intake,
    RequestStatus.submitted,
)
```

`FINISHED_REQUEST_STATUSES` — add `RequestStatus.withdrawn`.

- [ ] **Step 6: Make the template lookup non-fatal**

`app/templates/request_list.html:162`:

```jinja
            {# .get, not [] — a status missing from RAIL_STAGES would otherwise raise
               inside this loop and 500 the whole page rather than one row. The
               exhaustiveness test in tests/test_request_status_coverage.py is the
               real guard; this is so a miss is never catastrophic in production. #}
            {% set rail = rail_stages.get(r.status, neutral_rail) %}
```

and pass it in `list_requests()`'s template context, beside `"rail_stages": RAIL_STAGES`:

```python
            "neutral_rail": NEUTRAL_RAIL,
```

- [ ] **Step 7: Run the tests and watch them pass**

Run: `.venv/bin/python -m pytest tests/test_request_status_coverage.py -q -p no:cacheprovider`
Expected: PASS (all parametrized cases, including the two new statuses).

- [ ] **Step 8: Run the whole suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: PASS. If `test_every_status_is_either_open_or_finished` fails for `claimed`, that is a real pre-existing gap — add `claimed` wherever it belongs rather than weakening the test.

- [ ] **Step 9: Commit**

```bash
git add tests/test_request_status_coverage.py app/models/deployment_request.py app/routers/dashboard.py app/templates/request_list.html
git commit -m "Add returned/withdrawn statuses, and tests that iterate RequestStatus"
```

---

### Task 2: The return log table

**Files:**
- Create: `app/models/request_return.py`
- Modify: `app/models/__init__.py`
- Modify: `app/models/deployment_request.py` (relationship + `latest_return`)
- Test: `tests/test_request_return_model.py` (new)

**Interfaces:**
- Consumes: `RequestStatus.returned` (Task 1).
- Produces: `RequestReturn(request_id, reason, returned_by, returned_at, returned_from)`; `DeploymentRequest.returns` (newest first); `DeploymentRequest.latest_return`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_request_return_model.py`:

```python
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models on Base.metadata
from app.database import Base
from app.models.deployment_request import DeploymentRequest, RequestStatus
from app.models.request_return import RequestReturn
from app.models.user import User, UserRole


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _request(db_session):
    db_session.add(User(id=1, name="Rajib Ahamad", role=UserRole.devops))
    request = DeploymentRequest(
        task_id="PR-1", requested_by=1, status=RequestStatus.returned,
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    db_session.add(request)
    db_session.flush()
    return request


def test_a_return_round_trips(db_session):
    request = _request(db_session)
    db_session.add(
        RequestReturn(
            request_id=request.id, reason="branch client/foo was deleted", returned_by=1,
            returned_at=datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc),
            returned_from=RequestStatus.approved,
        )
    )
    db_session.commit()

    stored = db_session.query(RequestReturn).one()
    assert stored.reason == "branch client/foo was deleted"
    assert stored.returned_from == RequestStatus.approved
    assert stored.returner.name == "Rajib Ahamad"


def test_a_request_keeps_every_return_newest_first(db_session):
    """The whole point of a log: "returned three times" is a different problem
    from "returned once", and a team lead needs to see both."""
    request = _request(db_session)
    for day, reason in ((15, "branch deleted"), (16, "wrong commit"), (17, "migration error on live")):
        db_session.add(
            RequestReturn(
                request_id=request.id, reason=reason, returned_by=1,
                returned_at=datetime(2026, 9, day, tzinfo=timezone.utc),
                returned_from=RequestStatus.approved,
            )
        )
    db_session.commit()
    db_session.refresh(request)

    assert [r.reason for r in request.returns] == [
        "migration error on live", "wrong commit", "branch deleted",
    ]
    assert request.latest_return.reason == "migration error on live"


def test_latest_return_is_none_when_never_returned(db_session):
    request = _request(db_session)
    db_session.commit()

    assert request.latest_return is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_request_return_model.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.request_return'`.

- [ ] **Step 3: Create the model**

`app/models/request_return.py`:

```python
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.deployment_request import RequestStatus


class RequestReturn(Base):
    """One occasion on which DevOps handed a request back to its requester.

    A table rather than columns on the request because the history is the point:
    a team lead looking at their developer's request needs to see that it came
    back three times and why each time, not just the most recent sentence. See
    docs/superpowers/specs/2026-09-17-return-request-design.md.
    """

    __tablename__ = "request_returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Not unique, unlike DeploymentExecution.request_id — a request can be returned
    # any number of times, and each one is kept.
    request_id: Mapped[int] = mapped_column(ForeignKey("deployment_requests.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    returned_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    returned_at: Mapped[datetime] = mapped_column(DateTime)
    # `approved` or `in_progress`. Returning from in_progress deletes the
    # DeploymentExecution row (see the return route), so without this column the
    # fact that someone had already claimed and started the deployment would be
    # lost entirely.
    returned_from: Mapped[RequestStatus] = mapped_column(Enum(RequestStatus))

    request = relationship("DeploymentRequest", back_populates="returns")
    returner = relationship("User")
```

- [ ] **Step 4: Register it and relate it**

`app/models/__init__.py` — add `from app.models.request_return import RequestReturn` in alphabetical position and `"RequestReturn",` to `__all__`.

`app/models/deployment_request.py`, beside the other relationships (~line 136):

```python
    # Newest first: the status cell shows the most recent reason, and the info
    # dialog lists them in the order a reader wants them.
    returns = relationship(
        "RequestReturn", back_populates="request", order_by="RequestReturn.returned_at.desc()"
    )
```

and, beside `current_executor`:

```python
    @property
    def latest_return(self) -> "RequestReturn | None":
        """The most recent return, or None if this request has never come back.

        Same reasoning as current_executor above — `returns` is ordered newest
        first by the relationship, so the status cell needs no caller-supplied
        join. The listing must eager-load `returns`; one lazy load per row is an
        N+1 across the whole queue.
        """
        return self.returns[0] if self.returns else None
```

- [ ] **Step 5: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_request_return_model.py -q -p no:cacheprovider`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the whole suite, then commit**

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
git add app/models/request_return.py app/models/__init__.py app/models/deployment_request.py tests/test_request_return_model.py
git commit -m "Add request_returns: every return kept, not just the latest"
```

---

### Task 3: The migration

Separate task because it is the one piece the test suite cannot verify — the suite builds its schema from metadata and never runs migrations, so a broken migration fails at deploy time with a green CI.

**Files:**
- Create: `alembic/versions/<rev>_add_request_returns_and_return_statuses.py`

**Interfaces:**
- Consumes: the `RequestReturn` model (Task 2).
- Produces: `request_returns` table and two new enum values in Postgres.

- [ ] **Step 1: Pick a free revision id**

Run `grep -rl "b7c3d9e1f2a4" alembic/versions/` — if it matches anything, pick another. This repo has had an id collision before. Then `.venv/bin/python -m alembic heads` to get the current head for `down_revision`.

- [ ] **Step 2: Write the migration**

```python
"""add request_returns and the returned/withdrawn statuses

Revision ID: b7c3d9e1f2a4
Revises: <current head>
Create Date: 2026-09-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c3d9e1f2a4"
down_revision: Union[str, Sequence[str], None] = "<current head>"
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
        sa.Column(
            "returned_from",
            sa.Enum(name="requeststatus", create_type=False),
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
```

- [ ] **Step 3: Verify one head**

Run: `.venv/bin/python -m alembic heads`
Expected: exactly one head, the new revision.

- [ ] **Step 4: Run it against a throwaway database — never `deploy_tracker`**

```bash
docker exec deployment_status-db-1 psql -U deploy_tracker -d postgres -c "DROP DATABASE IF EXISTS returncheck;" -c "CREATE DATABASE returncheck;"
DATABASE_URL="postgresql+psycopg2://deploy_tracker:changeme@localhost:5432/returncheck" .venv/bin/python -m alembic upgrade head
docker exec deployment_status-db-1 psql -U deploy_tracker -d returncheck -c "\d request_returns"
docker exec deployment_status-db-1 psql -U deploy_tracker -d returncheck -tAc "select unnest(enum_range(null::requeststatus));"
DATABASE_URL="postgresql+psycopg2://deploy_tracker:changeme@localhost:5432/returncheck" .venv/bin/python -m alembic downgrade -1
docker exec deployment_status-db-1 psql -U deploy_tracker -d postgres -c "DROP DATABASE returncheck;"
```

Expected: the table has six columns and both FKs; the enum lists `returned` and `withdrawn`; downgrade drops the table without error.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/b7c3d9e1f2a4_add_request_returns_and_return_statuses.py
git commit -m "Migration: request_returns table and returned/withdrawn enum values"
```

---

### Task 4: Returning a request

**Files:**
- Modify: `app/routers/dashboard.py` (new route)
- Test: `tests/test_request_return_routes.py` (new)

**Interfaces:**
- Consumes: `RequestReturn` (Task 2), `RequestStatus.returned` (Task 1), existing `require_deploy_team_member` from `app/auth.py`.
- Produces: `POST /requests/{request_id}/return` taking form field `reason`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_request_return_routes.py`. Mirror the fixtures in `tests/test_dashboard.py` (`web`, `make_user`, `login_as`, `DEFAULT_TEST_PASSWORD`); read that file's `_seed_finished_request` for the shape.

```python
from datetime import datetime, timezone

from app.models.deployment_execution import DeploymentExecution, ExecutionStatus
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from app.models.request_return import RequestReturn
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def _deploy_team_user(session, settings_group="MG-00013"):
    """The deploy team is identified by machine_group_id — see
    require_deploy_team_member() in app/auth.py. An admin qualifies too."""
    return make_user(
        session, id=1, name="Zunayed Islam", username="zunayed",
        password=DEFAULT_TEST_PASSWORD, role=UserRole.admin,
    )


def _approved_request(session, *, task_id="PR-RET", request_type=RequestType.standard):
    request = DeploymentRequest(
        task_id=task_id, requested_by=2, status=RequestStatus.approved,
        request_type=request_type, created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    session.add(request)
    session.commit()
    return request


def test_deploy_team_can_return_an_approved_request(web):
    client, session = web
    _deploy_team_user(session)
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _approved_request(session)
    login_as(client, "zunayed")

    response = client.post(
        f"/requests/{request.id}/return",
        data={"reason": "branch client/foo was deleted"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.returned
    logged = session.query(RequestReturn).one()
    assert logged.reason == "branch client/foo was deleted"
    assert logged.returned_from == RequestStatus.approved
    assert logged.returned_by == 1


def test_returning_requires_a_reason(web):
    client, session = web
    _deploy_team_user(session)
    session.commit()
    request = _approved_request(session)
    login_as(client, "zunayed")

    response = client.post(f"/requests/{request.id}/return", data={"reason": "   "}, follow_redirects=False)

    assert response.status_code == 400
    session.refresh(request)
    assert request.status == RequestStatus.approved
    assert session.query(RequestReturn).count() == 0


def test_a_developer_cannot_return(web):
    client, session = web
    make_user(session, id=1, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _approved_request(session)
    login_as(client, "devone")

    response = client.post(f"/requests/{request.id}/return", data={"reason": "nope"}, follow_redirects=False)

    assert response.status_code == 403
    assert session.query(RequestReturn).count() == 0


def test_returning_an_unfinished_status_is_refused(web):
    """Only a request sitting in the deploy queue can be handed back."""
    client, session = web
    _deploy_team_user(session)
    session.commit()
    request = _approved_request(session)
    request.status = RequestStatus.completed
    session.commit()
    login_as(client, "zunayed")

    response = client.post(f"/requests/{request.id}/return", data={"reason": "x"}, follow_redirects=False)

    assert response.status_code == 409


def test_returning_from_in_progress_drops_the_claim(web):
    """DeploymentExecution.request_id is unique on purpose, so a leftover claim
    would make the later Start Deployment insert fail — at the exact moment
    devops picks the request back up. The claim is still recorded as
    returned_from."""
    client, session = web
    _deploy_team_user(session)
    session.commit()
    request = _approved_request(session, task_id="PR-INPROG")
    request.status = RequestStatus.in_progress
    session.add(
        DeploymentExecution(
            request_id=request.id, executed_by=1,
            claimed_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            started_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            status=ExecutionStatus.claimed,
        )
    )
    session.commit()
    login_as(client, "zunayed")

    response = client.post(
        f"/requests/{request.id}/return", data={"reason": "migration error on live"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert session.query(DeploymentExecution).filter_by(request_id=request.id).count() == 0
    assert session.query(RequestReturn).one().returned_from == RequestStatus.in_progress
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_request_return_routes.py -q -p no:cacheprovider`
Expected: FAIL — 405/404 on a route that does not exist yet.

- [ ] **Step 3: Add the route**

In `app/routers/dashboard.py`, after `reject_request`, following that route's shape:

```python
# A request can only be handed back from the two states where devops holds it.
RETURNABLE_REQUEST_STATUSES = (RequestStatus.approved, RequestStatus.in_progress)


@router.post("/requests/{request_id}/return")
def return_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_deploy_team_member),
    reason: str = Form(""),
):
    """Hand a request back to its requester, with a reason.

    The fourth exit from the deploy queue, alongside Mark Deployed, Reject and
    leaving it to rot — which is what used to happen when the branch named on a
    request had been deleted. Distinct from Reject, which is the approval gate's
    verdict rather than a devops finding.
    """
    deployment_request = _get_request_or_404(db, request_id)
    if deployment_request.status not in RETURNABLE_REQUEST_STATUSES:
        raise HTTPException(status_code=409, detail="Only a request awaiting or under deployment can be returned")
    if not reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to return a request")

    returned_from = deployment_request.status
    db.add(
        RequestReturn(
            request_id=request_id,
            reason=reason.strip(),
            returned_by=current_user.id,
            returned_at=datetime.now(timezone.utc),
            returned_from=returned_from,
        )
    )
    if returned_from == RequestStatus.in_progress:
        # DeploymentExecution.request_id is unique — "a request can only ever be
        # claimed once" — so leaving the claim behind would make Start Deployment
        # fail on the constraint after the requester resubmits. The deployment did
        # not happen, and a dangling claim would also make current_executor report
        # a handler for a request nobody is handling. returned_from above keeps the
        # fact that it had been claimed.
        db.query(DeploymentExecution).filter_by(request_id=request_id).delete()
    deployment_request.status = RequestStatus.returned
    db.commit()
    manager.notify()
    return RedirectResponse(url="/requests", status_code=303)
```

Add `RequestReturn` and `require_deploy_team_member` to the module's imports if not already present.

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/test_request_return_routes.py -q -p no:cacheprovider`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the whole suite, then commit**

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
git add app/routers/dashboard.py tests/test_request_return_routes.py
git commit -m "Return a request to its requester, with a required reason"
```

---

### Task 5: Resubmit and Withdraw, and letting the requester edit

**Files:**
- Modify: `app/models/deployment_request.py` (`EDITABLE_REQUEST_STATUSES`)
- Modify: `app/auth.py` (`can_edit_request` type rule; new `can_resubmit_request`)
- Modify: `app/routers/dashboard.py` (two routes; extract the creation-status helper)
- Test: `tests/test_request_return_routes.py` (append)

**Interfaces:**
- Consumes: `RequestStatus.returned`/`withdrawn` (Task 1).
- Produces: `POST /requests/{id}/resubmit`, `POST /requests/{id}/withdraw`; `initial_status_for(request_type) -> RequestStatus`; `can_resubmit_request(user, request) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_request_return_routes.py`:

```python
def _returned_request(session, *, request_type=RequestType.standard, requester_id=2):
    request = DeploymentRequest(
        task_id="PR-BACK", requested_by=requester_id, status=RequestStatus.returned,
        request_type=request_type, created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    session.add(request)
    session.commit()
    session.add(
        RequestReturn(
            request_id=request.id, reason="branch deleted", returned_by=1,
            returned_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            returned_from=RequestStatus.approved,
        )
    )
    session.commit()
    return request


def test_resubmitting_a_standard_request_goes_back_for_approval(web):
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    login_as(client, "devone")

    response = client.post(f"/requests/{request.id}/resubmit", follow_redirects=False)

    assert response.status_code == 303
    session.refresh(request)
    # The branch changed, so the team lead's approval was for something that no
    # longer exists.
    assert request.status == RequestStatus.pending_approval


def test_resubmitting_a_test_local_request_goes_straight_to_the_deploy_queue(web):
    """test_local and db_dump_restore skip the approval gate at creation, so they
    skip it on the way back too."""
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session, request_type=RequestType.test_local)
    login_as(client, "devone")

    client.post(f"/requests/{request.id}/resubmit", follow_redirects=False)

    session.refresh(request)
    assert request.status == RequestStatus.approved


def test_someone_elses_request_cannot_be_resubmitted(web):
    client, session = web
    make_user(session, id=3, name="Other Dev", username="other", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    login_as(client, "other")

    response = client.post(f"/requests/{request.id}/resubmit", follow_redirects=False)

    assert response.status_code == 403


def test_resubmitting_something_not_returned_is_refused(web):
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    request.status = RequestStatus.approved
    session.commit()
    login_as(client, "devone")

    assert client.post(f"/requests/{request.id}/resubmit", follow_redirects=False).status_code == 409


def test_the_requester_can_withdraw_a_returned_request(web):
    """Withdraw, not delete: the return log has to survive, or the team lead's
    view of why it kept coming back disappears with it."""
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    login_as(client, "devone")

    response = client.post(f"/requests/{request.id}/withdraw", follow_redirects=False)

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.withdrawn
    assert session.query(RequestReturn).count() == 1


def test_a_returned_request_is_editable_even_when_not_standard(web):
    """can_edit_request refuses non-standard types outright, on the grounds they
    have no pre-decision window. A return creates one by design — without this
    exception a returned test_local request could never be corrected."""
    from app.auth import can_edit_request

    client, session = web
    requester = make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session, request_type=RequestType.test_local)

    assert can_edit_request(requester, request) is True


def test_a_returned_request_is_not_deletable(web):
    """It carries a log now, and DELETABLE_REQUEST_STATUSES' own reasoning is that
    deleting a row with history breaks the audit trail this tool exists for."""
    from app.auth import can_delete_request

    client, session = web
    requester = make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)

    assert can_delete_request(requester, request) is False
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_request_return_routes.py -q -p no:cacheprovider -k "resubmit or withdraw or editable or deletable"`
Expected: FAIL.

- [ ] **Step 3: Extract the creation-status helper**

In `app/models/deployment_request.py`:

```python
def initial_status_for(request_type: RequestType) -> RequestStatus:
    """The status a freshly created request of this type starts in.

    `standard` waits for a team lead; db_dump_restore and test_local skip the
    approval gate entirely (see RequestType). Shared by the creation routes and
    by resubmission so the two can never drift — a resubmitted request must land
    exactly where a new one of its type would.
    """
    return (
        RequestStatus.pending_approval
        if request_type == RequestType.standard
        else RequestStatus.approved
    )
```

Then use it in `app/routers/dashboard.py` at the three creation sites that currently hardcode `status=RequestStatus.pending_approval` / `status=RequestStatus.approved` (~lines 443, 493, 537), replacing each with `status=initial_status_for(<the type being created>)`. Keep the existing explanatory comments.

- [ ] **Step 4: Widen edit, add the resubmit permission**

`app/models/deployment_request.py` — add `RequestStatus.returned` to `EDITABLE_REQUEST_STATUSES` with a comment that fixing the branch is the entire point of a return.

`app/auth.py`, in `can_edit_request`, replace the blanket type check:

```python
    # Non-standard types have no pre-decision window of their own — they are created
    # straight into `approved`. A return creates one by design, though: the requester
    # is being asked to fix something, so they must be able to edit it. Without this
    # a returned test_local request could not be corrected by anyone, which removes
    # the point of returning it.
    if (
        deployment_request.request_type != RequestType.standard
        and deployment_request.status != RequestStatus.returned
    ):
        return False
```

and add:

```python
def can_resubmit_request(current_user: User, deployment_request) -> bool:
    """Whether current_user may push a returned request back into the queue: the
    original requester, or an admin. Resubmission is deliberately a separate act
    from saving an edit — correcting a typo should not silently re-enter the
    deploy queue."""
    if deployment_request.status != RequestStatus.returned:
        return False
    if current_user.role == UserRole.admin:
        return True
    return current_user.id == deployment_request.requested_by
```

- [ ] **Step 5: Add the two routes**

In `app/routers/dashboard.py`, after `return_request`:

```python
@router.post("/requests/{request_id}/resubmit")
def resubmit_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
):
    deployment_request = _get_request_or_404(db, request_id)
    if deployment_request.status != RequestStatus.returned:
        raise HTTPException(status_code=409, detail="Only a returned request can be resubmitted")
    if not can_resubmit_request(current_user, deployment_request):
        raise HTTPException(status_code=403, detail="Only the requester (or an admin) can resubmit this request")

    # Exactly where a new request of this type would start — a standard request goes
    # back through its team lead, since the branch changed and the original approval
    # was for something that no longer exists.
    deployment_request.status = initial_status_for(deployment_request.request_type)
    db.commit()
    manager.notify()
    return RedirectResponse(url="/requests", status_code=303)


@router.post("/requests/{request_id}/withdraw")
def withdraw_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_login),
):
    """Abandon a returned request without destroying its history.

    Not a delete: by this point the request carries a return log, and deleting the
    row would hand the person with the strongest motive to erase an unflattering
    record the means to do it.
    """
    deployment_request = _get_request_or_404(db, request_id)
    if deployment_request.status != RequestStatus.returned:
        raise HTTPException(status_code=409, detail="Only a returned request can be withdrawn")
    if not can_resubmit_request(current_user, deployment_request):
        raise HTTPException(status_code=403, detail="Only the requester (or an admin) can withdraw this request")

    deployment_request.status = RequestStatus.withdrawn
    db.commit()
    manager.notify()
    return RedirectResponse(url="/requests", status_code=303)
```

- [ ] **Step 6: Run them and watch them pass, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_request_return_routes.py -q -p no:cacheprovider` then `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: PASS. If a creation test fails after Step 3, the helper returned the wrong status for that type — fix the helper, not the test.

- [ ] **Step 7: Commit**

```bash
git add app/models/deployment_request.py app/auth.py app/routers/dashboard.py tests/test_request_return_routes.py
git commit -m "Resubmit and withdraw a returned request"
```

---

### Task 6: The UI

**Files:**
- Modify: `app/templates/request_list.html`
- Modify: `app/static/style.css`
- Modify: `app/routers/dashboard.py` (eager-load `returns`)
- Test: `tests/test_request_return_routes.py` (append)

**Interfaces:**
- Consumes: everything above.
- Produces: the Return dialog, the `[i]` log dialog, the returned-row action bar.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_returned_row_shows_the_reason_and_its_actions(web):
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    login_as(client, "devone")

    page = client.get("/requests").text

    assert "Returned" in page
    assert "branch deleted" in page
    assert f"/requests/{request.id}/resubmit" in page
    assert f"/requests/{request.id}/withdraw" in page
    # Not deletable — the log has to survive.
    assert f"/requests/{request.id}/delete" not in page


def test_the_info_button_counts_repeat_returns(web):
    """"Returned three times" is the fact a team lead is looking for; it should
    not require opening the dialog to discover."""
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    for day, reason in ((3, "wrong commit"), (4, "migration error on live")):
        session.add(
            RequestReturn(
                request_id=request.id, reason=reason, returned_by=1,
                returned_at=datetime(2026, 9, day, tzinfo=timezone.utc),
                returned_from=RequestStatus.approved,
            )
        )
    session.commit()
    login_as(client, "devone")

    page = client.get("/requests").text

    assert 'class="return-log-count"' in page
    assert ">3<" in page
    assert "migration error on live" in page
    assert "wrong commit" in page


def test_the_deploy_queue_offers_return(web):
    client, session = web
    make_user(session, id=1, name="Zunayed Islam", username="zunayed",
              password=DEFAULT_TEST_PASSWORD, role=UserRole.admin)
    session.commit()
    request = _approved_request(session)
    login_as(client, "zunayed")

    page = client.get("/requests").text

    assert f"/requests/{request.id}/return" in page


def test_the_listing_eager_loads_returns(web):
    """One lazy load per row would be an N+1 across the whole queue — the same
    trap the seeder listing hit."""
    from sqlalchemy import event

    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    for i in range(3):
        request = _returned_request(session)
        request.task_id = f"PR-{i}"
    session.commit()
    login_as(client, "devone")

    client.get("/requests")  # warm anything lazy

    queries = []

    def record(conn, cursor, statement, *rest):
        if "request_returns" in statement:
            queries.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        client.get("/requests")
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(queries) <= 1, f"returns loaded per row, not eagerly: {len(queries)} queries"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_request_return_routes.py -q -p no:cacheprovider -k "returned_row or info_button or deploy_queue_offers or eager"`
Expected: FAIL.

- [ ] **Step 3: Eager-load returns**

In `list_requests()`, extend the existing options chain:

```python
        .options(
            joinedload(DeploymentRequest.executions).joinedload(DeploymentExecution.executor),
            # Every returned row renders its log; lazy-loading would be an N+1.
            selectinload(DeploymentRequest.returns).joinedload(RequestReturn.returner),
        )
```

Import `selectinload` from `sqlalchemy.orm`.

- [ ] **Step 4: Status cell — the label and the info button**

In `request_list.html`, inside the status cell after the existing status span:

```jinja
            {% if r.status == RequestStatus.returned and r.returns %}
              <button
                type="button" class="return-log-button" data-return-log="{{ r.id }}"
                title="Why this was returned"
              >i{% if r.returns | length > 1 %}<span class="return-log-count">{{ r.returns | length }}</span>{% endif %}</button>
              <div class="return-log-data" id="return-log-{{ r.id }}" hidden>
                {% for entry in r.returns %}
                  <div class="return-log-entry">
                    <div class="return-log-meta">
                      {{ localtime(entry.returned_at) }} ·
                      {{ entry.returner.name if entry.returner else "—" }} ·
                      from {{ status_labels.get(entry.returned_from, entry.returned_from.value) }}
                    </div>
                    <p class="return-log-reason">{{ entry.reason }}</p>
                  </div>
                {% endfor %}
              </div>
            {% endif %}
```

- [ ] **Step 5: Action bar — Return, Resubmit, Withdraw**

Inside the `approved` and `in_progress` branches, after each existing button, for `can_deploy` users:

```jinja
                  <button type="button" class="return" data-return-request-id="{{ r.id }}">Return</button>
```

and add a `returned` branch:

```jinja
              {% elif r.status == RequestStatus.returned %}
                {% if can_resubmit_request(r) %}
                  <form method="post" action="/requests/{{ r.id }}/resubmit" class="inline-form">
                    <button type="submit" class="start">Resubmit</button>
                  </form>
                  <form
                    method="post" action="/requests/{{ r.id }}/withdraw" class="inline-form"
                    onsubmit="return confirm('Withdraw this request? It stays on record with its return history, but leaves the queue.');"
                  >
                    <button type="submit" class="reject">Withdraw</button>
                  </form>
                {% endif %}
```

Pass `"can_resubmit_request": lambda r: can_resubmit_request(current_user, r),` in the template context, beside the existing `can_approve_request` lambda.

- [ ] **Step 6: The two dialogs**

Beside `#deploy-version-modal`, add a return dialog (a reason is required, so the button cannot submit directly) and a log dialog:

```jinja
  <dialog id="return-modal" class="changes-modal">
    <h2>Return to requester</h2>
    <form method="post" id="return-form" class="form">
      <div class="field">
        <label for="return-reason">Why is this going back?</label>
        <textarea id="return-reason" name="reason" rows="3" required
                  placeholder="e.g. branch client/foo was deleted"></textarea>
      </div>
      <div class="changes-modal-actions">
        <button type="submit" class="reject">Return request</button>
        <button type="button" class="button-secondary" id="return-cancel">Cancel</button>
      </div>
    </form>
  </dialog>

  <dialog id="return-log-modal" class="changes-modal">
    <h2>Return history</h2>
    <div id="return-log-body"></div>
    <div class="changes-modal-actions">
      <button type="button" class="button-secondary" id="return-log-close">Close</button>
    </div>
  </dialog>
```

and the script, beside the existing deploy-modal script:

```javascript
    (function () {
      var returnModal = document.getElementById("return-modal");
      var returnForm = document.getElementById("return-form");
      document.querySelectorAll("[data-return-request-id]").forEach(function (button) {
        button.addEventListener("click", function () {
          returnForm.action = "/requests/" + button.getAttribute("data-return-request-id") + "/return";
          returnForm.reset();
          returnModal.showModal();
        });
      });
      document.getElementById("return-cancel").addEventListener("click", function () {
        returnModal.close();
      });

      var logModal = document.getElementById("return-log-modal");
      var logBody = document.getElementById("return-log-body");
      document.querySelectorAll("[data-return-log]").forEach(function (button) {
        button.addEventListener("click", function () {
          var source = document.getElementById("return-log-" + button.getAttribute("data-return-log"));
          logBody.innerHTML = source ? source.innerHTML : "";
          logModal.showModal();
        });
      });
      document.getElementById("return-log-close").addEventListener("click", function () {
        logModal.close();
      });
    })();
```

- [ ] **Step 7: Styles**

Append to `app/static/style.css`, tokens only:

```css
/* Withdrawn is neither a failure nor a rejection, so its rail dot is the neutral
   slate the test.local badge already uses, not red. */
.rail-dot-slate, .rail-line-slate { background: var(--slate-chip); }

/* Returned: amber, the signal this system already uses for "waiting on a person". */
.status-returned { color: var(--amber); font-weight: 600; }
.status-withdrawn { color: var(--fog); }

.return-log-button {
  margin-left: 6px;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  border: 1px solid var(--amber);
  background: var(--amber-dim);
  color: var(--amber);
  font-family: var(--font-body);
  font-size: 0.7rem;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
}

.return-log-count {
  margin-left: 2px;
  font-size: 0.65rem;
}

.return-log-entry { margin-bottom: 12px; }
.return-log-meta { font-size: 0.72rem; color: var(--fog-dim); }
.return-log-reason { margin: 2px 0 0; color: var(--paper); }

.inline-form button.return {
  background: transparent;
  color: var(--amber);
  border: 1px solid var(--amber-dim);
}
```

- [ ] **Step 8: Run the tests, then the whole suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/templates/request_list.html app/static/style.css app/routers/dashboard.py tests/test_request_return_routes.py
git commit -m "Return/Resubmit/Withdraw in the queue UI, with the return log behind an info button"
```

---

### Task 7: Notify the requester

**Files:**
- Modify: `app/routers/dashboard.py` (`ACTIVE_REQUEST_STATUSES_FOR_NOTIFICATIONS`, payload)
- Modify: `app/templates/request_list.html` (notification text)
- Test: `tests/test_request_return_routes.py` (append)

**Interfaces:**
- Consumes: `RequestStatus.returned`.
- Produces: returned requests in `active_requests_json`, each with its latest reason.

- [ ] **Step 1: Write the failing test**

```python
def test_returned_requests_reach_the_notification_feed(web):
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    _returned_request(session)
    login_as(client, "devone")

    page = client.get("/requests").text
    payload = page.split('id="active-requests-data">')[1].split("</script>")[0]

    assert '"status": "returned"' in payload
    assert "branch deleted" in payload
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_request_return_routes.py -q -p no:cacheprovider -k notification_feed`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add `RequestStatus.returned` to `ACTIVE_REQUEST_STATUSES_FOR_NOTIFICATIONS`, eager-load `returns` on that query too, and add to each entry of `active_requests_json`:

```python
                "returnReason": r.latest_return.reason if r.latest_return else "",
```

In `request_list.html`'s notification script, where the popup body is built, use the reason for returned requests so the popup says what to fix rather than just that something changed.

- [ ] **Step 4: Run it, then the whole suite, then commit**

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
git add app/routers/dashboard.py app/templates/request_list.html tests/test_request_return_routes.py
git commit -m "Notify the requester when their request is returned"
```

---

### Task 8: Prove nothing else broke

No new behaviour. This task exists because the previous ordering change shipped a regression that the entire suite passed, and because CSS and JS are not covered by any test.

**Files:** none (verification only), plus the spec's status line.

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: PASS, no skips.

- [ ] **Step 2: Migration, up and down, on a throwaway database**

Repeat Task 3 Step 4 from a database at the *previous* head, confirming a real upgrade path rather than a fresh create.

- [ ] **Step 3: Exercise the untouched happy path in the browser**

```bash
docker compose up -d --build
```

Then at http://localhost:8010, with a request that is never returned: submit → approve → Start Deployment → Mark Deployed. Confirm it behaves exactly as before. Check the Dashboard, Release Tracker and Excel export still render — all three read only `completed` rows, so they should be untouched, but confirm rather than assume.

- [ ] **Step 4: Exercise the new path in the browser**

Return a request from Pending Deployment, then from In Progress. Check: the amber pulsing Returned rail, the `[i]` button and its count, the log dialog listing each return with who/when/from-where, Edit working on a `test_local` returned request, Resubmit landing in the right queue for each type, Withdraw keeping the row, and no Delete button on a returned row.

- [ ] **Step 5: Confirm the round trip that the unique constraint would have broken**

Start Deployment → Return → Resubmit → Start Deployment again. This must not 500. It is the one failure mode in this feature that only appears in a real database.

- [ ] **Step 6: Look at the queue order with real data**

Returned rows at the top, then pending approval, then the deploy queue, then history. The `pending_intake` regression passed every test and was only visible here.

- [ ] **Step 7: Mark the spec implemented and commit**

Change the spec's `Status:` line to `implemented`, then:

```bash
git add docs/superpowers/specs/2026-09-17-return-request-design.md
git commit -m "Mark the return-request spec implemented"
```
