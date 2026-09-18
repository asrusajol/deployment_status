# Return a request to its requester — Design

Status: approved in chat, pending written-spec review.
Author: Claude (session brainstorm with the user), 2026-09-17.

## Problem

DevOps picks up a request from Pending Deployment and finds it cannot be
deployed through no fault of their own — most often the git branch named on
the request has been deleted, or never existed. Today there is nowhere for
that request to go. The three existing exits are Mark Deployed (untrue),
Reject (a team lead's verdict on whether the work *should* happen, not a
devops finding about whether it *can*), and leaving it sitting in Pending
Deployment. In practice it sits, which is what the user described as
"bothering" — the deploy queue accumulates rows that look actionable and
are not, and nobody tells the requester anything.

This adds a fourth exit: **Return**, with a required reason, which hands the
request back to the person who raised it.

## What Return is not

Not Reject. Reject is the approval gate's "no" and is terminal. Return is
"not yet, and here is what to fix" — the request stays alive, keeps its Task
ID and its history, and comes back through the normal flow once corrected.
Keeping them distinct matters for the audit trail this tool exists to
provide: "returned twice because the branch kept disappearing" and "rejected
by the team lead" are different facts about a deployment.

## Data model

Two new statuses, `RequestStatus.returned` and `RequestStatus.withdrawn`
(see Transitions for why the second one exists), plus a new table
`request_returns` — one row
per return, not a set of "last return" columns on the request.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `request_id` | FK → `deployment_requests.id`, indexed, **not** unique | many returns per request |
| `reason` | text, not null | why it went back; the route refuses a blank one |
| `returned_by` | FK → `users.id` | who returned it |
| `returned_at` | timestamp, not null | when |
| `returned_from` | enum `RequestStatus` | `approved` or `in_progress` — what state it was pulled out of |

A table rather than columns because the history is the point: a team lead
looking at their developer's request needs to see *that it was returned three
times and why each time*, not just the most recent sentence. Overwriting a
reason destroys exactly the fact someone would go looking for — "this keeps
coming back" is a different problem from "this came back once".

`returned_from` also preserves what the previous draft of this spec threw
away. Returning from `in_progress` deletes the `DeploymentExecution` row (see
Transitions), so without this column the fact that someone had already
claimed and started the deployment would be lost. With it, the log reads
"returned while being deployed, by X" and nothing about the attempt
disappears.

`DeploymentRequest.latest_return` is a property over the relationship,
mirroring the existing `current_executor`/`finished_at` pattern, so the
status cell does not need a caller-supplied join. The requests listing must
eager-load `returns` alongside `executions` — one lazy load per row would be
an N+1 across the whole queue, the same trap the seeder listing hit.

Visibility: the log is readable by anyone who can see the request. The queue
is already shared across the team; a per-role restriction on the reason would
be new policy, not a consequence of this feature, and nothing in the reason is
more sensitive than the request beside it.

## Transitions

| From | Who | Action | To |
|---|---|---|---|
| `approved`, `in_progress` | deploy team member or admin | **Return** + reason | `returned` |
| `returned` | the requester, or an admin | **Resubmit** | see below |
| `returned` | the requester, or an admin | **Edit** | stays `returned` |
| `returned` | the requester, or an admin | **Withdraw** | `withdrawn` (terminal) |

**Return** is gated by `require_deploy_team_member` — the same dependency
that guards Start Deployment and Mark Deployed. The people who discover the
branch is missing are exactly the people holding the deploy.

**Returning from `in_progress` deletes the execution row.**
`DeploymentExecution.request_id` is `unique=True` on purpose — "a request can
only ever be claimed once" — and Start Deployment creates that row. Without
this step the sequence return → resubmit → Start Deployment fails on the
unique constraint, at the moment devops tries to pick the request up again,
which is the worst possible time to discover it. The claim is deleted rather
than kept because the deployment did not happen: nothing was executed, and
leaving a dangling claimed-but-abandoned row would make `current_executor`
report a handler for a request nobody is handling. Who returned it, and when,
is preserved in the `request_returns` row, whose `returned_from` records that
the request was `in_progress` when it went back — so the log still says
someone had claimed and started it.

The alternative, relaxing the unique constraint to allow several executions
per request, is the better model if returns from `in_progress` turn out to be
common: it would keep every attempt. It is a much larger change (the
constraint is load-bearing for `current_executor`, which assumes one row) and
is not justified yet.

**Resubmit** returns the request to *the status its own type is created
with*: `standard` → `pending_approval`, `db_dump_restore` and `test_local` →
`approved` (both skip the approval gate — see `RequestType`). This must be
derived from one helper shared with the creation routes, not a second list
of types, or the two drift the first time a request type is added. A
`standard` request goes back through its team lead: the branch changed, so
the approval was given for something that no longer exists.

Resubmit is an explicit button, not a side effect of saving an edit. A
requester correcting a typo should not silently re-enter the deploy queue,
and the resubmission wants to be a deliberate, recorded act.

**A returned request is editable, and deliberately NOT deletable.**

`returned` joins `EDITABLE_REQUEST_STATUSES`. Editing is the entire point:
they have to fix the branch name. This also needs an exception in
`can_edit_request()`, which today refuses any request whose type is not
`standard` — the stated reason being that `db_dump_restore`/`test_local`
requests are "created straight into `approved` and have no real pre-decision
window". A return *creates* that window by design, so the rule has to become
"non-standard types are editable only while `returned`". Without this, a
returned test_local request cannot be corrected by anyone, which removes the
point of returning it.

`returned` does **not** join `DELETABLE_REQUEST_STATUSES`. A returned request
now carries a return log, and that constant's own reasoning is that deleting
a row with history "would silently break the audit trail this whole tool
exists for". Deleting would hand the one person with the strongest motive to
erase an unflattering record the means to do it — the team lead's view of
"why did my developer's request come back three times" is exactly what
disappears.

Instead the requester gets **Withdraw**, a terminal status meaning "we are
not doing this after all". The row and its full return log survive, the
deploy queue is clear, and nobody needs anyone's permission to abandon their
own request. Requiring devops to approve a deletion was considered and
rejected: it is a second approval flow to build and maintain, and it leaves a
requester waiting on someone else to let them drop work nobody wants.

Worked example, which stays readable forever:
`requested -> returned (migration error on live) -> withdrawn by the requester`.

## Queue position

`withdrawn` is terminal and joins `FINISHED_REQUEST_STATUSES`, sorting into
history with the rest.

**Revised after real use** (2026-09-18): `returned` was originally ranked
**first** in `OPEN_REQUEST_STATUS_ORDER`, above `pending_approval`, on the
reasoning that it's the only state where a request has moved *backwards* and
waits on someone not watching the deploy queue. Once the "Returned to you"
table shipped (see below), that reasoning stopped applying to the main
queue: the person who actually needs to notice a return sees it hoisted into
their own table on every page load regardless of where it sits in the main
list. Leaving it pinned first there too meant every OTHER viewer — mainly
admin/devops, who manage the shared queue and usually have no reason to act
on someone else's return until it's resubmitted — got it shoved to the top
of their queue by default, reported as noise.

`returned` now falls through to the finished branch (newest-first, alongside
`withdrawn`/`completed`/...) for everyone. Once the requester resubmits it,
its new status (`pending_approval`/`approved`) is open again in the normal
way — nothing here changes what resubmitting does, only where a request
sits while it is actually `returned`.

This was itself a judgement call the first time round, flagged as such at
the time: the preceding ordering change had shipped a regression that every
test passed (`pending_intake` ranked first, pinning one stale row above
everything newer), so pinning `returned` first was meant to be checked
against the real queue before merge — and once it was, it turned out wrong
for the same reason `pending_intake` was: a good-sounding priority pinned to
the very top of everyone's queue is only right for the audience it was
designed for. Here that audience is now served a different way.

## UI

**Status cell.** "Returned" in bold amber — amber is already this design
system's "waiting on a person" signal (pending badges, the Test chip), so
Returned reads as another instance of it rather than a new colour.

Rail for `returned`: `_Rail(("amber", "empty", "empty", "empty"), 0)` — back
to the first dot, amber, and pulsing, exactly as `pending_approval` does
today (`pulse=0` is what drives `.is-pulsing`). The request really has gone
back to the beginning and really is waiting on a person, so it should look
like it. Deliberately distinct from Rejected's red: rejected is over,
returned is not.

Rail for `withdrawn`: `_Rail(("slate", "empty", "empty", "empty"), None)` —
no pulse, because nothing is waiting on anyone. Slate is a neutral the design
system already has (`--slate-chip`, used for the test.local badge) but which
has no rail dot yet, so this adds one `.rail-dot-slate` rule. Red would be
wrong: withdrawing is not a failure or a rejection, it is a decision not to
proceed.

**Reason.** An `[i]` button beside the status opens the return log in the
existing `<dialog class="changes-modal">` used for Changes and
Branch/Commit — not a tooltip and not a `title` attribute, neither of which
can hold a multi-entry list, and `title` is invisible on touch besides.

The dialog lists every return, newest first: when, by whom, out of which
state, and the reason. The button shows a count once there is more than one
(`[i] 3`), because "returned three times" is the fact a team lead is looking
for and it should not require opening the dialog to discover.

**Action bar.** A returned row offers `Edit`, `Resubmit`, `Withdraw`. A row in
`approved` or `in_progress` gains a `Return` button beside its existing
button, for deploy-team members only.

**Return form.** Returning requires a reason, so the button opens a small
dialog with a textarea rather than submitting immediately — the same shape as
the existing deploy-confirmation dialog. A blank reason is refused server
side too, not just in the browser.

## Notification

`returned` joins `ACTIVE_REQUEST_STATUSES_FOR_NOTIFICATIONS`, so the existing
desktop-notification script announces it the way it already announces
approvals and deploy-ready requests. The popup names the request and its
reason. No new mechanism, and the existing per-row permission filtering
carries over.

## Migration

One Alembic revision:

1. `ALTER TYPE requeststatus ADD VALUE 'returned'`. Postgres will not add an
   enum value inside a transaction block, and Alembic runs migrations in one
   by default — this needs `with op.get_context().autocommit_block():`.
   Getting this wrong fails at deploy time, not in tests: the suite builds its
   schema from `Base.metadata.create_all` and never runs migrations (see
   CLAUDE.md).
2. Create `request_returns` with the columns above, FKs to
   `deployment_requests` and `users`, and an index on `request_id`.

No data migration — a brand new table, and no existing request has ever been
returned. Downgrade drops the table; the enum value cannot be removed cleanly
and is left in place, which is harmless and should be said in the migration's
docstring.

## Not breaking what already works

Adding a status to this app means touching five separate constants, and
missing one fails in a different way each time. Enumerated here because the
last ordering change shipped a regression that the whole suite passed:

| Place | Miss it and |
|---|---|
| `RAIL_STAGES` | `request_list.html`'s `rail_stages[r.status]` is a bare lookup — `KeyError` inside the row loop, so the **entire Requests page 500s**, not just one row |
| `OPEN_REQUEST_STATUS_ORDER` / `FINISHED_REQUEST_STATUSES` | the status sorts as neither and sinks into history — silent |
| `EDITABLE_REQUEST_STATUSES` + `can_edit_request()`'s type rule | the requester cannot fix the branch, so the feature does nothing |
| `ACTIVE_REQUEST_STATUSES_FOR_NOTIFICATIONS` | no popup; the requester never learns |
| `STATUS_LABELS` | falls back to the raw enum value — visible but ugly |

Two structural defences, rather than trusting anyone to remember this list:

1. **Exhaustiveness tests that iterate `RequestStatus` itself**, so a status
   added next year fails them without anyone thinking to update the tests:
   every status has a rail entry and renders a row; every status is in
   exactly one of the open/finished sets; every status has a label.
2. **`rail_stages.get(status, NEUTRAL_RAIL)`** in the template, so a miss
   degrades to a plain row instead of taking the page down. The test catches
   it in CI; the `.get()` means it is never catastrophic in production.

Also to remove while here: `ACTIVE_REQUEST_STATUSES` in
`app/models/deployment_request.py:13` — a tuple of *strings* referenced
nowhere in `app/`, `tests/` or `alembic/`. It is dead, and it reads exactly
like a list someone would dutifully update while adding a status, believing
they had done something.

## Regression checks before merge

Beyond the new tests, this feature must leave the existing flows untouched:

- The full suite passes (CLAUDE.md: it is what guards master).
- A request that is never returned behaves exactly as before, end to end:
  submit → approve → start → mark deployed.
- The dashboard, Release Tracker and Excel export are unaffected — all three
  read only `completed` requests/executions, verified before this was
  written.
- The queue is looked at **in the browser with real data**, not only asserted
  in tests. The `pending_intake` ordering regression passed every test.

## Testing

- Model: a return round-trips its reason, time, returner and source status;
  a request with several returns exposes them newest-first, and
  `latest_return` is the most recent.
- The listing eager-loads `returns` — asserted by counting queries after the
  call, so the test fails if the eager load is dropped rather than merely
  getting slower.
- Returning from `in_progress` removes the execution row, and the full
  round trip — start → return → resubmit → start again — succeeds rather
  than failing on the unique constraint. This is the test that matters most;
  it is the one failure mode here that surfaces only in production.
- Service/route: deploy-team member can return from `approved` and from
  `in_progress`; a developer or team lead gets 403; a blank reason is
  rejected with the form re-shown.
- Resubmit: `standard` lands in `pending_approval`; `test_local` and
  `db_dump_restore` land in `approved`; a non-requester who is not an admin
  gets 403; resubmitting from any other status is refused.
- Edit and Delete are permitted on a returned request and still refused on a
  completed one.
- Ordering: a returned request sorts above `pending_approval` and above
  finished work.
- Template: the amber Returned label, the `[i]` button and its count, every
  return listed in the dialog with who/when/from-where, and the
  Edit/Resubmit/Delete action bar.
- A request returned, resubmitted and returned again shows both entries —
  the case the whole log exists for.
- The notification payload includes returned requests.

## Open items

None blocking. Two deliberate limitations recorded above: returning from
`in_progress` deletes the claim row (though `returned_from` preserves the
fact it was claimed), and the Excel export gains the new status but not the
return log.
