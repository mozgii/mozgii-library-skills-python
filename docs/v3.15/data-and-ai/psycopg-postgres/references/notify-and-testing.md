---
title: LISTEN/NOTIFY and testing with psycopg
author: MOZGII, Inc.
lang: en
---

# LISTEN/NOTIFY and testing with psycopg

How to receive PostgreSQL notifications in sync and async Python code with psycopg 3, what NOTIFY does not guarantee, and how to test database code against a real PostgreSQL server: a throwaway cluster per test session, a rolled-back transaction per test, and template databases for fast fresh copies.

## Contents

This reference has 8 sections.

1. LISTEN and NOTIFY in short
2. Sending a notification
3. Receiving with notifies()
4. Async listeners
5. A real PostgreSQL for tests
6. A throwaway cluster as a pytest fixture
7. Isolating each test
8. Gotchas

## 1. LISTEN and NOTIFY in short

`NOTIFY channel, 'payload'` sends a message to every session that ran `LISTEN channel`. It is delivered when the sending transaction commits, never if it rolls back, and is lost for a listener that is not connected at that moment.

- Use it as a wake-up signal ("job table changed"), and keep the real state in a table the listener reads after waking up.
- The payload must be shorter than 8000 bytes in the default configuration; send an id, not the data.
- Notifications with the same channel and payload in one transaction may be merged into one.

## 2. Sending a notification

`NOTIFY` cannot take bind parameters, but the function `pg_notify(channel, payload)` can, so send with it.

```python
import json

import psycopg
from psycopg import sql


def notify_job_ready(conn: psycopg.Connection, job_id: int) -> None:
    with conn.transaction():
        conn.execute("UPDATE job SET state = 'ready' WHERE id = %s", (job_id,))
        conn.execute("SELECT pg_notify(%s, %s)", ("job_ready", json.dumps({"id": job_id})))
        # delivered only when this transaction commits, together with the UPDATE
```

A trigger can send it instead (`PERFORM pg_notify('job_ready', NEW.id::text)` in an `AFTER INSERT` trigger function), so every writer notifies without remembering to.

## 3. Receiving with notifies()

`conn.notifies()` is a generator of `Notify` objects (`channel`, `payload`, `pid`). The listening connection must be in autocommit mode: a notification is delivered between transactions, so one stuck in an open transaction never arrives.

```python
import threading
from collections.abc import Callable


def listen_jobs(conninfo: str, handle: Callable[[int], None], stop: threading.Event) -> None:
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier("job_ready")))
        for job_id in pending_job_ids(conn):              # catch up on anything sent while offline
            handle(job_id)
        while not stop.is_set():
            for notify in conn.notifies(timeout=5.0):     # returns after 5 s without messages
                handle(json.loads(notify.payload)["id"])


def pending_job_ids(conn: psycopg.Connection) -> list[int]:
    return [row[0] for row in conn.execute("SELECT id FROM job WHERE state = 'ready' ORDER BY id")]
```

- `timeout=` (psycopg 3.2+) ends the generator after that many seconds of silence, so the loop can check a stop flag; `stop_after=n` ends it after n messages.
- `conn.add_notify_handler(fn)` calls `fn` for notifications that arrive while the connection runs other queries; `notifies()` is clearer for a dedicated listener.
- Reconnect in a loop with backoff when the connection drops, and run the catch-up query after every reconnect: messages sent in between are gone.

## 4. Async listeners

`AsyncConnection.notifies()` is an async generator with the same parameters, so a listener runs as one asyncio task beside the rest of the program.

```python
from collections.abc import Awaitable


async def listen_async(conninfo: str, handle: Callable[[str], Awaitable[None]]) -> None:
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as aconn:
        await aconn.execute("LISTEN job_ready")
        async for notify in aconn.notifies():
            await handle(notify.payload)                  # cancel the task to stop listening
```

Cancelling the task that runs `listen_async` stops the loop and closes the connection through `async with`. On Windows run it under `asyncio.SelectorEventLoop`.

## 5. A real PostgreSQL for tests

Database code is tested against a real PostgreSQL of the same major version as production. SQLite or mocks miss the things that break: types, constraints, `ON CONFLICT`, locking and error classes.

| Option | Needs | Good for |
|---|---|---|
| throwaway cluster with `initdb` (section 6) | PostgreSQL server binaries on the machine | fast local runs, CI without Docker, desktop apps that ship their own Postgres |
| testcontainers (4.15) `PostgresContainer` | Docker or Podman | CI and teams that already run containers |
| pytest-postgresql (9.1) | server binaries | a ready-made fixture set over the same idea as section 6 |
| a shared dev database | nothing extra | never: tests then depend on each other and on leftover data |

Pin the major version in CI (the same `postgres:18` image or package as production), and run the same suite on every OS the app supports.

## 6. A throwaway cluster as a pytest fixture

A session-scoped fixture creates a cluster in a temporary folder, starts it on a private socket or port, and removes it at the end. Nothing touches a developer's own databases.

```python
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def pg_conninfo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if initdb is None or pg_ctl is None:
        pytest.skip("PostgreSQL server binaries (initdb, pg_ctl) are not on PATH")
    base = tmp_path_factory.mktemp("pg")
    data = base / "data"
    subprocess.run([initdb, "-D", str(data), "-U", "test", "--auth=trust", "--encoding=UTF8", "--no-instructions"],
                   check=True, capture_output=True, timeout=120)
    if sys.platform == "win32":
        opts, conninfo = "-c listen_addresses=127.0.0.1 -p 54329", "host=127.0.0.1 port=54329 user=test dbname=postgres"
    else:
        sock = Path(os.environ.get("TMPDIR", "/tmp")) / f"pgtest-{os.getpid()}"   # socket paths have a ~100 byte limit
        sock.mkdir(exist_ok=True)
        opts, conninfo = f"-c listen_addresses='' -k {sock}", f"host={sock} user=test dbname=postgres"
    subprocess.run([pg_ctl, "-D", str(data), "-o", opts, "-l", str(base / "log.txt"), "-w", "start"],
                   check=True, capture_output=True, timeout=120)
    try:
        yield conninfo
    finally:
        subprocess.run([pg_ctl, "-D", str(data), "-m", "immediate", "-w", "stop"], capture_output=True, timeout=60)
```

- `--auth=trust` is acceptable only because the cluster listens on a private socket or loopback and lives for one test run.
- A Unix socket path must stay under about 100 bytes, which deep pytest temp folders exceed on macOS; hence the short socket folder.
- A fixed port on Windows can collide with another run; pick a free one by binding a socket to port 0 first, or derive it from the worker id under pytest-xdist.

## 7. Isolating each test

Each test sees a clean database. The cheapest way is one transaction per test that is always rolled back; the strongest is a fresh database per test copied from a template.

```python
@pytest.fixture
def conn(pg_conninfo: str, schema_ready: None) -> Iterator[psycopg.Connection]:
    with psycopg.connect(pg_conninfo, autocommit=True) as c:
        with c.transaction(force_rollback=True):          # everything the test wrote disappears
            yield c


@pytest.fixture(scope="session")
def schema_ready(pg_conninfo: str) -> None:
    with psycopg.connect(pg_conninfo, autocommit=True) as c:
        c.execute("CREATE TABLE IF NOT EXISTS job (id serial PRIMARY KEY, state text NOT NULL DEFAULT 'new')")
```

- Code under test that opens its own transactions works inside the outer one: psycopg turns nested `transaction()` blocks into savepoints.
- The rollback approach cannot test code that commits on another connection, LISTEN/NOTIFY (delivered only on commit), or `CREATE DATABASE`. Those tests get a database of their own.
- A database per test: build the schema once in `app_template`, then `CREATE DATABASE test_1 TEMPLATE app_template` (autocommit, no other connection to the template) and drop it afterwards. Copying a small template takes a fraction of a second, much less than running migrations again.
- Run the migrations themselves in one test against an empty database, up and down, so they stay tested (the alembic-migrations skill).

## 8. Gotchas

These are the LISTEN/NOTIFY and test problems seen most.

1. **The listener never receives anything.** It is not in autocommit mode, or the sender's transaction never committed. Listen with `autocommit=True`; check that the sender commits.
2. **Messages are missing after a network blip.** NOTIFY is not queued for disconnected sessions. Re-run the catch-up query after each reconnect.
3. **`payload string too long`.** The payload reached 8000 bytes. Send an id and read the row.
4. **Tests pass alone and fail together.** They share data through a database that is not reset. Use the rollback fixture or a database per test.
5. **`pg_ctl start` fails on macOS with an error about the Unix-domain socket path being too long.** The pytest temp path exceeds the socket limit. Use a short socket directory.
