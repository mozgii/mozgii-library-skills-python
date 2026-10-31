---
name: sqlalchemy-orm
description: Uses SQLAlchemy 2.x (2.1) in the modern style - one Engine per process with the right URL and pool, typed models with DeclarativeBase, Mapped and mapped_column, Session lifecycles with sessionmaker and session.begin() per request or job, select() queries with scalars, relationships with back_populates and cascades, loading strategies (selectinload, joinedload, lazy="raise") and N+1 detection, the unit of work and autoflush, bulk inserts and updates through Core, AsyncSession with async_sessionmaker, and type checking with pyright or mypy without a plugin. Use when defining ORM models, writing queries in 2.0 style, fixing slow pages caused by lazy loading, choosing session scope, using SQLAlchemy with asyncio, migrating from 1.x Query code, or when an error mentions DetachedInstanceError, MissingGreenlet or a closed session.
metadata:
  tags:
  - python
  - sqlalchemy
  - orm
  - database
  - sql
  - asyncio
  - typing
  - sqlite
---

# SQLAlchemy ORM in the 2.x style

SQLAlchemy has two layers: Core (tables, `select()`, connections) and the ORM (classes mapped to tables, a `Session` that tracks objects). Version 2.0 replaced the old `Query` API with `select()` everywhere and made models typed with `Mapped[...]`; 2.1 builds on that. This skill covers engines, models, sessions, queries and loading. Loading strategies and bulk work in depth are in [loading-and-bulk.md](references/loading-and-bulk.md); asyncio and tests are in [async-and-testing.md](references/async-and-testing.md). Schema migrations are the alembic-migrations skill.

## Step 0: read the versions

Read the installed SQLAlchemy and the driver before writing code; 1.4 and 2.x code look very different.

```bash
uv run python -c "import sys, sqlalchemy; print(sys.version_info[:2], sqlalchemy.__version__)"
uv run python -c "import greenlet" 2>/dev/null && echo "asyncio extra installed"
grep -rn "session.query(\|declarative_base()" src/ | head     # 1.x patterns still in the code
```

| Version | Change that matters |
|---|---|
| 2.0 | `select()` for ORM queries; `Mapped[...]` and `mapped_column`; `DeclarativeBase`; `Session.execute` returns `Result`; `insertmanyvalues` makes bulk `INSERT ... RETURNING` fast |
| 2.1 | Python 3.11+; `greenlet` only with `sqlalchemy[asyncio]`; the mypy plugin is gone; `postgresql://` uses psycopg 3 by default; autoflush before every statement, Core ones included; `Row` typed as `Row[int, str]`; `tstring()` for Python 3.14 t-strings |

## Engine and connection URL

The `Engine` owns the connection pool. Create one per process and database at start-up, pass it to whatever needs it, and never create engines per request.

```python
from sqlalchemy import create_engine, event

engine = create_engine(
    "sqlite:///app.db",                 # postgresql+psycopg://user@host/db for Postgres (password from a secret store)
    echo=False,                         # True logs every statement: for debugging only
)


@event.listens_for(engine, "connect")
def sqlite_pragmas(dbapi_connection, connection_record) -> None:
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA foreign_keys = ON")       # SQLite ignores foreign keys without it
    cur.execute("PRAGMA journal_mode = WAL")
    cur.close()
```

- Postgres options: `pool_size`, `max_overflow`, `pool_pre_ping=True` (checks a connection before use, survives server restarts) and `pool_recycle`. Keep `pool_size + max_overflow` per process under the server's `max_connections`.
- After `os.fork()` (Gunicorn-style pre-fork, multiprocessing with fork), call `engine.dispose(close=False)` in the child so it opens its own connections.
- Build URLs with `sqlalchemy.URL.create(...)` when parts come from settings; it escapes special characters in passwords.

## Models with DeclarativeBase and Mapped

A model is a class that inherits from your `DeclarativeBase` subclass. `Mapped[T]` declares the Python type, `mapped_column()` adds database details, and `Optional`/`| None` decides `NULL`.

```python
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING = {
    "ix": "ix_%(column_0_label)s", "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)     # stable constraint names for Alembic


class Author(Base):
    __tablename__ = "author"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    bio: Mapped[str | None]                            # nullable, no mapped_column needed
    created: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    books: Mapped[list["Book"]] = relationship(back_populates="author", cascade="all, delete-orphan",
                                               lazy="raise")


class Book(Base):
    __tablename__ = "book"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("author.id", ondelete="CASCADE"))
    author: Mapped[Author] = relationship(back_populates="books", lazy="raise")


Base.metadata.create_all(engine)                       # tests and prototypes; production uses Alembic
```

- `Mapped[str]` is `NOT NULL`; `Mapped[str | None]` is nullable. The annotation is the single source of truth for both.
- Store instants as timezone-aware values; on Postgres use `DateTime(timezone=True)` (`timestamptz`). SQLite stores text and returns naive datetimes, so convert at the boundary.
- `MappedAsDataclass` as an extra base gives models a dataclass `__init__` and `repr`; it works well with pyright, but then every field needs a default or `init=False` in the right order.
- The naming convention matters from the first migration: without it, constraints get database-generated names that Alembic cannot drop portably.

## Session lifecycle: one session per unit of work

A `Session` is a unit of work: it holds a transaction, an identity map of loaded objects, and the pending changes. Create one per request, job or command, and close it at the end. `sessionmaker` is the factory; `begin()` commits on success and rolls back on an exception.

```python
from sqlalchemy.orm import Session, sessionmaker

SessionLocal = sessionmaker(engine, expire_on_commit=False)


def add_author(name: str, titles: list[str]) -> int:
    with SessionLocal.begin() as session:              # BEGIN ... COMMIT (or ROLLBACK), then close
        author = Author(name=name, books=[Book(title=t) for t in titles])
        session.add(author)
        session.flush()                                # sends the INSERTs now, so author.id is known
        return author.id


ada_id = add_author("Ada", ["Notes", "Engines"])
add_author("Brian", ["Pipes"])
ada_id                                                 # 1
```

- Never share a session between threads or tasks, and never keep a module-level session. `scoped_session` exists for old code; pass sessions explicitly instead.
- `expire_on_commit=False` keeps loaded attributes readable after commit, which is what code that returns objects from a session needs. Leave the default `True` where stale data after commit would be a bug.
- A function that receives a session does not commit it; the caller that owns the unit of work does. This keeps several operations in one transaction.

## Queries with select()

ORM queries are `select()` statements run by the session. `session.scalars()` returns model objects; `session.execute()` returns rows of several columns.

```python
from sqlalchemy import select

with SessionLocal() as session:
    ada = session.scalars(select(Author).where(Author.name == "Ada")).one()
    titles = session.scalars(select(Book.title).order_by(Book.title)).all()
    counts = session.execute(
        select(Author.name, func.count(Book.id)).join(Author.books).group_by(Author.name).order_by(Author.name)
    ).all()
    same = session.get(Author, ada_id)                 # by primary key; no SQL if already in the session

titles                                                 # ['Engines', 'Notes', 'Pipes']
[tuple(r) for r in counts]                             # [('Ada', 2), ('Brian', 1)]
ada is same                                            # True: one object per row per session
```

| Want | Call |
|---|---|
| exactly one row, error otherwise | `.one()` |
| zero or one | `.one_or_none()` |
| first of many | `.first()` (add `order_by`) |
| a list | `.all()` |
| a single value | `session.scalar(select(func.count()).select_from(Book))` |

Values always go in as Python objects (`Author.name == name`), which become bound parameters; build dynamic filters by adding `.where()` clauses, never by formatting strings into `text()`.

## Relationships and loading strategies

A relationship loads related objects either lazily (a new query when the attribute is first read) or eagerly with the main query. Lazy loading inside a loop is the N+1 problem: one query for the list and one per row.

```python
from sqlalchemy.orm import selectinload

with SessionLocal() as session:
    authors = session.scalars(select(Author).options(selectinload(Author.books)).order_by(Author.name)).all()
    {a.name: sorted(b.title for b in a.books) for a in authors}   # {'Ada': ['Engines', 'Notes'], 'Brian': ['Pipes']}
```

| Strategy | SQL | Use for |
|---|---|---|
| `selectinload` | second query with `WHERE id IN (...)` | collections (one-to-many, many-to-many): the default choice |
| `joinedload` | `LEFT OUTER JOIN` in the same query | many-to-one (`Book.author`); with collections, call `.unique()` on the result |
| `lazy="raise"` / `raiseload("*")` | none: reading the attribute raises | make every missing eager load an error in development and async code |
| `lazy="select"` (default) | a query on first access | scripts and admin tools only |

With `lazy="raise"` on the model, as above, forgetting `selectinload` fails at once instead of quietly running hundreds of queries:

```python
from sqlalchemy.exc import InvalidRequestError

with SessionLocal() as session:
    brian = session.scalars(select(Author).where(Author.name == "Brian")).one()
    try:
        brian.books
    except InvalidRequestError as exc:
        "lazy='raise'" in str(exc)                     # True
```

## The unit of work and autoflush

The session collects changes to objects and writes them at flush time, in dependency order: parents before children on insert, children before parents on delete. Autoflush runs before each query, so a query sees the pending changes; in 2.1 that includes Core and `text()` statements.

```python
with SessionLocal.begin() as session:
    pipes = session.scalars(select(Book).where(Book.title == "Pipes")).one()
    pipes.title = "Pipes and Filters"                  # change tracked, not yet sent
    session.scalars(select(Book).where(Book.title.like("Pipes%"))).one().title   # 'Pipes and Filters': autoflush sent the UPDATE first
```

- `session.flush()` sends pending changes without committing; use it to get generated keys or to hit constraint errors early.
- `session.refresh(obj)` reloads from the database; `session.expire_all()` marks everything stale.
- An exception during flush leaves the session needing `rollback()`; the `begin()` block does that.

## Bulk inserts and updates through Core

Adding thousands of objects one by one builds thousands of Python objects and tracks each. For bulk work, pass lists of dicts to `insert()` or `update()`; the session runs them as `executemany` with `insertmanyvalues` batching.

```python
from sqlalchemy import insert, update

with SessionLocal.begin() as session:
    session.execute(insert(Book), [{"title": f"Vol {i}", "author_id": ada_id} for i in range(1000)])
    session.execute(update(Book).where(Book.title.like("Vol 9%")).values(title=Book.title + " (rev)"))
    session.scalar(select(func.count()).select_from(Book).where(Book.title.like("% (rev)")))   # 111
```

- `update()` and `delete()` with `where()` run one SQL statement; objects already loaded in the session are synchronised by default (`synchronize_session="auto"`).
- Upserts use the dialect's insert: `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(...)`, and the same for SQLite. Recipes are in [loading-and-bulk.md](references/loading-and-bulk.md).

## AsyncSession for asyncio code

The asyncio extension wraps the same ORM: `create_async_engine`, `async_sessionmaker` and `AsyncSession`, with async drivers (`postgresql+psycopg_async://`, `sqlite+aiosqlite://`). Install `sqlalchemy[asyncio]`, which brings `greenlet`. Lazy loading cannot run implicitly under `await`, so every relationship is loaded eagerly. The full pattern is in [async-and-testing.md](references/async-and-testing.md).

## Typing with pyright and mypy

Models typed with `Mapped[...]` need no plugin: pyright and mypy read the annotations directly. The old mypy plugin (`sqlalchemy.ext.mypy`) no longer exists in 2.1; remove it from `[tool.mypy] plugins`.

- `session.scalars(select(Author))` is typed `ScalarResult[Author]`; `session.execute(select(Author.name, Author.id))` gives `Result[str, int]` rows in 2.1.
- Pyright strict reports unannotated event listener parameters (`reportMissingParameterType`); annotate them (`DBAPIConnection` from `sqlalchemy.engine.interfaces`, `ConnectionPoolEntry` from `sqlalchemy.pool`).

## Verify

Prove the loading plan by counting statements: a list page must not grow a query per row. `my_app.models` stands for the module holding `Base`, `Author` and `Book`.

```python
# tests/test_queries.py
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session, selectinload

import pytest

from my_app.models import Author, Base, Book


def statement_log(engine: Engine) -> list[str]:
    seen: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda _c, _cur, sql, *_rest: seen.append(sql))
    return seen


@pytest.fixture
def engine() -> Engine:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        session.add_all(Author(name=f"a{i}", books=[Book(title=f"b{i}")]) for i in range(20))
    return engine


def test_list_page_runs_two_statements(engine: Engine) -> None:
    seen = statement_log(engine)
    with Session(engine) as session:
        authors = session.scalars(select(Author).options(selectinload(Author.books))).all()
        assert sum(len(a.books) for a in authors) == 20
    assert len(seen) == 2                                # one per entity, not one per row


def test_forgotten_eager_load_raises(engine: Engine) -> None:
    with Session(engine) as session:
        author = session.scalars(select(Author).limit(1)).one()
        with pytest.raises(InvalidRequestError):
            _ = author.books
```

```bash
uv run pytest -q tests/test_queries.py
grep -rnE 'session\.query\(|declarative_base\(\)' src     # expect nothing in new code
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `session.query(Model)` in new code | `session.scalars(select(Model))` |
| a module-level session | one per request or job, from `sessionmaker` |
| `commit()` inside a helper that receives a session | the caller that owns the unit of work commits |
| lazy loading in a loop | `selectinload`, and `lazy="raise"` on the model |
| `session.add` in a loop for thousands of rows | `session.execute(insert(Model), rows)` |
| `text(f"... {value}")` | bound parameters, or expressions |

## Checklist

SQLAlchemy code is correct when every item holds.

- [ ] One engine per process; Postgres engines use `pool_pre_ping`; SQLite engines turn on `foreign_keys`.
- [ ] Models use `DeclarativeBase`, `Mapped[...]` and `mapped_column`; `MetaData` has a naming convention.
- [ ] One session per request, job or command, opened with `sessionmaker` and `begin()`; no global session.
- [ ] Queries use `select()`; no `session.query` in new code; filters are expressions, not formatted strings.
- [ ] Relationships default to `lazy="raise"`; list pages use `selectinload` or `joinedload`.
- [ ] Bulk work uses `insert()`/`update()` with lists of dicts; async code uses `AsyncSession` with eager loads.

## Gotchas

These are the SQLAlchemy problems seen most.

1. **`DetachedInstanceError: Parent instance is not bound to a Session`.** An attribute was read after the session closed and was not loaded. Load it eagerly, or keep `expire_on_commit=False` and load before leaving the block.
2. **A page runs hundreds of queries.** Lazy loading in a loop (N+1). Add `selectinload`, and set `lazy="raise"` so it fails in tests.
3. **`MissingGreenlet: greenlet_spawn has not been called`.** A lazy load or expired attribute was touched in async code. Eager-load, use `AsyncAttrs.awaitable_attrs`, or set `expire_on_commit=False`.
4. **`ImportError: ... requires that the Python 'greenlet' library is installed`.** SQLAlchemy 2.1 no longer installs it. Depend on `sqlalchemy[asyncio]`.
5. **Duplicate parents when joining a collection.** `joinedload` on a one-to-many multiplies rows. Call `.unique()` or use `selectinload`.
6. **`PendingRollbackError: This Session's transaction has been rolled back`.** A flush failed earlier and the session was reused. Use `with Session.begin()` blocks, or call `rollback()` in the error path.
7. **Foreign keys are not enforced in SQLite tests.** The PRAGMA is off per connection. Set it in a `connect` event listener.

## References

The reference files hold the longer recipes.

- [loading-and-bulk.md](references/loading-and-bulk.md): every loading strategy, counting queries to catch N+1, `contains_eager`, write-only relationships, ORM and Core bulk inserts, upserts on SQLite and Postgres
- [async-and-testing.md](references/async-and-testing.md): AsyncSession set-up, `run_sync`, `awaitable_attrs`, sessions in tests with rollback, in-memory SQLite with `StaticPool`, typing results

## See also

- `alembic-migrations`: schema changes for these models
- `python-sqlite`: SQLite behaviour underneath the ORM
- `psycopg-postgres`: the Postgres driver and pools
- `python-asyncio`: where `AsyncSession` runs
- `pyright-strict`: typing listeners and results in strict mode
