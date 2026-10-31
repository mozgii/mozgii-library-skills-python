---
title: SQLite schema changes, column types and backups
author: MOZGII, Inc.
lang: en
---

# SQLite schema changes, column types and backups

How SQLite types really work (affinity and STRICT tables), how to change a column that `ALTER TABLE` cannot change, and how to back up, restore and check a database file that an app is using.

## Contents

This reference has 7 sections.

1. Type affinity and why STRICT tables help
2. Rebuilding a table to change a column
3. Online backups with progress
4. VACUUM INTO for a compact copy
5. Restoring a backup
6. Integrity checks
7. Gotchas

## 1. Type affinity and why STRICT tables help

SQLite column types are only a preference, called affinity: an ordinary table stores `'abc'` in an `INTEGER` column without complaint. `STRICT` tables (SQLite 3.37+) check every value against the declared type and accept only `INTEGER`, `REAL`, `TEXT`, `BLOB` and `ANY`.

```python
import sqlite3
from contextlib import closing

con = sqlite3.connect(":memory:", autocommit=True)
con.execute("CREATE TABLE loose (n INTEGER)")
con.execute("INSERT INTO loose VALUES ('abc')")
con.execute("SELECT typeof(n) FROM loose").fetchone()[0]          # 'text': stored as given

con.execute("CREATE TABLE tight (n INTEGER) STRICT")
try:
    con.execute("INSERT INTO tight VALUES ('abc')")
except sqlite3.IntegrityError as exc:
    str(exc)                                        # 'cannot store TEXT value in INTEGER column tight.n'
con.execute("INSERT INTO tight VALUES ('42')")      # a string that is a clean integer is converted
con.execute("SELECT typeof(n) FROM tight").fetchone()[0]          # 'integer'
```

- A `STRICT` table cannot declare `DATETIME` or `BOOLEAN`; store dates as `TEXT` (ISO 8601) or `INTEGER` (Unix seconds) and booleans as `INTEGER` with `CHECK (flag IN (0, 1))`.
- With a STRICT table, `PARSE_DECLTYPES` converters keyed on custom type names cannot be used; convert in the data layer instead, or use `PARSE_COLNAMES` aliases.
- `INTEGER PRIMARY KEY` is the rowid itself: the fastest key, assigned automatically. Add `AUTOINCREMENT` only if deleted ids must never be reused.

## 2. Rebuilding a table to change a column

`ALTER TABLE` can rename a table, add, rename and drop a column (3.35+), but cannot change a column's type, `NOT NULL`, `CHECK` or foreign keys. The supported method is to build a new table and copy, inside one transaction, with foreign key enforcement off.

```python
con.executescript("""
CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT, age TEXT);
INSERT INTO person (name, age) VALUES ('Ann', '34'), ('Bo', '29');
""")


def rebuild_person(con: sqlite3.Connection) -> None:
    """Make age an INTEGER NOT NULL. The connection is in autocommit mode."""
    con.execute("PRAGMA foreign_keys = OFF")        # only works outside a transaction
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("CREATE TABLE person_new (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                    "age INTEGER NOT NULL CHECK (age >= 0)) STRICT")
        con.execute("INSERT INTO person_new (id, name, age) SELECT id, name, CAST(age AS INTEGER) FROM person")
        con.execute("DROP TABLE person")
        con.execute("ALTER TABLE person_new RENAME TO person")
        # recreate indexes, triggers and views that referred to person here
        problems = con.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"foreign key violations after rebuild: {problems[:5]}")
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    finally:
        con.execute("PRAGMA foreign_keys = ON")


rebuild_person(con)
con.execute("SELECT typeof(age), sum(age) FROM person").fetchone()   # ('integer', 63)
```

- Indexes, triggers and views on the old table are dropped with it; list them first with `SELECT sql FROM sqlite_schema WHERE tbl_name = 'person' AND type IN ('index', 'trigger')` and recreate them.
- Do the rebuild as one numbered migration (the `user_version` runner in SKILL.md), so a crash halfway leaves the old schema intact.
- Alembic's batch mode generates the same steps from SQLAlchemy models (the alembic-migrations skill).

## 3. Online backups with progress

`Connection.backup` copies the database page by page into another connection. Between steps it releases the lock, so the app keeps writing; if the source changes during the copy, SQLite restarts the copy, and the result is always a consistent snapshot.

```python
def backup_with_progress(source: sqlite3.Connection, target_path: str) -> None:
    def progress(status: int, remaining: int, total: int) -> None:
        print(f"copied {total - remaining} of {total} pages")

    with closing(sqlite3.connect(target_path)) as target:
        source.backup(target, pages=256, progress=progress, sleep=0.05)


src = sqlite3.connect("live.db", autocommit=True)
src.execute("CREATE TABLE IF NOT EXISTS big (x BLOB)")
src.executemany("INSERT INTO big VALUES (randomblob(4000))", [()] * 500)
backup_with_progress(src, "live-backup.db")
```

- `pages=-1` (the default) copies everything in one step and holds the read lock for the whole copy; a positive number gives writers a chance between steps.
- Write the backup to a temporary name in the same folder and rename it when done, so a crash never leaves a half-written file under the real name (`os.replace` works on Windows too).
- The target must not be in use by other connections during the backup.

## 4. VACUUM INTO for a compact copy

`VACUUM INTO 'file'` writes a compacted, defragmented copy of the database in one statement. It is a transaction-consistent snapshot like `backup`, usually smaller, and refuses to overwrite an existing file.

```python
import os

if os.path.exists("compact.db"):
    os.remove("compact.db")
src.execute("VACUUM INTO ?", ("compact.db",))       # the file name can be a parameter
os.path.getsize("compact.db") <= os.path.getsize("live.db")       # True
```

Use `VACUUM INTO` for exports and archives; use `backup` when the copy must not block writers for long on a large file, or when the target is an existing connection such as `:memory:`.

## 5. Restoring a backup

Restoring copies the backup into the live database with `backup` in the other direction, which keeps the file name, permissions and WAL state correct. Close every other connection to the live database first.

```python
with closing(sqlite3.connect("live-backup.db")) as backup_con:
    backup_con.backup(src)                          # overwrite live.db's content with the backup
src.execute("SELECT count(*) FROM big").fetchone()[0]             # 500
src.close()
```

Replacing the file on disk instead (`os.replace(backup, live)`) works only with no connection open, and must also delete the old `-wal` and `-shm` files, or SQLite replays the old WAL into the restored file.

## 6. Integrity checks

`PRAGMA integrity_check` reads the whole file and returns one row `ok`, or a list of problems. `PRAGMA quick_check` skips index-content checks and is much faster on large files.

```python
with closing(sqlite3.connect("live-backup.db")) as check:
    check.execute("PRAGMA integrity_check").fetchall()   # [('ok',)]
    check.execute("PRAGMA foreign_key_check").fetchall() # []: no rows means no violations
```

Run `quick_check` on start-up only if the file is small or the app was killed during a write, and `integrity_check` on every backup before trusting it.

## 7. Gotchas

These are the schema and backup problems seen most.

1. **A value of the wrong type sits in a column.** Ordinary tables only prefer types. Use `STRICT` for new tables.
2. **`PRAGMA foreign_keys = OFF` had no effect during a rebuild.** It was set inside a transaction. Set it before `BEGIN`.
3. **Indexes vanished after a migration.** The rebuild dropped the old table with its indexes. Recreate them from `sqlite_schema`.
4. **A restored database contains rows that were deleted before the backup.** An old `-wal` file was replayed. Restore with `backup`, or delete `-wal` and `-shm` with the file.
5. **`VACUUM INTO` fails with "output file already exists".** It never overwrites. Delete or rename the target first.
