# Deployment Request & Tracking Tool — Project Plan

## 1. Problem Statement

Today, deployment requests arrive from developers as free-form text (chat/email), which causes:

- **No reliable Task ID** — requests sometimes omit it, so deployments can't be traced back to a ticket.
- **No approval gate** — deployments start without a team lead's sign-off being recorded anywhere.
- **Wrong-request mix-ups** — a DevOps engineer sometimes executes person A's request while it gets logged/attributed as person B's.
- **No time tracking** — there's no record of how long a request sat waiting for approval, or how long the deployment itself took.

## 2. Goals

1. Every deployment request must have a **Task ID**, **Client**, and **Developer** — pulled from your in-house system via API, not typed free-hand.
2. No deployment can start without an **explicit approval** from a team lead, recorded with who/when.
3. Every request is **locked to one executor** once claimed, eliminating "deployed under someone else's request."
4. Full **timestamp trail** per request: submitted → approved → started → completed, so lead time and deployment duration are always measurable.
5. Single dashboard/queue replacing scattered chat messages.

## 3. Users & Roles

| Role | Can do |
|---|---|
| **Developer (Requester)** | Create a request, attach Task ID (auto-filled from API), track its status |
| **Team Lead (Approver)** | Approve/reject requests, see pending queue, view team metrics — **implemented, scoped**: only a team lead who *belongs to the requester's own team* (matched by `machine_group_id`) can approve/reject that request, not any team lead in the CRM roster and not necessarily the deploy team |
| **DevOps Engineer (Executor)** | Claim an approved request, mark start/complete/fail, cannot claim a request already claimed by someone else — **implemented differently**: any member of the deploy team can mark a request deployed (role doesn't matter, membership does), not specifically the `devops` role |
| **Admin** | Manage users/clients, view all data, export reports — **implemented**: grant/reset any user's login access and change roles at `/admin/users`, the only way `devops`/`admin` get assigned today; also bypasses both the approval and deploy-team scoping below |

Login is opt-in per user, not automatic for everyone the CRM sync brings in — an admin has to explicitly grant it (or reset a forgotten password) via `/admin/users`. The very first admin is bootstrapped with `python -m app.cli create-admin`, since granting admin access through the app requires an admin to already be logged in. Whenever a password is (re)set by an admin — or by that bootstrap command — the user is forced to choose their own on next login (`must_change_password`); after that, they can also change it any time from the nav.

**Approval and deploy-team scoping** (confirmed by the user, and corrected after an initial mis-implementation conflated the two): these are two independent axes. Approval follows the *requester's own team* — a `team_lead` may approve/reject a request only if they share the requester's `machine_group_id`, whichever team that happens to be per request (`can_approve_deployment_request()` in `app/auth.py`). Deploying is unrelated to who requested it: only members of MG-00013 ("Team Rajib" — the same `task_api_deployable_machine_group_id` the `deployable-tasks` sync is already scoped to, Section 6a) may mark a request deployed, regardless of role (`require_deploy_team_member()`). Only the deploy check is a single global setting; approval is resolved per-request from the requester's own team and needs no config value, so it already generalizes to multiple teams without further changes.

## 4. Core Workflow (state machine)

```
Draft → Submitted → Pending Approval → Approved ──▶ Claimed ──▶ In Progress ──▶ Completed
                              │                                                   ▲
                              └──▶ Rejected                                       │
                                                                       In Progress ──▶ Failed / Rolled Back
```

Every transition writes a timestamp + actor. This is what makes time-tracking automatic instead of manual.

**Implemented so far (web UI)**: `Pending Approval → Approved → In Progress → Completed` (labeled "Pending Team Lead Approval" / "Pending Deployment" / "In Progress" / "Deployed" in the UI), or `Pending Approval → Rejected`. `In Progress` was added so the requester can see that a deploy-team member has actually picked up the request, not just that it's approved and sitting in the queue — "Start Deployment" (`app/routers/dashboard.py`'s `start_request()`) moves `Approved → In Progress` and writes the `DeploymentExecution` row's `claimed_at`/`started_at`; "Mark Deployed" (`deploy_request()`) then only accepts `In Progress` and fills in `completed_at`. Not restricted to whoever started it — any deploy-team member may mark it deployed, same membership-not-personal-lock model as the rest of this router. The bare `Claimed` state still isn't surfaced separately (starting sets both `claimed_at` and `started_at` together) — that's the one remaining gap from the full state machine above, not a redesign.

## 5. Data Model (draft)

- **User**(id, name, email, username, role, source_system_id, machine_group_id, last_synced_at, password_hash, must_change_password) — `password_hash` is NULL until an admin grants login access (Section 3); no separate "accounts" table, login rides directly on the same CRM-mirrored roster used everywhere else.
- **Team**(id, source_system_id, name, leader_user_id, last_synced_at) — mirrors the CRM's MachineGroups; `id` *is* the CRM's own MachineGroups id (see Section 6). `leader_user_id` is a real FK to `User.id`, set by `sync_team_leads()` for the user in the CRM's "Team Leads" userGroup whose own team this is.
- **Client**(id, name, source_system_id, last_synced_at)
- **DeploymentRequest**(id, request_type, task_id, client_id, server, environment, git_branch, commit_hash, version, changes_description, dump_source, restore_source, share_with_requestor, requested_by, deadline_at, raw_text, status, created_at) — `environment` (`test`/`live`) is the clean dropdown-driven field the web UI and dashboard key off; `server` is left as-is for the older intake-skill's freeform "CRM Live"-style text (also reused by `test_local` requests, see below, to hold the target `*.test.local` host). `request_type` (`standard` / `db_dump_restore` / `test_local`) picks which of the three "New Deployment Request" tabs a row came from and which fields are populated — see Section 4a.

### 4a. No-approval-required request types

Two request types skip the team-lead approval gate (Section 3/4) entirely by design, confirmed by the user — everything else about the state machine (deploy-team "Mark Deployed" step, timestamps, audit trail) still applies, so who executed them and when stays tracked:

- **`db_dump_restore`** — a database dump/restore with no CRM task, client, or environment attached. Fields: `dump_source` (required), and exactly one of `restore_source` or `share_with_requestor` (mutually exclusive — either the dump gets restored somewhere else, or it's just handed back to the requester).
- **`test_local`** — a deploy to one of the internal `*.test.local` boxes (e.g. `crm.test.local`, `tmp.test.local`, `vop.test.local` — not an exhaustive list) rather than a real client system. Fields: `server` (any `*.test.local` host — free text with suggestions, not a fixed list, since more can be added later), `git_branch`, and an optional additional-instruction note (reuses `changes_description`).

Both are created directly in `RequestStatus.approved` (skipping `pending_approval`), so they land straight in the deploy team's "Pending Deployment" queue alongside normally-approved requests. Neither sets `client_id`/`environment`, so — same as any other request missing those — they're automatically excluded from the per-client "Current Deployment Status" dashboard (Section 8), which only tracks completed `standard` deployments; they still show up in the `/requests` queue and history.
- **Approval**(id, request_id, approver_id, decision, decided_at, comment)
- **DeploymentExecution**(id, request_id, executed_by, claimed_at, started_at, completed_at, status, notes)
- **AuditLog**(id, request_id, actor_id, action, timestamp)

`User` and `Client` are local mirrors of what the in-house API returns, not hand-entered — see Section 6 for how they stay in sync.

`DeploymentExecution.request_id` is unique-per-active-execution — this is the constraint that prevents the "wrong request" problem: one request can only be actively claimed by one executor at a time.

## 6. Integration with Your In-House Task System

Since the task tracker is custom/in-house, build against a small adapter interface rather than hardcoding the API:

```python
class TaskSourceProvider(Protocol):
    def get_task(self, task_id: str) -> TaskInfo: ...            # single task -> client, developer, title
    def search_tasks(self, query: str) -> list[TaskInfo]: ...
    def list_users(self) -> list[UserInfo]: ...                  # full developer/user roster
    def list_clients(self) -> list[ClientInfo]: ...              # full client roster
```

Today's implementation calls your in-house API; if the tracker ever changes, only the adapter changes — nothing else in the app does.

There are two different integration patterns here, driven by different needs — don't conflate them:

- **On-demand, per-request:** when a developer enters a Task ID on a new request, the app calls `get_task()` synchronously to auto-fill client + developer + title right then. This has to be fast and fresh.
- **Daily bulk sync, User + Client rosters:** a scheduled job (APScheduler, once per day — e.g. 06:00 before the workday starts) calls `list_users()` and `list_clients()` and upserts the local `User`/`Client` tables, stamping `last_synced_at`. This keeps dropdowns, approver lists, and reporting fast (no API round-trip per page load) without going stale for more than a day. If your in-house API supports webhooks or a "changed since" filter later, this can move from a full daily pull to incremental — not needed for MVP.

**Needed from you before Phase 3 is fully complete:** sample JSON responses for a client list and a single-order lookup by Task ID (`get_task()`). Base URL, auth, user list, team list, and the deployment-readiness feed below are already confirmed and implemented.

### 6a. Deployable tasks (`/get-orders`)

Source switched from `/planvisu/orders/list` to `/get-orders`, scoped to this team's hall +
machine group (`halls`/`machineGroups` params — hall 5 / machine group 13, "Team Rajib",
confirmed by the user, configurable via `task_api_deployable_hall_id`/
`task_api_deployable_machine_group_id`). This endpoint returns a **flat list of
operations** (no nested order → position → operations structure), already filtered
server-side to just deploy-named operations via a `name=deployment` param — confirmed:
every row's own `name` is `"Deployment Test system"` or `"Deployment Live System"`, never
anything else.

Deploy operations are matched by operation **name**, not by `pos` — **confirmed by the
user: `pos` codes are not stable across orders** (the same operation can show up as a
different `pos` number depending on the order). Only rows whose own `status_plan` is
`PLANNED` are imported; anything else is skipped entirely. `pos` is still stored
(`pos_id`) for reference, but purely informational.

**No gate/readiness concept anymore.** The earlier design tracked a `gate_status` (the
preceding QA operation's status) to compute an `is_ready` "ready to deploy" flag. This
endpoint's flat, pre-filtered response doesn't include the preceding QA operation at all,
so that can't be computed from it — confirmed with the user after the endpoint switch,
who chose to drop `gate_status`/`is_ready` entirely rather than make a second call to
recover it. `list_deployable_tasks()` in `task_source.py` is now just a flat "currently
planned deploy operations" list, alongside the assigned developer
(`operation.machine.custom_id` — the same `custom_id` as the Machines/Users feeds, so it
resolves to an existing `User` directly).

**Important:** the order's `custom_id` (Task ID, e.g. `PR-03045`) is **not guaranteed
unique across orders** — confirmed by the user; two distinct orders can share the same
number. This is why `DeployableTask` is keyed by `operation_id` (the CRM's own operation
id — always unique) and separately stores `order_id` (the CRM's own order id) alongside
`task_id`, so two same-numbered orders never collide and can still be told apart by a
human. Anywhere else in this system that accepts a Task ID from a developer needs the
same caution: don't assume `task_id` uniquely identifies a request.

This feeds a **flag-only** design, not auto-creation: `deployable-tasks` (run every ~5
minutes via cron) surfaces the currently-planned deploy operations, but a developer still
explicitly submits the request referencing the Task ID — this is a discovery aid, not a
bypass of the approval flow. Since this feed no longer carries QA-gate status (Section
6a), any deployment-target readiness check still needed at submission time (Phase 1/2)
will need its own lookup against the CRM rather than relying on this import.

## 7. Preventing "Deployed Under the Wrong Request"

- **Task ID: done as designed, and extended beyond the original one-task-per-request scope.** Never free-typed — searched and added from currently-PLANNED `deployable_tasks` (Section 6a), each resolved server-side to the CRM's own operation id, not the (non-unique) task_id string. A request can now combine *multiple* orders (confirmed by the user: "sometimes multiple order deployed in a single deployment") as long as they're all for the same client AND the same target (test/live) — enforced both client-side (`request_form.html`'s JS) and server-side (`create_request()` in `app/routers/dashboard.py`); the combined task_ids are stored as one comma-joined string on `DeploymentRequest.task_id` (e.g. `"PR-03045, PR-03046"`) rather than a separate join table, since there's still no FK from `DeploymentRequest` to `DeployableTask` to preserve. The same-target rule was added after a reported bug: two `deployable_tasks` rows can share one Task ID but differ only by `target` (e.g. a Test and a Live order both named "PR-02960"), and the initial same-client-only check let both get combined into one request even though a request has exactly one `environment` — silently deploying one of the two orders under the wrong system. **Requester: done, differently than originally planned** — not resolved from the Task ID, but from who's logged in (Section 3), which is at least as strong a guarantee. **Client: partially done** — auto-fills from the first selected task's own `client_name` as a convenience default, but stays a free-editable dropdown (with inline "add new client") rather than being locked/resolved — a developer can still override it to something unrelated to the Task ID(s) they picked.
- Deployment-target validation (Phase 1/2, not yet implemented): whether a submitted request is actually allowed to proceed to Test/Live needs its own check against the CRM at submit time — `deployable-tasks` (Section 6a) no longer carries the QA-gate status needed to answer this, only which deploy operations are currently planned.
- The DevOps execution queue only shows **Approved** requests not yet claimed.
- Claiming a request locks it (`claimed_by`, `claimed_at`) — it disappears from other executors' queues immediately.
- Optional guard: reject a new request if the same `task_id` + `version` is already Approved/In Progress (duplicate submission). **Caveat (confirmed, not hypothetical):** `task_id` alone can't be trusted as unique — two distinct orders can share the same number (Section 6a) — so this guard needs the CRM's own order id in the comparison too, not `task_id` + `version` alone.

## 8. Time Tracking & Reporting

Dashboard metrics, all derived from the timestamp trail — no manual entry:

- Time to approve (submitted → approved)
- Time to deploy (approved → completed)
- Total lead time (submitted → completed)
- SLA breaches: requests where `completed_at` > requester's stated deadline
- Per-client, per-developer, per-DevOps-engineer breakdowns
- Weekly/monthly export (CSV) for management reporting

## 9. Notifications

Recommended minimum viable channel: **email**. Add Slack/Teams webhook later if desired.

- Request submitted → notify approver
- Approved/Rejected → notify requester
- Deadline approaching / breached → notify DevOps queue + team lead
- Completed → notify requester + approver

## 10. Recommended Tech Stack

Chosen for low maintenance overhead for a small internal DevOps team:

| Layer | Choice | Why |
|---|---|---|
| Backend | **FastAPI** (Python) | Async, auto-generated API docs, easy adapter pattern for the in-house API |
| DB | **PostgreSQL** (SQLite for local dev) | Reliable, easy timestamp/interval queries for metrics |
| ORM/Migrations | **SQLAlchemy + Alembic** | Standard, well supported |
| Auth | Session-based login; RBAC via `role` field; swap in company SSO later if available | Fastest to ship; upgradeable |
| Frontend (MVP) | **Jinja2 + HTMX + Bootstrap** | Server-rendered, no separate SPA build — fastest path to a usable internal tool |
| Background jobs | **APScheduler** | SLA/deadline reminders, periodic API syncs |
| Notifications | SMTP email first; Slack/Teams webhook optional | Minimal setup |
| Deployment | Docker Compose on an internal VM | Matches "no cloud dependency" for internal tools |
| Testing | **pytest** | Standard |

## 11. Milestones

- **Phase 0 — Setup (now):** finalize this plan, confirm notification channel, scaffold repo (`app/`, `models/`, `routers/`, `tests/`), set up Postgres + Alembic.
- **Phase 1 — MVP (core loop): done.** Server-rendered web UI (`app/routers/dashboard.py` + `app/templates/`) — request form with Task ID sourced from `deployable_tasks` (not free text, not yet `get_task()`), team-lead approval queue, request list, DB persistence, plus a live per-client/system deployment-status dashboard (git branch/commit/version/who-deployed) and a full `/dashboard/history` audit trail of every completed deployment. Both are filterable by Client/System/Task ID and exportable to `.xlsx` (`app/services/export.py`). *This kills the "no Task ID" and "no approval record" problems, and adds "what's actually running where" plus "what ran historically," which weren't in the original scope but were requested alongside it.*
- **Login/auth: done**, ahead of where it sat in the original phase plan (added because the identity work in Phase 2 needed it to mean anything). Session-cookie login, bcrypt hashes, forced password change on first login, self-service change after that, admin-granted access (`/admin/users`) rather than self-registration, `create-admin` CLI to bootstrap the first admin. Approve/Reject/Mark-Deployed now attribute to the logged-in user (with a server-side role check on Approve/Reject), not a name picked from a dropdown.
- **Phase 2 — Execution tracking: mostly done.** "Start Deployment" and "Mark Deployed" are now two separate actions (`start_request()`/`deploy_request()` in `app/routers/dashboard.py`), writing `claimed_at`/`started_at` and `completed_at` on the same `DeploymentExecution` row at each step — so the requester sees `In Progress` the moment a deploy-team member picks the request up, not just at final completion. Still missing: separate fail/roll-back buttons, and an enforced one-executor-per-request *claim* lock (today any deploy-team member may complete a request someone else started — a deliberate choice, "membership not a personal lock," not an oversight). Both actions are scoped to deploy-team membership rather than open to any logged-in user (Section 3) — by team membership, not the `devops` role specifically, since role assignment and team membership turned out to be the more natural fit for how this team actually wanted it gated.
- **Phase 3 — API integration:** wire `TaskSourceProvider` to the real in-house API — per-request `get_task()` auto-fill/validation, plus the daily `list_users()`/`list_clients()` sync job; add duplicate-submission guard. `list_users()`, `list_teams()`, `list_team_leads()`, `list_user_contacts()`, and `list_deployable_tasks()` are done — `get_task()` and `list_clients()` remain.
- **Phase 4 — Reporting & notifications:** SLA dashboard, per-person/per-client stats, email notifications, CSV export.

## 12. Open Inputs Needed From You

- In-house task API: sample response payload for a single-order lookup by Task ID (`get_task()`), and a client-list endpoint. Base URL, auth, users, teams, and the deployment-readiness feed are confirmed and implemented (Section 6a).
- Whether the API can filter by "changed since" a given time (enables incremental sync later) — not required for MVP, full daily pull is fine to start.
- Confirm notification channel (email is the default assumption).
- Any existing login/SSO system to integrate with, or is a simple username/password fine for an internal tool?

## 13. Risks

- **Confirmed** (was hypothetical): the Task ID (order `custom_id`) is not unique — two distinct orders can share the same number. Duplicate-detection (Section 7) and any future `get_task(task_id)` lookup must key off the CRM's own order id, not `task_id` alone. `DeployableTask` already does this (Section 6a); apply the same pattern wherever a Task ID is accepted from a developer.
- Adoption risk: developers must stop requesting via chat once the tool is live — needs a short cutover announcement, not just tooling.
