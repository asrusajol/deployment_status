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

`app/static/style.css` opens with a design system ("Ops Console"): a dark
surface and a **signal palette where each hue means one specific thing** —
amber = Test / waiting, violet = Live, green = Main Version / success,
red = failure, teal = primary action.

Use the `:root` tokens (`var(--paper)`, `var(--fog)`, `var(--panel)`,
`var(--amber-dim)`, `var(--font-mono)`, …). **Never hardcode hex.** Light-mode
values shipped to live once in this codebase and rendered dark grey text on the
dark panel; a second set invented tan/green chips for Test/Live, where green
already meant something else. Both were invisible to the test suite — CSS is
not covered, so check a page in the browser before claiming a visual change works.

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
