---
title: Worker database access with psycopg
author: MOZGII, Inc.
lang: en
---

# Worker database access with psycopg

How a worker reaches the application's Postgres, what it writes and what it leaves to the host's job runner. Every block ran against Postgres 18 over its unix socket (a throwaway container, `listen_addresses=''`), with `PGPORT` and `PGDATABASE` set to wrong values in the worker's environment.

The blocks import from `worker_skeleton`; in your package that module is `my_app_worker.runtime` (Program layout in SKILL.md).

## Contents

This reference has 5 sections.

1. Who owns what
2. Connecting: conninfo from the host, one pool
3. Job methods: params, checkpoints, transactions
4. Detached jobs: heartbeat and cancel through Postgres
5. Windows and other platform notes

## 1. Who owns what

Ownership decides which process may create, migrate or write each thing.

| Thing | Owner | The worker |
|---|---|---|
| cluster, socket directory, roles, extensions, schema, migrations | the host or a migration step | connects, never creates or migrates |
| `jobs` rows: state, attempts, `progress`, `heartbeat_at` | the host's job runner | reports `$/progress` with a checkpoint; writes the row only for detached jobs (§4) |
| domain rows (file facts, transcripts, index entries) | the worker method | writes them in transactions, idempotently |
| connection info | the host, in the worker's environment | reads `MY_APP_DB`, never `PG*`, never `DATABASE_URL` |

## 2. Connecting: conninfo from the host, one pool

The host builds each worker's environment from an allowlist that drops `PG*` and `PYTHON*`, and adds the database coordinates in one app-prefixed variable, for example:

```bash
MY_APP_DB='{"host": "/run/my-app", "port": 5432, "dbname": "my_app", "user": "my_app_worker", "passfile": "/run/my-app/db.pgpass"}'
```

- `host` is the socket directory on macOS and Linux (libpq treats an absolute path as a Unix-domain socket directory; the socket file is `.s.PGSQL.<port>` inside it). macOS limits a socket path to about 103 characters, so keep the directory short. On Windows use `127.0.0.1` over TCP on the cluster's own port.
- No password in the environment or argv. App roles log in with `scram-sha-256`, so the worker needs one: the host writes `db.pgpass` (0600, one exact line `<host>:<port>:<dbname>:<user>:<password>`, host spelled exactly as in `MY_APP_DB`) and sends only its path as `passfile`. A password the host holds only in memory goes in the first request instead (a `configure` method), and the worker keeps it in memory.

The worker side, as one module of methods:

```python
"""Database methods for the worker: one psycopg async pool over the app cluster's unix socket."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, TypedDict, cast

from worker_skeleton import AppError, Ctx, int_param, method

if TYPE_CHECKING:                                             # types only: the driver loads on first use
    from psycopg import AsyncConnection
    from psycopg.rows import TupleRow
    from psycopg_pool import AsyncConnectionPool

    Pool = AsyncConnectionPool[AsyncConnection[TupleRow]]

_pool: Pool | None = None


def conninfo() -> str:
    from psycopg.conninfo import make_conninfo                # lazy: the handshake never waits for the driver

    spec = cast(dict[str, object], json.loads(os.environ.get("MY_APP_DB") or "{}"))
    host, port, dbname, user, passfile = (spec.get(k) for k in ("host", "port", "dbname", "user", "passfile"))
    if not (isinstance(host, str) and (host.startswith("/") or host == "127.0.0.1") and isinstance(port, int)
            and isinstance(dbname, str) and isinstance(user, str) and isinstance(passfile, str)):
        raise AppError("DB_CONFIG", "MY_APP_DB must name a socket directory or 127.0.0.1, port, dbname, user and passfile")
    return make_conninfo(host=host, port=port, dbname=dbname, user=user, passfile=passfile,
                         sslmode="disable", application_name="my_app_worker", connect_timeout=5)


async def pool(ctx: Ctx) -> Pool:
    global _pool
    if _pool is not None:
        return _pool
    from psycopg_pool import AsyncConnectionPool

    for key in [k for k in os.environ if k.startswith("PG")]:
        del os.environ[key]                                   # libpq fills unset parameters from PG*
    candidate: Pool = AsyncConnectionPool(conninfo(), min_size=1, max_size=4, open=False, name="worker")
    await candidate.open(wait=True, timeout=10)               # fail on the first DB call, loudly
    ctx.rt.on_stop.append(candidate.close)
    _pool = candidate
    return candidate


class FactsResult(TypedDict):
    job_id: int
    written: int


@method("facts.write")
async def facts_write(params: dict[str, object], ctx: Ctx) -> FactsResult:
    job_id = int_param(params, "jobId", 0, 1, 2**62)
    count = int_param(params, "count", 3, 1, 10_000)
    p = await pool(ctx)
    async with p.connection() as conn, conn.transaction():   # one transaction per job: all rows or none
        for i in range(count):
            ctx.token.check()
            await conn.execute("insert into facts (job_id, path) values (%s, %s)", (job_id, f"/raw/{i}.mov"))
            ctx.progress(i + 1, count)                       # progress only: nothing is durable yet
    ctx.progress(count, count, checkpoint={"next": count})   # the transaction has committed
    return {"job_id": job_id, "written": count}
```

- Delete `PG*` in the worker as well: libpq fills every parameter the conninfo leaves unset from its environment variable. In the live run a conninfo without `dbname` plus `PGDATABASE=postgres` connected to `postgres`, and a URL without a port plus `PGPORT=1` tried `.s.PGSQL.1`. The host strips them in production; developers' shells and test runners do not.
- Name every parameter: `host`, `port`, `dbname`, `user`, `passfile`, `sslmode`, plus `application_name` (shows the worker in `pg_stat_activity`) and `connect_timeout`.
- One pool per process, opened on the first database call (`open=False`, then `await pool.open(wait=True, timeout=...)`; opening in the constructor is deprecated for async pools), closed through `ctx.rt.on_stop` at shutdown. `max_size` 4 covers a worker whose GPU queue is the real bottleneck; size it from the lane limits, not from cores.
- pyright strict: annotate the pool as `AsyncConnectionPool[AsyncConnection[TupleRow]]`, import `make_conninfo` from `psycopg.conninfo`, and pass query strings as literals (psycopg types them `LiteralString`; build dynamic SQL with `psycopg.sql`).
- `~/.pgpass` (Windows `%APPDATA%\postgresql\pgpass.conf`) is read whenever the conninfo carries no password and the server asks for one, which is every scram login. A `*:*:*:*:` line in a developer's file answers for any server, so always pass `passfile` explicitly: it beats `PGPASSFILE` and the default file (live, libpq 18.6). A `service=` parameter or `PGSERVICE` would pull in `pg_service.conf`; the scrub removes the variable and the conninfo never sets `service`.

## 3. Job methods: params, checkpoints, transactions

A job method is a method the host's job runner calls with a checkpoint so the work can resume. A job command reaches the worker as `{ jobId, params, checkpoint }`; `checkpoint` is the last one the runtime persisted, or `null`.

```python
class ImportResult(TypedDict):
    job_id: int
    next: int


@method("facts.import")
async def facts_import(params: dict[str, object], ctx: Ctx) -> ImportResult:
    """A resumable job: commit a batch, then report its checkpoint; a resume starts after the last commit."""
    job_id = int_param(params, "jobId", 0, 1, 2**62)
    inner = params.get("params")
    total = int_param(cast(dict[str, object], inner) if isinstance(inner, dict) else {}, "count", 1000, 1, 10**7)
    checkpoint = params.get("checkpoint")
    start = int_param(cast(dict[str, object], checkpoint), "next", 0, 0, total) if isinstance(checkpoint, dict) else 0
    p = await pool(ctx)
    batch = 100
    for lo in range(start, total, batch):
        ctx.token.check()
        hi = min(total, lo + batch)
        async with p.connection() as conn, conn.transaction():
            await conn.execute(
                "insert into facts (job_id, path) select %s, '/raw/' || n || '.mov' from generate_series(%s::int, %s::int - 1) n "
                "on conflict (job_id, path) do nothing",               # idempotent: a replayed batch adds nothing
                (job_id, lo, hi))
        ctx.progress(hi, total, checkpoint={"next": hi})           # only after the commit
    return {"job_id": job_id, "next": total}
```

- Commit, then report: a checkpoint names work that is already durable. Reporting first and crashing before the commit would make a resume skip rows.
- Make every batch idempotent (`ON CONFLICT DO NOTHING`, upserts, "skip if the file with this size and hash exists"), because the job runner may hand back a checkpoint older than the last commit: it persists at most one progress write per second. In the live run a job cancelled at 6,500 rows and resumed from 6,200 ended with exactly 300,000 distinct rows.
- One transaction per job when the job is small; one per batch when it is long, so a cancel or crash loses at most one batch and locks stay short.
- Check the token before each batch; a cancelled job ends with `-32800`, and the job runner marks it `cancelled`.
- Do not update `jobs` from a supervised job: the job runner is the single writer of state and progress, and two writers race.

## 4. Detached jobs: heartbeat and cancel through Postgres

A detached job (one that outlives the host and has no stdin) writes its own progress and heartbeat and learns about a cancel from Postgres. One statement does all three:

```python
PROGRESS_SQL = """
    WITH u AS (UPDATE jobs SET progress = %(progress)s, heartbeat_at = now()
               WHERE id = %(id)s AND state = 'running' RETURNING id, cancel_requested)
    SELECT cancel_requested, pg_notify('job_progress', id::text) FROM u"""


async def report_detached(p: Pool, job_id: int, progress: dict[str, object]) -> bool:
    """Detached jobs only: persist progress and the heartbeat; returns True when a cancel was requested."""
    async with p.connection() as conn:
        row = await (await conn.execute(PROGRESS_SQL, {"id": job_id, "progress": json.dumps(progress)})).fetchone()
    return row is None or bool(row[0])                           # no row: the job is no longer running


async def watch_cancel(job_id: int, ctx: Ctx) -> None:
    """Detached jobs only: LISTEN jobs_cancel and set the token as soon as this job's id arrives."""
    from psycopg import AsyncConnection

    async with await AsyncConnection.connect(conninfo(), autocommit=True) as conn:   # LISTEN needs autocommit
        await conn.execute("LISTEN jobs_cancel")
        async for note in conn.notifies():
            if note.payload == str(job_id):
                ctx.token.cancel()
                return
```

- `report_detached` persists progress, moves `heartbeat_at` (the lease the job runner checks) and returns the cancel flag; call it at most once per second.
- `watch_cancel` runs as a task beside the job: `LISTEN` needs an autocommit connection, and `async for note in conn.notifies()` yields each notification as it arrives (`notifies(timeout=...)` returns after the timeout when nothing came). In the live run the flag and the notification both arrived; the flag on the next write is the reliable one, the notification is the fast one.
- NOTIFY payloads stay under 8,000 bytes: send ids, never progress bodies.

## 5. Windows and other platform notes

The platform notes cover Windows event loops, TCP instead of sockets, and dropped connections.

- psycopg's async mode does not work on the default Windows Proactor loop; the skeleton starts `asyncio.run(..., loop_factory=asyncio.SelectorEventLoop)` on Windows (runtime.md §4). Not run here; cover it with the Windows contract job.
- Windows has no peer authentication, so a local cluster there listens on `127.0.0.1` with `scram-sha-256` on every `pg_hba.conf` line. `MY_APP_DB.host` is then `127.0.0.1`, `conninfo()` accepts it, and the `db.pgpass` line starts with `127.0.0.1`.
- A connection that dies (cluster restart) raises on the next use; `pool.connection()` hands out a fresh one. Let the job fail or retry by the job runner's policy instead of retrying writes inside the method.
