# Release Tracker — Design

Status: approved by user in chat, pending written-spec review.
Author: Claude (session brainstorm with the user), 2026-08-27.

## Problem

There is currently no record, anywhere in this app, of what version is actually
running for a given client + system (test/live), what version ran before that, or
how far that trails the latest release on the `main` branch of the client-facing
frontend repo. `DeploymentRequest.version` only records what a *request* asked to
deploy — it's never updated once the deploy happens, and nothing computes
"previous" or compares against `main`.

This feature adds that: a new **Release Tracker** tab showing, per client + system,
the current deployed version, the version before that, the latest version on
`main` (with its most recent merged PR number) at the moment of that deploy, and
when it was recorded. The current/previous values are entered by DevOps through a
popup shown when they mark a Standard Deployment request as deployed; the
`main`-branch reference value comes from a small background sync against
Bitbucket, refreshed every 5 minutes — same shape as the existing CRM
`deployable-tasks` sync. Full automation (deriving current/previous without manual
entry) is an explicitly out-of-scope future step.

## Scope

**In scope:** Standard Deployment requests only (the only request type that
carries a `client_id` + `environment` pair meaningfully — `db_dump_restore` and
`test_local` requests don't fit this concept and are unaffected). One repo
(`shopfloor-suite`) is the only Bitbucket source; clients are not mapped to
separate repos — they're differentiated only by which `DeploymentRequest` they
came from, not by any Bitbucket branch lookup per client.

**Out of scope:** automatically deriving current/previous version without manual
entry (explicitly future work per the user); any per-client Bitbucket branch
lookups; changes to `db_dump_restore`/`test_local` deploy flows.

## Data model

### New table: `client_version_records`

Full history — one new row every time DevOps confirms a Standard Deployment
request as deployed. This is the Release Tracker tab's primary data source.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `client_id` | FK → `clients.id` | |
| `environment` | enum (`test`/`live`) | reuses `DeploymentEnvironment` |
| `current_version` | string, required | typed by DevOps in the popup |
| `previous_version` | string, nullable | auto-filled from this client+environment's most recent prior row's `current_version`; null if this is the first-ever record for that client+environment |
| `main_version` | string, nullable | snapshotted from `bitbucket_main_branch_status` at the moment this row is created |
| `main_pr_number` | int, nullable | snapshotted alongside `main_version` |
| `deployment_request_id` | FK → `deployment_requests.id` | which deploy this came from, for traceability |
| `recorded_by` | FK → `users.id` | who submitted the popup |
| `created_at` | timestamp | when originally recorded |
| `updated_at` | timestamp | bumped on correction (see Edit section) |

Never deleted, never overwritten in place by the sync — each deploy always
inserts a new row (this is the "full history" table the user explicitly asked
for, as opposed to a single-row-per-client+environment "latest state" table).

### New table: `bitbucket_main_branch_status`

A single-row cache (conceptually a singleton, like a settings row), overwritten
in place every sync cycle — this is *not* a history table.

| Column | Type | Notes |
|---|---|---|
| `id` | PK (always 1 row in practice) | |
| `version` | string | from `release.json`'s `"release"` field on `main` |
| `pr_number` | int | most recently merged PR into `main`, regardless of what it touched |
| `last_synced_at` | timestamp | |

## Bitbucket sync

Mirrors the existing CRM `deployable-tasks` sync pattern (`app/services/sync.py`,
`app/services/task_source.py`, `app/cli.py`) as closely as possible, for
consistency with how this codebase already does periodic external syncs.

- **New adapter**: `app/services/bitbucket_source.py` — a `BitbucketProvider`
  wrapping `httpx`, authenticated via `Authorization: Bearer {token}` (a
  Bitbucket Repository or Workspace Access Token — confirmed with the user, not
  an App Password, so no username is needed).
  - Fetch file content: `GET /2.0/repositories/{workspace}/{repo_slug}/src/{branch}/{path}`
    → parse the `"release"` field out of the JSON body.
  - Fetch latest merged PR into `main`: `GET /2.0/repositories/{workspace}/{repo_slug}/pullrequests?state=MERGED&q=destination.branch.name="main"&sort=-updated_on`
    → take the first result's PR `id`. (Confirmed with the user: "latest PR" means
    the most recently merged PR into `main` overall, not specifically the PR that
    touched `release.json`.)
- **New service function**: `sync_bitbucket_main_status(db, provider)` in
  `app/services/sync.py` — calls both adapter methods, upserts the single
  `bitbucket_main_branch_status` row (create if absent, else update in place).
- **New config** in `app/config.py` (`Settings`), following the existing
  `task_api_*` convention — read from `.env`, token never committed:
  - `bitbucket_api_token` (secret)
  - `bitbucket_workspace` (default `"SCT"`)
  - `bitbucket_repo_slug` (default `"shopfloor-suite"`)
  - `bitbucket_release_path` (default `"frontend-sap/src/assets/release.json"`)
  - `bitbucket_branch` (default `"main"`)
- **New CLI subcommand**: `python -m app.cli sync-bitbucket-main` in
  `app/cli.py`, calling `sync_bitbucket_main_status`. This is what an external
  cron entry runs every 5 minutes (documented in README.md's crontab section,
  same as the existing `deployable-tasks` line):
  ```
  */5 * * * * cd /path/to/Deployment_status && .venv/bin/python -m app.cli sync-bitbucket-main >> /var/log/bitbucket-main-sync.log 2>&1
  ```
- **No manual "Sync Now" button** — confirmed with the user, the 5-minute
  background job is enough; this is a pure background cache with no
  user-facing "I need this fresher right now" moment (unlike the CRM task sync,
  which backs an active data-entry form).

## Deploy-time popup

- **Trigger**: the existing "Mark Deployed" button in `request_list.html`
  (currently `<form method="post" action="/requests/{{ r.id }}/deploy">` with a
  bare submit button) — only for `standard`-type requests in `in_progress`
  status, which is already the only case the button renders for. `db_dump_restore`
  and `test_local` rows are unaffected: their "Mark Deployed" stays a bare submit,
  no popup, no version tracking.
- **New `<dialog>`** in `request_list.html`, following `base.html`'s existing
  dialog conventions (`showModal()`, close on backdrop click, matching visual
  style):
  - Read-only: Client name, System (test/live) — confirmation context.
  - Read-only: "Previous version" — the latest existing `client_version_records`
    row's `current_version` for this client+environment (or "—" if none exists
    yet). Confirmed with the user: DevOps does not retype this, only the system
    displays it.
  - Required text input: "Current version" — what DevOps types.
  - Submit button (e.g. "Confirm Deployment").
- **Endpoint change**: `POST /requests/{id}/deploy` (`deploy_request` in
  `app/routers/dashboard.py`) gains a new required form field `current_version`.
  Behavior, in order:
  1. Validate `current_version` is non-blank *before* touching any state — 400
     error if not (mirrors `create_request`'s validation pattern), confirmed
     required (not skippable) with the user. Existing pre-conditions (status
     must be `in_progress`) are still checked first, unchanged.
  2. Existing behavior unchanged: complete the `DeploymentExecution` row, set
     `deployment_request.status = completed`.
  3. Look up the latest prior `client_version_records` row for this
     `(client_id, environment)`, if any, for `previous_version`.
  4. Read the single `bitbucket_main_branch_status` row for `main_version`/
     `main_pr_number` (both null if that table is somehow still empty — e.g. the
     very first 5 minutes after this feature ships, before the first sync runs).
  5. Insert the new `client_version_records` row.

## Release Tracker tab

- **Nav**: new `<a href="/release-tracker">Release Tracker</a>` in
  `base.html`'s nav block, visible to every logged-in user — no role gating
  (confirmed with the user: same visibility as Dashboard/History/Requests).
- **Route**: `GET /release-tracker` — `Depends(require_login)`, queries
  `client_version_records` joined to `Client`, newest-first. Same
  Client/System filter-dropdown pattern already used on Dashboard/History
  (`_filter_context` in `app/routers/dashboard.py`). Landing router file
  (`dashboard.py` vs. a new `release_tracker.py`) decided at implementation
  time based on `dashboard.py`'s current size.
- **Template**: `release_tracker.html`, table styled like
  `request_list.html`/`dashboard.html`. Columns: Client, System, Current
  Version, Previous Version, Current Version at Main (rendered as e.g.
  "2026.34.34 (PR #1234)"), Updated At, Action (Edit).
- **Export**: `GET /release-tracker/export.xlsx`, reusing the existing
  `rows_to_xlsx` helper — confirmed with the user, matching Dashboard/History.

## Row correction (edit)

- **New permission function** `can_edit_client_version_record(current_user,
  record)` in `app/auth.py`: admin OR `current_user.id ==
  record.recorded_by`. No status-window restriction (there's no approval
  workflow on these rows — unlike `DeploymentRequest`, this is a plain
  correction, not a decision being reopened).
- **New routes**: `GET`/`POST /release-tracker/{id}/edit` — a small form
  editing **only** `current_version` (confirmed with the user:
  `previous_version`/`main_version`/`main_pr_number` stay as originally
  recorded, since they reflect what was true at that historical moment, not
  something to retroactively rewrite). Updates `updated_at` on save.
- **Edit button** on each row in `release_tracker.html`, gated by
  `can_edit_client_version_record`.

## Testing

Following this repo's existing pattern (`tests/test_dashboard.py`,
`tests/test_sync.py`, `tests/test_task_source.py`):

- `bitbucket_source.py`: unit tests against a mocked `httpx` transport (same
  style as `test_task_source.py`'s `_make_provider`/`httpx.MockTransport`
  pattern) — file-fetch parsing, PR-lookup parsing, auth header sent correctly.
- `sync_bitbucket_main_status`: upsert behavior (create then update in place,
  never a second row) — mirrors `test_sync.py`'s existing upsert tests.
- `deploy_request` (extended): current_version required/rejected-if-blank;
  previous_version correctly pulled from prior row; main_version/PR correctly
  snapshotted from the cache table; a brand-new client+environment gets
  `previous_version = None`.
- `can_edit_client_version_record`: admin can edit any row; `recorded_by` can
  edit their own; another non-admin user cannot.
- `/release-tracker` route: renders, filters work, only intended columns
  shown.
- `/release-tracker/{id}/edit`: happy path updates `current_version` and
  `updated_at`; permission-denied path leaves the row untouched.

## Open items requiring the user before implementation can fully complete

- **Bitbucket Repository/Workspace Access Token** — user has confirmed they'll
  provide one, scoped for read access to `SCT/shopfloor-suite` (repository
  content read + pull request read). Not needed to write the code, but needed
  before the sync can run against the real API instead of a mock in tests.
