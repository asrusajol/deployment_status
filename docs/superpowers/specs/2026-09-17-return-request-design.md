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

New `RequestStatus.returned`, plus three columns on `deployment_requests`:

| Column | Type | Notes |
|---|---|---|
| `returned_reason` | text, nullable | why it went back; required by the route whenever a return happens, nullable only because every pre-existing row has none |
| `returned_at` | timestamp, nullable | when |
| `returned_by` | FK → `users.id`, nullable | who returned it |

These live on the request, not on `DeploymentExecution`. A return can happen
from `approved`, where no execution row exists yet — `DeploymentExecution` is
created only when someone claims the request (Start Deployment).

They are deliberately *last-return* columns, not a history table. A request
returned three times keeps only the most recent reason. A `request_events`
table would preserve every one, and is the right answer if returns turn out
to be frequent or disputed; it is not worth building before there is any
evidence of that. This is called out so the limitation is a decision rather
than an oversight.

## Transitions

| From | Who | Action | To |
|---|---|---|---|
| `approved`, `in_progress` | deploy team member or admin | **Return** + reason | `returned` |
| `returned` | the requester, or an admin | **Resubmit** | see below |
| `returned` | the requester, or an admin | **Edit**, **Delete** | unchanged status / gone |

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
is preserved in `returned_by`/`returned_at` — the same person who had claimed
it.

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

`returned` joins `EDITABLE_REQUEST_STATUSES` and `DELETABLE_REQUEST_STATUSES`
(both currently stop at `pending_approval`). Editing is the entire point —
they have to fix the branch name — and deleting covers the case the user
raised: the work is no longer needed and the requester drops it themselves.

## Queue position

`returned` is an open status, ranked **first** in `OPEN_REQUEST_STATUS_ORDER`,
above `pending_approval`. It is the only state where a request has moved
*backwards*, and it waits on someone who is not watching the deploy queue —
so it is the easiest thing on the page to forget. Within the stage the
existing oldest-first rule applies.

This is a judgement call, flagged as such: the alternative is ranking it
below `pending_approval`, on the grounds that the deploy team's own queue
should lead. The preceding change to this ordering shipped a regression that
every test passed (`pending_intake` ranked first, pinning one stale row above
everything newer), so this ranking should be looked at in the real queue
before the branch merges, not just asserted in a test.

## UI

**Status cell.** "Returned" in bold amber — amber is already this design
system's "waiting on a person" signal (pending badges, the Test chip), so
Returned reads as another instance of it rather than a new colour. Rail:
`(amber, empty, empty, empty)`, back to stage one, deliberately distinct from
Rejected's red: rejected is over, returned is not.

**Reason.** An `[i]` button beside the status opens the reason in the
existing `<dialog class="changes-modal">` used for Changes and
Branch/Commit — not a new tooltip, and not a `title` attribute, which cannot
hold a multi-line reason and is invisible on touch.

**Action bar.** A returned row offers `Edit`, `Resubmit`, `Delete`. A row in
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
2. Add the three nullable columns.

No data migration; every existing row is correct with three nulls. Downgrade
drops the columns; the enum value cannot be removed cleanly and is left in
place, which is harmless and should be stated in the migration's docstring.

## Testing

- Model: a returned request round-trips its reason, time and returner.
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
- Template: the amber Returned label, the `[i]` button, the reason in the
  dialog, and the Edit/Resubmit/Delete action bar.
- The notification payload includes returned requests.

## Open items

None blocking. Three deliberate limitations recorded above: only the latest
return reason is kept, returning from `in_progress` discards the claim rather
than preserving it as a deployment attempt, and the Excel export gains the new
status but not the reason column.
