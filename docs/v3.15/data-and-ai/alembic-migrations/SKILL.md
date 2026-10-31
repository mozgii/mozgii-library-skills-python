---
name: alembic-migrations
description: Manages database schema changes for SQLAlchemy projects with Alembic - alembic init and the pyproject template, env.py with target_metadata and a URL from the environment, autogenerate and the changes it misses (renames, CHECK constraints, server defaults), writing upgrade and downgrade by hand, naming conventions for constraints, data migrations with a frozen table definition, batch mode for SQLite, branches and merge revisions, offline SQL scripts with --sql, running migrations at app start versus at deploy, testing migrations up and down with alembic check and a stairway test, and the rule never to edit an applied revision. Use when adding a table or column, generating or reviewing a migration, fixing "multiple heads", changing a column on SQLite, migrating data, producing SQL for a DBA, or wiring migrations into an app's start-up or CI.
metadata:
  tags:
  - python
  - alembic
  - sqlalchemy
  - migrations
  - database
  - schema
  - sqlite
  - postgresql
---

# Database migrations with Alembic

Alembic records each schema change as a revision: a Python file with `upgrade()` and `downgrade()` and a pointer to the revision before it. The database stores the revision it is at in the `alembic_version` table, so `alembic upgrade head` applies exactly the missing steps. This skill covers the set-up, writing and reviewing revisions, and running them. Data migrations, branches and offline SQL are in [data-branches-offline.md](references/data-branches-offline.md); running and testing migrations from code is in [running-and-testing.md](references/running-and-testing.md). The models themselves are the sqlalchemy-orm skill.

## Step 0: read the versions and the state

Read the installed versions and where the database stands before writing a revision.

```bash
uv run python -c "import sys, alembic, sqlalchemy; print(sys.version_info[:2], alembic.__version__, sqlalchemy.__version__)"
uv run alembic current            # the revision the database is at
uv run alembic heads              # the newest revision(s) in the code; more than one means a merge is due
uv run alembic history | head -3  # the newest three, newest first; works on any history, merges included
```

Alembic 1.20 (October 2026) requires SQLAlchemy 2.0 or newer; this skill assumes SQLAlchemy 2.1. Since 1.16, configuration can live in `pyproject.toml` (`[tool.alembic]`) instead of `alembic.ini`.

## Set up a project with alembic init

`alembic init` creates the migration folder and a config file. Pick the template by where you want the settings.

```bash
uv add alembic
uv run alembic init --template generic migrations      # alembic.ini + migrations/
uv run alembic init --template pyproject migrations    # settings in pyproject.toml, logging in alembic.ini
uv run alembic list_templates                          # also async, pyproject_async, multidb
```

| Template | Settings live in | Use when |
|---|---|---|
| `generic` | `alembic.ini` | the default for a sync project |
| `pyproject` | `pyproject.toml`, logging in `alembic.ini` | the project keeps tool settings in `pyproject.toml` |
| `async`, `pyproject_async` | as above, with an async `env.py` | the app's only driver is async |
| `multidb` | `alembic.ini` | one revision history across several databases |

In `alembic.ini` (or `[tool.alembic]`) set three things: `prepend_sys_path = src` so `env.py` can import a src-layout package, an empty `sqlalchemy.url` (the URL comes from the environment, never from a committed file), and a `file_template` that sorts files by date:

```ini
[alembic]
script_location = %(here)s/migrations
prepend_sys_path = src
file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s
sqlalchemy.url =
```

## env.py with target_metadata

`env.py` runs for every command. It must import the models' `MetaData` (`target_metadata`) so autogenerate can compare, read the URL from the environment, and accept a connection passed in by code that runs migrations itself.

```python
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool

from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)        # logging from alembic.ini; skipped when run from code

target_metadata = Base.metadata                # import every module that defines models before this line


def database_url() -> str:
    url = os.environ.get("APP_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("set APP_DATABASE_URL or sqlalchemy.url")
    return url


def run_migrations_offline() -> None:
    url = database_url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=url.startswith("sqlite"), dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_with(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",   # ALTER through copy-and-move on SQLite
        compare_server_default=True,                           # off by default
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    shared = config.attributes.get("connection")       # set by app code or tests
    if shared is not None:
        run_with(shared)
        return
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        run_with(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- A model module that is never imported is invisible to autogenerate, which then proposes dropping its table. Import all model modules in `app/models/__init__.py`.
- `compare_type` is on by default; `compare_server_default=True` must be asked for.

## Naming conventions for constraints

Constraints need stable names so a later revision can drop or alter them on every database. Give the `MetaData` a naming convention before the first revision; otherwise unnamed constraints get database-generated names that differ between PostgreSQL, SQLite and each developer's copy.

```python
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
```

Autogenerate then writes names with `op.f("uq_user_account_email")`, which marks them as final. A CHECK constraint still needs its own `name=` (`CheckConstraint("length(email) > 3", name="email_len")`), which the `ck` pattern expands.

## Autogenerate and what it misses

`alembic revision --autogenerate -m "..."` compares `target_metadata` with the live database and writes a draft. It is a draft: read every line before committing it.

```bash
APP_DATABASE_URL=sqlite:///app.db uv run alembic upgrade head            # the database must be at head first
APP_DATABASE_URL=sqlite:///app.db uv run alembic revision --autogenerate -m "add is_active"
uv run alembic check                    # exit code 1 if models and migrations differ: a CI gate
```

| Change | What autogenerate writes |
|---|---|
| new or dropped table, column, index, unique constraint, foreign key | detected |
| column type, nullability | detected (`compare_type` is on by default) |
| server default | only with `compare_server_default=True`, and noisy on some backends |
| renamed table or column | a drop and an add: **the data is lost** |
| added or changed CHECK constraint | not detected |
| a new `NOT NULL` column on a table with rows | written without a default: fails on upgrade |
| enum value changes, sequences, triggers, views, functions | not detected; write `op.execute(...)` |

The rename case is the dangerous one. Model `full_name` renamed to `display_name` produces `add_column('display_name')` and `drop_column('full_name')`; replace it by hand with a rename.

## Writing upgrade and downgrade by hand

Each revision's `upgrade()` moves the schema one step forward and `downgrade()` reverses it exactly. Write both, and keep each revision to one logical change.

```python
"""rename full_name, add is_active

Revision ID: bf3d6bc3a05e
Revises: 3c7906760b67
"""
import sqlalchemy as sa
from alembic import op

revision = "bf3d6bc3a05e"
down_revision = "3c7906760b67"


def upgrade() -> None:
    with op.batch_alter_table("user_account") as batch_op:
        batch_op.alter_column("full_name", new_column_name="display_name")   # a rename keeps the data
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False))
        batch_op.add_column(sa.Column("bio", sa.String(length=2000), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_account") as batch_op:
        batch_op.drop_column("bio")
        batch_op.drop_column("is_active")
        batch_op.alter_column("display_name", new_column_name="full_name")
```

- A new `NOT NULL` column on a table with rows needs a `server_default`, or three steps: add it nullable, fill it, then set `nullable=False`.
- Never import application models in a revision: the models change later and the old revision breaks. Use `sa.table()` for data steps (in [data-branches-offline.md](references/data-branches-offline.md)).
- A downgrade that cannot restore data (a dropped column) should still restore the schema, and say so in a comment.
- `--rev-id 0007_add_tags` gives readable ids instead of random hex; keep one style per project.

## Batch mode for SQLite

SQLite's `ALTER TABLE` cannot change a column's type, nullability or constraints. Batch mode works around it: `op.batch_alter_table` creates a new table, copies the rows, drops the old one and renames, all inside the revision. With `render_as_batch=True` in `env.py`, autogenerate writes batch blocks for SQLite on its own.

- On PostgreSQL and other databases, the same `batch_alter_table` block emits ordinary `ALTER TABLE` statements, so one revision serves both.
- The copy recreates the table, so foreign keys pointing at it from other tables must be valid afterwards; turn `PRAGMA foreign_keys` off around the migration when the app enables it.
- Batch mode reflects the live table, so it cannot run in `--sql` (offline) mode against SQLite without `copy_from=` a full `Table` object.
- Unnamed constraints in SQLite cannot be targeted by batch operations; the naming convention prevents that problem.

## Never edit an applied revision

A revision that has run anywhere (a teammate's machine, CI with a persistent database, staging, a user's install) is history. Editing it changes nothing in those databases, which then differ from new ones forever. Fix mistakes with a new revision. The only safe edit is to a revision that exists only on your branch and has run only on your own scratch database; downgrade it, edit, upgrade.

## Verify

A migration set is proven by running it in both directions on an empty database, not by reading it. This run uses a scratch SQLite file; point `APP_DATABASE_URL` at a scratch PostgreSQL when production runs there.

```bash
export APP_DATABASE_URL=sqlite:////tmp/migrate_probe.db
uv run alembic heads                                # exactly one line
uv run alembic upgrade head && uv run alembic check # "No new upgrade operations detected."
uv run alembic downgrade base && uv run alembic upgrade head; echo "exit $?"   # expect 0: every downgrade works
grep -rnE "^(from|import) (app|my_app)\b" migrations/versions        # expect nothing: revisions never import models
rm /tmp/migrate_probe.db
```

## Anti-patterns

| Instead of | Do |
|---|---|
| committing an autogenerated revision unread | read every line; renames and CHECK constraints need your hand |
| importing models in a revision | `sa.table()` with only the columns the step needs |
| editing a revision that has run anywhere | a new revision that corrects it |
| a real URL in `alembic.ini` | an empty `sqlalchemy.url`, the value from `APP_DATABASE_URL` |
| `Base.metadata.create_all()` beside Alembic | migrations alone own the schema; tables made elsewhere have no history |
| `alembic stamp head` to get past a failed upgrade | fix the revision and rerun it; stamping hides the difference |
| `downgrade()` left as `pass` | restore the schema, and say in a comment what data cannot come back |

## Checklist

A migration set-up is correct when every item holds.

- [ ] `env.py` imports all models into `target_metadata` and reads the URL from the environment.
- [ ] `MetaData` has a naming convention; CHECK constraints have names.
- [ ] Every autogenerated revision was read; renames are `alter_column(new_column_name=...)`, not drop and add.
- [ ] Each revision has a real `downgrade()`; revisions never import app models.
- [ ] SQLite projects use `render_as_batch=True`.
- [ ] CI runs `alembic check` and an upgrade, downgrade, upgrade test; there is a single head.
- [ ] Applied revisions are never edited.

## Gotchas

These are the Alembic problems seen most.

1. **Autogenerate wants to drop a table that exists in the models.** The model's module was never imported in `env.py`. Import all model modules.
2. **A rename lost a column's data.** Autogenerate wrote drop plus add. Replace with `alter_column(..., new_column_name=...)` before running it.
3. **`Multiple head revisions are present`.** Two branches each added a revision. Run `alembic merge heads -m "merge"`.
4. **`Cannot add a NOT NULL column with default value NULL` on SQLite** (or a failed `ADD COLUMN` on Postgres). The table has rows. Add a `server_default`, or add nullable, fill, then alter.
5. **`No support for ALTER of constraints in SQLite dialect`.** Batch mode is off. Set `render_as_batch=True` and use `batch_alter_table`.
6. **`Target database is not up to date`** when autogenerating. The database is behind. Run `alembic upgrade head` first.
7. **The app's logging stops after migrations run at start-up.** `fileConfig` in `env.py` disabled existing loggers. Run from code with a `Config()` that has no file name, or pass `disable_existing_loggers=False`.

## References

The reference files hold the longer recipes.

- [data-branches-offline.md](references/data-branches-offline.md): data migrations with `sa.table`, batching large updates, branches and `merge heads`, offline `--sql` scripts, PostgreSQL-specific operations
- [running-and-testing.md](references/running-and-testing.md): migrations at app start versus at deploy, the command API with a shared connection, packaged migrations, stamping existing databases, tests: up and down, `alembic check`, the stairway test

## See also

- `sqlalchemy-orm`: the models and `MetaData` that autogenerate compares
- `psycopg-postgres`: the PostgreSQL driver and transactions under the migrations
- `python-sqlite`: the SQLite limits that batch mode works around
- `pytest-testing`: where the upgrade and downgrade test lives
