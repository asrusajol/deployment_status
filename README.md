# Deployment Tracker

Internal tool for tracking developer deployment requests through approval and execution,
so nothing gets deployed without a Task ID and a recorded approval, and every request has
a full timestamp trail (submitted → approved → claimed → completed).

Full design and rationale: [project_plan.md](project_plan.md). Current status: basic
request/approval/deploy web UI and dashboard are up (Phase 1, partial Phase 2) — see
project_plan.md Section 11 for what's next.

## Prerequisites

- Python 3.10+
- Docker (for local Postgres)

## Quick start

```bash
# 1. Create a virtualenv and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Copy the env template and fill in anything you have (CRM API creds, etc.)
cp .env.example .env

# 3. Start local Postgres (matches the credentials in .env.example)
docker compose up -d db

# 4. Apply migrations
alembic upgrade head

# 5. Run the app
uvicorn app.main:app --reload
```

Check it's alive:

```bash
curl http://127.0.0.1:8000/health      # app is up
curl http://127.0.0.1:8000/health/db   # app can reach Postgres
```

## Using the web UI

With the app running, open **http://127.0.0.1:8000** (or `:8010` under Docker) in a
browser — it redirects to `/dashboard`, or `/login` if you're not signed in. **Every
route requires login** — there's no public/anonymous view of the dashboard.

### Logging in for the first time (bootstrapping the first admin)

Login access is opt-in per user and granted by an admin — but the very first admin has
to come from somewhere, so that one bootstrap step is a CLI command instead:

```bash
source .venv/bin/activate
python -m app.cli sync-users   # if you haven't already — this is who create-admin can promote
python -m app.cli create-admin ahamad --username ahamad
# prompts for a password (or pass --password, e.g. in a non-interactive script)
```

The `identifier` argument is an existing user's `username` or CRM `custom_id` — this
command never creates a new person, only grants admin access + a password to someone
already synced from the CRM. Log in with that username/password; you'll immediately be
forced to `/change-password` before you can do anything else (see below).

### Once logged in

- **`/dashboard`** — current deployment status per client + system (Test/Live): Task ID,
  git branch, commit hash, version, who approved and deployed it, and when it was
  requested/deployed. Always shows the most recently *deployed* (not just requested)
  state per client/system pair — one row per client+system, ever.
- **`/dashboard/history`** — the full audit trail behind the row above: every completed
  deployment, newest first, not deduped to one-per-client+system. Same columns and
  filters as `/dashboard`.
- Both dashboards share a filter bar (Client, System, Task ID substring search) and an
  **Export to Excel** button that downloads the currently-filtered rows as `.xlsx`
  (`app/services/export.py`, via `openpyxl`) — the export always matches whatever's on
  screen, since it's built from the same filtered query, not a separate "export
  everything" path.
- **`/requests/new`** — submit a deployment request:
  - **Task ID** — type-to-search over currently-PLANNED tasks from `deployable_tasks`
    (kept fresh by the `deployable-tasks` sync below), not free text; add one or more.
    Multiple orders can be combined into a single request — a single deployment often
    ships several orders at once — but only if they're all for the **same client** and
    the **same system** (Test/Live); a request has exactly one System field, so an order
    already synced as Test can't be combined with the same client's Live order (a real
    bug that came up: two `deployable_tasks` rows can share one Task ID but differ only
    by target). Picking a mismatched task is rejected client-side (and re-checked
    server-side in `create_request()`). The combined Task IDs are stored as one
    comma-joined string on the request (e.g. `"PR-03045, PR-03046"`). Adding the first
    task auto-fills Client and System from that task's own data, but both stay
    editable — the requester's own selection always wins at submit time, not the task's.
  - Client (dropdown of existing `clients`, or type a new name inline), System, git
    branch, commit hash, and version are all required. Changes description is optional.
  - **Requested by** is the logged-in user — not a field you fill in.
- **`/requests`** — the queue. Each request moves through:

  `Pending Team Lead Approval` → `Pending Deployment` → `Deployed` (or `Rejected` off the first step)

  - **Approve/Reject** — only an `admin`, or a `team_lead` who belongs to the same team
    as the *requester* (matched by `machine_group_id`) — not just any team lead anywhere
    in the CRM roster, and not necessarily the deploy team either. Each team's own lead
    signs off on their own team's requests. Enforced server-side
    (`can_approve_deployment_request()` in `app/auth.py`) — a direct POST from anyone
    else gets a 403, not just a hidden button. Writes an `Approval` row attributed to
    whoever's actually logged in.
  - **Mark Deployed** — only an `admin`, or a member of the deploy team (`machine_group_id`
    matches `TASK_API_DEPLOYABLE_MACHINE_GROUP_ID`, i.e. MG-00013 / "Team Rajib" today;
    any role — membership is what matters, not being a team lead), can deploy
    (`require_deploy_team_member()`). Writes a `DeploymentExecution` row attributed to
    the logged-in user, and is what makes the deployment show up on `/dashboard`.
  - These are two different axes, deliberately kept independent: approval follows the
    *requester's* own team lead (whoever that is, per request), while deploying is
    always gated on membership in the one fixed deploy team regardless of who requested
    it. Only the deploy check reuses `TASK_API_DEPLOYABLE_MACHINE_GROUP_ID`; approval
    doesn't reference that setting at all.
- **`/admin/users`** (admin only) — grant or reset a user's login access (sets/rehashes
  their password, forces `must_change_password` on next login), and change a user's role.
  This is also the only way `devops`/`admin` roles get assigned today — CRM sync only
  ever promotes `developer` → `team_lead` (see `sync_team_leads()` below), so before this
  page existed those two roles were permanently unreachable.
- **`/change-password`** — available any time from the nav, not just when forced.

This is a simplified one-step "mark deployed" flow rather than the full
claim → start → complete `DeploymentExecution` lifecycle described in project_plan.md
Section 5 — deliberately, to match what was actually asked for. Extending it to a real
claim/start/complete flow (so a request can be *in progress* between approval and
deployment, not just pending) is natural future work, not yet built.

Login itself is intentionally minimal for an internal tool: a signed session cookie
(Starlette's `SessionMiddleware`, keyed by `SESSION_SECRET_KEY` in `.env` — **must** be
overridden with a long random value outside local dev), bcrypt-hashed passwords, no
OAuth/SSO, no "remember me," no password-complexity rules beyond a length minimum.

## Running the whole stack in Docker

```bash
docker compose up -d --build
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/health/db
```

The `app` service uses `network_mode: host` — this is required so the container can
resolve `crm.test.local` (and anything else on the CRM's private network) through
whatever DNS server the host machine uses, instead of Docker's own isolated DNS.
Two consequences of that:

- No port remapping — the app binds directly to a host port (`8010` by default,
  override with `APP_PORT=1234 docker compose up`). Pick a free one if 8010 is taken.
- The app reaches Postgres via `localhost:5432`, not the `db` service name — host
  networking means it's no longer on the compose bridge network with `db`.

## Production deployment

This is the same `docker compose up -d --build` setup as local Docker above — there's no
separate prod-only compose file — but a few things need real values instead of the local
dev defaults before it's actually safe to expose to real users.

### 1. Get the code onto the server

```bash
git clone <this repo's URL> /opt/deployment-tracker
cd /opt/deployment-tracker
# Pin to a specific tag/commit rather than tracking a branch, so a deploy is a deliberate
# `git pull` + redeploy, not whatever happened to be on the branch tip:
git checkout v1.0.0
```

### 2. Configure `.env` with real secrets

```bash
cp .env.example .env
```

Then edit `.env` and set, at minimum:

- **`POSTGRES_PASSWORD`** — a real password, not `changeme`. This is the single source
  of truth for both the `db` service's own credentials and the `app` service's
  `DATABASE_URL` (see `docker-compose.yml`) — set it once here, nowhere else.
- **`SESSION_SECRET_KEY`** — signs the login session cookie; anyone who knows this value
  can forge a session. Generate one with:
  ```bash
  python3 -c "import secrets; print(secrets.token_hex(32))"
  ```
  There is no safe default for this in production — the value baked into `app/config.py`
  is deliberately obvious (`dev-only-insecure-secret-change-me`) so it's never mistaken
  for a real one.
- **`TASK_API_BASE_URL`**, **`TASK_API_USERNAME`**, **`TASK_API_PASSWORD`** — point these
  at the real CRM, not `crm.test.local` (the test/staging system). No code change is
  needed for this — it's the only thing that differs between environments.
- **`TASK_API_DEPLOYABLE_HALL_ID`** / **`TASK_API_DEPLOYABLE_MACHINE_GROUP_ID`** — only if
  this deployment is for a different team than the defaults (hall 5 / MG-00013).
- **`POSTGRES_PORT`** — only if 5432 is already taken on this host (common on a shared DB
  server). Change *only* this value, not the `db` service's `ports:` line directly — the
  app's `DATABASE_URL` is built from the same variable (`docker-compose.yml`), so the two
  can't drift apart. (This exact mismatch — `db` published to a non-default port while
  the app was still hardcoded to connect to 5432 — has actually happened and looks like a
  connection-refused crash loop on the `app` container with `db` itself perfectly healthy;
  if you see that, check `docker compose config` and confirm `DATABASE_URL`'s port matches
  what `db` actually publishes.)

`.env` is gitignored and — as of this cleanup — also excluded from the Docker build
context (`.dockerignore`), so it's never baked into an image layer; `docker-compose.yml`
passes it into the running container at deploy time instead (`env_file: .env`). Treat it
like any other production secret: readable only by whoever operates this server, never
committed, never pasted into a ticket/chat.

### 3. Put a reverse proxy in front of it, with TLS

The app itself only speaks plain HTTP on `APP_PORT` (`8010` by default) — it has no
built-in TLS. Login credentials and session cookies cross the network in the clear
without one. Put a reverse proxy in front (Caddy, nginx, Traefik — whatever this team
already runs elsewhere) terminating HTTPS and forwarding to `127.0.0.1:${APP_PORT}`, and
don't expose `${APP_PORT}` itself beyond localhost/the internal network. A minimal Caddy
example:

```
deploy-tracker.your-domain.internal {
  reverse_proxy 127.0.0.1:8010
}
```

### 4. Bring it up and bootstrap the first admin

```bash
docker compose up -d --build
curl http://127.0.0.1:8010/health      # app is up
curl http://127.0.0.1:8010/health/db   # app can reach Postgres

# One-time, from the host (or `docker compose exec app sh` then run without the prefix):
docker compose exec app python -m app.cli sync-users
docker compose exec app python -m app.cli sync-teams
docker compose exec app python -m app.cli create-admin <username> --username <username>
```

See "Logging in for the first time" above for what `create-admin` actually does.

### 5. Keep `deployable_tasks` current

The `deployable-tasks` sync (see "Listing currently-planned deploy operations" below) has
to run on a schedule for Task ID search to stay useful — it's not triggered by anything in
the web app itself. Add the cron job from that section on the host (or in a sidecar
container), pointed at this same `.env`.

### 6. Updating to a new release

```bash
cd /opt/deployment-tracker
git fetch --tags
git checkout v1.1.0            # or whatever the next tag is
docker compose up -d --build   # rebuilds the image; alembic upgrade head runs automatically
                                # on container start (see docker-compose.yml's command)
```

Migrations run automatically on every app container start — there's no separate manual
migration step, but it does mean a bad migration blocks startup rather than deploying
half-migrated; check `docker compose logs app` if the container doesn't come up after an
update.

### 7. Backups

Data lives in the `db_data` named volume. A simple logical backup (substitute your own
`POSTGRES_USER`/`POSTGRES_DB` from `.env` if you changed them from the defaults):

```bash
docker compose exec -T db pg_dump -U deploy_tracker deploy_tracker > "backup-$(date +%F).sql"
```

Restore into a fresh volume with `psql -U deploy_tracker -d deploy_tracker < backup-*.sql`
after `docker compose up -d db` on an empty `db_data` volume. Automate the backup command
above on a cron schedule and ship the output somewhere off this host — a lost volume with
no off-host copy is a full data loss, including every request/approval/deployment history
record.

### 8. Logs and monitoring

```bash
docker compose logs -f app   # follow the app's stdout/stderr
docker compose logs -f db    # Postgres logs
docker compose ps            # container status — restart: unless-stopped means a crashed
                              # container comes back on its own; ps shows if it's stuck restarting
```

There's no external log shipping or alerting wired up yet (project_plan.md Section 11,
Phase 4) — for now, monitoring this deployment means periodically checking `/health` and
`/health/db`, and `docker compose ps` for a container stuck in a restart loop.

## Running tests

```bash
source .venv/bin/activate
python -m pytest -q
```

Tests run against an isolated in-memory SQLite database (see `tests/conftest.py`) —
independent of whatever `DATABASE_URL` is set to, so they don't need Postgres running.

## Importing data from the CRM API

```bash
source .venv/bin/activate
python -m app.cli sync-users      # pulls employees (Machines), then matches+promotes team leads
python -m app.cli sync-teams      # pulls teams (MachineGroups) into the local Team table
python -m app.cli users-by-team   # prints every local user grouped by their team
```

Safe to re-run any time — all syncs upsert by the CRM's own id, so re-running never
creates duplicates, and a manually-assigned role (`devops`/`admin`) or email you've set
locally is never overwritten by a later sync.

`sync-users` does two things in one run: pulls the Machines roster, then cross-references
the CRM's `/odata/Users` feed — expanded with `userGroup` — by `custom_id` and promotes
any matched `developer` to `team_lead`, backfilling `email`/`username` at the same time
(neither is in the Machines feed). Team Lead status comes from membership in the CRM's
"Team Leads" userGroup (`custom_id` `UG-00002`), not `is_supervisor` — that flag is also
set for QA, HR, Ticketing, and Development Management supervisors who aren't team leads
(confirmed by inspecting live data). It only ever promotes, never demotes — leaving the
"Team Leads" group in the CRM doesn't downgrade anyone here. A team lead with no matching
Machines record (non-factory staff — admins, sales, etc.) is skipped rather than creating
a new user from that feed alone.

Each promoted team lead is also recorded as their own team's leader: `Team.leader_user_id`
is set by resolving the lead's own `machine_group_id` (already synced onto their `User`
row from the same Machines record) to a local `Team`. Run `sync-teams` before `sync-users`
if you want this populated on a fresh database — a lead whose team hasn't been synced yet
is skipped rather than left half-wired, same as the "no matching Machines record" case
above.

## Listing currently-planned deploy operations

```bash
source .venv/bin/activate
python -m app.cli deployable-tasks
```

Pulls `/get-orders`, scoped to this team's hall + machine group, and matches operations by
name (`"Deployment Test system"` / `"Deployment Live System"` — `pos` codes shift between
orders so they're not used for matching), keeping only rows whose own `status_plan` is
`PLANNED`. This is a flat list of currently-planned deploy operations — there's no
QA-gate/readiness signal here (the endpoint doesn't return the preceding QA operation at
all), so unlike the earlier design this doesn't flag which ones are actually unblocked.

This is meant to be **run frequently** (e.g. every 5 minutes) so the list stays current.
Schedule it with cron:

```cron
*/5 * * * * cd /path/to/Deployment_status && .venv/bin/python -m app.cli deployable-tasks >> /var/log/deployable-tasks.log 2>&1
```

This deliberately only *flags* what's planned — it doesn't auto-create a
`DeploymentRequest`. A developer/DevOps still submits the request explicitly, referencing
the order's `custom_id` as the Task ID, same as everywhere else in this tool.

## Project layout

```
app/
  main.py        FastAPI app + health endpoints; wires up SessionMiddleware, static
                 files, and all three routers; translates NotAuthenticatedError into a
                 redirect to /login or /change-password
  auth.py        Password hashing (bcrypt, not passlib — see requirements.txt) and the
                 require_login/require_admin/require_approver dependencies every
                 protected route uses
  cli.py         Operational commands: sync-users, sync-teams, users-by-team,
                 deployable-tasks, create-admin (bootstraps the first admin)
  config.py      Settings, loaded from .env
  database.py    SQLAlchemy engine/session setup
  models/        SQLAlchemy models (User, Team, Client, DeployableTask, DeploymentRequest,
                 Approval, DeploymentExecution, AuditLog — see project_plan.md Section 5)
  services/
    task_source.py   Adapter to the in-house CRM API (login, OData pagination,
                      take/skip pagination, list_users(), list_teams(), list_team_leads(),
                      list_deployable_tasks() — Section 6)
    sync.py           Upserts CRM data into local tables, incl. supervisor -> team_lead
                      promotion and deployable-task upserts
    reports.py        Read-only queries, e.g. users_by_team()
    dashboard.py      current_deployment_status() — the query behind /dashboard
  routers/
    auth.py      Login, logout, change-password
    admin.py     Admin-only: grant/reset a user's login access, change roles
    dashboard.py The rest of the web UI: dashboard, request form, approve/reject/deploy
  templates/       Jinja2 templates rendered by the routers above (server-rendered,
                   no separate frontend build)
  static/          style.css — plain CSS, no framework
  schemas/       Pydantic request/response models (empty — no JSON API yet, the web UI
                 uses HTML forms directly)
alembic/         Migrations
tests/           pytest suite
docker-compose.yml  Postgres + app container, local dev and production alike (see
                    "Production deployment" above)
.dockerignore    Keeps .env/.git/.venv/tests out of the built image — see "Production
                 deployment" above for why this matters
.env.example     Template for .env (gitignored) — real secrets never committed
.claude/skills/deployment-request-intake/  Claude Code skill for parsing raw
  deployment-request text into structured fields until the app's intake exists
```

## Working with the in-house CRM API

- Base URL, auth (`/login` → bearer token), and endpoints go in `.env` — never commit
  real credentials; `.env` is gitignored, `.env.example` is the template.
- Employees are modeled as `Machine` entities, and teams as `MachineGroup` entities, in
  the CRM's OData API — `list_users()`/`list_teams()` query `/odata/Machines` and
  `/odata/MachineGroups`, not `/Users`/`/Teams`. See `task_source.py` for why.
- There's also a *separate* `/odata/Users` entity (same people, different entity, matched
  by `custom_id`) — that one carries `email`/`username` and a nested `userGroup` array
  (via `$expand=userGroup`), which `list_team_leads()` filters client-side for membership
  in the "Team Leads" group (`custom_id` `UG-00002`) to identify team leads. Machines has
  neither field. `is_supervisor` was tried first but rejected — it's also set for QA, HR,
  Ticketing, and Development Management supervisors, not just team leads.
- Two separate path roots on the same host: auth/REST lives under `{base_url}` (e.g.
  `/api/login`), but OData endpoints live at the domain root, one level up
  (`/odata/...`, not `/api/odata/...`).
- A third pagination style: `/planvisu/orders/list` is plain REST under `{base_url}`
  (like `/login`), but uses `take`/`skip` query params — not OData's `$top`/`$skip`, and
  not any of the more common names (`page`, `offset`, `limit`) either. Confirmed by
  testing directly against the real endpoint; see `_rest_get_all()` in `task_source.py`.
  (No longer used by `list_deployable_tasks()`, which now uses `/get-orders` instead —
  see below — but kept in case another endpoint needs it.)
- A fourth pagination style: `/get-orders` (used by `list_deployable_tasks()`) is under
  `{base_url}` like `/login`, but takes OData-style `$top`/`$skip` params like the
  domain-root endpoints do — see `_rest_odata_get_all()` in `task_source.py`.
- `User.machine_group_id` is deliberately **not** a DB-level foreign key to `Team.id` —
  it's the CRM's raw id, and the CRM's own referential integrity (renamed/deactivated/
  deleted teams) is outside our control. `User.team` resolves it at the ORM level
  instead, so a dangling id just means `user.team is None`, never a broken sync.
  `Team.leader_user_id` is the opposite case: it references `users.id`, a locally-owned,
  app-generated primary key, so it *does* get a real DB-level foreign key.
- Still needed before Phase 3 is complete: the client-list and task-lookup endpoints
  (project_plan.md, Section 12).

## Status / what's implemented so far

- [x] Project skeleton, models, Alembic migrations (Phase 0)
- [x] Postgres wired up for local dev and matching production, incl. Docker host networking
      so the app can resolve the CRM's private DNS (`crm.test.local`)
- [x] CRM API auth (login → bearer token, auto re-auth on 401)
- [x] `list_users()` / `sync-users` via `/odata/Machines`
- [x] `list_teams()` / `sync-teams` via `/odata/MachineGroups`, plus `users-by-team` report
- [x] `list_team_leads()` via `/odata/Users` (`userGroup` membership in "Team Leads",
      `custom_id` `UG-00002`) — auto-promotes matched developers to `team_lead`,
      backfills email/username, and sets `Team.leader_user_id` for each lead's own team
- [x] `list_user_contacts()` via `/odata/Users` (unfiltered) — backfills email/username
      for every user, not just supervisors
- [x] `list_deployable_tasks()` / `deployable-tasks` via `/get-orders` — lists currently
      PLANNED "Deployment Test/Live system" operations (no QA-gate/readiness signal)
- [ ] `list_clients()`, `get_task()` (single-order lookup) — waiting on endpoint details
- [x] Request form, approval queue (Phase 1) — server-rendered web UI, no separate
      frontend build; see "Using the web UI" above
- [x] Live deployment-status dashboard per client/system (branch, commit, who/when),
      plus a full filterable `/dashboard/history` audit trail and an Excel (`.xlsx`)
      export of either view's current filter
- [x] Task ID sourced from `deployable_tasks` (type-to-search, constrained to a real
      task both client- and server-side — not free text), with support for combining
      multiple same-client orders into one request — see "Using the web UI" above
- [x] Login (session cookie, bcrypt password hashes), forced password change on first
      login, self-service change-password, admin-only user management (`/admin/users`)
      to grant/reset access and assign roles, `create-admin` CLI to bootstrap the first
      admin. Approvals and deploys are now attributed to the logged-in user automatically
      — no more picking a name from a dropdown.
- [x] Approve/Reject is scoped to the requester's own team lead
      (`can_approve_deployment_request()`), and Mark Deployed to deploy-team membership
      (`require_deploy_team_member()`) — both in `app/auth.py`, not just any team
      lead/user anywhere in the CRM roster
- [ ] Full claim → start → complete execution tracking (Phase 2) — currently a single
      "mark deployed" step instead of separate claim/start/complete states
- [ ] Daily User/Team/Client sync job (currently on-demand via CLI), notifications, SLA
      dashboard (Phase 3–4)
