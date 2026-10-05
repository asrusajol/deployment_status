# Request Checklists + Management Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hard-coded DB dump start checklist with admin-managed checklist terms per request type, record every confirmation as an audit trail, and put the management tools under a new Management tab gated by per-user switches.

**Architecture:** Two new tables (`checklist_items`, `checklist_confirmations`) and two new boolean switches on `users`. A new `app/routers/management.py` serves the Management hub and the Checklists page; `app/services/checklists.py` owns "which terms are required" and "record a confirmed start", which `start_request()` calls inside its existing transaction. Templates reuse the existing `<dialog class="changes-modal">` patterns.

**Tech Stack:** FastAPI, SQLAlchemy 2 (typed `Mapped`), Jinja2, Alembic, Postgres 16 (tests use SQLite via `Base.metadata.create_all`), pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-request-checklists-design.md`

## Global Constraints

- Run Python as `.venv/bin/python` — `python`/bare `pytest` pick up the wrong interpreter.
- Full suite before every commit: `.venv/bin/python -m pytest -q -p no:cacheprovider` (~3 min). Every commit green on its own.
- Tests first; watch each fail for the expected reason before writing code.
- Assertions on HTML are scoped to the row / dialog / section they test — never the whole page (the queue embeds a JSON blob of every active request).
- Exactly one Alembic head at every commit. Revision ids `a7c1e4d2f9b3` and `c2e8f5a1d6b7` (checked free on 2026-09-30; re-check with `grep -rl <id> alembic/versions app tests`).
- The new table reuses the existing Postgres enum type `requesttype` — `postgresql.ENUM(..., name="requesttype", create_type=False)`.
- Never run migrations, DDL or bulk writes against `deploy_tracker`; use a throwaway database.
- CSS: `:root` tokens only (`var(--panel)`, `var(--fog)`, …). No hex.
- Gate every route with its dependency **and** hide the link; hiding alone is not access control.
- Eager-load anything rendered per row (`selectinload`/`joinedload`).
- Terms are never hard-deleted; `is_active=False` retires them.
- Comments explain *why*, matching the codebase's style.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A term added while a Start pop-up is open** — the submit must fail with 400 and write nothing, not start with an unconfirmed term. (Task 4, `test_term_added_after_page_load_blocks_start`.)
2. **Start → Return → Resubmit → Start** — Return deletes the execution row; confirmations must survive and the second start must add its own set. (Task 4, `test_confirmations_survive_return_and_restart`.)
3. **Editing a term's wording after it was confirmed** — the audit must still show what was actually ticked. (Task 4, `test_confirmation_keeps_label_snapshot_after_edit`.)
4. **A Management-access user POSTing to an admin-only route directly** (edit / move / retire) — 403, no change. (Task 3, `test_management_user_cannot_edit_move_or_retire`.)
5. **Audit contents leaking to a user without audit access** via the page source — the snapshot text must not appear anywhere in their HTML. (Task 5, `test_audit_contents_absent_for_user_without_access`.)

---

## File map

| File | Responsibility | Task |
|---|---|---|
| `alembic/versions/a7c1e4d2f9b3_add_management_and_audit_switches.py` | two user columns | 1 |
| `app/models/user.py` | `can_access_management`, `can_view_checklist_audit` | 1 |
| `app/auth.py` | `can_access_management`, `require_management`, `can_view_checklist_audit` | 1 |
| `app/routers/management.py` (new) | `/management` hub; later `/management/checklists*` | 1, 3 |
| `app/templates/management.html` (new) | hub cards | 1, 3 |
| `app/templates/base.html` | nav: Admin → Management | 1 |
| `app/routers/admin.py`, `app/templates/admin_users.html` | the two switches | 1, 5 |
| `app/main.py` | include management router | 1 |
| `app/models/checklist.py` (new), `app/models/__init__.py`, `app/models/deployment_request.py` | the two tables + relationship | 2 |
| `alembic/versions/c2e8f5a1d6b7_add_checklist_tables.py` | create tables + seed 4 dump terms | 2 |
| `app/templates/management_checklists.html` (new) | Checklists page | 3 |
| `app/services/checklists.py` (new) | required terms; record confirmations | 4 |
| `app/routers/dashboard.py`, `_request_row.html`, `request_list.html` | Start uses DB terms; audit view | 4, 5 |
| `app/static/style.css` | hub cards, checklist page, audit entries | 1, 3, 5 |
| `tests/test_management.py` (new) | hub, nav, management switch | 1 |
| `tests/test_checklist_models.py` (new) | model defaults/relationships | 2 |
| `tests/test_management_checklists.py` (new) | Checklists page | 3 |
| `tests/test_start_checklist.py` (renamed from `test_db_dump_start_checklist.py`) | Start gate | 4 |
| `tests/test_checklist_audit.py` (new) | audit view + switch | 5 |

---

### Task 1: Management hub, Management-access switch, nav

**Files:**
- Create: `alembic/versions/a7c1e4d2f9b3_add_management_and_audit_switches.py`, `app/routers/management.py`, `app/templates/management.html`, `tests/test_management.py`
- Modify: `app/models/user.py` (after `can_manage_other_returns`), `app/auth.py` (after `require_devops`), `app/routers/admin.py` (after `set_return_override`), `app/templates/admin_users.html`, `app/templates/base.html:36-38`, `app/main.py:11-33`, `app/static/style.css` (end of file)

**Interfaces:**
- Produces: `User.can_access_management: bool`, `User.can_view_checklist_audit: bool` (both default False); `app.auth.can_access_management(user) -> bool`; `app.auth.require_management` (FastAPI dependency returning `User`, 403 otherwise); `app.auth.can_view_checklist_audit(user) -> bool`; `app.routers.management.router` (prefix `/management`); `POST /admin/users/{id}/set-management-access` (form checkbox `can_access_management`).

- [ ] **Step 1: Write the failing tests** — `tests/test_management.py`

```python
"""Management tab: hub page, nav link, and the per-user Management access switch.

Access is a switch, not a role — a user holds exactly one role, and a fifth one would
strip a DevOps user of deploy rights. See
docs/superpowers/specs/2026-09-30-request-checklists-design.md.
"""

import re

from app.models.user import User, UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def _nav(page: str) -> str:
    match = re.search(r"<nav>.*?</nav>", page, re.S)
    assert match, "nav missing"
    return match.group(0)


def _seed(session, *, dev_has_access=False):
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    dev = make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    dev.can_access_management = dev_has_access
    session.commit()


def test_switches_default_off(web):
    _client, session = web
    _seed(session)
    dev = session.get(User, 2)
    assert dev.can_access_management is False
    assert dev.can_view_checklist_audit is False


def test_user_without_access_gets_403_and_no_nav_link(web):
    client, session = web
    _seed(session)
    login_as(client, "devone")

    assert client.get("/management").status_code == 403
    assert 'href="/management"' not in _nav(client.get("/requests").text)


def test_user_with_access_sees_hub_but_not_users_card(web):
    client, session = web
    _seed(session, dev_has_access=True)
    login_as(client, "devone")

    response = client.get("/management")

    assert response.status_code == 200
    assert 'href="/admin/users"' not in response.text
    assert 'href="/management"' in _nav(client.get("/requests").text)


def test_admin_sees_hub_with_users_card(web):
    client, session = web
    _seed(session)
    login_as(client, "root")

    response = client.get("/management")

    assert response.status_code == 200
    assert 'href="/admin/users"' in response.text


def test_admin_nav_shows_management_instead_of_admin(web):
    client, session = web
    _seed(session)
    login_as(client, "root")

    nav = _nav(client.get("/requests").text)

    assert 'href="/management"' in nav
    assert 'href="/admin/users"' not in nav


def test_admin_toggles_management_access(web):
    client, session = web
    _seed(session)
    login_as(client, "root")

    on = client.post("/admin/users/2/set-management-access", data={"can_access_management": "on"}, follow_redirects=False)
    assert on.status_code == 303
    session.expire_all()
    assert session.get(User, 2).can_access_management is True

    client.post("/admin/users/2/set-management-access", data={}, follow_redirects=False)
    session.expire_all()
    assert session.get(User, 2).can_access_management is False


def test_non_admin_cannot_toggle_management_access(web):
    client, session = web
    _seed(session, dev_has_access=True)
    login_as(client, "devone")

    response = client.post("/admin/users/2/set-management-access", data={}, follow_redirects=False)

    assert response.status_code == 403
    session.expire_all()
    assert session.get(User, 2).can_access_management is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_management.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: 'User' object has no attribute 'can_access_management'` / 404 on `/management`.

- [ ] **Step 3: Model columns** — `app/models/user.py`, directly after `can_manage_other_returns`:

```python
    # Opens the Management tab (app/routers/management.py) and view+add on checklist
    # terms. A switch rather than a sixth role: a user holds exactly one role, and making
    # "management" a role would strip a DevOps user of deploy rights. Ignored for admins,
    # who always have full access. Granted per user on /admin/users.
    can_access_management: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Shows the checklist audit (who confirmed which start-checklist term, when) on
    # request rows. Its own grant, independent of role or team — see
    # docs/superpowers/specs/2026-09-30-request-checklists-design.md. Ignored for admins.
    can_view_checklist_audit: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
```

- [ ] **Step 4: Migration** — `alembic/versions/a7c1e4d2f9b3_add_management_and_audit_switches.py`

```python
"""add management-access and checklist-audit switches to users

Both default off: straight after deploy only admins can open the Management tab or
see checklist audits, until an admin grants either on /admin/users. Switches rather
than a role because a user holds exactly one role.

Revision ID: a7c1e4d2f9b3
Revises: f4a9c2e1b3d5
Create Date: 2026-09-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c1e4d2f9b3"
down_revision: Union[str, Sequence[str], None] = "f4a9c2e1b3d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("can_access_management", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("can_view_checklist_audit", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "can_view_checklist_audit")
    op.drop_column("users", "can_access_management")
```

- [ ] **Step 5: Permission helpers** — `app/auth.py`, after `require_devops = require_admin_or_devops`:

```python
def can_access_management(user: User) -> bool:
    """Admins always; anyone else only with the per-user switch (User.can_access_management)."""
    return user.role == UserRole.admin or user.can_access_management


def require_management(current_user: User = Depends(require_login)) -> User:
    if not can_access_management(current_user):
        raise HTTPException(status_code=403, detail="Management access required")
    return current_user


def can_view_checklist_audit(user: User) -> bool:
    """Admins always; anyone else only with the per-user switch. Deliberately not tied to
    role or deploy-team membership — the audit is granted, not inherited."""
    return user.role == UserRole.admin or user.can_view_checklist_audit
```

- [ ] **Step 6: Management router** — `app/routers/management.py`

```python
"""Management tab: a hub for management tools, gated by require_management.

Users (/admin/users) stays admin-only and keeps its URL; the hub only links to it.
Checklists (/management/checklists) is added to this router in a later commit. See
docs/superpowers/specs/2026-09-30-request-checklists-design.md.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from app.auth import require_management
from app.models.user import User, UserRole
from app.static_version import STATIC_VERSION

router = APIRouter(prefix="/management")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_version"] = STATIC_VERSION


@router.get("")
def management_hub(request: Request, current_user: User = Depends(require_management)):
    return templates.TemplateResponse(
        request,
        "management.html",
        {"current_user": current_user, "is_admin": current_user.role == UserRole.admin},
    )
```

`app/main.py`: add `from app.routers.management import router as management_router` with the other router imports, and `app.include_router(management_router)` after `app.include_router(admin_router)`.

- [ ] **Step 7: Hub template** — `app/templates/management.html`

```html
{% extends "base.html" %}
{% block title %}Management — Deployment Tracker{% endblock %}
{% block content %}
  <h1>Management</h1>
  <p class="subtitle">Tools for managing who can do what, and how requests are handled.</p>
  <div class="management-cards">
    {% if is_admin %}
      <a class="management-card" href="/admin/users">
        <span class="management-card-title">Users</span>
        <span class="management-card-text">Login access, roles and per-user switches.</span>
      </a>
    {% endif %}
  </div>
{% endblock %}
```

`app/static/style.css` (append):

```css
/* Management hub (management.html): one card per tool, only the ones the viewer may open. */
.management-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 0.9rem;
  margin-top: 1rem;
}
.management-card {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  padding: 1rem 1.1rem;
  background: var(--panel);
  border: 1px solid var(--hairline);
  border-radius: var(--radius);
  color: var(--paper);
  text-decoration: none;
}
.management-card:hover { border-color: var(--teal); }
.management-card-title { font-family: var(--font-display); font-weight: 600; }
.management-card-text { font-size: 0.85rem; color: var(--fog); }
```

- [ ] **Step 8: Nav** — `app/templates/base.html`, replace

```html
        {% if current_user.role.value == "admin" %}
          <a href="/admin/users">Admin</a>
        {% endif %}
```

with

```html
        {# Mirrors app.auth.can_access_management — the route's require_management is
           the actual gate; this only hides a link that would 403. #}
        {% if current_user.role.value == "admin" or current_user.can_access_management %}
          <a href="/management">Management</a>
        {% endif %}
```

- [ ] **Step 9: Admin switch route** — `app/routers/admin.py`, after `set_return_override`:

```python
@router.post("/users/{user_id}/set-management-access")
def set_management_access(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    # Absent from the POST body when unchecked — same parsing as set_return_override.
    can_access_management: bool = Form(False),
):
    """Grants or revokes the Management tab (User.can_access_management)."""
    user = _get_user_or_404(db, user_id)
    user.can_access_management = can_access_management
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)
```

- [ ] **Step 10: Users page** — `app/templates/admin_users.html`: add `<th>Management access</th>` after `<th>Show others' returns</th>`, a matching cell after the returns cell, and a back link under the `<h1>`:

```html
  <p><a href="/management" class="back-link">← Management</a></p>
```

```html
        <td>
          {% if u.role.value == "admin" %}
            <span class="muted">Always</span>
          {% else %}
            <form method="post" action="/admin/users/{{ u.id }}/set-management-access" class="inline-form">
              <label>
                <input type="checkbox" name="can_access_management" {% if u.can_access_management %}checked{% endif %}>
              </label>
              <button type="submit">Save</button>
            </form>
          {% endif %}
        </td>
```

- [ ] **Step 11: Run the task's tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_management.py -q -p no:cacheprovider` → all PASS.
Run: `.venv/bin/python -m pytest -q -p no:cacheprovider` → all PASS (a failure mentioning the nav "Admin" link means a test depended on the old link; fix the test's expectation to `/management`, not the code).

- [ ] **Step 12: Migration on a throwaway database**

```bash
docker exec deployment_status-db-1 psql -U deploy_tracker -d postgres -c "CREATE DATABASE migcheck;"
export DATABASE_URL="postgresql+psycopg2://deploy_tracker:changeme@localhost:5432/migcheck"
.venv/bin/python -m alembic upgrade head && .venv/bin/python -m alembic downgrade -1 && .venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic heads   # exactly one: a7c1e4d2f9b3 (head)
docker exec deployment_status-db-1 psql -U deploy_tracker -d postgres -c "DROP DATABASE migcheck;"
unset DATABASE_URL
```

- [ ] **Step 13: Commit**

```bash
git add alembic/versions/a7c1e4d2f9b3_add_management_and_audit_switches.py app/models/user.py app/auth.py \
  app/routers/management.py app/routers/admin.py app/main.py app/templates/management.html \
  app/templates/admin_users.html app/templates/base.html app/static/style.css tests/test_management.py
git commit -m "Add Management tab and per-user Management access switch

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Checklist tables, models, seeded migration

**Files:**
- Create: `app/models/checklist.py`, `alembic/versions/c2e8f5a1d6b7_add_checklist_tables.py`, `tests/test_checklist_models.py`
- Modify: `app/models/__init__.py`, `app/models/deployment_request.py` (relationships block, after `returns`)

**Interfaces:**
- Consumes: `RequestType` (`app.models.deployment_request`).
- Produces: `app.models.checklist.ChecklistItem` (`id, request_type, label, position, is_active, created_by, created_at, updated_by, updated_at`; relationships `creator`, `updater`); `app.models.checklist.ChecklistConfirmation` (`id, request_id, checklist_item_id, item_label, confirmed_by, confirmed_at`; relationships `request`, `item`, `confirmer`); `DeploymentRequest.checklist_confirmations` (newest first); `app.models.checklist.LABEL_MAX_LENGTH = 500`.

- [ ] **Step 1: Write the failing tests** — `tests/test_checklist_models.py`

```python
from datetime import datetime, timedelta, timezone

from app.models.checklist import ChecklistConfirmation, ChecklistItem
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from tests.conftest import make_user
from tests.test_dashboard import db_session  # noqa: F401 — reuse the in-memory session fixture


def test_item_defaults_to_active(db_session):
    item = ChecklistItem(request_type=RequestType.db_dump_restore, label="Close cronjobs", position=1,
                         created_at=datetime.now(timezone.utc))
    db_session.add(item)
    db_session.commit()
    assert item.is_active is True


def test_request_lists_confirmations_newest_first(db_session):
    make_user(db_session, id=1, name="Zunayed")
    request = DeploymentRequest(request_type=RequestType.db_dump_restore, status=RequestStatus.approved,
                                created_at=datetime.now(timezone.utc))
    item = ChecklistItem(request_type=RequestType.db_dump_restore, label="Close cronjobs", position=1,
                         created_at=datetime.now(timezone.utc))
    db_session.add_all([request, item])
    db_session.flush()
    older = datetime(2026, 9, 1, 10, 0)
    for at in (older, older + timedelta(hours=1)):
        db_session.add(ChecklistConfirmation(request_id=request.id, checklist_item_id=item.id,
                                             item_label=item.label, confirmed_by=1, confirmed_at=at))
    db_session.commit()
    db_session.refresh(request)

    times = [c.confirmed_at for c in request.checklist_confirmations]
    assert times == sorted(times, reverse=True)
    assert request.checklist_confirmations[0].confirmer.name == "Zunayed"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_checklist_models.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.checklist'`.

- [ ] **Step 3: Models** — `app/models/checklist.py`

```python
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.deployment_request import RequestType

LABEL_MAX_LENGTH = 500


class ChecklistItem(Base):
    """One term DevOps must confirm before starting a request of `request_type`.

    One type per row, deliberately: the same wording on two types is two rows, so
    editing one type's checklist can never change another's. Never hard-deleted —
    ChecklistConfirmation rows point here — `is_active=False` retires a term. See
    docs/superpowers/specs/2026-09-30-request-checklists-design.md.
    """

    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_type: Mapped[RequestType] = mapped_column(Enum(RequestType), index=True)
    label: Mapped[str] = mapped_column(String(LABEL_MAX_LENGTH))
    position: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Null only for the terms seeded by migration c2e8f5a1d6b7.
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])


class ChecklistConfirmation(Base):
    """One term confirmed by one person when starting one request.

    Keyed on the request, not the DeploymentExecution: Return deletes the execution
    row, and the audit must outlive it. No unique constraint — a request started,
    returned and started again keeps both sets. `item_label` is a snapshot, so
    rewording a term later never changes what someone agreed to.
    """

    __tablename__ = "checklist_confirmations"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("deployment_requests.id"), index=True)
    checklist_item_id: Mapped[int] = mapped_column(ForeignKey("checklist_items.id"))
    item_label: Mapped[str] = mapped_column(String(LABEL_MAX_LENGTH))
    confirmed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime)

    request = relationship("DeploymentRequest", back_populates="checklist_confirmations")
    item = relationship("ChecklistItem")
    confirmer = relationship("User")
```

`app/models/deployment_request.py`, after the `returns = relationship(...)` block:

```python
    # Newest first, for the audit dialog. The listing must eager-load this (and
    # confirmer) — one lazy load per row is an N+1 across the queue.
    checklist_confirmations = relationship(
        "ChecklistConfirmation",
        back_populates="request",
        order_by="(ChecklistConfirmation.confirmed_at.desc(), ChecklistConfirmation.id)",
    )
```

`app/models/__init__.py`: add `from app.models.checklist import ChecklistConfirmation, ChecklistItem` (alphabetical, after `approval`/`audit_log`) and both names to `__all__`.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_checklist_models.py -q -p no:cacheprovider` → PASS.

- [ ] **Step 5: Migration** — `alembic/versions/c2e8f5a1d6b7_add_checklist_tables.py`

```python
"""add checklist_items and checklist_confirmations, seed the DB dump terms

Replaces the hard-coded DB_DUMP_START_CHECKLIST (commit 7356a1e) with terms admins
manage from /management/checklists, plus an audit row per confirmed term. Seeds the
four existing db_dump_restore terms so behaviour is identical straight after upgrade.

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
        sa.Column("request_type", request_type, nullable=False),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_checklist_items_request_type", "checklist_items", ["request_type"])
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
        sa.column("request_type", request_type),
        sa.column("label", sa.String),
        sa.column("position", sa.Integer),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
    )
    now = datetime.now(timezone.utc)
    op.bulk_insert(
        items,
        [
            {"request_type": "db_dump_restore", "label": label, "position": position, "is_active": True, "created_at": now}
            for position, label in enumerate(SEED_DB_DUMP_TERMS, start=1)
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_checklist_confirmations_request_id", table_name="checklist_confirmations")
    op.drop_table("checklist_confirmations")
    op.drop_index("ix_checklist_items_request_type", table_name="checklist_items")
    op.drop_table("checklist_items")
```

- [ ] **Step 6: Migration on a throwaway database** — as Task 1 Step 12, plus after the first upgrade:

```bash
docker exec deployment_status-db-1 psql -U deploy_tracker -d migcheck \
  -c "select request_type, position, label, is_active from checklist_items order by position;"
```
Expected: 4 `db_dump_restore` rows, positions 1–4, all active. After `downgrade -1`: `\dt checklist*` shows none, and `requesttype` still exists (`\dT requesttype`). `alembic heads` → `c2e8f5a1d6b7 (head)` only.

- [ ] **Step 7: Full suite, commit**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider` → all PASS.

```bash
git add app/models/checklist.py app/models/__init__.py app/models/deployment_request.py \
  alembic/versions/c2e8f5a1d6b7_add_checklist_tables.py tests/test_checklist_models.py
git commit -m "Add checklist_items and checklist_confirmations tables

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Checklists management page

**Files:**
- Create: `app/templates/management_checklists.html`, `tests/test_management_checklists.py`
- Modify: `app/routers/management.py`, `app/templates/management.html`, `app/static/style.css`

**Interfaces:**
- Consumes: `require_management`, `require_admin` (`app.auth`); `ChecklistItem`, `LABEL_MAX_LENGTH` (Task 2); `REQUEST_TYPE_LABELS` (`app.routers.dashboard`).
- Produces: `GET /management/checklists`; `POST /management/checklists` (`request_type`, `label`); `POST /management/checklists/{id}/edit` (`label`); `POST /management/checklists/{id}/move` (`direction` = `up`|`down`); `POST /management/checklists/{id}/deactivate`; `POST /management/checklists/{id}/activate`. All writes 303 → `/management/checklists`. Markup contract: each type is `<section class="checklist-section" data-request-type="<value>">`; each term row is `<tr data-item-id="<id>">`.

- [ ] **Step 1: Write the failing tests** — `tests/test_management_checklists.py`

```python
import re
from datetime import datetime, timezone

import pytest

from app.models.checklist import ChecklistItem
from app.models.deployment_request import RequestType
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def _seed_users(session):
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    manager = make_user(session, id=2, name="Mgr", username="mgr", password=DEFAULT_TEST_PASSWORD)
    manager.can_access_management = True
    make_user(session, id=3, name="Dev", username="dev", password=DEFAULT_TEST_PASSWORD)
    session.commit()


def _item(session, *, request_type=RequestType.db_dump_restore, label, position, is_active=True):
    item = ChecklistItem(request_type=request_type, label=label, position=position, is_active=is_active,
                         created_at=datetime.now(timezone.utc))
    session.add(item)
    session.commit()
    return item


def _section(page, request_type):
    match = re.search(rf'<section class="checklist-section" data-request-type="{request_type.value}">.*?</section>', page, re.S)
    assert match, f"no section for {request_type.value}"
    return match.group(0)


def _row(page, item_id):
    match = re.search(rf'<tr data-item-id="{item_id}">.*?</tr>', page, re.S)
    assert match, f"no row for item {item_id}"
    return match.group(0)


def test_user_without_management_access_gets_403(web):
    client, session = web
    _seed_users(session)
    login_as(client, "dev")
    assert client.get("/management/checklists").status_code == 403
    assert client.post("/management/checklists", data={"request_type": "standard", "label": "x"}).status_code == 403


def test_hub_links_to_checklists_for_management_user(web):
    client, session = web
    _seed_users(session)
    login_as(client, "mgr")
    assert 'href="/management/checklists"' in client.get("/management").text


def test_page_groups_terms_by_request_type(web):
    client, session = web
    _seed_users(session)
    dump = _item(session, label="Close cronjobs", position=1)
    local = _item(session, request_type=RequestType.test_local, label="Ping box", position=1)
    login_as(client, "mgr")

    page = client.get("/management/checklists").text

    assert f'data-item-id="{dump.id}"' in _section(page, RequestType.db_dump_restore)
    assert f'data-item-id="{local.id}"' in _section(page, RequestType.test_local)
    assert "data-item-id" not in _section(page, RequestType.standard)


def test_management_user_adds_term_at_end_of_its_type(web):
    client, session = web
    _seed_users(session)
    _item(session, label="Close cronjobs", position=1)
    _item(session, label="Restart workers", position=2)
    login_as(client, "mgr")

    response = client.post("/management/checklists", data={"request_type": "db_dump_restore", "label": "  Check .env  "},
                           follow_redirects=False)

    assert response.status_code == 303
    added = session.query(ChecklistItem).filter_by(label="Check .env").one()
    assert added.position == 3
    assert added.created_by == 2
    assert added.is_active is True


@pytest.mark.parametrize("data", [
    {"request_type": "db_dump_restore", "label": "   "},
    {"request_type": "not_a_type", "label": "x"},
    {"request_type": "db_dump_restore", "label": "x" * 501},
])
def test_add_rejects_bad_input(web, data):
    client, session = web
    _seed_users(session)
    login_as(client, "mgr")
    assert client.post("/management/checklists", data=data, follow_redirects=False).status_code == 400
    assert session.query(ChecklistItem).count() == 0


def test_management_user_cannot_edit_move_or_retire(web):
    client, session = web
    _seed_users(session)
    first = _item(session, label="Close cronjobs", position=1)
    _item(session, label="Restart workers", position=2)
    login_as(client, "mgr")

    assert client.post(f"/management/checklists/{first.id}/edit", data={"label": "changed"}).status_code == 403
    assert client.post(f"/management/checklists/{first.id}/move", data={"direction": "down"}).status_code == 403
    assert client.post(f"/management/checklists/{first.id}/deactivate").status_code == 403
    session.refresh(first)
    assert (first.label, first.position, first.is_active) == ("Close cronjobs", 1, True)


def test_management_user_sees_no_admin_controls(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", position=1)
    login_as(client, "mgr")

    row = _row(client.get("/management/checklists").text, item.id)

    assert "/edit" not in row and "/move" not in row and "/deactivate" not in row


def test_admin_edits_label_and_records_who(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", position=1)
    login_as(client, "root")

    response = client.post(f"/management/checklists/{item.id}/edit", data={"label": "Stop cron"}, follow_redirects=False)

    assert response.status_code == 303
    session.refresh(item)
    assert item.label == "Stop cron"
    assert item.updated_by == 1
    assert item.updated_at is not None


def test_admin_moves_term_within_its_type_only(web):
    client, session = web
    _seed_users(session)
    a = _item(session, label="A", position=1)
    b = _item(session, label="B", position=2)
    other = _item(session, request_type=RequestType.test_local, label="Other", position=3)
    login_as(client, "root")

    client.post(f"/management/checklists/{b.id}/move", data={"direction": "up"})

    for row in (a, b, other):
        session.refresh(row)
    assert (a.position, b.position, other.position) == (2, 1, 3)


def test_move_past_the_end_is_a_no_op(web):
    client, session = web
    _seed_users(session)
    a = _item(session, label="A", position=1)
    login_as(client, "root")

    response = client.post(f"/management/checklists/{a.id}/move", data={"direction": "up"}, follow_redirects=False)

    assert response.status_code == 303
    session.refresh(a)
    assert a.position == 1


def test_move_rejects_unknown_direction(web):
    client, session = web
    _seed_users(session)
    a = _item(session, label="A", position=1)
    login_as(client, "root")
    assert client.post(f"/management/checklists/{a.id}/move", data={"direction": "sideways"}).status_code == 400


def test_admin_retires_and_restores_term(web):
    client, session = web
    _seed_users(session)
    item = _item(session, label="Close cronjobs", position=1)
    login_as(client, "root")

    client.post(f"/management/checklists/{item.id}/deactivate")
    session.refresh(item)
    assert item.is_active is False
    assert "Retired" in _row(client.get("/management/checklists").text, item.id)

    client.post(f"/management/checklists/{item.id}/activate")
    session.refresh(item)
    assert item.is_active is True


def test_unknown_item_is_404(web):
    client, session = web
    _seed_users(session)
    login_as(client, "root")
    assert client.post("/management/checklists/999/deactivate").status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_management_checklists.py -q -p no:cacheprovider`
Expected: FAIL — 404s on `/management/checklists` (route missing), hub link missing.

- [ ] **Step 3: Routes** — append to `app/routers/management.py`; extend its imports:

```python
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import require_admin, require_management
from app.database import get_db
from app.models.checklist import LABEL_MAX_LENGTH, ChecklistItem
from app.models.deployment_request import RequestType
from app.models.user import User, UserRole
from app.routers.dashboard import REQUEST_TYPE_LABELS
from app.static_version import STATIC_VERSION
```

```python
CHECKLISTS_URL = "/management/checklists"


def _clean_label(label: str) -> str:
    label = label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="The term can't be blank.")
    if len(label) > LABEL_MAX_LENGTH:
        raise HTTPException(status_code=400, detail=f"Keep the term under {LABEL_MAX_LENGTH} characters.")
    return label


def _get_item_or_404(db: Session, item_id: int) -> ChecklistItem:
    item = db.get(ChecklistItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Checklist term not found")
    return item


def _touch(item: ChecklistItem, user: User) -> None:
    item.updated_by = user.id
    item.updated_at = datetime.now(timezone.utc)


@router.get("/checklists")
def list_checklists(request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_management)):
    items = db.query(ChecklistItem).order_by(ChecklistItem.position, ChecklistItem.id).all()
    # Grouped in Python, in RequestType declaration order: Postgres sorts an enum column
    # by declaration and SQLite by string, so ORDER BY request_type would differ by DB.
    sections = [
        (request_type, REQUEST_TYPE_LABELS[request_type], [i for i in items if i.request_type == request_type])
        for request_type in RequestType
    ]
    return templates.TemplateResponse(
        request,
        "management_checklists.html",
        {"current_user": current_user, "sections": sections, "is_admin": current_user.role == UserRole.admin},
    )


@router.post("/checklists")
def add_checklist_item(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_management),
    request_type: str = Form(...),
    label: str = Form(""),
):
    try:
        parsed_type = RequestType(request_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unknown request type")
    label = _clean_label(label)
    last = db.query(func.max(ChecklistItem.position)).filter(ChecklistItem.request_type == parsed_type).scalar()
    db.add(ChecklistItem(
        request_type=parsed_type, label=label, position=(last or 0) + 1, is_active=True,
        created_by=current_user.id, created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    return RedirectResponse(url=CHECKLISTS_URL, status_code=303)


@router.post("/checklists/{item_id}/edit")
def edit_checklist_item(
    item_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin), label: str = Form(""),
):
    # Safe to reword: ChecklistConfirmation snapshots the label it was confirmed with.
    item = _get_item_or_404(db, item_id)
    item.label = _clean_label(label)
    _touch(item, current_user)
    db.commit()
    return RedirectResponse(url=CHECKLISTS_URL, status_code=303)


@router.post("/checklists/{item_id}/move")
def move_checklist_item(
    item_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin), direction: str = Form(...),
):
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be up or down")
    item = _get_item_or_404(db, item_id)
    same_type = db.query(ChecklistItem).filter(ChecklistItem.request_type == item.request_type, ChecklistItem.id != item.id)
    if direction == "up":
        neighbour = same_type.filter(ChecklistItem.position < item.position).order_by(ChecklistItem.position.desc()).first()
    else:
        neighbour = same_type.filter(ChecklistItem.position > item.position).order_by(ChecklistItem.position.asc()).first()
    if neighbour is not None:
        item.position, neighbour.position = neighbour.position, item.position
        _touch(item, current_user)
        db.commit()
    return RedirectResponse(url=CHECKLISTS_URL, status_code=303)


def _set_active(db: Session, item_id: int, user: User, active: bool) -> RedirectResponse:
    item = _get_item_or_404(db, item_id)
    item.is_active = active
    _touch(item, user)
    db.commit()
    return RedirectResponse(url=CHECKLISTS_URL, status_code=303)


@router.post("/checklists/{item_id}/deactivate")
def deactivate_checklist_item(item_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    # Retire, never delete: confirmation rows reference this term.
    return _set_active(db, item_id, current_user, False)


@router.post("/checklists/{item_id}/activate")
def activate_checklist_item(item_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    return _set_active(db, item_id, current_user, True)
```

Before relying on `from app.routers.dashboard import REQUEST_TYPE_LABELS`, confirm there is no circular import: `.venv/bin/python -c "import app.main"` must succeed. If it fails, move `REQUEST_TYPE_LABELS` into `app/models/deployment_request.py` and import it from there in both routers.

- [ ] **Step 4: Page template** — `app/templates/management_checklists.html`

```html
{% extends "base.html" %}
{% block title %}Checklists — Management — Deployment Tracker{% endblock %}
{% block content %}
  <p><a href="/management" class="back-link">← Management</a></p>
  <h1>Checklists</h1>
  <p class="subtitle">Terms DevOps must tick before Start Deployment, per request type. A type with no active terms starts with one click.</p>

  {% for request_type, type_label, items in sections %}
    <section class="checklist-section" data-request-type="{{ request_type.value }}">
      <h2>{{ type_label }}</h2>
      {% if items %}
        <table class="status-table checklist-table">
          <tbody>
            {% for item in items %}
              <tr data-item-id="{{ item.id }}" {% if not item.is_active %}class="is-retired"{% endif %}>
                <td class="checklist-label">
                  {% if is_admin %}
                    <form method="post" action="/management/checklists/{{ item.id }}/edit" class="inline-form checklist-edit">
                      <input type="text" name="label" value="{{ item.label }}" maxlength="500" required>
                      <button type="submit">Save</button>
                    </form>
                  {% else %}
                    {{ item.label }}
                  {% endif %}
                  {% if not item.is_active %}<span class="status status-withdrawn">Retired</span>{% endif %}
                </td>
                {% if is_admin %}
                  <td class="checklist-actions">
                    <form method="post" action="/management/checklists/{{ item.id }}/move" class="inline-form">
                      <button type="submit" name="direction" value="up" class="button-secondary" title="Move up">↑</button>
                      <button type="submit" name="direction" value="down" class="button-secondary" title="Move down">↓</button>
                    </form>
                    {% if item.is_active %}
                      <form method="post" action="/management/checklists/{{ item.id }}/deactivate" class="inline-form">
                        <button type="submit" class="reject">Retire</button>
                      </form>
                    {% else %}
                      <form method="post" action="/management/checklists/{{ item.id }}/activate" class="inline-form">
                        <button type="submit" class="approve">Restore</button>
                      </form>
                    {% endif %}
                  </td>
                {% endif %}
              </tr>
            {% endfor %}
          </tbody>
        </table>
      {% else %}
        <p class="muted">No terms — Start Deployment is one click for this type.</p>
      {% endif %}
      <form method="post" action="/management/checklists" class="inline-form checklist-add">
        <input type="hidden" name="request_type" value="{{ request_type.value }}">
        <input type="text" name="label" placeholder="New term" maxlength="500" required>
        <button type="submit" class="start">Add term</button>
      </form>
    </section>
  {% endfor %}
{% endblock %}
```

Before adding CSS, check `status-withdrawn` exists in `style.css` (`grep -n "status-withdrawn" app/static/style.css`); if it doesn't, use `muted` for the Retired tag instead.

`app/static/style.css` (append):

```css
/* Checklists page (management_checklists.html): one panel per request type. */
.checklist-section {
  margin: 1.25rem 0;
  padding: 1rem 1.1rem;
  background: var(--panel);
  border: 1px solid var(--hairline);
  border-radius: var(--radius);
}
.checklist-section h2 { margin: 0 0 0.75rem; font-family: var(--font-display); font-size: 1rem; }
.checklist-table { margin-bottom: 0.75rem; }
.checklist-table tr.is-retired .checklist-label { color: var(--fog-dim); }
.checklist-edit input[type="text"],
.checklist-add input[type="text"] { width: min(520px, 100%); }
.checklist-actions { white-space: nowrap; }
```

`app/templates/management.html`: inside `.management-cards`, after the Users card:

```html
    <a class="management-card" href="/management/checklists">
      <span class="management-card-title">Checklists</span>
      <span class="management-card-text">Terms DevOps confirm before starting each request type.</span>
    </a>
```

- [ ] **Step 5: Run the task's tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_management_checklists.py tests/test_management.py -q -p no:cacheprovider` → PASS.
Run: `.venv/bin/python -m pytest -q -p no:cacheprovider` → all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routers/management.py app/templates/management_checklists.html app/templates/management.html \
  app/static/style.css tests/test_management_checklists.py
git commit -m "Add Checklists management page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Start Deployment reads terms from the database and records confirmations

**Files:**
- Create: `app/services/checklists.py`
- Rename + rewrite: `tests/test_db_dump_start_checklist.py` → `tests/test_start_checklist.py` (`git mv`)
- Modify: `app/routers/dashboard.py` (`start_request`, `list_requests` context, imports), `app/models/deployment_request.py` (remove `DB_DUMP_START_CHECKLIST`), `app/templates/_request_row.html` (Start branch), `app/templates/request_list.html` (dialog + JS)

**Interfaces:**
- Consumes: `ChecklistItem`, `ChecklistConfirmation` (Task 2).
- Produces: `app.services.checklists.active_items_by_type(db) -> dict[RequestType, list[ChecklistItem]]`; `app.services.checklists.ChecklistIncomplete(Exception)` with `.missing: list[ChecklistItem]`; `app.services.checklists.record_start_confirmations(db, deployment_request, submitted_ids: list[str], user: User, now: datetime) -> None` (adds rows, does **not** commit; raises `ChecklistIncomplete`). Template context key `start_checklists` (the dict above). Markup: dump-style row button `data-start-action="/requests/<id>/start" data-checklist-type="<type>"`; dialog `<dialog id="start-checklist-modal-<type>" class="changes-modal" data-start-checklist>`; checkbox `name="checklist" value="<item id>"`.

- [ ] **Step 1: Rename the old test file and replace its contents**

```bash
git mv tests/test_db_dump_start_checklist.py tests/test_start_checklist.py
```

`tests/test_start_checklist.py`:

```python
"""Start Deployment requires every active checklist term for the request's type.

Terms live in checklist_items (managed at /management/checklists); each confirmed start
writes one checklist_confirmations row per term, in the same transaction as the
execution row. The gate is the route, not the pop-up — several tests POST directly.
"""

import re
from datetime import datetime, timezone

import pytest

from app.models.checklist import ChecklistConfirmation, ChecklistItem
from app.models.deployment_execution import DeploymentExecution
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user

DUMP_TERMS = (
    "Close cronjobs / scheduled jobs",
    "Restart workers to apply the change",
    "Check .env for anything that can trigger emails",
    "After restoration, remove email settings from the settings table and the web UI (basevisu module)",
)


def _users(session):
    make_user(session, id=1, name="Zunayed Islam", username="zunayed", password=DEFAULT_TEST_PASSWORD, role=UserRole.admin)
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()


def _terms(session, request_type=RequestType.db_dump_restore, labels=DUMP_TERMS):
    items = [
        ChecklistItem(request_type=request_type, label=label, position=n, created_at=datetime.now(timezone.utc))
        for n, label in enumerate(labels, start=1)
    ]
    session.add_all(items)
    session.commit()
    return items


def _request(session, request_type=RequestType.db_dump_restore):
    request = DeploymentRequest(
        task_id="PR-X", requested_by=2, status=RequestStatus.approved, request_type=request_type,
        dump_source="crm-live", restore_source="crm-staging", created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    session.add(request)
    session.commit()
    return request


def _ids(items):
    return [str(i.id) for i in items]


def _start(client, request, ids=None):
    data = {"checklist": ids} if ids is not None else {}
    return client.post(f"/requests/{request.id}/start", data=data, follow_redirects=False)


def _assert_nothing_written(session, request):
    session.refresh(request)
    assert request.status == RequestStatus.approved
    assert session.query(DeploymentExecution).filter_by(request_id=request.id).count() == 0
    assert session.query(ChecklistConfirmation).filter_by(request_id=request.id).count() == 0


@pytest.fixture()
def dump(web):
    client, session = web
    _users(session)
    items = _terms(session)
    request = _request(session)
    login_as(client, "zunayed")
    return client, session, items, request


def test_start_without_checklist_is_rejected(dump):
    client, session, _items, request = dump
    assert _start(client, request).status_code == 400
    _assert_nothing_written(session, request)


def test_start_with_partial_checklist_is_rejected(dump):
    client, session, items, request = dump
    assert _start(client, request, _ids(items[:3])).status_code == 400
    _assert_nothing_written(session, request)


def test_start_with_full_checklist_records_each_confirmation(dump):
    client, session, items, request = dump

    assert _start(client, request, _ids(items)).status_code == 303

    session.refresh(request)
    assert request.status == RequestStatus.in_progress
    rows = session.query(ChecklistConfirmation).filter_by(request_id=request.id).order_by(ChecklistConfirmation.id).all()
    assert [r.item_label for r in rows] == list(DUMP_TERMS)
    assert {r.confirmed_by for r in rows} == {1}
    assert len({r.confirmed_at for r in rows}) == 1


def test_extra_unknown_ids_are_ignored(dump):
    client, session, items, request = dump
    assert _start(client, request, _ids(items) + ["99999", "not-a-number"]).status_code == 303
    assert session.query(ChecklistConfirmation).filter_by(request_id=request.id).count() == 4


def test_retired_term_is_not_required(dump):
    client, session, items, request = dump
    items[3].is_active = False
    session.commit()

    assert _start(client, request, _ids(items[:3])).status_code == 303
    assert session.query(ChecklistConfirmation).filter_by(request_id=request.id).count() == 3


def test_term_added_after_page_load_blocks_start(dump):
    client, session, items, request = dump
    seen_on_page = _ids(items)
    _terms(session, labels=("Snapshot the target DB",))

    response = _start(client, request, seen_on_page)

    assert response.status_code == 400
    assert "Snapshot the target DB" in response.text
    _assert_nothing_written(session, request)


@pytest.mark.parametrize("request_type", [RequestType.standard, RequestType.test_local])
def test_type_without_terms_starts_with_one_click(web, request_type):
    client, session = web
    _users(session)
    _terms(session)  # dump terms exist, but not for this type
    request = _request(session, request_type)
    login_as(client, "zunayed")

    assert _start(client, request).status_code == 303
    assert session.query(ChecklistConfirmation).count() == 0


def test_terms_added_to_standard_are_enforced(web):
    client, session = web
    _users(session)
    _terms(session, RequestType.standard, ("Confirm branch is merged",))
    request = _request(session, RequestType.standard)
    login_as(client, "zunayed")

    assert _start(client, request).status_code == 400
    _assert_nothing_written(session, request)


def test_confirmations_survive_return_and_restart(web):
    client, session = web
    _users(session)
    items = _terms(session, RequestType.test_local, ("Ping the box",))
    request = _request(session, RequestType.test_local)

    login_as(client, "zunayed")
    assert _start(client, request, _ids(items)).status_code == 303
    assert client.post(f"/requests/{request.id}/return", data={"reason": "wrong branch"}, follow_redirects=False).status_code == 303
    login_as(client, "devone")
    assert client.post(f"/requests/{request.id}/resubmit", follow_redirects=False).status_code == 303
    login_as(client, "zunayed")
    assert _start(client, request, _ids(items)).status_code == 303

    assert session.query(ChecklistConfirmation).filter_by(request_id=request.id).count() == 2


def test_confirmation_keeps_label_snapshot_after_edit(dump):
    client, session, items, request = dump
    _start(client, request, _ids(items))

    client.post(f"/management/checklists/{items[0].id}/edit", data={"label": "Reworded"})

    first = session.query(ChecklistConfirmation).filter_by(checklist_item_id=items[0].id).one()
    session.refresh(first)
    assert first.item_label == DUMP_TERMS[0]


def _row_html(page, request_id):
    for row in re.findall(r"<tr>.*?</tr>", page, re.S):
        if f'data-return-action="/requests/{request_id}/return"' in row:
            return row
    raise AssertionError(f"no row found for request {request_id}")


def _dialog_html(page, request_type):
    match = re.search(rf'<dialog id="start-checklist-modal-{request_type.value}".*?</dialog>', page, re.S)
    assert match, f"no start checklist dialog for {request_type.value}"
    return match.group(0)


def test_row_with_terms_opens_its_types_dialog(dump):
    client, _session, _items, request = dump

    row = _row_html(client.get("/requests").text, request.id)

    assert f'<form method="post" action="/requests/{request.id}/start"' not in row
    assert f'data-start-action="/requests/{request.id}/start"' in row
    assert 'data-checklist-type="db_dump_restore"' in row


def test_dialog_lists_active_terms_only(dump):
    client, session, items, _request_ = dump
    items[3].is_active = False
    session.commit()

    dialog = _dialog_html(client.get("/requests").text, RequestType.db_dump_restore)

    for item in items[:3]:
        assert f'name="checklist" value="{item.id}"' in dialog
    assert f'value="{items[3].id}"' not in dialog


def test_row_without_terms_keeps_direct_start_and_no_dialog(web):
    client, session = web
    _users(session)
    request = _request(session, RequestType.standard)
    login_as(client, "zunayed")

    page = client.get("/requests").text

    assert f'<form method="post" action="/requests/{request.id}/start"' in _row_html(page, request.id)
    assert 'id="start-checklist-modal-standard"' not in page


def test_non_deployer_gets_no_start_dialogs(web):
    client, session = web
    _users(session)
    _terms(session)
    _request(session)
    login_as(client, "devone")

    assert "start-checklist-modal" not in client.get("/requests").text
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_start_checklist.py -q -p no:cacheprovider`
Expected: FAIL — full-checklist starts return 400 (route still checks the old string ids), no `data-checklist-type`, no `start-checklist-modal-db_dump_restore`. `test_type_without_terms_*` and `test_non_deployer_*` may already pass; that is fine because they guard existing behaviour. Every other test must fail — if one passes, fix the test before continuing.

- [ ] **Step 3: Service** — `app/services/checklists.py`

```python
"""Which checklist terms a Start needs, and recording that they were confirmed.

start_request() (app/routers/dashboard.py) calls record_start_confirmations() before it
adds the DeploymentExecution row and commits once, so a start and its confirmations are
saved together or not at all.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.checklist import ChecklistConfirmation, ChecklistItem
from app.models.deployment_request import DeploymentRequest, RequestType
from app.models.user import User


class ChecklistIncomplete(Exception):
    def __init__(self, missing: list[ChecklistItem]):
        super().__init__("checklist incomplete")
        self.missing = missing


def active_items_by_type(db: Session) -> dict[RequestType, list[ChecklistItem]]:
    """Active terms grouped by request type, in display order. One query for the whole
    request listing — never per row."""
    items = (
        db.query(ChecklistItem)
        .filter(ChecklistItem.is_active.is_(True))
        .order_by(ChecklistItem.position, ChecklistItem.id)
        .all()
    )
    grouped: dict[RequestType, list[ChecklistItem]] = {}
    for item in items:
        grouped.setdefault(item.request_type, []).append(item)
    return grouped


def record_start_confirmations(
    db: Session, deployment_request: DeploymentRequest, submitted_ids: list[str], user: User, now: datetime
) -> None:
    """Adds one confirmation per required term, or raises ChecklistIncomplete.

    Required is re-read here, not trusted from the page: a term added while the pop-up
    was open must block the start. Submitted ids that aren't required (a term retired
    meanwhile, junk) are ignored. Does not commit — the caller's commit covers this and
    the execution row together.
    """
    required = active_items_by_type(db).get(deployment_request.request_type, [])
    submitted = set(submitted_ids)
    missing = [item for item in required if str(item.id) not in submitted]
    if missing:
        raise ChecklistIncomplete(missing)
    for item in required:
        db.add(ChecklistConfirmation(
            request_id=deployment_request.id, checklist_item_id=item.id, item_label=item.label,
            confirmed_by=user.id, confirmed_at=now,
        ))
```

- [ ] **Step 4: Route** — `app/routers/dashboard.py`

Imports: remove `DB_DUMP_START_CHECKLIST,` from the `app.models.deployment_request` import; add
`from app.services.checklists import ChecklistIncomplete, active_items_by_type, record_start_confirmations`.

In `start_request`, replace the comment on the `checklist` parameter with:

```python
    # Ids of the checklist terms ticked in the Start pop-up (app/services/checklists.py).
    # Form([]), not Form(...): a type with no active terms has no pop-up and posts nothing.
    checklist: list[str] = Form([]),
```

Replace the whole `if deployment_request.request_type == RequestType.db_dump_restore:` block **and** the line `now = datetime.now(timezone.utc)` below it with:

```python
    now = datetime.now(timezone.utc)
    # After the permission and status checks so a rejected attempt says what it always
    # did; before the execution row so a 400 leaves nothing behind.
    try:
        record_start_confirmations(db, deployment_request, checklist, current_user, now)
    except ChecklistIncomplete as exc:
        raise HTTPException(
            status_code=400,
            detail="The checklist for this request is incomplete or has changed — reload and confirm: "
            + "; ".join(item.label for item in exc.missing),
        )
```

In `list_requests`' template context, replace `"db_dump_start_checklist": DB_DUMP_START_CHECKLIST,` with:

```python
            # Only deployers get Start buttons, so only they need the pop-ups.
            "start_checklists": active_items_by_type(db) if can_deploy else {},
```

`app/models/deployment_request.py`: delete the `DB_DUMP_START_CHECKLIST` constant and its comment block. Then `grep -rn "DB_DUMP_START_CHECKLIST\|db_dump_start_checklist" app tests` → no output.

- [ ] **Step 5: Row template** — `app/templates/_request_row.html`, replace the Start branch added in `7356a1e`:

```html
                  {# A type with active checklist terms can't be started with one click:
                     its pop-up (request_list.html) collects the confirmations, and
                     start_request() re-checks them — this button alone is not the gate. #}
                  {% if start_checklists.get(r.request_type) %}
                    <button
                      type="button" class="start"
                      data-start-action="/requests/{{ r.id }}/start"
                      data-checklist-type="{{ r.request_type.value }}"
                    >Start Deployment</button>
                  {% else %}
                    <form method="post" action="/requests/{{ r.id }}/start" class="inline-form">
                      <button type="submit" class="start">Start Deployment</button>
                    </form>
                  {% endif %}
```

- [ ] **Step 6: Dialogs and JS** — `app/templates/request_list.html`

Replace the whole `<dialog id="start-checklist-modal" …>…</dialog>` block from `7356a1e` with:

```html
  {% for checklist_type, checklist_items in start_checklists.items() %}
    <dialog id="start-checklist-modal-{{ checklist_type.value }}" class="changes-modal" data-start-checklist>
      <h2>Before you start this {{ request_type_labels[checklist_type] }}</h2>
      <form method="post" class="form">
        <p class="hint">Every item is required.</p>
        {% for item in checklist_items %}
          <label class="checklist-item">
            <input type="checkbox" name="checklist" value="{{ item.id }}">
            <span>{{ item.label }}</span>
          </label>
        {% endfor %}
        <div class="changes-modal-actions">
          <button type="submit" class="start" disabled>Start Deployment</button>
          <button type="button" class="button-secondary" data-start-checklist-cancel>Cancel</button>
        </div>
      </form>
    </dialog>
  {% endfor %}
```

Replace the whole JS block that starts with `// --- DB dump pre-flight checklist` (through the end of its `(function () { … })();`) with:

```js
      // --- Start checklist pop-ups -----------------------------------------------------
      // One dialog per request type that has active terms. The row button names its type
      // and carries the action URL; Start stays disabled until every box is ticked.
      // start_request() enforces the same list server-side, so this only stops a person
      // submitting something doomed.
      (function () {
        document.querySelectorAll("dialog[data-start-checklist]").forEach(function (modal) {
          var submit = modal.querySelector('button[type="submit"]');
          var boxes = modal.querySelectorAll('input[name="checklist"]');
          boxes.forEach(function (box) {
            box.addEventListener("change", function () {
              submit.disabled = !Array.prototype.every.call(boxes, function (b) { return b.checked; });
            });
          });
          modal.querySelector("[data-start-checklist-cancel]").addEventListener("click", function () {
            modal.close();
          });
          modal.addEventListener("click", function (event) {
            if (event.target === modal) modal.close();
          });
        });
        document.querySelectorAll("[data-start-action]").forEach(function (button) {
          button.addEventListener("click", function () {
            var modal = document.getElementById("start-checklist-modal-" + button.getAttribute("data-checklist-type"));
            if (!modal) return;
            var form = modal.querySelector("form");
            form.action = button.getAttribute("data-start-action");
            form.reset();
            form.querySelector('button[type="submit"]').disabled = true;
            modal.showModal();
          });
        });
      })();
```

- [ ] **Step 7: Run the task's tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_start_checklist.py -q -p no:cacheprovider` → PASS.
Run: `.venv/bin/python -m pytest -q -p no:cacheprovider` → all PASS.

- [ ] **Step 8: Commit**

```bash
git add app/services/checklists.py app/routers/dashboard.py app/models/deployment_request.py \
  app/templates/_request_row.html app/templates/request_list.html tests/test_start_checklist.py
git commit -m "Drive Start Deployment checklists from the database and record confirmations

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Checklist audit view and its access switch

**Files:**
- Create: `tests/test_checklist_audit.py`
- Modify: `app/routers/admin.py`, `app/templates/admin_users.html`, `app/routers/dashboard.py` (`list_requests` eager loads + context), `app/templates/_request_row.html` (status cell), `app/templates/request_list.html` (dialog + JS), `app/static/style.css`

**Interfaces:**
- Consumes: `can_view_checklist_audit(user)` (Task 1); `DeploymentRequest.checklist_confirmations`, `ChecklistConfirmation.confirmer` (Task 2).
- Produces: `POST /admin/users/{id}/set-checklist-audit-access` (checkbox `can_view_checklist_audit`); context key `can_view_audit: bool`; markup `data-checklist-log="<request id>"` button, hidden `<div id="checklist-log-<request id>">`, `<dialog id="checklist-log-modal">`.

- [ ] **Step 1: Write the failing tests** — `tests/test_checklist_audit.py`

```python
"""The checklist audit on a request row is its own grant: admins always, anyone else only
with User.can_view_checklist_audit. Without it, the audit must not be in the page at all."""

from datetime import datetime, timezone

from app.models.checklist import ChecklistConfirmation, ChecklistItem
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from app.models.user import User, UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user

# Differs from the term's current label on purpose: proves the dialog shows the snapshot,
# and gives the leak test a string that can only come from the audit.
SNAPSHOT = "SNAPSHOT: close cronjobs as worded at start"


def _seed(session, *, dev_audit=False):
    make_user(session, id=1, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    dev = make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    dev.can_view_checklist_audit = dev_audit
    session.flush()
    item = ChecklistItem(request_type=RequestType.db_dump_restore, label="Reworded later", position=1,
                         created_at=datetime.now(timezone.utc))
    request = DeploymentRequest(task_id="PR-A", requested_by=2, status=RequestStatus.in_progress,
                                request_type=RequestType.db_dump_restore, dump_source="crm-live",
                                restore_source="crm-staging", created_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    session.add_all([item, request])
    session.flush()
    session.add(ChecklistConfirmation(request_id=request.id, checklist_item_id=item.id, item_label=SNAPSHOT,
                                      confirmed_by=1, confirmed_at=datetime(2026, 9, 2, 9, 30)))
    session.commit()
    return request


def test_audit_contents_absent_for_user_without_access(web):
    client, session = web
    _seed(session)
    login_as(client, "devone")

    page = client.get("/requests").text

    assert SNAPSHOT not in page
    assert "data-checklist-log" not in page


def test_user_with_access_sees_audit_with_who(web):
    client, session = web
    request = _seed(session, dev_audit=True)
    login_as(client, "devone")

    page = client.get("/requests").text

    assert f'data-checklist-log="{request.id}"' in page
    log_start = page.index(f'id="checklist-log-{request.id}"')
    log = page[log_start:page.index("</ul>", log_start)]
    assert SNAPSHOT in log
    assert "Root Admin" in log


def test_admin_always_sees_audit(web):
    client, session = web
    request = _seed(session)
    login_as(client, "root")

    assert f'data-checklist-log="{request.id}"' in client.get("/requests").text


def test_admin_toggles_audit_access(web):
    client, session = web
    _seed(session)
    login_as(client, "root")

    client.post("/admin/users/2/set-checklist-audit-access", data={"can_view_checklist_audit": "on"})
    session.expire_all()
    assert session.get(User, 2).can_view_checklist_audit is True

    client.post("/admin/users/2/set-checklist-audit-access", data={})
    session.expire_all()
    assert session.get(User, 2).can_view_checklist_audit is False


def test_non_admin_cannot_toggle_audit_access(web):
    client, session = web
    _seed(session, dev_audit=True)
    login_as(client, "devone")

    assert client.post("/admin/users/2/set-checklist-audit-access", data={}).status_code == 403
    session.expire_all()
    assert session.get(User, 2).can_view_checklist_audit is True
```

The `log` slice in `test_user_with_access_sees_audit_with_who` runs from the hidden log `<div>` to its first `</ul>` (Step 5's markup). It must stay scoped to that `<div>`, not the page.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_checklist_audit.py -q -p no:cacheprovider`
Expected: `test_audit_contents_absent_*` passes (nothing renders the audit yet — it guards the leak). All others FAIL: no `data-checklist-log`, 404/405 on `set-checklist-audit-access`.

- [ ] **Step 3: Admin switch** — `app/routers/admin.py`, after `set_management_access`:

```python
@router.post("/users/{user_id}/set-checklist-audit-access")
def set_checklist_audit_access(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    can_view_checklist_audit: bool = Form(False),
):
    """Grants or revokes the checklist audit view on request rows (User.can_view_checklist_audit)."""
    user = _get_user_or_404(db, user_id)
    user.can_view_checklist_audit = can_view_checklist_audit
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)
```

`app/templates/admin_users.html`: add `<th>Checklist audit</th>` after `<th>Management access</th>` and the matching cell:

```html
        <td>
          {% if u.role.value == "admin" %}
            <span class="muted">Always</span>
          {% else %}
            <form method="post" action="/admin/users/{{ u.id }}/set-checklist-audit-access" class="inline-form">
              <label>
                <input type="checkbox" name="can_view_checklist_audit" {% if u.can_view_checklist_audit %}checked{% endif %}>
              </label>
              <button type="submit">Save</button>
            </form>
          {% endif %}
        </td>
```

- [ ] **Step 4: Listing** — `app/routers/dashboard.py`

Add `can_view_checklist_audit` to the `app.auth` import and `from app.models.checklist import ChecklistConfirmation`.
In **both** `.options(...)` calls in `list_requests` (`my_returned_requests` and `requests_`), add:

```python
            # Every row with confirmations renders its audit dialog — N+1 otherwise.
            selectinload(DeploymentRequest.checklist_confirmations).joinedload(ChecklistConfirmation.confirmer),
```

Context: add `"can_view_audit": can_view_checklist_audit(current_user),`.

- [ ] **Step 5: Row markup** — `app/templates/_request_row.html`

Inside `.status-cell`, directly after the `{% if r.returns or r.withdrawn_note %}…{% endif %}` return-log button:

```html
              {# Gated here, not just hidden: without the grant the confirmations never
                 reach this viewer's HTML (can_view_checklist_audit in app/auth.py). #}
              {% if can_view_audit and r.checklist_confirmations %}
                <button
                  type="button" class="return-log-button is-muted" data-checklist-log="{{ r.id }}"
                  title="Checklist confirmations"
                >✓</button>
              {% endif %}
```

After the `{% if r.returns or r.withdrawn_note %}<div class="return-log-data" …>…</div>{% endif %}` block:

```html
            {% if can_view_audit and r.checklist_confirmations %}
              <div class="return-log-data" id="checklist-log-{{ r.id }}" hidden>
                {# One group per start (a start writes all its rows with one timestamp);
                   newest first, same as the return history. #}
                {% for confirmed_at, rows in r.checklist_confirmations | groupby("confirmed_at") | reverse %}
                  <div class="return-log-entry">
                    <div class="return-log-meta">
                      {{ localtime(confirmed_at) }} · {{ rows[0].confirmer.name if rows[0].confirmer else "—" }}
                    </div>
                    <ul class="checklist-log-items">
                      {% for c in rows %}<li>{{ c.item_label }}</li>{% endfor %}
                    </ul>
                  </div>
                {% endfor %}
              </div>
            {% endif %}
```

- [ ] **Step 6: Dialog + JS** — `app/templates/request_list.html`, after `<dialog id="return-log-modal" …>…</dialog>`:

```html
  <dialog id="checklist-log-modal" class="changes-modal">
    <h2>Checklist confirmations</h2>
    <div id="checklist-log-body"></div>
    <div class="changes-modal-actions">
      <button type="button" class="button-secondary" id="checklist-log-close">Close</button>
    </div>
  </dialog>
```

Inside the IIFE that wires `return-log-modal`, after its close handler:

```js
        // Same shape as the return history: the hidden per-row div is the source.
        var checklistLogModal = document.getElementById("checklist-log-modal");
        var checklistLogBody = document.getElementById("checklist-log-body");
        document.querySelectorAll("[data-checklist-log]").forEach(function (button) {
          button.addEventListener("click", function () {
            var source = document.getElementById("checklist-log-" + button.getAttribute("data-checklist-log"));
            checklistLogBody.innerHTML = source ? source.innerHTML : "";
            checklistLogModal.showModal();
          });
        });
        document.getElementById("checklist-log-close").addEventListener("click", function () {
          checklistLogModal.close();
        });
```

`app/static/style.css` (append):

```css
/* Checklist audit dialog: the terms confirmed at one start. */
.checklist-log-items {
  margin: 0.35rem 0 0;
  padding-left: 1.1rem;
  color: var(--fog);
  font-size: 0.88rem;
  line-height: 1.5;
}
```

- [ ] **Step 7: Run the task's tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_checklist_audit.py -q -p no:cacheprovider` → PASS.
Run: `.venv/bin/python -m pytest -q -p no:cacheprovider` → all PASS.

- [ ] **Step 8: Commit**

```bash
git add app/routers/admin.py app/templates/admin_users.html app/routers/dashboard.py \
  app/templates/_request_row.html app/templates/request_list.html app/static/style.css tests/test_checklist_audit.py
git commit -m "Show checklist confirmations to users granted audit access

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Whole-branch verification on 8010

No new code. CSS and JS aren't covered by the suite, so this is the visual check.

- [ ] **Step 1:** `.venv/bin/python -m alembic heads` → exactly `c2e8f5a1d6b7 (head)`.
- [ ] **Step 2:** Full chain on a throwaway database: create `migcheck`, `upgrade head`, confirm 4 seeded terms, `downgrade f4a9c2e1b3d5`, confirm both tables and both user columns are gone and `requesttype` remains, `upgrade head` again, drop `migcheck`.
- [ ] **Step 3:** `.venv/bin/python -m pytest -q -p no:cacheprovider` → all PASS.
- [ ] **Step 4:** Rebuild 8010 (local-only, confirmed by the user on 2026-09-29): `docker compose up -d --build app`. Its startup runs `alembic upgrade head` against the local `deploy_tracker` — this is the step that adds the tables and seeds the 4 terms there. **Stop and get the user's go-ahead before this step.**
- [ ] **Step 5:** In Chrome, as an admin: Management tab → hub shows Users + Checklists; Checklists page shows the 4 dump terms; add a term to Test.local, edit, move, retire, restore. Users page shows both switches. On Requests: DB dump Start opens the pop-up; a Test.local request with its new term opens its own pop-up; a standard request starts with one click. Any test request created is labelled `ZZ-CHECKLIST-TEST`, deleted afterwards, and the Test.local term retired.
- [ ] **Step 6:** Report results to the user. No push, PR or merge without their instruction; before any merge, re-run the full suite and check the **live** schema per CLAUDE.md.
