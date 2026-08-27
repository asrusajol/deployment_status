# Release Tracker — Redesign (v2)

Status: approved by user in chat, pending written-spec review.
Author: Claude (session brainstorm with the user), 2026-08-27.
Supersedes: `docs/superpowers/specs/2026-08-27-release-tracker-design.md`'s
data-model, tab, and popup sections (the Bitbucket-sync-mechanics section
mostly still holds — see the Bitbucket cache section below for the one
change to it).

## Problem

The first version of Release Tracker (already implemented, reviewed, and
merged into this branch) modeled `client_version_records` as full history —
a new row per client+environment every time DevOps confirmed a deploy. In
real use this is more than the team wants: they want one row per client
showing the latest state, not a growing log. The user supplied a mockup
(reproduced below in words, since it's an image): one row per client, with
Test's current version + updated-at as one column block, Live's as another,
and the shared Bitbucket main-branch version + updated-at as a third block —
each block color-coded (orange/Test, purple/Live, green/Main).

This redesign replaces the history table with a latest-state table, keeps
previous-version tracking (per environment, one step back, in the backend
only — not shown in this UI), fixes the Bitbucket "Updated at" semantics to
reflect when the version actually changed rather than every 5-minute poll,
and migrates the real, already-in-use history data forward before dropping
the old table.

## Data model

### Replace `client_version_records` with `client_version_status`

One row per client (`client_id` unique), environment flattened into columns:

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `client_id` | FK → `clients.id`, unique | one row per client |
| `test_current_version` | string, nullable | null until the client's first Test deploy |
| `test_previous_version` | string, nullable | backend-only; not rendered in the tab UI (per user: "keep it in backend... use it later if needed") |
| `test_updated_at` | timestamp, nullable | bumped on every Test deploy confirmation, even a redeploy of the same version (confirmed with the user — "always bump on deploy") |
| `test_recorded_by` | FK → `users.id`, nullable | who confirmed the current Test version |
| `test_deployment_request_id` | FK → `deployment_requests.id`, nullable | which deploy set the current Test version |
| `live_current_version` | string, nullable | mirrors the `test_*` fields, for Live |
| `live_previous_version` | string, nullable | |
| `live_updated_at` | timestamp, nullable | |
| `live_recorded_by` | FK → `users.id`, nullable | |
| `live_deployment_request_id` | FK → `deployment_requests.id`, nullable | |
| `main_version` | string, nullable | snapshot, see below |
| `main_pr_number` | int, nullable | snapshot, see below |
| `main_updated_at` | timestamp, nullable | snapshot, see below |

### Main Version is a per-client snapshot taken at that client's own deploy time

Revised per the user: NOT a single shared live value read at render time (an
earlier draft of this spec proposed that; superseded here). Each client row
keeps its own `main_version`/`main_pr_number`/`main_updated_at`. These are
written **only** when that specific client has a new deploy (test or live) —
at that moment, copy the current values out of the `bitbucket_main_branch_status`
cache (see below) into this client's row. The periodic 5-minute sync never
touches `client_version_status` directly — it only ever updates the one
global cache row, so "no need to update the whole clients table" on every
poll. `main_updated_at` is set to the cache's `version_changed_at` (not
"now") — see below for why that's the meaningful value here.

### `bitbucket_main_branch_status`: `version_changed_at` is the honest "when did main last actually change"

Per the user: a version's timestamp must only move when the release version
actually changes, not every 5-minute poll. Add a new column:

| Column | Type | Notes |
|---|---|---|
| `version_changed_at` | timestamp, nullable | new — set to now() only when `sync_bitbucket_main_status` observes `version` differing from what's already stored |

`last_synced_at` (existing column) keeps its current behavior — bumped on
every successful sync regardless of whether the value changed — kept for
ops/liveness diagnostics (e.g. "is the cron job actually still running"),
not shown anywhere in the Release Tracker UI.

This is the value client rows snapshot at their own deploy time (see Data
model above): a client's `main_updated_at` = the cache's `version_changed_at`
as of that deploy, not the deploy's own timestamp — so it answers "main had
been at this version since Y" rather than "I happened to look at time Y",
which is the actually useful signal for spotting a client that's fallen
behind.

## Deploy-time popup (unchanged behavior, different backend target)

The popup itself (Client/System/Previous version read-only, Current version
required input) is unchanged. What changes is what confirming it does:

- Get-or-create the `client_version_status` row for `deployment_request.client_id`.
- If `environment == live`: `live_previous_version = live_current_version` (the
  value about to be overwritten — this is also what the popup's read-only
  "Previous version" field displayed), then set `live_current_version` to the
  typed value, bump `live_updated_at`, set `live_recorded_by`/
  `live_deployment_request_id`. The `test_*` fields on that same row are not
  touched.
- If `environment == test`: the mirror of the above, touching only `test_*`
  fields.
- Either way, also snapshot `main_version`/`main_pr_number` from the current
  `bitbucket_main_branch_status` row (both null if no sync has run yet — same
  null-safety as v1) and set `main_updated_at` to that cache row's
  `version_changed_at`. This is a per-client write, same transaction as the
  `test_*`/`live_*` update above — no other client's row is touched.

## Release Tracker tab

- **One row per client**, not one row per deploy. Columns: Client, Test
  (Current Version, Updated At — orange block), Live (Current Version,
  Updated At — purple block), Main Version (Current Version, Updated At —
  green block), Action.
- **Colors**: reuse existing design tokens — `--amber` for the Test block,
  `--violet` for the Live block, `--green` for the Main Version block —
  matching this app's existing test/live badge convention rather than
  introducing new colors.
- **Filter bar**: Client only. The System/environment filter from v1 is
  dropped — every row always shows both Test and Live columns together, so
  filtering by system no longer means anything.
- **Excel export**: same column set as the table, one row per client.
- **Empty cells**: a client with no Test deploy yet shows "—" in the Test
  block (and similarly for Live, and for Main Version if that client has
  never deployed at all yet) rather than erroring — the normal state for a
  brand-new client. Different clients can legitimately show different Main
  Version values, since each one's is a snapshot from their own last deploy,
  not a live shared read.

## Row correction (edit)

One Edit action per client row. The edit form lets you correct
`test_current_version` and/or `live_current_version` independently — two
separate optional fields, not a single shared one. Permission is checked
**per column**, not per row: editing the Test value requires
`current_user.id == row.test_recorded_by` (or admin); editing the Live value
requires `current_user.id == row.live_recorded_by` (or admin) — so a user
who only ever confirmed this client's Test deploy can fix a Test typo but
cannot touch the Live value on the same row, and vice versa. Only the
`*_current_version` fields are ever editable — `*_previous_version`,
`*_updated_at` (except the bump on a successful edit, same as v1),
`*_recorded_by`, `*_deployment_request_id` stay as historical fact.

## Migration of real data

`client_version_records` already holds real production history (a
teammate's actual Scherer GmbH deploys, made during this feature's
development/verification). Per the user: drop the old table, but carry its
*logic* forward rather than just its data — the "compute previous before
overwriting current" pattern from v1's insert path is preserved, just
applied to updating one wide row instead of inserting a new history row (see
Deploy-time popup above).

**Data migration**, run as part of the Alembic migration that creates
`client_version_status` and drops `client_version_records`:

1. Create `client_version_status`.
2. Add `version_changed_at` to `bitbucket_main_branch_status`; backfill it to
   the existing `last_synced_at` value if `version` is already set (best
   available approximation — the exact moment the current value first
   appeared isn't recoverable), else leave null.
3. For each distinct `client_id` present in `client_version_records`: query
   that client's `ClientVersionRecord` rows ordered by `created_at` ascending,
   separately for `environment == test` and `environment == live`. For each
   environment with at least one row: `*_current_version` = the last row's
   `current_version`; `*_previous_version` = the second-to-last row's
   `current_version` (null if only one row exists); `*_updated_at` = the last
   row's `updated_at`; `*_recorded_by` = the last row's `recorded_by`;
   `*_deployment_request_id` = the last row's `deployment_request_id`. Also
   backfill `main_version`/`main_pr_number` from whichever of that client's
   old rows is most recent overall (test or live, whichever was later) —
   those columns already existed on `client_version_records` in v1 — and set
   `main_updated_at` to `bitbucket_main_branch_status.version_changed_at`
   (the newly-backfilled value from step 2), since the exact historical
   moment isn't recoverable per-client either.
4. Drop `client_version_records`.

This migration touches real data — run it against the dev/staging DB first
and confirm the migrated `client_version_status` rows match expectations
before this branch is considered ready to merge to `master`.

## Testing

Following this repo's existing pattern:

- `client_version_status` model: round-trip test (mirrors v1's model test).
- Upsert logic (new/renamed service function, replacing the v1 insert path
  in `deploy_request`): get-or-create by client; Live deploy touches only
  `live_*` fields, Test only `test_*`; previous-version correctly captured
  before overwrite; repeat deploys to the same environment update in place,
  never create a second row per client.
- `sync_bitbucket_main_status`: `version_changed_at` bumps when version
  differs from stored, stays put when it's the same, even though
  `last_synced_at` bumps every time either way; confirm the sync never
  touches `client_version_status` at all.
- Deploy confirmation: a client's `main_version`/`main_pr_number`/
  `main_updated_at` are correctly snapshotted from the cache at deploy time
  (`main_updated_at` = the cache's `version_changed_at`, not the deploy's own
  timestamp); confirm no other client's row is touched by one client's deploy.
- Release Tracker route/template: one row per client with both environment
  blocks rendered; empty-state "—" for a client missing one environment or
  never deployed at all; two different clients can show two different Main
  Version values (since each is that client's own snapshot); System filter
  is gone, Client filter still works.
- Per-column edit permission: a user who recorded only the Test value can
  edit Test but gets 403 attempting to edit Live on the same row (and vice
  versa); admin can edit either.
- Migration: seed v1-shaped history data (multiple rows per client per
  environment), run the migration, assert the resulting
  `client_version_status` rows have the correct current/previous values.

## Open items requiring the user before implementation can fully complete

None — this redesign is self-contained; the Bitbucket token and repo config
already exist and don't change.
