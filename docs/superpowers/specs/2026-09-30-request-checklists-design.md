# Request checklists + Management tab — design

Status: draft for review · 2026-09-30 · branch `feature/db-dump-start-checklist`

## Why

Commit `7356a1e` added a pre-flight checklist to Start Deployment on `db_dump_restore`
requests: a restored dump carries the source system's cron schedules, worker config and
email settings, so a restore target started blind begins mailing real customers. That
list is hard-coded (`DB_DUMP_START_CHECKLIST`) and nothing records that it was ticked.

This design replaces it with:

1. checklist terms stored in the database, managed from the UI, per request type — no code
   change needed to add or reword a term;
2. an audit trail of who confirmed which term, when, for which request;
3. a **Management** tab that holds this and future management tools, with a per-user
   **Management access** switch so non-admins can be given access.

## Decisions (agreed in conversation)

| # | Decision |
|---|---|
| D1 | Two stages (revised 2026-10-05): each term is due **Before start** (gates Start Deployment) or **Before Mark Deployed** (gates Mark Deployed; for steps done after the work). Ticks are saved one at a time as they're made, per attempt (`round`), so a closed pop-up loses nothing and a Return starts the next attempt fresh. Added by migration `b9d3f6a2e8c4` on top of the already-deployed `c2e8f5a1d6b7`. |
| D2 | **One term, many request types** (revised 2026-09-30). A term is assigned to any set of types via `checklist_item_types`; each type keeps its own order. Editing the wording changes it everywhere. Assigning is refused if that type already has an active term with the same wording. |
| D3 | Management access is a **per-user permission switch**, not a role. A user keeps their one existing role (`developer`/`team_lead`/`devops`/`admin`); a fifth role would strip a DevOps user of deploy rights. |
| D4 | **Admin**: view, add, edit, reorder, deactivate/reactivate checklist terms. **Management access**: view and add only. Everyone else: no tab, 403 on the URLs. |
| D5 | The checklist audit on a request row is its own access: visible to **admins always**, and to any other user **only if an admin grants it** ("Checklist audit access", off by default). Team or role grants nothing on its own. |
| D6 | Management tab holds **Users** (today's "Admin" tab) and **Checklists** only. Clients, Seeder Collection and Release Tracker stay where they are. |
| D7 | Terms are **never hard-deleted** — audit rows reference them. Deactivation retires a term. |

## Data model

### `checklist_items` — the terms

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `label` | `String(500)`, NOT NULL | Stripped; blank rejected |
| `is_active` | bool, NOT NULL, default true | Retiring a term removes it from every type's checklist |
| `created_by` | FK `users.id`, nullable | Null only for the rows seeded by the migration |
| `created_at` | DateTime, NOT NULL | |
| `updated_by` | FK `users.id`, nullable | Last edit / assignment / reorder / (de)activation |
| `updated_at` | DateTime, nullable | |

### `checklist_item_types` — which types a term applies to

| Column | Type | Notes |
|---|---|---|
| `item_id` | FK `checklist_items.id`, PK | |
| `request_type` | `requesttype` enum, PK | Reuses the existing Postgres enum type (`create_type=False`) |
| `position` | int, NOT NULL | Order within that type; a newly assigned type appends at the end |

Unassigning a type deletes its row (nothing references an assignment). A term with no
rows is "not assigned" — kept, required nowhere.

### `checklist_confirmations` — the audit trail

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `request_id` | FK `deployment_requests.id`, NOT NULL, indexed | Keyed on the request, **not** the execution: Return deletes the `DeploymentExecution` row |
| `checklist_item_id` | FK `checklist_items.id`, NOT NULL | |
| `item_label` | `String(500)`, NOT NULL | Snapshot of the wording at confirmation time |
| `confirmed_by` | FK `users.id`, NOT NULL | |
| `confirmed_at` | DateTime, NOT NULL | |

No unique constraint: Start → Return → Resubmit → Start writes a second set of rows, and
both are kept. `DeploymentRequest.checklist_confirmations` relationship, newest first.

### `users` — two new switches

| Column | Default | Meaning |
|---|---|---|
| `can_access_management` | `false` | Grants the Management tab and view+add on Checklists. Ignored for admins, who always have full access. |
| `can_view_checklist_audit` | `false` | Grants the checklist audit view on request rows. Any user can be given it by an admin. Ignored for admins, who always see it. |

Both are toggled on the Users page by an admin, the same way `can_manage_other_returns` is
today (checkbox form → `POST /admin/users/{id}/set-…`).

## Permissions (`app/auth.py`)

- `can_access_management(user)` → admin or `user.can_access_management`.
- `require_management` dependency → 403 unless `can_access_management`.
- Checklist edit/reorder/deactivate routes use the existing `require_admin`.
- `can_view_checklist_audit(user)` → admin or `user.can_view_checklist_audit`. The
  confirmations are rendered into the page only for these viewers, so the dialog's
  contents never reach anyone else's browser.

Every route is gated by its dependency; the nav link and buttons are hidden to match.
Hiding alone is never the control.

## Management tab

- Nav: the **Admin** link is replaced by **Management**, shown when
  `can_access_management(current_user)`.
- `GET /management` — hub page listing tools as cards; each card shown only to users who
  may open it: **Users** (admin only) → `/admin/users`; **Checklists** → `/management/checklists`.
- `/admin/users` keeps its URL (existing tests and bookmarks depend on it); it only gains
  the two new switches and a "← Management" link.

## Checklists page — `/management/checklists`

- `GET` (require_management): one section per request type, in `RequestType` order, each
  listing its terms by `position`; inactive terms shown greyed with a "Retired" tag. Each
  section has an **Add term** form.
- `POST /management/checklists` (require_management): `request_type`, `label` → append.
  400 on blank label or unknown type.
- `POST /management/checklists/{id}/edit` (require_admin): new `label`.
- `POST /management/checklists/{id}/move` (require_admin): `direction=up|down`, swaps
  `position` with the neighbour of the same type. No drag-and-drop.
- `POST /management/checklists/{id}/deactivate` and `/activate` (require_admin).

All writes set `updated_by`/`updated_at` (or `created_*`), then redirect back (303).
Styling uses the existing `:root` tokens and `changes-modal`/form patterns — no hex.

## Start Deployment

- The request listing loads active terms once per page, grouped by type (one query — no
  per-row lookups).
- A row whose type has **no** active terms keeps the one-click Start form — so `standard`
  and `test_local` behave exactly as today until someone adds terms for them.
- A row whose type **has** terms gets a button with `data-start-action` and
  `data-checklist-type`; request_list.html renders **one `<dialog>` per request type that
  has terms** (`id="start-checklist-modal-<type>"`). Start stays disabled until every box
  in that dialog is ticked. Checkbox `name="checklist"`, `value="<checklist_item.id>"`.
- `start_request()`, after the existing permission and status checks:
  1. required = active term ids for the request's type;
  2. if any required id is missing from the submitted `checklist` → **400**, nothing
     written. Extra ids (e.g. a term retired while the pop-up was open) are ignored.
  3. otherwise insert one `checklist_confirmations` row per required term (label
     snapshotted) and the `DeploymentExecution` row, in **one commit**.
- A term added while a pop-up was open makes the submit fail with 400: "The checklist
  for this request type changed — reload and confirm again." Fails safe.

`DB_DUMP_START_CHECKLIST` and its string ids are removed.

## Audit view on the row

When `can_view_checklist_audit(viewer)` and the request has confirmations, the status cell
shows a **Checklist ✓** link that opens a dialog listing each confirmation set, newest
first: time · who · the terms. Same pattern as the existing return-history dialog
(`data-return-log` → `return-log-modal`). The listing eager-loads confirmations and their
confirmer (`selectinload`), no N+1.

## Migrations

Two revisions, chained linearly after `f4a9c2e1b3d5`; one head at every commit.

1. **users switches** — add `can_access_management` and `can_view_checklist_audit`,
   both server_default false: after deploy nobody but admins has either until an admin
   grants it.
2. **checklist tables** — create both tables and **seed the four current
   `db_dump_restore` terms** (positions 1–4, `created_by` null), so live behaves exactly as
   it does on `7356a1e` straight after `alembic upgrade head`:
   1. Close cronjobs / scheduled jobs
   2. Restart workers to apply the change
   3. Check .env for anything that can trigger emails
   4. After restoration, remove email settings from the settings table and the web UI (basevisu module)

   Downgrade drops both tables (confirmations first).

Before merging: revision ids checked free (`grep -rl`), `alembic heads` = one, upgrade and
downgrade both run on a throwaway database, live schema checked before deploy.

## Delivery — ordered commits on this branch

Each commit passes the full suite (`.venv/bin/python -m pytest -q`) on its own.

1. Management hub + `can_access_management` switch + nav change (migration 1 adds both
   user columns).
2. Checklist models + migration 2 with seed.
3. Checklists management page.
4. Start Deployment reads terms from the database; writes confirmations; hard-coded list
   removed; `tests/test_db_dump_start_checklist.py` rewritten against seeded rows.
5. Audit view on the row + "Checklist audit access" switch on the Users page.

Tests are written first and watched to fail, scoped to the row/dialog/section they assert
on, never the whole page.

## Out of scope

- Checklists at any stage other than Start.
- Moving Clients / Seeder Collection / Release Tracker under Management (D6).
- Drag-and-drop ordering; bulk import of terms.
- Recording that item 4 ("after restoration…") was actually done — the audit records the
  commitment at Start, not the later action.

## Risks

- **[MEDIUM]** Two new tables, two new user columns and a data seed on a live database.
  No existing column is altered; downgrade reverses each revision.
- **[LOW]** Replacing the Admin nav link with Management changes where admins click; the
  `/admin/users` URL is unchanged.
- **[LOW]** A stale pop-up after a term is added fails with 400 rather than starting —
  deliberate.
