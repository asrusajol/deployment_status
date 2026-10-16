# Deployment Tracker — working notes

Internal FastAPI + SQLAlchemy + Jinja app tracking deployment requests, release
versions, and per-client seeder commands. Postgres only.

## Never break master

`master` is deployed to the live system. Before any commit, and always before a
merge or PR:

```bash
.venv/bin/python -m pytest -q          # full suite, ~2 min
```

- **`python` is not on PATH** — use `.venv/bin/python`. `pytest` alone picks up a
  different interpreter without the project's dependencies.
- The whole suite must pass, not just the files you touched. Ordering, template,
  and permission changes routinely break tests in `test_dashboard.py` that look
  unrelated.
- Add `-p no:cacheprovider` if pytest warns it cannot write `.pytest_cache`.
- Never commit with a failing or skipped test to "fix later".

Write the test before the code and watch it fail first. A test that passes the
moment you write it has proved nothing — twice this session a new test passed
against unchanged code (once matching a JSON blob elsewhere on the page instead
of the table it meant to assert on), which would have shipped a rule nothing
actually enforced.

## The local app and its database

The app runs in Docker Compose, **not** from the working tree:

```bash
docker compose up -d --build     # rebuild + restart after code changes
```

- App: **http://localhost:8010** (`deployment_status-app-1`, host networking, so
  no port mapping is listed by `docker ps`). Code lives at `/srv/app` inside it.
- Database: `deployment_status-db-1`, Postgres 16, published on **5432**,
  database `deploy_tracker`.
- The image bakes the code in (`COPY . .`) — editing files does **not** hot
  reload. Rebuild, or run a second instance from the tree for quick iteration:

```bash
DATABASE_URL="postgresql+psycopg2://deploy_tracker:changeme@localhost:5432/deploy_tracker" \
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8011
```

Port 8011 is the convention for showing a change side by side against the real
app on 8010. Stop it when finished.

`app/static_version.py` computes the CSS cache-buster **at import**, so a
stylesheet edit needs a restart, not just a reload.

### Treat `deploy_tracker` as real data

It holds the team's actual clients, requests, and users. Read from it freely;
never run migrations, DDL, or bulk updates against it to test something. Use a
throwaway database instead:

```bash
docker exec deployment_status-db-1 psql -U deploy_tracker -d postgres \
  -c "CREATE DATABASE migcheck;"
DATABASE_URL="postgresql+psycopg2://deploy_tracker:changeme@localhost:5432/migcheck" \
  .venv/bin/python -m alembic upgrade head
docker exec deployment_status-db-1 psql -U deploy_tracker -d postgres \
  -c "DROP DATABASE migcheck;"
```

## Migrations

- Alembic, and **there must be exactly one head**: `.venv/bin/python -m alembic heads`.
  Feature branches that add a revision alongside master's need a merge revision.
- Check the id is free before writing one — the repo has collided before
  (`grep -rl "<revision-id>" alembic/versions/`).
- Some existing migrations use Postgres-only DDL (`ALTER COLUMN ... TYPE ... USING`),
  so `alembic upgrade head` **cannot run on SQLite**. Tests build their schema
  from `Base.metadata.create_all` instead and never run migrations.
- Verify both `upgrade` and `downgrade` on a throwaway database before merging.
- Before deploying a destructive migration, check the live schema first — the
  column being dropped may not exist there at all if the feature never shipped.

## Styling

The company design standard is **Deployment Tracker V12.1** (an HTML mock generated
from Figma; the user can supply the Figma file). It has **two themes, both
maintained**: `standard` (SAP Morning Horizon — white shell, blue `#0064D9`) and
`schertech` (blue `#005981` shell and nav, orange `#FFA600` accents).

- The theme is per browser: cookie `dt-theme`, resolved in `app/main.py`'s
  `theme_middleware` (values in `app/theme.py`), rendered server-side as
  `<html data-theme="…">`. `?theme=schertech` on any GET sets it. `login.html` does
  not extend `base.html` and sets `data-theme` itself.
- `app/static/style.css` opens with the two token blocks (`:root` and
  `:root[data-theme="schertech"]`). **Never hardcode hex in a rule** — add a token
  to both blocks. The older names (`--panel`, `--paper`, `--fog`, `--teal`, …) are
  aliases onto the SAP tokens; text on a filled control uses `--on-accent`, not
  `--ink` (which is now the page background).
- Each hue means one thing: Test = `--sysTest*`, Live = `--sysLive*` (violet in
  Standard, red-orange in Schertech), waiting = `--amber`, in flight = `--teal`
  (brand), success = `--green`, failure = `--red`.
- Fonts and icons are the reference's own, extracted from it into `app/static/fonts/`:
  "72" (a locally installed copy wins), SAP-icons, and BusinessSuiteInAppSymbols (the
  Dashboard nav glyph only). `_icons.html`'s `icon(name)` maps the reference's icon
  names to codepoints — take new ones from the reference's icon map, not by eye.
- Match the reference exactly. The only agreed colour departures are two System tag
  colours (Standard Live violet, dark text on Schertech Test) — see the header of
  `style.css`; anything else goes back to UI/UX first.
- Every page is a `section.card` with a `.toolbar` (`h1.toolbar-title` + `.toolbar-buttons`).
  Use the reference's classes for new UI: `.btn` + `.btn-emph` / `.btn-std` / `.btn-lite` /
  `.btn-icon`, `.st-tag.st-{positive,info,critical,neutral,negative}` for statuses,
  `.panel` for collapsible tiles, `.ro-field` for read-only values, `.mbox` dialogs.
- Icon-only buttons keep their old classes and `data-` hooks (scripts and tests key on
  them), a `title` with the action's name, and the name again in `<span class="sr-only">`.
- The templates' `.field` and `.status` classes predate the design and mean
  something different from the mock's — don't paste the mock's CSS for them.

CSS is not covered by the test suite. Check every page in **both themes** in the
browser before claiming a visual change works.

## Conventions worth following

- Reuse existing partials rather than copying markup: `_client_combobox.html`
  (the type-to-filter client picker used by the requests filter bar and the
  seeder form), `_deployment_filter_bar.html`, `_copy_button.html`.
- Permissions live in `app/auth.py` as FastAPI dependencies
  (`require_login`, `require_admin`, `require_admin_or_devops`/`require_devops`,
  `require_deploy_team_member`). Gate the route **and** hide the nav link;
  hiding the link alone is not access control.
- Eager-load relationships rendered per row (`joinedload`/`selectinload`) — the
  requests and seeder listings both render one card or row per client.
- Comments in this codebase explain *why*, often naming the incident that
  motivated the code. Match that when adding non-obvious logic.
