# Order Task Dependency Report — Design

Status: approved by user in chat, pending written-spec review.
Author: Claude (session brainstorm with the user), 2026-09-09.

## Problem

A dev on the `shopfloor-suite` team built an "Order Task Dependency" report there
(branch `order-dependency-crm`): per production order, it walks each order
position's operations in sequence and flags which ones are blocked by an
unfinished predecessor, which are overdue against the position's due date, and
lets a user filter by machine group and date range, viewing results as a grid or
exporting a PDF. The user wants this same report reachable from
`deployment_status` instead, so DevOps/leads don't have to open the separate
shopfloor-suite (v12) frontend just to check it.

`deployment_status` has no local concept of production orders/operations — but it
already treats the shopfloor-suite instance (`crm.test.local` / `crm.schertech.com`)
as an external system it authenticates against and pulls data from
(`InHouseTaskSourceProvider` in `app/services/task_source.py`, used today for
`/get-orders` → `deployable_tasks`). That existing feed is a flat, name- and
status-filtered slice with no dependency/sequence data — rebuilding the
blocked/overdue logic from it in Python would duplicate business logic
shopfloor-suite already computes server-side.

Instead: shopfloor-suite's own `GET /report-visu/order-task-dependency` endpoint
(`ReportController::getOrderTaskDependencyReport`, added in the same branch)
already returns exactly the shape needed — orders → tasks, each carrying
`has_dependency`, `is_blocked`, `blocked_by`, `is_overdue`, `machine`,
`machine_group_id`/`machine_group_name`, `due_date` — pre-computed. It sits
behind plain `auth:sanctum` with no extra permission check, so the same
service-account bearer-token login `InHouseTaskSourceProvider` already performs
for `/get-orders` can call it directly. This design proxies that endpoint rather
than re-deriving its logic.

## Scope

**In scope:**
- A new **Reports** tab in `deployment_status`, structured to hold more than one
  report type over time (this is the first).
- One report under it: **Order Task Dependency**, live/on-demand (no local
  caching or history table — matches how the source report itself works: pick
  filters, view results).
- Filters: date range (start/end) and machine group — mirroring the
  shopfloor-suite endpoint's own query params (`start`, `end`, `machineGroups`)
  and defaults (no `end` → today; no `start` → one month before `end`).
- An "Overdue only" filter, replicated client-side (in the new Python route) over
  the fetched JSON — mirrors what shopfloor-suite's PDF export does
  (`filterOverdueEligible`), since the JSON endpoint itself doesn't filter it.
- Excel export of the currently-filtered view, following the existing
  `_columns_to_xlsx` pattern.
- Access restricted to `admin`, `devops`, and `team_lead` roles only.

**Out of scope:**
- Any local persistence/history of report results.
- Modifying shopfloor-suite's backend — the endpoint this relies on already
  exists on the `order-dependency-crm` branch and needs no changes.
- Any report type other than Order Task Dependency (the Reports tab is just
  structured to not block adding more later).

## Reports tab structure

To avoid every future report needing its own top-level nav entry:

- New nav item **`<a href="/reports">Reports</a>`** in `base.html`, gated the
  same way as the rest of the new surface (see Access control) — placed near
  `Release Tracker`.
- **`GET /reports`** — a landing page listing available report types (just
  "Order Task Dependency" today) as simple links/cards, driven by a small
  in-code registry so adding a report later means adding one entry + one router
  module, not touching this one.
- Each report type gets its own sub-route: **`GET /reports/order-task-dependency`**
  (HTML view) and **`GET /reports/order-task-dependency/export.xlsx`**.

File layout:
```
app/routers/reports.py                      # GET /reports (landing + registry)
app/routers/reports_order_task_dependency.py  # GET /reports/order-task-dependency (+ export.xlsx)
app/templates/reports_index.html
app/templates/reports/order_task_dependency.html
```
(exact module/template names may be adjusted at implementation time to match
whatever `dashboard.py`'s current size/conventions suggest.)

## New adapter method

`app/services/task_source.py`, `InHouseTaskSourceProvider`:

```python
def get_order_task_dependency_report(
    self, start: str | None, end: str | None, machine_groups: list[int] | None
) -> list[dict]:
    ...
```

- Reuses the existing `_login()` / `_request()` bearer-auth helpers — same host
  (`settings.task_api_base_url`), same token/401-retry behavior.
- Calls `GET /report-visu/order-task-dependency` with query params
  `start`, `end`, `machineGroups` (comma-joined ids) exactly as shopfloor-suite's
  `OrderTaskDependencyFilter::tryMake()` expects. This is a plain custom-REST
  call (not OData, not `$top`/`$skip` paginated) — a single direct `_request()`
  call, no pagination helper needed.
- Returns the parsed JSON array of orders as-is (list of dicts) — no new
  dataclass wrapping needed at the adapter layer; the router maps fields
  directly into the export/template row shape.
- On a non-2xx response (e.g. a 422 for a malformed date range, or a network
  failure), let `httpx`'s `raise_for_status()` propagate — the router catches it
  (see Error handling).

Not added to the `TaskSourceProvider` Protocol as a required method for every
implementation for now — follow whatever the Protocol's current convention is at
implementation time (the existing Protocol already lists all adapter methods,
so this should be added there too for consistency, with a test double updated to
match).

## Machine group filter options

The machine-group dropdown reuses the already-synced `teams` table (mirrors CRM
`MachineGroups`, kept fresh by the existing team sync job) rather than making a
second CRM call just to populate a dropdown.

## Route behavior

`app/routers/reports_order_task_dependency.py`:

- `GET /reports/order-task-dependency` — query params `start`, `end`,
  `machine_group_id` (repeatable or comma-list, matching this app's existing
  filter param conventions), `overdue_only` (bool). Calls the new adapter
  method, applies the overdue filter in-memory if requested, renders the
  template with orders/tasks flattened for display (one row per task, grouped
  visually by order).
- `GET /reports/order-task-dependency/export.xlsx` — same filter parsing and
  fetch, reusing `_columns_to_xlsx` from `app/services/export.py` with a new
  column spec: Order, Item, Task/Operation, Machine, Machine Group, Status, Due
  Date, Blocked By, Overdue — one row per task (flat), not per order.
- Both routes share one filter-parsing helper, same principle as
  `_parse_filters`/`_filter_context` in `dashboard.py`, so the HTML view and the
  export can never disagree on what's "currently filtered."

## Error handling

If the adapter call fails (CRM unreachable, non-2xx, or a malformed date range
rejected by shopfloor-suite's own 422), the route catches it and renders the
report page with an inline error banner instead of a 500 — consistent with how
this app already tolerates CRM sync failures elsewhere (a broken CRM call
degrades gracefully, it doesn't crash the page).

## Access control

`app/auth.py` gains a new guard:

```python
def require_reports_access(current_user: User = Depends(require_login)) -> User:
    if current_user.role not in (UserRole.admin, UserRole.devops, UserRole.team_lead):
        raise HTTPException(status_code=403, detail="Reports access requires admin, devops, or team lead")
    return current_user
```

modeled directly on the existing `require_deploy_team_member`/admin-or-devops
patterns already in that file. This is a flat role check — unlike
`can_approve_deployment_request`, it is **not** scoped by team/`machine_group_id`;
any user with one of these three roles sees all reports.

Applied as a `Depends(require_reports_access)` on every route under `/reports*`
(landing page, the order-task-dependency page, and its export). The nav link in
`base.html` is also conditioned on the same role check (`developer` accounts
don't see the "Reports" link at all, matching how `Admin` is already hidden from
non-admins there).

## Testing

Following this repo's existing pattern (`tests/test_task_source.py`,
`tests/test_dashboard.py`, `tests/test_export.py`):

- `get_order_task_dependency_report`: unit test against a mocked `httpx`
  transport (same `_make_provider`/`httpx.MockTransport` style as
  `test_task_source.py`) — correct query params sent, response parsed, 401
  triggers one re-login-and-retry, a non-2xx propagates as an exception.
- `require_reports_access`: admin/devops/team_lead pass; `developer` gets 403.
- `/reports` route: renders the landing page listing the one report; hidden/403
  for a `developer` role.
- `/reports/order-task-dependency`: renders with mocked adapter data; filters
  (date range, machine group, overdue-only) passed through correctly; an
  adapter exception renders the error banner instead of a 500.
- `/reports/order-task-dependency/export.xlsx`: same filtered row set as the
  HTML view produces a workbook with the expected columns (mirrors
  `test_export.py`'s existing xlsx-content assertions).

## Open items requiring confirmation before implementation can fully complete

- None blocking — the shopfloor-suite endpoint this relies on already exists on
  `order-dependency-crm` and needs no changes; the service-account credentials
  `InHouseTaskSourceProvider` already uses for `/get-orders` should already have
  whatever access this endpoint needs, since the route carries no extra
  permission middleware. Worth a quick sanity check against the real
  `crm.test.local` instance once code is running locally, to confirm the
  service account isn't blocked by something outside `routes/api.php` (e.g. a
  frontend-only guard that doesn't apply here anyway).
