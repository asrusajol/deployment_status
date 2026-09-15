# Seeder Collection — Design

Status: approved by user in chat, pending written-spec review.
Author: Claude (session brainstorm with the user), 2026-08-30.

## Problem

DevOps runs client-specific Laravel seeder commands (e.g. a "Dynamic
Permission Seeder" with a per-client `--modules=` list) when deploying or
restoring a client's environment. These commands currently live wherever
each devops person happens to keep them (notes, chat history, memory) — there
is no shared, in-app place to save and find them. The user supplied a
mockup: a card per client showing the client name, an environment badge,
host/IP, the seeder title, the full command text, and Edit/Remove/Copy
actions, with a single filter box across client/host/seeder name.

This feature adds a new "Seeder Collection" tab, visible only to devops and
admin, for saving and retrieving these commands. Scope was narrowed from the
mockup during brainstorming: no environment badge (the same command serves
Test and Live), no suggest/approve workflow (direct CRUD only — mockup
wording was incidental), and one row per client (not one per
client+environment).

## Data model

New table `seeder_commands`:

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `client_id` | FK → `clients.id`, unique | one row per client |
| `title` | string | free text label, e.g. "Dynamic Permission Seeder" — not a fixed catalog, devops names it whatever the command is for |
| `command` | text | the full command, verbatim, copy-pasteable |
| `created_by` | FK → `users.id`, nullable | who first saved this row |
| `updated_by` | FK → `users.id`, nullable | who last edited it |
| `created_at` | timestamp | |
| `updated_at` | timestamp | bumped on every edit |

No environment column, no approval/status column — this is a straight
reference catalog, not a workflow queue.

No host column either (revised 2026-09-15, after the first version shipped with
a free-text `host`). The servers a seeder runs against are the client's own
Test/Live URLs, which the app already stores in `client_system_urls` and lets
admin/devops edit on the /clients page. A card reads them live through
`client.system_urls`, so a URL corrected there is right everywhere at once,
where a copy on the seeder row would silently go stale. Migration
`c9d8e7f6a5b4` drops the column.

## Permissions

New `require_devops` dependency in `app/auth.py`, mirroring the existing
`require_admin`: allows `UserRole.admin` or `UserRole.devops`, else 403. Every
route below depends on it. The "Seeder Collection" nav link in `base.html`
is only rendered when `current_user.role.value in ("admin", "devops")`,
matching the existing "Admin" link's pattern.

## Routes

`app/routers/seeder_collection.py`, backed by `app/services/seeder_collection.py`:

- `GET /seeder-collection` — list all rows, one card per client, ordered by
  client name, eager-loading `client.system_urls` (one lazy load per card
  would be an N+1 across 55 clients). No server-side filtering; the filter box
  is client-side JS (see UI below).
- `GET /seeder-collection/new` — add form. Client picker is restricted to
  clients that don't already have a `seeder_commands` row (enforcing "one
  per client" at the UI level; the DB unique constraint on `client_id` is
  the hard backstop).
- `POST /seeder-collection/new` — create. 400 with the form re-shown if the
  chosen client already has a row (race with another devops user), no client
  was picked, or any field is blank.
- `GET /seeder-collection/{id}/edit` — edit form (title/command; client is
  fixed, not re-assignable — deleting and re-adding covers that rare case).
- `POST /seeder-collection/{id}/edit` — update; sets `updated_by`/`updated_at`.
- `POST /seeder-collection/{id}/delete` — delete.

## UI

Cards, not a table (matches the mockup's visual shape better for a
multi-line command block than a table cell would). Each card: client name,
the seeder title, the client's Test and Live server URLs (every URL for each
environment, with its optional label like "Line 2" — the same command serves
both systems, which is exactly why both are shown), the command in a
`<pre>`/monospace block, a Copy button (reuses the existing `.icon-button`
copy-with-checkmark pattern already in `base.html`), and Edit/Delete action
links. A client with no URLs on record gets a muted prompt linking to the
Clients page rather than a blank space.

The add form's client picker is the shared type-to-filter combobox extracted
into `_client_combobox.html` — the same one the dashboard/requests filter bar
uses, so client search behaves identically across the app. Picking a client
immediately renders that client's Test/Live URLs beneath the picker, read from
an embedded datalist, matching how `request_form.html` populates its server-URL
dropdown.

A single text input above the list ("Filter by client, server URL, or seeder
name") is a client-side JS live filter — no page reload, no server round
trip — hiding cards whose client name, server URLs, or title don't match the typed
substring (case-insensitive). This differs from Release Tracker's
server-side dropdown filter bar on purpose: the mockup shows instant-typing
filtering across three free-text-ish fields, which is what a client-side
filter is naturally good at, whereas Release Tracker's filter is a single
exact-match client dropdown.

"+ Add seeder" button opens the new-entry form (a separate page, like
`request_form.html`/`release_tracker_edit.html`, not a modal).

## Testing

Following this repo's existing pattern (`tests/test_release_tracker*.py`):

- `SeederCommand` model: round-trip test.
- Service layer: create/update/delete; create rejects a second row for the
  same client.
- Routes: devops and admin can list/add/edit/delete; a `developer`/`team_lead`
  user gets 403 on every route and doesn't see the nav link.
- Template smoke test: card renders client/title/command, both environments'
  server URLs with their labels, and the "no URLs on record" prompt for a
  client that has none; empty state when no rows exist.
- Service: `seeder_collection_rows` eager-loads `client.system_urls` (asserted
  by counting queries issued after the call, so the test actually fails if the
  eager load is dropped).

## Migration

`f7a8b9c0d1e2` creates `seeder_commands` with the columns above, FK
constraints to `clients` and `users`, unique constraint on `client_id`. No
data migration — this is a brand new table with no prior data.

`c9d8e7f6a5b4` then drops `host` (see Data model). Its downgrade re-adds the
column as nullable; the values themselves are not recoverable, which is
deliberate — the client record owns the URLs now.

## Open items requiring the user before implementation can fully complete

None — self-contained; no external services involved.
