---
title: SQLAlchemy with asyncio, and testing ORM code
author: MOZGII, Inc.
lang: en
---

# SQLAlchemy with asyncio, and testing ORM code

How to run the SQLAlchemy 2.x ORM under asyncio with `AsyncSession`, why lazy loading fails there and what to do instead, and how to test ORM code: a session per test that rolls back, in-memory SQLite shared across connections with `StaticPool`, and typed results that pyright and mypy check. The examples run against SQLite with aiosqlite.

## Contents

This reference has 7 sections.

1. Installing the asyncio extension
2. Async engine and async_sessionmaker
3. Relationships under asyncio
4. run_sync for sync-only APIs
5. A session per test that rolls back
6. In-memory SQLite with StaticPool
7. Typed results

## 1. Installing the asyncio extension

The asyncio extension needs `greenlet`, which SQLAlchemy 2.1 installs only with the `asyncio` extra, plus an async driver.

```bash
uv add "sqlalchemy[asyncio]" aiosqlite          # SQLite: sqlite+aiosqlite:///app.db
uv add "sqlalchemy[asyncio]" "psycopg[binary]"  # PostgreSQL: postgresql+psycopg_async://user@host/db
```

`greenlet` ships wheels for macOS, Linux and Windows on current Python versions; on a brand-new Python release, check that a wheel exists before upgrading (the python-version-upgrade skill).

## 2. Async engine and async_sessionmaker

`create_async_engine` and `async_sessionmaker` mirror their sync versions. Each task gets its own `AsyncSession`; a session is not safe to share between concurrent tasks.

```python
import asyncio

from sqlalchemy import ForeignKey, select
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "project"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    tasks: Mapped[list["Task"]] = relationship(back_populates="project", lazy="raise")


class Task(Base):
    __tablename__ = "task"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    title: Mapped[str]
    project: Mapped[Project] = relationship(back_populates="tasks", lazy="raise")


engine = create_async_engine("sqlite+aiosqlite:///async.db")
Session = async_sessionmaker(engine, expire_on_commit=False)


async def setup() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)        # sync DDL run inside the async connection
    async with Session.begin() as session:
        session.add(Project(name="site", tasks=[Task(title="logo"), Task(title="copy")]))


async def task_titles(project_name: str) -> list[str]:
    async with Session() as session:
        project = await session.scalar(
            select(Project).where(Project.name == project_name).options(selectinload(Project.tasks))
        )
        return sorted(t.title for t in project.tasks) if project else []


async def main() -> list[str]:
    await setup()
    try:
        return await task_titles("site")
    finally:
        await engine.dispose()                               # close pooled connections before the loop ends


asyncio.run(main())                                          # ['copy', 'logo']
```

- `expire_on_commit=False` is close to required: after commit, an expired attribute would need an implicit query, which async code cannot run.
- Call `await engine.dispose()` before the event loop closes, or aiosqlite and asyncpg warn about connections closed by the garbage collector.
- Run blocking work outside the session block; holding a session across slow `await`s keeps a pooled connection busy.

## 3. Relationships under asyncio

Reading an unloaded relationship in async code would need a hidden query in the middle of an attribute access, which asyncio cannot do; SQLAlchemy raises `MissingGreenlet` (or the `lazy="raise"` error). There are three ways to load it.

| Way | Code | Use for |
|---|---|---|
| eager load in the query | `.options(selectinload(Project.tasks))` | almost always |
| refresh named attributes | `await session.refresh(project, ["tasks"])` | loading one relationship on an object you already have; works with `lazy="raise"` |
| awaitable attribute | `await project.awaitable_attrs.tasks` (needs `AsyncAttrs` on the base) | relationships left at the default `lazy="select"`; with `lazy="raise"` it raises like any access |

```python
async def tasks_on_demand() -> int:
    async with Session() as session:
        project = await session.scalar(select(Project).where(Project.name == "site"))
        assert project is not None
        await session.refresh(project, ["tasks"])            # one awaited load of the relationship
        return len(project.tasks)


async def run_on_demand() -> int:
    try:
        return await tasks_on_demand()
    finally:
        await engine.dispose()


asyncio.run(run_on_demand())                                 # 2
```

Models used from async code keep `lazy="raise"`: every load is then visible in the code as an option or an awaited call.

## 4. run_sync for sync-only APIs

Some APIs exist only in sync form: `MetaData.create_all`, `inspect()` on a connection, or legacy helpers that take a sync `Session`. `AsyncConnection.run_sync` and `AsyncSession.run_sync` call them in the greenlet context where sync database calls are allowed.

```python
from sqlalchemy import inspect


async def table_names() -> list[str]:
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    await engine.dispose()
    return sorted(names)


asyncio.run(table_names())                                   # ['project', 'task']
```

Do not use `run_sync` to run whole blocks of sync ORM code as a shortcut; the code inside still blocks the event loop while it computes.

## 5. A session per test that rolls back

The fastest isolated database test opens a connection, begins a transaction, binds a session to that connection, and rolls everything back at the end. `join_transaction_mode="create_savepoint"` lets code under test call `session.commit()` or `session.rollback()` freely: those act on a savepoint, and the outer transaction still discards everything.

```python
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session as SyncSession


@pytest.fixture(scope="session")
def db_engine(tmp_path_factory: pytest.TempPathFactory):
    eng = create_engine(f"sqlite:///{tmp_path_factory.mktemp('db') / 'test.db'}")

    @event.listens_for(eng, "connect")
    def _driver_transactions_off(dbapi_connection, connection_record) -> None:
        dbapi_connection.isolation_level = None              # SQLite only: let SQLAlchemy emit BEGIN

    @event.listens_for(eng, "begin")
    def _begin(conn) -> None:
        conn.exec_driver_sql("BEGIN")

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(db_engine) -> Iterator[SyncSession]:
    with db_engine.connect() as conn:
        outer = conn.begin()
        with SyncSession(bind=conn, join_transaction_mode="create_savepoint") as s:
            yield s
        outer.rollback()                                     # nothing the test wrote survives


def test_commit_is_rolled_back(session: SyncSession) -> None:
    session.add(Project(name="temp"))
    session.commit()                                         # commits only the savepoint
    assert session.scalar(select(Project).where(Project.name == "temp")) is not None
```

- The two SQLite listeners are required: the `sqlite3` driver's own transaction handling breaks savepoints, and without them data from one test leaks into the next. On PostgreSQL the fixture works without them.
- Tests that need data committed for another connection or process cannot use this pattern; give them a fresh database (the psycopg-postgres skill describes template databases).

## 6. In-memory SQLite with StaticPool

`sqlite://` (in-memory) gives each new connection an empty database. For tests that use several sessions, `StaticPool` with `check_same_thread=False` makes the engine reuse one connection, so all sessions see the same in-memory data.

```python
from sqlalchemy.pool import StaticPool

mem = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
Base.metadata.create_all(mem)
with SyncSession(mem) as s1, s1.begin():
    s1.add(Project(name="shared"))
with SyncSession(mem) as s2:
    s2.scalar(select(Project.name))                          # 'shared': same connection, same database
```

In-memory SQLite is fine for logic tests; behaviour that depends on the real database (Postgres types, `ON CONFLICT` targets, locking, JSON operators) needs a test against that database.

## 7. Typed results

SQLAlchemy 2.x types flow from `Mapped[...]` through `select()` to results, so checkers catch wrong attribute names and types without a plugin.

```python
from sqlalchemy import Select


def projects_by_name(name: str) -> Select[tuple[Project]] | Select[Project]:
    return select(Project).where(Project.name == name)


with SyncSession(mem) as s:
    found: list[Project] = list(s.scalars(projects_by_name("shared")))
    pairs = s.execute(select(Project.id, Project.name)).all()   # typed rows with (int, str) columns
mem.dispose()
```

- In 2.1, `select(Project.id, Project.name)` is typed `Select[int, str]` and its rows `Row[int, str]` (PEP 646); in 2.0 it was `Select[Tuple[int, str]]`. Annotations written for 2.0 with `Tuple[...]` need updating when moving to 2.1; let the checker infer where possible.
- Pyright strict reports unknown types on event listener parameters and on `relationship()` arguments built dynamically; keep those in a small module with explicit annotations.
- mypy reads the same annotations; delete `sqlalchemy.ext.mypy.plugin` from the mypy configuration, as it no longer exists in 2.1.
