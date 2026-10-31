---
name: python-sqlite
description: Uses SQLite from Python with the standard sqlite3 module correctly - opening connections with the right PRAGMAs, transactions with the autocommit attribute (3.12+) and with con blocks, BEGIN IMMEDIATE for writers, ? and :name parameters instead of f-strings, row_factory with sqlite3.Row, executemany for bulk inserts, WAL mode with busy_timeout and synchronous=NORMAL, foreign_keys per connection, one connection per thread, adapters and converters for dates, schema migrations with PRAGMA user_version, online backups with Connection.backup, JSON functions, FTS5 full-text search, and aiosqlite for asyncio code. Use when storing app data in a local SQLite file, fixing "database is locked" errors, making inserts faster, adding a schema migration, backing up a live database, searching text in SQLite, or calling SQLite from async code.
metadata:
  tags:
  - python
  - sqlite
  - sqlite3
  - database
  - sql
  - wal
  - fts5
  - aiosqlite
---

# SQLite from Python with sqlite3

SQLite is a database in one file, linked into Python through the `sqlite3` module. It suits desktop apps, CLIs, caches and tests, and fails in predictable ways: "database is locked", writes lost to a missing commit, slow inserts, dates back as strings. This skill gives the settings that avoid them.

## Step 0: read the Python and SQLite versions

The `sqlite3` module changes with Python, and the SQLite library under it is a separate version with its own features.

```bash
uv run python -c "import sys, sqlite3; print(sys.version_info[:2], sqlite3.sqlite_version)"
python3 -I scripts/sqlite_info.py app.db      # versions, compile options and the file's PRAGMAs
```

Without `scripts/` on disk (an install that keeps only searchable text), this reads the same facts from a file, opened read-only:

```bash
python3 -I - app.db <<'PY'
import pathlib, sqlite3, sys
print(sys.version_info[:3], sqlite3.sqlite_version)
con = sqlite3.connect(pathlib.Path(sys.argv[1]).resolve().as_uri() + "?mode=ro", uri=True)
for name in ("journal_mode", "synchronous", "user_version", "page_count"):
    print(name, con.execute(f"PRAGMA {name}").fetchone()[0])
print("quick_check", con.execute("PRAGMA quick_check").fetchone()[0])
con.close()
PY
```

| Version | Change that matters |
|---|---|
| Python 3.12 | `autocommit` attribute and `connect(autocommit=...)`; default date adapters and converters deprecated; `python -m sqlite3` shell |
| Python 3.14 | named placeholders with a sequence of parameters raise `ProgrammingError`; `sqlite3.version` removed (use `sqlite_version`) |
| Python 3.15 | every `connect()` argument except `database` is keyword-only; the shell completes keywords and table names on Tab |

python.org and uv builds bundle a recent SQLite with FTS5 (uv's CPython 3.15.2 has 3.53.1); Linux distribution Pythons link the system `libsqlite3`, which may be years older.

## Open a connection with the right settings

A connection is opened once per thread, configured with PRAGMAs, and closed explicitly. Set the PRAGMAs with `autocommit=True` and switch to `False` after them: in a transaction `journal_mode = WAL` fails and `foreign_keys = ON` is silently ignored. `with con:` manages a transaction, never closes the connection.

```python
import sqlite3
from contextlib import closing


def open_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, autocommit=True, timeout=5.0)    # timeout: wait up to 5 s for a lock
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")       # readers and one writer at the same time; stored in the file
    con.execute("PRAGMA synchronous = NORMAL")     # safe with WAL, much faster than FULL
    con.execute("PRAGMA foreign_keys = ON")        # off by default, per connection
    con.execute("PRAGMA busy_timeout = 5000")      # same as timeout=5.0, in milliseconds
    con.autocommit = False                         # from here on: PEP 249 transactions
    return con


with closing(open_db("app.db")) as con:
    con.execute("CREATE TABLE IF NOT EXISTS note (id INTEGER PRIMARY KEY, title TEXT NOT NULL)")
    con.commit()
```

- `autocommit=False` (3.12+): a transaction is always open and nothing is saved until `commit()`; closing without it rolls back.
- Keep the file in the per-user data folder (`platformdirs.user_data_dir(app)`), not beside the code.
- On Windows an open database file cannot be deleted or replaced.

## Transactions with the autocommit attribute

The `autocommit` attribute (Python 3.12+) decides who opens transactions; pick one mode per program.

| `autocommit` | Behaviour | Use for |
|---|---|---|
| `False` | a transaction is always open; `commit()` and `rollback()` end it and start the next | most application code |
| `True` | each statement commits on its own; you write `BEGIN` and `COMMIT` | `BEGIN IMMEDIATE`, PRAGMAs |
| `LEGACY_TRANSACTION_CONTROL` (default) | pre-3.12 `isolation_level` rules: implicit `BEGIN` before DML only | old code only |

The `with con:` block commits when the block succeeds and rolls back when it raises, so a group of writes lands together or not at all.

```python
con = open_db("app.db")
with con:                                          # commit on success, rollback on exception
    con.execute("INSERT INTO note (title) VALUES (?)", ("first",))
    con.execute("INSERT INTO note (title) VALUES (?)", ("second",))

try:
    with con:
        con.execute("INSERT INTO note (title) VALUES (?)", ("lost",))
        raise RuntimeError("something failed")     # the insert above is rolled back
except RuntimeError:
    pass
con.execute("SELECT count(*) FROM note").fetchone()[0]   # 2
```

### BEGIN IMMEDIATE for read-then-write

A deferred transaction (plain `BEGIN`) takes the write lock at its first write. Two connections that read and then write can deadlock, and one gets `database is locked` at once, ignoring `busy_timeout`. `BEGIN IMMEDIATE` takes the write lock up front.

```python
writer = sqlite3.connect("app.db", autocommit=True, timeout=5.0)


def rename_note(con: sqlite3.Connection, note_id: int, title: str) -> None:
    con.execute("BEGIN IMMEDIATE")                 # take the write lock now, waiting up to the timeout
    try:
        row = con.execute("SELECT title FROM note WHERE id = ?", (note_id,)).fetchone()
        if row is not None and row[0] != title:
            con.execute("UPDATE note SET title = ? WHERE id = ?", (title, note_id))
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


rename_note(writer, 1, "renamed")
writer.close()
```

### Read transactions stay open with autocommit=False

With `autocommit=False` a `SELECT` also runs in the always-open transaction, which in WAL mode reads one snapshot until it ends. The connection sees old data, and if another connection committed meanwhile, its next write fails at once with `database is locked`. End reads with `commit()` too.

```python
con.execute("SELECT title FROM note WHERE id = 1").fetchone()[0]   # 'first': the snapshot from before the rename
con.commit()                                       # ends the read transaction
con.execute("SELECT title FROM note WHERE id = 1").fetchone()[0]   # 'renamed'
con.commit()
```

## Parameters, never f-strings

Values travel as parameters: `?` with a tuple, or `:name` with a dict. An f-string puts input into the SQL text: SQL injection, and a syntax error on the first apostrophe.

```python
title = "O'Brien's list; DROP TABLE note"
con.execute("SELECT id FROM note WHERE title = ?", (title,)).fetchall()             # []: safe
con.execute("SELECT id FROM note WHERE title = :t", {"t": title}).fetchall()        # []: safe
# WRONG: con.execute(f"SELECT id FROM note WHERE title = '{title}'")  -> syntax error at best
```

- A single value still needs a tuple: `(title,)`, not `(title)`.
- `:name` with a tuple raises `ProgrammingError` since 3.14.
- Table and column names cannot be parameters: check them against a fixed allow-list, then quote them with double quotes.
- `IN` lists: generate one `?` per value (`",".join("?" * len(ids))`) and pass the values as parameters.

## Rows as sqlite3.Row or dicts

`row_factory` decides what each fetched row is. `sqlite3.Row` reads by name or by index with little overhead; a small factory gives plain dicts for JSON output.

```python
row = con.execute("SELECT id, title FROM note ORDER BY id").fetchone()   # con.row_factory is sqlite3.Row
row["title"], row[0], row.keys()                    # ('renamed', 1, ['id', 'title'])

cur = con.execute("SELECT id, title FROM note ORDER BY id")
cur.row_factory = lambda c, values: dict(zip([d[0] for d in c.description], values))   # this cursor only
cur.fetchall()[1]                                   # {'id': 2, 'title': 'second'}
```

## executemany for bulk inserts

`executemany` runs one statement for every item of an iterable, inside the current transaction. Inserting many rows is fast only when they share one transaction: a commit per row forces a disk sync per row.

```python
rows = ((f"note {i}",) for i in range(10_000))        # any iterable, a generator streams
with con:
    con.executemany("INSERT INTO note (title) VALUES (?)", rows)
con.execute("SELECT count(*) FROM note").fetchone()[0]   # 10002
```

- For millions of rows, commit every 10,000 to 100,000 rows (`itertools.batched`) so the WAL file stays bounded.
- `INSERT ... ON CONFLICT (key) DO UPDATE` is the upsert; `RETURNING id` (SQLite 3.35+) returns new keys.

## WAL mode and the PRAGMAs that matter

PRAGMAs are SQLite's settings; some live in the file and some in each connection. Write-ahead logging (`journal_mode=WAL`) lets readers keep reading while one writer writes, where the default rollback journal blocks them.

| PRAGMA | Scope | Recommended | Note |
|---|---|---|---|
| `journal_mode` | file | `WAL` | returns the new mode as a row: check it |
| `synchronous` | connection | `NORMAL` with WAL | may lose the last commits on power loss, never corrupts; `FULL` keeps every commit |
| `foreign_keys` | connection | `ON` | off by default; ignored inside an open transaction |
| `busy_timeout` | connection | `5000` | wait for a lock (ms) instead of failing at once; `connect(timeout=5.0)` is the same |
| `user_version` | file | your schema version | an integer you own |
| `optimize` | connection | run before close | updates query planner statistics cheaply |

- WAL adds `app.db-wal` and `app.db-shm` beside the file; move or delete the three together.
- WAL needs shared memory, so it fails on network filesystems (SMB, NFS). Keep the file on a local disk.
- One writer at a time: keep write transactions short, never open across a network call or a dialog.
- `foreign_keys` and `journal_mode` do nothing or fail inside a transaction, and with `autocommit=False` the module runs `BEGIN` inside `connect()` (`con.in_transaction` is `True` at once). Set PRAGMAs in autocommit mode.

## Dates and custom types with adapters and converters

SQLite has no date type: store instants as ISO 8601 text in UTC (sorts correctly) or as integer Unix seconds. The default adapters and converters are deprecated since 3.12, so register your own at start-up. Time zone rules are the python-datetime skill.

```python
from datetime import UTC, datetime

sqlite3.register_adapter(datetime, lambda d: d.astimezone(UTC).isoformat())
sqlite3.register_converter("timestamp", lambda b: datetime.fromisoformat(b.decode()))

tcon = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES, autocommit=False)
tcon.execute("CREATE TABLE event (at timestamp NOT NULL)")
tcon.execute("INSERT INTO event VALUES (?)", (datetime(2026, 10, 28, 9, 15, tzinfo=UTC),))
tcon.execute("SELECT at FROM event").fetchone()[0]   # datetime.datetime(2026, 10, 28, 9, 15, tzinfo=datetime.timezone.utc)
tcon.close()
```

- `PARSE_DECLTYPES` converts by declared column type, `PARSE_COLNAMES` by an alias (`SELECT at AS "at [timestamp]"`); without either, values are `str`.
- Adapters are global to the process: register them once, in the module that opens the database.

## Schema migrations with user_version

`PRAGMA user_version` is an integer stored in the database header that SQLite never touches, which makes it a migration counter with no extra table. Each migration runs in one transaction together with the version bump.

```python
MIGRATIONS: list[str] = [
    "CREATE TABLE IF NOT EXISTS note (id INTEGER PRIMARY KEY, title TEXT NOT NULL)",
    "ALTER TABLE note ADD COLUMN body TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX note_title ON note (title)",
]


def migrate(con: sqlite3.Connection) -> int:
    current = con.execute("PRAGMA user_version").fetchone()[0]
    if current > len(MIGRATIONS):
        raise RuntimeError(f"database schema {current} is newer than this app ({len(MIGRATIONS)})")
    for version, ddl in enumerate(MIGRATIONS[current:], start=current + 1):
        with con:                                   # DDL and the version bump commit together
            con.execute(ddl)
            con.execute(f"PRAGMA user_version = {version}")   # PRAGMAs take no parameters; version is ours
    return len(MIGRATIONS)


migrate(con)                                       # 3
```

- Never edit a migration that has shipped: add a new one. A user's file may be at any version.
- `ALTER TABLE` cannot change a column's type or constraints; the table-rebuild recipe is in [schema-types-and-backups.md](references/schema-types-and-backups.md). With SQLAlchemy models, Alembic's batch mode does it (the alembic-migrations skill).

## Backups with Connection.backup

`Connection.backup` copies a live database page by page while others keep using it; `shutil.copy` during writes can give a corrupt copy and misses the `-wal` file. Progress, `VACUUM INTO` and restore are in [schema-types-and-backups.md](references/schema-types-and-backups.md).

```python
with closing(sqlite3.connect("app-backup.db")) as target:
    con.backup(target, pages=1024)                 # 1024 pages per step, yields to writers between steps
con.close()
```

## Verify

Prove the settings on a real connection: `foreign_keys` is per connection, so only the connection your code opens can answer. `my_app.db` stands for the module holding `open_db` and `migrate`.

```python
# tests/test_db.py
import sqlite3
from pathlib import Path

import pytest

from my_app.db import MIGRATIONS, migrate, open_db


def test_settings_and_migrations(tmp_path: Path) -> None:
    con = open_db(str(tmp_path / "app.db"))
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert migrate(con) == migrate(con) == len(MIGRATIONS)       # a second run changes nothing
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.commit()
    con.close()


def test_hostile_value_stays_a_value(tmp_path: Path) -> None:
    con = open_db(str(tmp_path / "app.db"))
    migrate(con)
    title = "x'); DROP TABLE note; --"
    with con:
        con.execute("INSERT INTO note (title) VALUES (?)", (title,))
    assert con.execute("SELECT title FROM note").fetchone()[0] == title
    con.close()


def test_failed_block_rolls_back(tmp_path: Path) -> None:
    con = open_db(str(tmp_path / "app.db"))
    migrate(con)
    with pytest.raises(sqlite3.IntegrityError), con:
        con.execute("INSERT INTO note (title) VALUES (?)", ("kept out",))
        con.execute("INSERT INTO note (title) VALUES (NULL)")
    assert con.execute("SELECT count(*) FROM note").fetchone()[0] == 0
    con.close()
```

```bash
uv run pytest -q tests/test_db.py -W error::ResourceWarning     # all pass, no unclosed connection
grep -rnE 'execute\(f"' src                                      # expect only PRAGMAs with your own integers
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `isolation_level=None` or legacy transaction rules in new code | `autocommit=True` for PRAGMAs, then `autocommit=False` |
| one global connection shared by threads | one per thread, or a single writer thread |
| plain `BEGIN` for read-then-write | `BEGIN IMMEDIATE` on an autocommit connection |
| `time.sleep` retries around `database is locked` | `busy_timeout`, short transactions, one writer |
| `shutil.copy` of a live database | `Connection.backup` or `VACUUM INTO` |
| a database on SMB or NFS | a local disk; a server database when several machines write |

## Checklist

SQLite code is correct when every item holds.

- [ ] One `open_db` sets WAL, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout`, then `autocommit=False`.
- [ ] Connections are closed explicitly; each thread has its own, or one writer thread owns all writes.
- [ ] Every value is a parameter; identifiers come from an allow-list.
- [ ] Related writes share one `with con:`; bulk inserts use `executemany`; read-then-write uses `BEGIN IMMEDIATE`.
- [ ] Dates are UTC ISO text or integers with registered adapters.
- [ ] Migrations bump `user_version` in the same transaction; backups use `Connection.backup`.

## Gotchas

These are the SQLite problems seen most.

1. **Data disappears after the program exits.** No `commit()` with `autocommit=False` or legacy mode. Use `with con:` around writes.
2. **`sqlite3.OperationalError: database is locked`.** Another connection holds the write lock longer than the timeout, or two deferred transactions upgraded to writes. Keep writes short, use WAL, `BEGIN IMMEDIATE` for read-then-write, and one writer.
3. **`ProgrammingError: SQLite objects created in a thread can only be used in that same thread`.** A shared connection. Open one per thread, or send writes to one thread.
4. **Foreign keys are not enforced.** `foreign_keys` is off by default, per connection, and ignored inside a transaction, which `autocommit=False` opens at `connect()`. Set it in autocommit mode, then switch.
5. **100,000 inserts take minutes.** One commit per row. Use one transaction with `executemany`.
6. **Corruption on a network share.** WAL and file locks do not work over SMB or NFS. Keep the file local.
7. **`ResourceWarning: unclosed database` (3.13+).** `with con:` does not close. Use `closing()` or `close()`.
8. **A long-lived connection shows old rows, then its first write fails with `database is locked`.** With `autocommit=False` an earlier `SELECT` left a read transaction open on an old WAL snapshot. Call `commit()` after reads too, or read on a separate autocommit connection.
9. **A copied database lacks recent rows.** The `-wal` file was left behind. Use `Connection.backup`.

## References

The reference files hold the longer recipes.

- [json-and-fts5.md](references/json-and-fts5.md): JSON functions, `->>`, indexes on JSON fields, FTS5 tables kept in sync with triggers, ranking with bm25, snippets and safe query syntax
- [async-and-concurrency.md](references/async-and-concurrency.md): aiosqlite, connection per thread, the single-writer queue, multiple processes, in-memory databases for tests
- [schema-types-and-backups.md](references/schema-types-and-backups.md): STRICT tables and type affinity, rebuilding a table to change a column, online backups with progress, VACUUM INTO, restore and integrity checks
- `scripts/sqlite_info.py`: Python and SQLite versions, build options, and a file's PRAGMAs, tables and `quick_check` (read-only)

## See also

- `sqlalchemy-orm`: the ORM on the same file, with typed models
- `alembic-migrations`: migrations once models live in SQLAlchemy
- `python-datetime`: UTC instants and time zones
- `psycopg-postgres`: when several machines or heavy writers outgrow one file
