---
title: Running and testing Alembic migrations
author: MOZGII, Inc.
lang: en
---

# Running and testing Alembic migrations

When and how migrations run: at deploy for servers, at start-up for desktop apps and CLIs that own their database, from code through Alembic's command API with a shared connection, from inside an installed package, and how to adopt Alembic on a database that already exists. Then the tests that keep migrations honest: upgrade and downgrade, `alembic check`, and the stairway test. Every example ran against SQLite with Alembic 1.20 and SQLAlchemy 2.1.

## Contents

This reference has 8 sections.

1. At deploy or at start-up
2. The command API with a shared connection
3. Migrations shipped inside a package
4. Upgrading at start-up safely
5. One migrator at a time
6. Adopting Alembic on an existing database
7. Testing migrations
8. Gotchas

## 1. At deploy or at start-up

Where migrations run depends on who owns the database.

| Situation | Run migrations | Why |
|---|---|---|
| a server app with several instances | as a separate deploy step (`alembic upgrade head` in the release job), before new instances start | one runner, a visible failure that stops the rollout, no race between instances |
| a desktop app or CLI with its own SQLite or embedded Postgres | at start-up, before opening any window or handling a command | the user never runs a separate tool; app and schema upgrade together |
| a database shared with other teams | offline `--sql` reviewed by its owner | someone else applies it |

Never let several app instances each upgrade at start: they race on the same revision. If start-up migration is unavoidable on a server, serialise it (section 5).

## 2. The command API with a shared connection

`alembic.command` exposes every CLI command as a function taking a `Config`. Passing an open connection through `config.attributes["connection"]` makes `env.py` use it instead of creating its own engine (the `env.py` in SKILL.md checks for it).

```python
from importlib.resources import files

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine


def alembic_config() -> Config:
    cfg = Config()                                   # no ini file: env.py skips fileConfig, app logging survives
    cfg.set_main_option("script_location", str(files("app") / "migrations"))
    return cfg


def upgrade(engine: Engine, target: str = "head") -> None:
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, target)


# SQLite only, Python 3.12+: without this a failed upgrade is not rolled back.
sqlite_engine = create_engine("sqlite:///app.db", connect_args={"autocommit": False})
```

- `engine.begin()` commits when the block ends. On PostgreSQL a failure rolls back the whole upgrade.
- SQLite can roll back DDL, but with the driver's default transaction handling it does not: a failed upgrade left the tables already made and an empty `alembic_version`. With `connect_args={"autocommit": False}` (Python 3.12+) the same failure rolled back cleanly. On Python below 3.12 take a copy of the file first.
- Other commands work the same way: `command.downgrade(cfg, "-1")`, `command.current(cfg)`, `command.check(cfg)`, `command.stamp(cfg, "head")`.

## 3. Migrations shipped inside a package

An installed app has no `alembic.ini` beside it, so the migrations folder goes inside the package (`src/app/migrations/` with `env.py`, `script.py.mako` and `versions/`) and the code finds it with `importlib.resources`. Check that the build backend includes the non-Python file `script.py.mako` (the python-packaging skill).

- `files("app") / "migrations"` is a real directory for packages installed from a wheel, which is what Alembic needs. Zip imports are not supported.
- Keep `alembic.ini` (or `[tool.alembic]`) in the repository for developers, with `script_location = %(here)s/src/app/migrations`, so the CLI and the app use the same folder.
- In frozen apps (PyInstaller, Briefcase), add the migrations folder as data files and point `script_location` at its unpacked path.

## 4. Upgrading at start-up safely

A desktop app upgrades its own database at start: back it up first, refuse a database written by a newer version, then upgrade. The current revision comes from `MigrationContext`, the known revisions from `ScriptDirectory`.

```python
from alembic.util import CommandError


class SchemaTooNew(RuntimeError):
    """The database was migrated by a newer version of the app."""


def migrate_at_start(engine: Engine) -> str | None:
    script = ScriptDirectory.from_config(alembic_config())
    head = script.get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current is not None:
        try:
            script.get_revision(current)
        except CommandError as exc:                  # "Can't locate revision identified by ..."
            raise SchemaTooNew(f"database revision {current} is unknown to this app; update the app") from exc
    if current != head:
        # back up here first (for SQLite: sqlite3.Connection.backup, the python-sqlite skill)
        upgrade(engine)
    return head
```

- A downgrade of an end user's database is rarely the right answer to `SchemaTooNew`; tell the person to update the app.
- `get_current_head()` raises if the code has several heads; CI should never let that ship (section 7).

## 5. One migrator at a time

On PostgreSQL, an advisory lock taken at the start of the migration transaction makes concurrent runners wait for each other instead of colliding. Put it in `run_with` in `env.py`, inside `begin_transaction()`.

```python
from sqlalchemy import text

MIGRATION_LOCK = 72_350_001                          # any constant bigint, the same in every process


def run_with_lock(connection, context, target_metadata) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": MIGRATION_LOCK})
        context.run_migrations()                     # the second runner finds nothing left to do
```

The lock is released when the transaction ends. SQLite needs no lock: its single writer serialises the runners, and the second one sees the new revision.

## 6. Adopting Alembic on an existing database

A database created by `metadata.create_all()` or by hand has no `alembic_version` table. Make the first revision describe that existing schema, then mark the database as being at it without running anything.

```bash
uv run alembic revision --autogenerate -m "baseline"   # against an EMPTY database: the full schema
uv run alembic stamp head                              # against the existing database: record, do not run
uv run alembic check                                   # should report no differences
```

- Generate the baseline against an empty database, so it contains every table; generated against the existing one it would be empty.
- `stamp` is also how to recover after fixing a database by hand: `alembic stamp <revision>` records where it now stands.
- Once Alembic owns the schema, remove `create_all()` from production code; keep it only in tests that do not test migrations.

## 7. Testing migrations

Three tests catch nearly every migration bug: the full chain runs up, down and up again; the models match the migrations; and every single revision can be applied and reverted on its own. Each test uses a fresh SQLite file; run the same tests against PostgreSQL in CI when production uses it.

```python
from pathlib import Path

import pytest


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return create_engine(f"sqlite:///{tmp_path / 'migrations.db'}")


def run(engine: Engine, fn, *args: str) -> None:
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        fn(cfg, *args)


def test_upgrade_downgrade_upgrade(engine: Engine) -> None:
    run(engine, command.upgrade, "head")
    run(engine, command.downgrade, "base")
    run(engine, command.upgrade, "head")


def test_models_match_migrations(engine: Engine) -> None:
    run(engine, command.upgrade, "head")
    run(engine, command.check)                       # raises AutogenerateDiffsDetected on drift


def test_stairway(engine: Engine) -> None:
    """Each revision upgrades, downgrades and upgrades again on its own (linear history)."""
    script = ScriptDirectory.from_config(alembic_config())
    for rev in reversed(list(script.walk_revisions())):   # oldest first
        run(engine, command.upgrade, rev.revision)
        run(engine, command.downgrade, rev.down_revision or "base")
        run(engine, command.upgrade, rev.revision)


def test_single_head() -> None:
    assert len(ScriptDirectory.from_config(alembic_config()).get_heads()) == 1
```

- `command.check` fails with `AutogenerateDiffsDetected: New upgrade operations detected: [('add_table', ...)]` when a model changed without a revision. It cannot see what autogenerate misses (renames, CHECK constraints), so review still matters.
- The stairway test catches downgrades that do not undo their upgrade, which the full-chain test can miss when a later revision hides the damage. With merge revisions, walk only the linear part or upgrade and downgrade by `+1` and `-1` from each head.
- Add a data test for each data migration: insert rows at the revision before, upgrade one step, assert the result.

## 8. Gotchas

These are the problems seen most when running migrations from code and in tests.

1. **Logging stops working after migrations run in the app.** `env.py` called `fileConfig` with the default `disable_existing_loggers=True`. Use a `Config()` without a file in app code.
2. **`Can't locate revision identified by 'abc123'`.** The database was migrated by a newer build, or a revision file was deleted. Ship every revision; refuse to start on unknown revisions.
3. **Two instances both ran the same migration and one failed.** Start-up migrations on a multi-instance server. Migrate in the deploy step, or take an advisory lock.
4. **`check` passes but production differs.** The difference is one autogenerate cannot detect, or the test ran on SQLite while production is PostgreSQL. Run the migration tests on the production database engine too.
5. **The installed app cannot find `script.py.mako` or `env.py`.** The wheel left out non-Python files. Include the migrations folder as package data and test the built wheel.
