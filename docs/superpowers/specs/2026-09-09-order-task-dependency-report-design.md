# Order Task Dependency Report — Design

Status: revised after the live-availability constraint surfaced; approved in
chat, pending written-spec review.
Author: Claude (session brainstorm with the user), 2026-09-09, revised 2026-09-10.

## Problem

A dev on the `shopfloor-suite` team built an "Order Task Dependency" report
there (branch `order-dependency-crm`): per production order, it walks each order
position's operations in sequence, flags which ones are blocked by an unfinished
predecessor and which are overdue against the position's due date, filters by
machine group and date range, and exports a PDF. The user wants that same report
reachable from `deployment_status`, so DevOps/leads don't have to open the
separate shopfloor-suite (v12) frontend just to check it — and they need a PDF
for submission.

`deployment_status` has no local concept of production orders/operations, but it
already authenticates against the shopfloor-suite instance (`crm.test.local` /
`crm.schertech.com`, referred to throughout this app as "the CRM") and pulls data
from it via `InHouseTaskSourceProvider` (`app/services/task_source.py`).

### The constraint that shapes this design

The obvious approach — proxy shopfloor-suite's own
`GET /report-visu/order-task-dependency` endpoint, which already returns
`is_blocked`/`blocked_by`/`is_overdue` pre-computed — **is not viable**. That
endpoint lives only on the `order-dependency-crm` feature branch, and the user
has confirmed that backend work will **permanently never ship to the live
system**. Verified directly against the live branch:

| Checked on `crm-master` (the live branch) | Result |
|---|---|
| `backend/routes/api.php` → `order-task-dependency` routes | **absent** |
| `backend/app/Services/Reports/OrderTaskDependencyReportService.php` | **absent** |
| `Lodata::discover(ProdOrder / ProdOrderPos / ProdOrderPosOperation / Machine / MachineGroup)` | **all present** |
| Operation fields `pos`, `name`, `start`, `end`, `status`, `machine_id`, `prod_order_pos_id` | **all present** |
| Position fields `due_date`, `name`, `prod_order_id` | **all present** |
| `ProdOrderPosOperationStatus` enum incl. `CLOSED`, `DELETED` | **present** |

So the report's *pre-computed output* is unavailable on live, but every piece of
*raw data* it is derived from is already exposed on live through generic OData
entity sets that predate the feature branch and are not going anywhere.

**Therefore: deployment_status reads the raw OData entity sets and computes the
dependency/overdue logic itself, in Python.** Nothing in this design depends on
`order-dependency-crm` merging, and the report behaves identically against
`crm.test.local` and `crm.schertech.com`.

### Note on the OData-vs-REST house rule

`shopfloor-suite`'s own CLAUDE.md tells its developers to build **new endpoints
as custom REST, never OData**. That rule governs writing new code *inside*
shopfloor-suite. It does not apply here: this design adds nothing to
shopfloor-suite, and OData is the only interface that carries this data on the
live system. This is a deliberate, constraint-driven choice, not an oversight —
please don't "fix" it later by pointing this at a REST endpoint that only exists
on a branch.

## Scope

**In scope:**
- A new **Reports** tab, structured to hold more than one report type over time
  (this is the first).
- One report under it: **Order Task Dependency** — live/on-demand, no local
  caching or history table (matching how the source report works: pick filters,
  view results).
- Filters: date range (start/end), machine group, and "overdue only".
- Three outputs from one filtered row set: HTML view, Excel export, PDF export.
- Access restricted to `admin`, `devops`, and `team_lead`.

**Out of scope:**
- Any local persistence/history of report results.
- Any change to `shopfloor-suite` (this design requires none, on any branch).
- Any report type other than Order Task Dependency.

## Data sourcing (OData)

New methods on `InHouseTaskSourceProvider` (`app/services/task_source.py`),
reusing the existing `_login()`/`_request()`/`_odata_get_all()` helpers — same
host, same bearer token, same 401-retry, same `$top`/`$skip` pagination the
`Machines`/`Users`/`MachineGroups` calls already use. OData lives at the domain
root (`_odata_base_url`), not under `/api` — `_odata_get_all()` already handles
that distinction.

### What this OData surface actually supports (probed against `crm.test.local`, 2026-09-10)

Confirmed empirically, not assumed. **Two of these are correctness traps** —
read this section before changing any query.

| Capability | Result |
|---|---|
| Service account can read `ProdOrders`, `ProdOrderPos`, `ProdOrderPosOperations`, `Machines`, `MachineGroups` | ✅ works |
| `$filter` on the entity's **own** fields (`start lt …`, `status ne 'DELETED'`) | ✅ works, genuinely filters (verified: `start ge 2099-01-01` → 0 rows) |
| `$select`, `$orderby`, `$top`/`$skip` paging | ✅ all work |
| `in (…)` operator (`machine_id in (1,2,3)`) | ✅ works, lists of 500+ ids fine |
| `$expand`, including nested (`prodOrderPos($expand=prodOrder)`) | ✅ works |
| `$count=true` | ❌ **not supported** — returns no `@odata.count`; page until a short page |
| `$filter` across a nav property to a *different* field name (`prodOrderPos/due_date ge …`) | ❌ **parser error** (`expression_parser_error`) |
| `$filter` across a nav property to a *same-named* field (`machine/machine_group_id eq …`) | ⚠️ **SILENT TRAP — returns 200 and wrong rows** |

**The `machine/machine_group_id` trap, in detail.** That filter looks like it
works — it returns 200 and a plausible, non-empty, apparently-filtered result
set. It is actually resolving to the operation's **own** `machine_group_id`
column (operations carry one too), ignoring the `machine/` prefix entirely.
Those two values genuinely differ in real data — 5 mismatched operations turned
up within the first 1,200 rows scanned on test. Verified on operation `460`
(own group `9`, its machine's group `1`): filtering by the machine's real group
`1` returned **0 rows**, filtering by `9` returned it. The Laravel service
filters by the **machine's** group (`whereHas('machine', …)`), so using this
would have silently produced a subtly wrong report — no error, no empty page,
just the wrong operations. **Never filter through a nav path here.**

### The fetch strategy this supports

1. **Lookup tables, fetched whole (they're tiny — 54 machines / 14 groups on
   test):** `Machines` (`$select=id,machine_group_id,name,custom_id`) and
   `MachineGroups` (`$select=id,name`). This gives the machine → group mapping
   in Python, which is what makes the next step correct.
2. **Qualifying stage** — `GET /odata/ProdOrderPosOperations` with only
   push-down-safe predicates: `start lt {range end}`, `status ne 'DELETED'`,
   and — when the machine-group filter is set — `machine_id in (…)`, where the
   id list is resolved from step 1. This is the correct server-side equivalent
   of the Laravel `whereHas('machine', …)`, verified to return only operations
   whose machine really is in the requested group.
3. **Positions** — fetch the referenced `ProdOrderPos` via `id in (…)`
   (`$select=id,name,due_date,prod_order_id`), batched. The `due_date >= {range
   start}` predicate is applied **in Python**, since it cannot be expressed in
   this OData dialect.
4. **Display stage** — for the orders that qualified, fetch **all** their
   non-deleted operations (not just the date-qualifying ones), because the
   source report shows an order's full task chain once the order qualifies.
5. **Orders** — `ProdOrders` via `id in (…)` for `custom_id`.

Every stage pages with `$top`/`$skip` + a stable `$orderby=id`, looping until a
short page, since `$count` is unavailable.

## Computation (port of the Laravel service)

A new pure module — `app/services/order_task_dependency.py` — takes the fetched
operations and produces the report rows. This is a direct port of
`OrderTaskDependencyReportService::mapOrder()`:

- Group operations by `prod_order_pos_id`; within each position, sort by
  `int(pos)`.
- Walk that sorted sequence keeping the previous operation as `predecessor`:
  - `has_dependency` — true when a predecessor exists.
  - `is_blocked` — true when a predecessor exists **and** its status is not
    `CLOSED`.
  - `blocked_by` — the predecessor's `name` when blocked, else null.
- `is_overdue` — the position's `due_date` is before today (date-level
  comparison), and the operation itself is not `CLOSED`. Null due date → not
  overdue.
- Drop orders with no tasks, and orders where every task is `CLOSED`
  (the source's `hasOpenTask` gate).
- "Overdue only", when set, keeps orders having at least one overdue task that
  also matches the machine-group filter — mirroring `filterOverdueEligible()`,
  which shopfloor-suite applies to its PDF only. Here it applies to **all three
  outputs**, so the HTML view, Excel, and PDF can never disagree.

**On `pos` as the sequence key:** `pos` codes are not stable *across* orders
(this app already avoids matching deploy operations by `pos` for that reason —
see `DEPLOY_OPERATION_TARGETS_BY_NAME`). That does not conflict with using `pos`
to order operations *within a single position*, which is exactly what
shopfloor-suite's own service does (`sortBy((int) $operation->pos)`). Keep it.

**Status values are enums, not string literals** (both repos' standing rule): add
a `ProdOrderPosOperationStatus` enum to `deployment_status` mirroring the CRM's
values actually used here (`CLOSED`, `DELETED`), in the same style as the
existing `UserRole`/`DeploymentEnvironment` enums. No bare `"CLOSED"` anywhere,
tests included.

## Timezone handling

The CRM returns operation `start`/`end` as UTC ISO-8601 and `due_date` as a
date. shopfloor-suite's rule is store-UTC/display-local. This app renders
server-side, so: keep everything UTC internally, convert only when formatting
for the HTML/Excel/PDF output, following whatever `app/templates` and
`app/services/export.py` already do for `DeploymentRequest` timestamps —
consistency with this app's existing columns beats inventing a second
convention.

## Reports tab structure

To avoid every future report needing its own top-level nav entry:

- New nav item **`<a href="/reports">Reports</a>`** in `base.html`, near
  `Release Tracker`, conditioned on the same role check as the routes.
- **`GET /reports`** — landing page listing available report types (one today)
  as links/cards, driven by a small in-code registry, so adding a report later
  means one registry entry + one router module.
- Per-report sub-routes:
  - `GET /reports/order-task-dependency` (HTML)
  - `GET /reports/order-task-dependency/export.xlsx`
  - `GET /reports/order-task-dependency/export.pdf`

File layout:
```
app/routers/reports.py                        # GET /reports (landing + registry)
app/routers/reports_order_task_dependency.py  # the three routes above
app/services/order_task_dependency.py         # fetch orchestration + the ported logic
app/templates/reports_index.html
app/templates/reports/order_task_dependency.html
app/templates/reports/order_task_dependency_pdf.html
```
(names may be adjusted at implementation time to match `dashboard.py`'s
conventions.)

All three routes share one filter-parsing helper — same principle as
`_parse_filters`/`_filter_context` in `dashboard.py` — so the view and both
exports can never disagree on what is "currently filtered".

## Machine group filter options

The machine-group dropdown reads the already-synced local `teams` table (mirrors
CRM `MachineGroups`, kept fresh by the existing team sync job) rather than making
another CRM call just to populate a dropdown.

## PDF export

shopfloor-suite's PDF endpoint is branch-only too, so the PDF is generated
locally. `deployment_status` has no PDF capability today (`openpyxl` only).

- **Library: WeasyPrint** (HTML/CSS → PDF). Chosen so the PDF is a Jinja
  template rendered through the same row set as the HTML view — matching the
  order-sectioned layout of shopfloor-suite's Blade PDF — rather than
  hand-positioned output (reportlab). Added to `requirements.txt`; its system
  deps (cairo/pango) added to the `Dockerfile`.
- Layout mirrors the source PDF: sectioned per order rather than one flat table,
  with a one-line summary of the active filters near the top.
- Served as a `Response` with `media_type="application/pdf"` and a
  `Content-Disposition` filename including the date range.
- "Download PDF" button next to "Export to Excel" on the report page.

## Excel export

`GET /reports/order-task-dependency/export.xlsx`, reusing `_columns_to_xlsx`
from `app/services/export.py` with a new column spec — one flat row per task:
Order, Item, Task/Operation, Machine, Machine Group, Status, Due Date, Blocked
By, Overdue.

## Error handling

**An OData error arrives as HTTP 200 with a corrupt body.** Probed and
confirmed: a bad filter returns `200`, `content-type: application/json`, and a
body with an `OData-error: {...}` blob spliced into the middle of the JSON, so
`.json()` raises `JSONDecodeError`. `raise_for_status()` sees nothing wrong.

The adapter must therefore treat a response as failed when **either** the body
contains the `OData-error` marker **or** JSON parsing fails — raising a
dedicated exception with the parsed OData `code`/`message` where available.
Skipping this would let a malformed query surface as an empty report rather
than an error, which is the same class of silent-wrong-data problem as the nav
filter trap above.

Beyond that: if the CRM call fails (unreachable, non-2xx, auth failure) or a
filter is malformed, the route renders the report page with an inline error
banner rather than a 500 — consistent with how this app already tolerates CRM
sync failures elsewhere. The Excel/PDF routes surface the same failure as an
error response rather than a corrupt file.

## Access control

`app/auth.py` gains:

```python
def require_reports_access(current_user: User = Depends(require_login)) -> User:
    if current_user.role not in (UserRole.admin, UserRole.devops, UserRole.team_lead):
        raise HTTPException(status_code=403, detail="Reports access requires admin, devops, or team lead")
    return current_user
```

modeled on the existing admin/devops guards in that file. This is a flat role
check — unlike `can_approve_deployment_request`, it is **not** scoped by
team/`machine_group_id`. Applied to every `/reports*` route including both
exports; the nav link is conditioned on the same check, so `developer` accounts
don't see it (matching how `Admin` is already hidden from non-admins).

## Testing

Following this repo's existing patterns (`tests/test_task_source.py`,
`tests/test_dashboard.py`, `tests/test_export.py`):

- **Adapter**: mocked `httpx.MockTransport` (same `_make_provider` style) —
  correct OData URL/params built, pagination followed, 401 triggers one
  re-login-and-retry, non-2xx propagates.
- **Ported logic** (the highest-value tests, pure functions, no HTTP): first
  operation in a position has no dependency; a successor of a non-`CLOSED`
  predecessor is blocked with the right `blocked_by`; a successor of a `CLOSED`
  predecessor is not; `DELETED` operations are excluded; ordering is by numeric
  `pos`, not string (so `10` sorts after `9`); overdue requires a past
  `due_date` **and** a non-`CLOSED` operation; null `due_date` is never overdue;
  an all-`CLOSED` order is dropped; overdue-only respects the machine-group
  filter.
- **Access**: admin/devops/team_lead pass; `developer` gets 403 on every
  `/reports*` route.
- **Routes**: `/reports` lists the report; the report page renders with mocked
  data; filters pass through; an adapter exception renders the error banner, not
  a 500.
- **Exports**: xlsx has the expected columns over the same filtered rows
  (mirrors `test_export.py`'s existing assertions); pdf returns
  `application/pdf` with non-empty content.

## Open items

Both original open items are now **closed** by the probe against
`crm.test.local` (2026-09-10):

- ~~Lodata `$filter`-across-navigation-properties support~~ — answered: nav-path
  filters are either a parser error or a silent trap; the fetch strategy above
  avoids them entirely. See the capability table.
- ~~Service-account access~~ — confirmed: the existing credentials read all five
  required entity sets.

Remaining, to check during implementation:

- **Live data volume.** The test instance holds ~3,400 operations total, so
  paging cost is trivial there. Live will be larger, and the `due_date`
  predicate cannot be pushed down — so the qualifying stage transfers every
  non-deleted operation starting before the range end, then narrows in Python.
  Measured on `crm.test.local` 2026-09-10: 100 orders / 654 tasks in 19.7s for the default
  one-month window. Re-measure against live volume before relying on it there.
