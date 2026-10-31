---
title: SQLite with threads, processes and asyncio
author: MOZGII, Inc.
lang: en
---

# SQLite with threads, processes and asyncio

How to use one SQLite file from several threads, several processes or an asyncio program without "database is locked" errors and without blocking the event loop: connection per thread, one writer thread fed by a queue, aiosqlite, and shared in-memory databases for tests.

## Contents

This reference has 7 sections.

1. What SQLite allows at the same time
2. One connection per thread
3. The single-writer thread
4. Several processes on one file
5. aiosqlite in asyncio code
6. In-memory databases for tests
7. Gotchas

## 1. What SQLite allows at the same time

SQLite in WAL mode allows any number of readers and one writer at a time, across all threads and processes that open the file. Writers do not queue fairly: a writer that finds the lock taken sleeps and retries until `busy_timeout` runs out, then raises `database is locked`.

| Need | Pattern |
|---|---|
| a desktop app or CLI with a few threads | one connection per thread, WAL, short write transactions |
| many writers (background jobs, a sync loop) | one writer thread owns all writes; others send it work |
| asyncio | aiosqlite, or `asyncio.to_thread` around a sync function that opens its own connection |
| several processes | WAL on a local disk, `busy_timeout`, `BEGIN IMMEDIATE` for writers |
| heavy concurrent writes from many machines | not SQLite: use PostgreSQL (the psycopg-postgres skill) |

The Python module is built thread-safe (`sqlite3.threadsafety == 3`), but a connection still refuses use from another thread unless `check_same_thread=False`. Turning that check off is safe only when your own lock serialises every use, transactions included, so it rarely pays off.

## 2. One connection per thread

A connection per thread avoids sharing; `threading.local` holds each thread's connection, and a pool of worker threads reuses them.

```python
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

DB = "jobs.db"
_local = threading.local()
_opened: list[sqlite3.Connection] = []
_opened_lock = threading.Lock()


def db() -> sqlite3.Connection:
    con: sqlite3.Connection | None = getattr(_local, "con", None)
    if con is None:
        # check_same_thread=False only so the main thread can close it after the pool stopped
        con = sqlite3.connect(DB, autocommit=True, timeout=10.0, check_same_thread=False)
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA foreign_keys = ON")
        con.autocommit = False
        _local.con = con
        with _opened_lock:
            _opened.append(con)                     # closed by the owner at shutdown
    return con


with closing(sqlite3.connect(DB, autocommit=True)) as setup:
    setup.execute("CREATE TABLE IF NOT EXISTS hit (n INTEGER NOT NULL)")


def record(n: int) -> None:
    con = db()
    with con:
        con.execute("INSERT INTO hit (n) VALUES (?)", (n,))


with ThreadPoolExecutor(max_workers=8) as pool:
    list(pool.map(record, range(200)))
for con in _opened:
    con.close()
with closing(sqlite3.connect(DB)) as check:
    check.execute("SELECT count(*) FROM hit").fetchone()[0]   # 200
```

A pool thread that exits does not close its connection, and by default only the creating thread may call `close()`. The example keeps a list and turns the thread check off so the main thread can close them all once the pool has stopped; while the pool runs, each connection is still used by one thread only.

## 3. The single-writer thread

When many parts of a program write, one thread that owns the only write connection removes lock contention entirely. Others put work on a queue and wait for a future; reads still use their own connections.

```python
import queue
from collections.abc import Callable
from concurrent.futures import Future


class Writer:
    """Runs write functions one at a time on a dedicated thread and connection."""

    def __init__(self, path: str) -> None:
        self._jobs: queue.Queue[tuple[Callable[[sqlite3.Connection], object], Future[object]] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, args=(path,), name="sqlite-writer")
        self._thread.start()

    def _run(self, path: str) -> None:
        con = sqlite3.connect(path, autocommit=True, timeout=30.0)
        con.execute("PRAGMA journal_mode = WAL")
        try:
            while (job := self._jobs.get()) is not None:
                fn, future = job
                try:
                    con.execute("BEGIN IMMEDIATE")
                    result = fn(con)
                    con.execute("COMMIT")
                    future.set_result(result)
                except BaseException as exc:
                    if con.in_transaction:
                        con.execute("ROLLBACK")
                    future.set_exception(exc)
        finally:
            con.close()

    def submit(self, fn: Callable[[sqlite3.Connection], object]) -> Future[object]:
        future: Future[object] = Future()
        self._jobs.put((fn, future))
        return future

    def close(self) -> None:
        self._jobs.put(None)
        self._thread.join()


writer = Writer(DB)
futures = [writer.submit(lambda c, i=i: c.execute("INSERT INTO hit (n) VALUES (?)", (i,)).lastrowid) for i in range(50)]
[f.result() for f in futures][-1]                   # 250: the last rowid
writer.close()
```

- Group many small writes into one job (a list of rows per `executemany`) so each commit carries more work.
- From asyncio, wait on the future with `await asyncio.wrap_future(writer.submit(fn))`.

## 4. Several processes on one file

Several processes can share one database file in WAL mode on a local disk. Each process opens its own connections; nothing is passed between processes but the file path.

- Never open a connection before `fork` and use it in the child. Open connections inside the worker process (in the pool `initializer`, or inside the task).
- Set `busy_timeout` high enough for the longest write transaction plus a margin, and start writers with `BEGIN IMMEDIATE`.
- On Windows, antivirus scanners and backup tools can lock the file briefly; a generous `busy_timeout` covers them. Exclude the data folder from indexing if the app writes constantly.
- A checkpoint moves the WAL content into the main file. It runs automatically at about 1000 pages, but only when no reader holds an old snapshot; a process that keeps a read transaction open forever lets the `-wal` file grow without limit. `PRAGMA wal_checkpoint(TRUNCATE)` forces one when the app is idle.

## 5. aiosqlite in asyncio code

aiosqlite (0.22) runs each connection on its own thread and gives it an `async` API, so queries never block the event loop. `aiosqlite.connect` passes its keyword arguments to `sqlite3.connect`. The proxy has no `autocommit` attribute, so open it with `autocommit=True` and write transactions as SQL: with `autocommit=True` the `commit()` and `rollback()` methods do nothing, so a transaction opened with `BEGIN` and ended with `commit()` is lost when the connection closes.

```python
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite


@asynccontextmanager
async def transaction(db: aiosqlite.Connection) -> AsyncIterator[aiosqlite.Connection]:
    await db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        await db.execute("ROLLBACK")
        raise
    await db.execute("COMMIT")


async def main() -> tuple[list[str], int]:
    async with aiosqlite.connect("async.db", autocommit=True, timeout=10.0) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("CREATE TABLE IF NOT EXISTS job (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        async with transaction(db):
            await db.executemany("INSERT INTO job (name) VALUES (?)", [("a",), ("b",)])
        async with db.execute("SELECT id, name FROM job ORDER BY id") as cursor:
            names = [row["name"] async for row in cursor]
        rows = list(await db.execute_fetchall("SELECT count(*) AS n FROM job"))
        return names, rows[0]["n"]


asyncio.run(main())                                 # (['a', 'b'], 2)
```

- One aiosqlite connection serialises its calls on its thread. Concurrent tasks sharing it are safe but run one query at a time; a transaction held open across `await` points blocks every other task's queries on that connection.
- Always close it (`async with`), or the thread keeps the process alive and aiosqlite emits a `ResourceWarning`.
- Instead of aiosqlite, `await asyncio.to_thread(sync_function, args)` works when the sync function opens and closes its own connection.

## 6. In-memory databases for tests

`":memory:"` creates a private database per connection, which is fast and isolated, but a second connection sees an empty database. Name a shared in-memory database with a URI when a test needs two connections.

```python
uri = "file:testdb?mode=memory&cache=shared"
keeper = sqlite3.connect(uri, uri=True, autocommit=True)          # the database lives while one connection is open
keeper.execute("CREATE TABLE t (x INTEGER)")
other = sqlite3.connect(uri, uri=True, autocommit=True)
other.execute("SELECT count(*) FROM t").fetchone()[0]             # 0: same database, table visible
other.close()
keeper.close()
```

- For tests of code that takes a path, use pytest's `tmp_path / "test.db"`: a real file with WAL behaves like production, `:memory:` does not support WAL.
- Copy a prepared database into each test with `Connection.backup` instead of re-running migrations for every test.

## 7. Gotchas

These are the concurrency failures seen most with SQLite.

1. **`database is locked` under load even with WAL.** Too many writers retrying, or a long write transaction. Move writes to one writer thread and batch them.
2. **The `-wal` file grows to gigabytes.** A reader never ends its transaction, so checkpoints cannot finish. Close or commit long-lived read connections and run `wal_checkpoint(TRUNCATE)` when idle.
3. **An asyncio app freezes while saving.** `sqlite3` was called directly on the event loop. Use aiosqlite or `asyncio.to_thread`.
4. **A forked worker corrupts the database.** It used a connection opened by the parent. Open connections after the process starts.
5. **Tests pass with `:memory:` and fail on real files.** In-memory databases have no WAL and no locking between processes. Test with `tmp_path` files.
