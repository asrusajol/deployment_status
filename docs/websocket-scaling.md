# Scaling `/ws/requests` beyond one worker process

`app/ws.py`'s `ConnectionManager` is deliberately the simplest thing that works: a plain
in-memory Python `set` of connected `WebSocket` objects, living inside one running
process. Every mutation to a `DeploymentRequest` (`app/routers/dashboard.py`) calls
`manager.notify()` right after its `db.commit()`, which broadcasts a bare `"changed"`
string to every socket in that set; the client (`request_list.html`) just reloads the
page on receipt.

This works correctly and requires nothing extra **as long as this app runs as a single
process** — which is what `docker-compose.yml` runs today (`uvicorn app.main:app`, no
`--workers` flag). This document is what changes the day that stops being true — either
because the app gets `--workers N` for more throughput on one machine, or because it gets
deployed as multiple replicas behind a load balancer.

## Why it breaks

Each worker process gets its **own** `ConnectionManager` instance — they don't share
memory. Concretely:

- A browser's WebSocket connection to `/ws/requests` lands on exactly one worker (worker
  A, say) and stays there for the life of that connection.
- When a different request happens to be handled by worker B (e.g. another user clicks
  "Approve", and the load balancer/uvicorn routes that HTTP request to worker B), worker
  B's `manager.notify()` only broadcasts to worker B's own local `_connections` set.
- The browser connected to worker A never hears about it. Its live-update ping silently
  stops working — degrading (not crashing) back to the 2-minute fallback reload already
  built into `request_list.html`, so nobody's blocked, but "live" updates become "up to
  2 minutes late" for some fraction of users depending on which worker they landed on.

This is invisible in testing with one worker (exactly what today's test suite and
single-process deployment both are) and only shows up once there's more than one.

## What needs to change

The fix is a standard pub/sub fan-out: instead of `notify()` broadcasting directly to a
local set, it **publishes** a message to a channel every worker subscribes to; each
worker's own subscriber then calls its own local `_broadcast()` when a message arrives.
`ConnectionManager`'s public shape (`connect`/`disconnect`/`notify`) doesn't need to
change — only what happens inside `notify()`, plus a new background subscriber task.

Two realistic options, in order of how little new infrastructure they need:

### Option A — Postgres `LISTEN`/`NOTIFY` (no new infrastructure)

This app already runs Postgres for everything else, so this is the option that adds zero
new moving parts:

1. Each worker opens one dedicated `asyncpg` (or `psycopg` async) connection at startup
   and runs `LISTEN deployment_requests_changed;` on it, in a background `asyncio` task
   that loops on incoming notifications and calls the local `manager._broadcast(...)` for
   each one.
2. `notify()` changes from broadcasting directly to executing
   `NOTIFY deployment_requests_changed, 'changed';` against the database (a single
   lightweight statement — commit the triggering transaction first, then notify, same as
   today's "commit, then notify" ordering).
3. Caveat: Postgres `NOTIFY` payloads are capped at 8000 bytes and, more importantly, are
   **not durable** — a worker that's down (or whose listener connection dropped) when a
   `NOTIFY` fires simply never sees it. That's fine here (a missed "changed" ping just
   means that worker's clients fall back to their 2-minute reload timer instead of an
   instant one — never wrong, just occasionally slower), but would NOT be fine if this
   channel ever needs to carry anything that must be delivered exactly-once.

### Option B — Redis Pub/Sub (if Redis is ever added for other reasons)

Same shape as Option A, using Redis's `PUBLISH`/`SUBSCRIBE` instead of Postgres
`LISTEN`/`NOTIFY`. Only worth the extra infrastructure if this project ends up needing
Redis for something else anyway (a cache, a task queue, rate limiting) — introducing a
whole new service just for this one feature isn't worth it while Option A covers the
same need with what's already running.

## Load balancer / reverse proxy notes

Once there's more than one **replica** (separate machines/containers, not just
`--workers` on one machine), whatever sits in front of them (nginx, a cloud load
balancer) needs to:

- Actually forward WebSocket upgrade requests end-to-end — already true for the nginx
  config in this repo (`deploy/nginx/deployment.test.local.conf`'s `location /ws/`
  block), but re-verify this on whatever fronts a multi-replica deployment; not every
  load balancer forwards `Upgrade`/`Connection` headers by default.
- **Session affinity ("sticky sessions") is *not* required** once Option A or B above is
  in place — because every replica can push to every one of its own locally-connected
  clients regardless of which replica handled the HTTP request that triggered the
  change, a client can be load-balanced to a different replica on every request without
  ever missing an update. Sticky sessions would only be a workaround if pub/sub fan-out
  were skipped entirely — not recommended, since that reintroduces exactly the
  single-point-of-delivery problem this document exists to avoid.

## Summary checklist for whoever implements this

- [ ] Decide Option A (Postgres `LISTEN`/`NOTIFY`) vs. Option B (Redis), per the tradeoffs above — default to A unless Redis is already planned for something else.
- [ ] Add a background subscriber task per worker, started on app startup, that calls `manager._broadcast()` on each incoming message.
- [ ] Change `ConnectionManager.notify()` to publish to the shared channel instead of broadcasting locally.
- [ ] Confirm the reverse proxy / load balancer in front of the real multi-replica deployment forwards WebSocket upgrades (see nginx notes above) — don't assume it does by default.
- [ ] No client-side change needed — `request_list.html`'s WebSocket client and 2-minute fallback reload are already replica-count-agnostic.
