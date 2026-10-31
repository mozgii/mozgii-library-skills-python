---
title: Alembic data migrations, branches and offline SQL
author: MOZGII, Inc.
lang: en
---

# Alembic data migrations, branches and offline SQL

How to change data inside an Alembic revision without importing application models, how to update large tables in batches, how branches and merge revisions work when two people add migrations at once, how to produce SQL scripts with `--sql` for a DBA or a locked-down server, and the PostgreSQL operations that need special handling.

## Contents

This reference has 8 sections.

1. Data migrations with a frozen table
2. Updating large tables in batches
3. Expand, migrate, contract for zero-downtime changes
4. Branches and merge revisions
5. Offline SQL with --sql
6. Operations that cannot run in a transaction
7. Enum and type changes on PostgreSQL
8. Gotchas

## 1. Data migrations with a frozen table

A data migration changes rows, not structure: filling a new column, normalising values, splitting a field. Write it against a minimal `sa.table()` definition frozen at that revision, never against the application's models, which will have changed by the time someone runs this revision on an old database.

```python
"""lowercase emails

Revision ID: 0003_lower_emails
Revises: bf3d6bc3a05e
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_lower_emails"
down_revision = "bf3d6bc3a05e"

# A frozen, minimal view of the table as it is at this revision; never import app models here.
user_account = sa.table(
    "user_account",
    sa.column("id", sa.Integer),
    sa.column("email", sa.String),
)


def upgrade() -> None:
    op.execute(
        user_account.update()
        .where(user_account.c.email != sa.func.lower(user_account.c.email))
        .values(email=sa.func.lower(user_account.c.email))
    )


def downgrade() -> None:
    pass  # the original capitalisation is gone; downgrading leaves emails lowercase
```

- Keep schema and data changes in separate revisions when the data step is slow or risky, so a failure is easy to locate and retry.
- `op.execute` with a SQLAlchemy statement renders for every dialect, works in `--sql` mode and needs no connection object.
- Code that must read rows and decide in Python uses `op.get_bind()`, which returns the migration's connection; such a revision cannot run in `--sql` mode.

## 2. Updating large tables in batches

One `UPDATE` over millions of rows holds locks and grows the transaction log for the whole run. Update in id ranges instead; each batch is a short statement, and progress is visible.

```python
def upgrade() -> None:
    bind = op.get_bind()
    low, high = bind.execute(sa.select(sa.func.min(user_account.c.id), sa.func.max(user_account.c.id))).one()
    if low is None:
        return
    step = 10_000
    for start in range(low, high + 1, step):
        bind.execute(
            user_account.update()
            .where(user_account.c.id.between(start, start + step - 1))
            .where(user_account.c.email != sa.func.lower(user_account.c.email))
            .values(email=sa.func.lower(user_account.c.email))
        )
```

Alembic runs the whole revision in one transaction by default (on databases with transactional DDL), so the batches still commit together. For tables large enough that this matters, run the backfill outside Alembic as a job that commits per batch, and keep only the schema steps in revisions.

## 3. Expand, migrate, contract for zero-downtime changes

When old and new versions of an app run at the same time (rolling deploys, several desktop clients on one server), a breaking change is split across releases so every running version finds the columns it expects.

| Release | Revision | App code |
|---|---|---|
| 1. expand | add `display_name` (nullable), keep `full_name` | writes both, reads `full_name` |
| 2. migrate | backfill `display_name` from `full_name` | reads `display_name` |
| 3. contract | drop `full_name`, set `display_name` NOT NULL | uses only `display_name` |

A direct rename in one revision is fine for a desktop app with a local database, where the app and its schema always upgrade together.

## 4. Branches and merge revisions

Two developers who each add a revision on top of the same parent create two heads. Alembic then refuses to guess an order.

```bash
uv run alembic heads
#   0004_audit (head)
#   0004_tags (head)
uv run alembic upgrade head
#   FAILED: Multiple head revisions are present for given argument 'head'; ...
uv run alembic merge heads -m "merge tags and audit" --rev-id 0005_merge
uv run alembic upgrade head
#   Running upgrade 0003_lower_emails -> 0004_tags, add tags
#   Running upgrade 0003_lower_emails -> 0004_audit, add audit
#   Running upgrade 0004_audit, 0004_tags -> 0005_merge, merge tags and audit
```

- The merge revision has `down_revision = ("0004_audit", "0004_tags")` and usually empty `upgrade()` and `downgrade()`.
- Merge only when the two branches touch different things. If both alter the same table, rebase one instead: change its `down_revision` to the other's id, as long as neither has been applied outside your machine.
- Catch two heads in CI before merge: `test "$(uv run alembic heads | wc -l)" -eq 1`.
- A new revision on an older parent needs `alembic revision --head <rev> --splice`; without `--splice` Alembic refuses to branch from a non-head.

## 5. Offline SQL with --sql

`alembic upgrade <range> --sql` prints the SQL instead of running it, for a DBA to review or for a server the app cannot reach. No database connection is made; the dialect comes from the URL.

```bash
APP_DATABASE_URL=postgresql+psycopg://app@db/app uv run alembic upgrade head --sql > upgrade.sql
APP_DATABASE_URL=postgresql+psycopg://app@db/app uv run alembic upgrade 0003_lower_emails:head --sql   # from a known revision
```

For the revisions in section 1 on PostgreSQL, the script contains `BEGIN;`, `CREATE TABLE alembic_version ...`, each revision's DDL (`ALTER TABLE user_account RENAME full_name TO display_name; ...`), the `UPDATE alembic_version` lines, and `COMMIT;`.

- `literal_binds=True` in `run_migrations_offline` renders parameter values into the SQL text.
- Revisions that read data (`op.get_bind()`) cannot run offline. Keep them out of the range, or write them with `op.execute` only.
- Batch operations on SQLite reflect the live table and fail offline with "batch mode with dialect sqlite requires a live database connection"; pass `copy_from=` a complete `Table` to `batch_alter_table`, or generate SQLite migrations online.

## 6. Operations that cannot run in a transaction

PostgreSQL refuses some statements inside a transaction: `CREATE INDEX CONCURRENTLY`, `DROP INDEX CONCURRENTLY`, and, before PostgreSQL 12, `ALTER TYPE ... ADD VALUE` (from 12 on it runs in a transaction, but the new value is usable only after commit). `autocommit_block()` ends the migration's transaction, runs the block in autocommit, and starts a new transaction after it.

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index("ix_user_account_bio", "user_account", ["bio"], postgresql_concurrently=True,
                        if_not_exists=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("ix_user_account_bio", table_name="user_account", postgresql_concurrently=True,
                      if_exists=True)
```

- Put such a step in a revision of its own: if it fails halfway, the revisions before it are already committed and the database is at a known revision.
- A failed concurrent index build leaves an `INVALID` index; `if_not_exists=True` would then skip it, so drop it before retrying.
- `transaction_per_migration=True` in `context.configure` commits after each revision instead of once for the whole upgrade, which makes long upgrades restartable.

## 7. Enum and type changes on PostgreSQL

Autogenerate does not detect changes to the values of a PostgreSQL `ENUM` type. Add values by hand with `ALTER TYPE`.

```python
def upgrade() -> None:
    op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'refunded'")


def downgrade() -> None:
    pass  # PostgreSQL cannot drop an enum value; leave it, or rebuild the type in a new revision
```

- Many teams store such values in a `text` column with a CHECK constraint instead of an ENUM type: adding a value is then an ordinary constraint change.
- Type changes that need a conversion use `alter_column(..., type_=..., postgresql_using="col::integer")`.

## 8. Gotchas

These are the problems seen most with data migrations, branches and offline scripts.

1. **An old revision fails with `ImportError` or wrong columns.** It imported application models that have since changed. Use `sa.table()` definitions inside revisions.
2. **A data migration locks a big table for minutes.** One huge `UPDATE`. Batch it, or move the backfill to a job.
3. **`Multiple head revisions are present`.** Two parallel revisions. `alembic merge heads`, or rebase the unpublished one.
4. **`--sql` fails with "requires a live database connection".** SQLite batch mode or `op.get_bind()` in the range. Generate online, or use `copy_from=` and `op.execute`.
5. **`CREATE INDEX CONCURRENTLY cannot run inside a transaction block`.** Wrap it in `op.get_context().autocommit_block()`.
