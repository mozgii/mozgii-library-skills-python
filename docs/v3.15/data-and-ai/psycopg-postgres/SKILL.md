---
name: psycopg-postgres
description: Talks to PostgreSQL from Python with psycopg 3, sync and async - installing the binary or C build, an explicit conninfo with timeouts and application_name, transactions with conn.transaction() and savepoints, %s and %(name)s parameters or t-string queries instead of f-strings, sql.Identifier and sql.SQL for dynamic table and column names, row factories (dict_row, class_row, scalar_row), connection pools with psycopg_pool, error classes such as UniqueViolation and SerializationFailure, type adaptation for jsonb, uuid, numeric and timestamptz, COPY for bulk loads, server-side cursors, pipeline mode, prepared statements and PgBouncer, LISTEN/NOTIFY, and testing against a real Postgres. Use when connecting a Python program to Postgres, migrating from psycopg2, writing safe dynamic SQL, loading many rows fast, streaming a large result, setting up a pool, handling a duplicate-key error, or getting notifications from the database.
metadata:
  tags:
  - python
  - postgresql
  - psycopg
  - psycopg-pool
  - database
  - sql
  - asyncio
  - copy
---

# PostgreSQL from Python with psycopg 3

psycopg 3 (package `psycopg`) is the current PostgreSQL driver for Python: sync and async connections, server-side parameter binding, binary COPY, pipeline mode and typed row factories. New code uses psycopg 3 rather than psycopg2, and SQLAlchemy 2.1 picks it by default for `postgresql://` URLs. This skill covers connections, transactions, safe SQL, pools, errors and types. Bulk loads, streaming and pipelines are in [bulk-and-streaming.md](references/bulk-and-streaming.md); LISTEN/NOTIFY and tests are in [notify-and-testing.md](references/notify-and-testing.md). A worker process that gets its conninfo from a host program is the python-stdio-worker skill.

## Step 0: read the versions

psycopg, its libpq and the server each have a version, and features depend on all three.

```bash
uv run python -c "import psycopg; print(psycopg.__version__, psycopg.pq.__impl__, psycopg.pq.version())"
uv run python -c "import psycopg_pool; print(psycopg_pool.__version__)"
uv run python -c "import psycopg; c = psycopg.connect('dbname=app'); print(c.info.server_version); c.close()"
```

| Version | Change that matters |
|---|---|
| psycopg 3.2 | `scalar_row`; `notifies(timeout=, stop_after=)`; `stream(size=)` chunks; `as_string()` without a connection; numpy scalars adapted |
| psycopg 3.3 | template-string queries (`t"..."`, Python 3.14+); cursors are iterators (`next(cur)`); `Cursor.results()` after `executemany(returning=True)`; Python 3.10+ only |
| psycopg_pool 3.2+ | warns when a sync pool is opened implicitly in the constructor; pass `open=` or use `with` |
| psycopg_pool 3.3 | `drain()`; `conninfo` and `kwargs` may be callables, for rotating credentials |

`pq.__impl__` is `binary` (bundled libpq), `c` (system libpq) or `python` (pure Python, slowest); `pq.version()` is libpq's version, `180006` for 18.6.

## Install the right build

Applications install `psycopg[binary]`: its wheels for macOS, Linux and Windows carry their own libpq. Libraries depend on plain `psycopg` and let the application choose. `psycopg[c]` builds against the system libpq and needs `pg_config` and a compiler.

| Install | Use when | Why the others lose here |
|---|---|---|
| `psycopg[binary]` | an application | no compiler or system libpq needed on any OS |
| `psycopg` | a library | a binary pin would choose the build for every application using it |
| `psycopg[c]` | you must link the system libpq | it needs `pg_config` and a compiler at install time |

```bash
uv add "psycopg[binary,pool]"          # driver with bundled libpq, plus psycopg_pool
```

## Connect with an explicit conninfo

A conninfo string or keyword arguments name every connection setting. Write them out, with a connect timeout and an application name, instead of relying on whatever `PG*` variables happen to be set.

```python
from psycopg.conninfo import conninfo_to_dict, make_conninfo

conninfo = make_conninfo(
    "",
    host="db.internal", port=5432, dbname="app", user="app_rw",
    connect_timeout=5,                                # seconds, per host attempt
    application_name="invoice-sync",                  # shows in pg_stat_activity
    sslmode="verify-full",                            # check the certificate and the host name
    options="-c TimeZone=UTC -c statement_timeout=30s",
)
conninfo                                              # 'host=db.internal port=5432 dbname=app user=app_rw connect_timeout=5 application_name=invoice-sync sslmode=verify-full options=\'-c TimeZone=UTC -c statement_timeout=30s\''
conninfo_to_dict("postgresql://me@localhost:5433/app?sslmode=disable")["port"]   # '5433'
```

- Never put the password in code or a logged string. Pass `password=` from a secret store, or let libpq read the password file (`~/.pgpass`; `%APPDATA%\postgresql\pgpass.conf` on Windows).
- A `host` starting with `/` is a Unix-socket directory (macOS, Linux); Windows programs normally use TCP to `localhost`.
- `statement_timeout` in `options` caps every query; `TimeZone=UTC` returns `timestamptz` values in UTC.
- `sslmode=verify-full` checks the certificate and host name; `require` only encrypts.

## Transactions with conn.transaction()

`with psycopg.connect(...) as conn:` commits at the end of the block, rolls back on an exception, and closes the connection. Without `autocommit`, the first statement opens a transaction that lasts until `commit()`, so a long-lived connection is easily left "idle in transaction", holding locks. The clearer pattern is `autocommit=True` plus `conn.transaction()` blocks; a nested block becomes a savepoint.

```python
import psycopg
from psycopg import errors


def transfer(conn: psycopg.Connection, src: int, dst: int, cents: int) -> None:
    with conn.transaction():                          # BEGIN ... COMMIT, or ROLLBACK on exception
        conn.execute("UPDATE account SET cents = cents - %s WHERE id = %s", (cents, src))
        conn.execute("UPDATE account SET cents = cents + %s WHERE id = %s", (cents, dst))
        try:
            with conn.transaction():                  # SAVEPOINT: a failure here keeps the transfer
                conn.execute("INSERT INTO audit (src, dst, cents) VALUES (%s, %s, %s)", (src, dst, cents))
        except errors.UniqueViolation:
            pass                                      # the savepoint was rolled back; the outer block goes on


with psycopg.connect(conninfo, autocommit=True) as conn:
    transfer(conn, 1, 2, 500)
```

- `conn.transaction(force_rollback=True)` always rolls back: for dry runs and tests.
- Statements that cannot run in a transaction (`CREATE DATABASE`, `VACUUM`, `CREATE INDEX CONCURRENTLY`) need `autocommit=True` and no open `transaction()` block.
- After an error inside a transaction, every following statement fails with `InFailedSqlTransaction` until rollback. The `with` blocks handle that; manual code must call `conn.rollback()`.

## Parameters, never f-strings

psycopg sends values separately from the SQL text (server-side binding), so input can never change the statement. Placeholders are `%s` with a sequence and `%(name)s` with a mapping, for every type; a literal `%` in the SQL is written `%%`.

```python
user_input = "x'; DROP TABLE account; --"
query = "SELECT id FROM account WHERE owner = %s AND note LIKE %s"
params = (user_input, "%" + "refund" + "%")          # the wildcard lives in the value, not the SQL
# WRONG: f"SELECT id FROM account WHERE owner = '{user_input}'"
```

- `IN` with a list: `WHERE id = ANY(%s)` with the list as one parameter.
- Placeholders stand only for values. Table and column names and `ORDER BY` directions need `psycopg.sql`.

### Template-string queries (psycopg 3.3, Python 3.14+)

A t-string query puts each value where it is used and lets psycopg bind it safely; `:i` marks an identifier. `sql.as_string` shows the final text without a server.

```python
from psycopg import sql

name, table = "O'Reilly", "customer list"
sql.as_string(t"INSERT INTO {table:i} (name) VALUES ({name})")   # 'INSERT INTO "customer list" (name) VALUES (\'O\'\'Reilly\')'
```

Specifiers: none or `:s` for automatic format, `:b` binary, `:t` text, `:i` identifier, `:l` literal merged on the client, `:q` a nested template or `sql.SQL`. An `f` typed instead of `t` makes a plain `str` with no protection; Ruff's `S608` flags it.

## Dynamic SQL with sql.Identifier

`psycopg.sql` composes queries whose structure changes at run time: `sql.Identifier` quotes names, `sql.SQL` holds trusted SQL text, `sql.Placeholder` adds parameters, and `.join` builds lists.

```python
def select_columns(table: str, columns: list[str], allowed: frozenset[str]) -> sql.Composed:
    unknown = set(columns) - allowed
    if unknown:
        raise ValueError(f"unknown columns: {sorted(unknown)}")
    return sql.SQL("SELECT {cols} FROM {table} WHERE id = {id}").format(
        cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
        table=sql.Identifier("app", table),           # schema-qualified: "app"."table"
        id=sql.Placeholder(),
    )


q = select_columns("user", ["id", "Display Name"], frozenset({"id", "Display Name", "email"}))
q.as_string()                                         # 'SELECT "id", "Display Name" FROM "app"."user" WHERE id = %s'
```

Quoting stops injection; the allow-list stops reading a column the caller should not see.

## Row factories

A row factory turns each row into the type the code wants; set it per connection or per cursor. `dict_row` gives dicts, `class_row(Cls)` builds a dataclass or pydantic model by column name, `scalar_row` returns the first column only.

```python
from dataclasses import dataclass

from psycopg.rows import class_row, dict_row, scalar_row


@dataclass(frozen=True, slots=True)
class Account:
    id: int
    owner: str
    cents: int


with psycopg.connect(conninfo, autocommit=True) as conn:
    with conn.cursor(row_factory=class_row(Account)) as cur:
        accounts = cur.execute("SELECT id, owner, cents FROM account ORDER BY id").fetchall()
    total = conn.cursor(row_factory=scalar_row).execute("SELECT sum(cents) FROM account").fetchone()
    rows = conn.cursor(row_factory=dict_row).execute("SELECT id, owner FROM account").fetchall()
```

Type checkers follow the row factory: `fetchall()` above is typed `list[Account]`.

## Connection pools with psycopg_pool

A pool keeps connections open and lends one per unit of work. Create one pool per process at start-up, open it explicitly, and borrow with `with pool.connection()`, which commits or rolls back and returns the connection.

```python
from psycopg_pool import ConnectionPool


def configure(conn: psycopg.Connection) -> None:
    conn.autocommit = True                            # runs once for each new connection


pool = ConnectionPool(
    conninfo, min_size=2, max_size=10, open=False,
    configure=configure, check=ConnectionPool.check_connection,   # test a connection before lending it
    timeout=10.0, max_idle=300.0, name="app",
)
pool.open(wait=True, timeout=30.0)                    # fail at start-up, not on the first request
with pool.connection() as conn:
    conn.execute("SELECT 1")
pool.close()
```

- `max_size` times the number of processes stays below the server's `max_connections`.
- `PoolTimeout`: every connection was busy for `timeout` seconds; a connection held across slow work, or a pool too small.
- `AsyncConnectionPool` has the same API with `await`; open it inside the running event loop.
- Behind PgBouncer in transaction mode, pass `kwargs={"prepare_threshold": None}`.

## Errors and SQLSTATE classes

Every server error is a subclass of `psycopg.Error` named after its SQLSTATE, in `psycopg.errors`, so code catches exactly the failure it can handle.

```python
import psycopg
from psycopg import errors

errors.lookup("23505") is errors.UniqueViolation      # True
issubclass(errors.UniqueViolation, psycopg.IntegrityError)        # True
```

| Class | SQLSTATE | Typical handling |
|---|---|---|
| `UniqueViolation` | 23505 | report a duplicate; or use `INSERT ... ON CONFLICT` |
| `ForeignKeyViolation` | 23503 | the referenced row is missing |
| `SerializationFailure`, `DeadlockDetected` | 40001, 40P01 | retry the whole transaction a few times |
| `QueryCanceled` | 57014 | `statement_timeout` hit or cancelled |
| `OperationalError` (base) | various | connection lost: reconnect (a pool does it) |

`exc.diag.constraint_name` and `exc.sqlstate` say what failed without parsing the message. Retry only whole transactions; backoff is in the python-error-handling skill.

## Type adaptation

psycopg converts Python values to Postgres types and back. Most types work out of the box; a few need a wrapper or a setting.

| Python | PostgreSQL | Note |
|---|---|---|
| `dict`, `list` for JSON | `jsonb`, `json` | wrap with `Jsonb(value)`; a plain dict raises `cannot adapt type 'dict'` |
| `list` | array | `ANY(%s)` for membership |
| `uuid.UUID` | `uuid` | both directions |
| `Decimal` | `numeric` | `numeric` loads as `Decimal`, never `float` |
| aware `datetime` | `timestamptz` | loaded in the session `TimeZone` |
| naive `datetime` | `timestamp` | avoid for instants: no zone (the python-datetime skill) |

```python
import uuid
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

sql.Literal(Jsonb({"plan": "pro"})).as_string()       # '\'{"plan": "pro"}\'::jsonb'
sql.Literal(uuid.UUID(int=1)).as_string()             # "'00000000000000000000000000000001'::uuid"
sql.Literal(datetime(2026, 10, 28, 9, 15, tzinfo=UTC)).as_string()   # "'2026-10-28 09:15:00+00:00'::timestamptz"
```

## Async connections

`psycopg.AsyncConnection` has the same API with `await` and `async with`. Use it in asyncio programs; never call the sync driver from a coroutine.

```python
import asyncio
import sys


async def count_accounts() -> int:
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as aconn:
        cur = await aconn.execute("SELECT count(*) FROM account")
        row = await cur.fetchone()
        return row[0] if row else 0


loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
asyncio.run(count_accounts(), loop_factory=loop_factory)
```

On Windows, async psycopg refuses the default Proactor event loop; `loop_factory=asyncio.SelectorEventLoop` (3.12+) picks the one it needs.

## Verify

Prove the driver build, then run these attack cases against a scratch database: hostile values stay data, and a hostile name stays one identifier. `DATABASE_URL` must point at a throwaway database, never at production.

```bash
uv run python -c "import psycopg, psycopg_pool; print(psycopg.__version__, psycopg.pq.__impl__, psycopg_pool.__version__)"
grep -rnE "execute(many)?\(\s*f[\"']" src            # expect nothing: no f-string reaches execute
uv run ruff check --select S608 src                  # no SQL built from strings
uv run python attack_probe.py                        # prints ok
```

```python
# attack_probe.py
import os

import psycopg
from psycopg import sql

ATTACKS = ["x'; DROP TABLE account; --", "' OR '1'='1", 'a"b', "%s", "\\'; SELECT 1; --"]

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
    conn.execute("CREATE TEMP TABLE account (owner text)")
    for attack in ATTACKS:
        conn.execute("INSERT INTO account (owner) VALUES (%s)", (attack,))
    rows = conn.execute("SELECT owner FROM account WHERE owner = %s", ("' OR '1'='1",)).fetchall()
    assert rows == [("' OR '1'='1",)], rows              # the quote matched itself, not every row
    stored = conn.execute("SELECT count(*) FROM account").fetchone()
    assert stored == (len(ATTACKS),), stored             # the table survived with every value in it
    name = sql.Identifier('a"; DROP TABLE account; --')
    assert name.as_string() == '"a""; DROP TABLE account; --"'
    try:
        conn.execute("SELECT %s", ("a\x00b",))
    except psycopg.DataError:
        print("ok")                                      # PostgreSQL text has no NUL: refused, not cut short
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `f"... WHERE id = {user_id}"` | `%s` with a parameter |
| a table name from a request in the SQL text | `sql.Identifier` plus an allow-list |
| one connection shared by threads doing transactions | `ConnectionPool`, one borrow per unit of work |
| a pool opened implicitly in the constructor | `open=False`, then `pool.open(wait=True)` at start-up |
| catching `psycopg.Error` for everything | the exact class, such as `errors.UniqueViolation` |
| a password in the conninfo string in code | a secret store or `~/.pgpass` |
| retrying one statement after a serialization failure | retrying the whole transaction |
| the sync driver inside a coroutine | `AsyncConnection` |

## Checklist

psycopg code is correct when every item holds.

- [ ] Explicit conninfo with `connect_timeout`, `application_name`, `sslmode`; no password in code or logs.
- [ ] Writes run in `conn.transaction()` blocks; no connection stays idle in transaction.
- [ ] Values are parameters or t-string fields; names go through `sql.Identifier` and an allow-list.
- [ ] One pool per process, opened at start-up with `wait=True`; errors caught by class.
- [ ] JSON through `Jsonb`, instants in `timestamptz`, money as `Decimal`; bulk loads use COPY.
- [ ] Tests run against a real Postgres of the production major version.

## Gotchas

These are the psycopg problems seen most.

1. **`cannot adapt type 'dict'`.** psycopg does not guess JSON. Wrap with `Jsonb(...)`.
2. **The database shows sessions "idle in transaction" holding locks.** A non-autocommit connection ran a `SELECT` and never committed. Use `autocommit=True` with `transaction()` blocks, or commit after reads.
3. **`InFailedSqlTransaction: current transaction is aborted`.** An earlier statement failed in the same transaction. Roll back, or wrap the risky statement in a nested `transaction()` (a savepoint).
4. **`ProgrammingError: only '%s', '%b', '%t' are allowed as placeholders, got '%''`** for a `LIKE 'a%'`. With parameters, a literal `%` must be `%%`; better, put the pattern in the parameter.
5. **`prepared statement "_pg3_0" does not exist` behind PgBouncer.** Transaction pooling drops prepared statements. Set `prepare_threshold=None`.
6. **Async code fails on Windows.** The Proactor loop. Use `loop_factory=asyncio.SelectorEventLoop`.
7. **Times are off by the server's offset.** Naive datetimes went into `timestamp` columns, or the session `TimeZone` differs. Use aware values and `timestamptz`; set `TimeZone=UTC` in `options`.

## References

The reference files hold the longer recipes.

- [bulk-and-streaming.md](references/bulk-and-streaming.md): COPY in and out with `write_row` and binary format, `executemany` with `returning`, pipeline mode, server-side cursors and `stream()`, prepared statements
- [notify-and-testing.md](references/notify-and-testing.md): LISTEN/NOTIFY with `notifies()`, async listeners, payload limits, testing with a throwaway cluster, per-test rollback and template databases

## See also

- `sqlalchemy-orm`: an ORM on top of psycopg
- `alembic-migrations`: schema changes over time
- `python-stdio-worker`: a worker that receives its conninfo from a host program
- `python-error-handling`: backoff for retried transactions
- `python-datetime`: instants and `timestamptz`
- `python-security`: injection and secrets in general
