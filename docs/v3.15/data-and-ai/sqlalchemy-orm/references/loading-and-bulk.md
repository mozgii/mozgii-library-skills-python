---
title: SQLAlchemy loading strategies and bulk operations
author: MOZGII, Inc.
lang: en
---

# SQLAlchemy loading strategies and bulk operations

How to control the SQL that the SQLAlchemy 2.x ORM emits: every relationship loading strategy and when to pick it, counting queries to catch N+1 problems in tests, `contains_eager` for filtered joins, write-only collections for large relationships, streaming large results, and fast bulk inserts, updates and upserts. All examples run against SQLite.

## Contents

This reference has 8 sections.

1. The example models
2. Loading strategies compared
3. Counting queries to catch N+1
4. contains_eager for joins you filter on
5. Write-only relationships for large collections
6. Streaming large results with yield_per
7. Bulk inserts with RETURNING
8. Upserts on SQLite and PostgreSQL

## 1. The example models

The sections below share two models: a customer with many orders. Relationships are `lazy="raise"`, so every query must say how it loads them.

```python
from sqlalchemy import ForeignKey, create_engine, event, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customer"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True)
    orders: Mapped[list["Order"]] = relationship(back_populates="customer", lazy="raise")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), index=True)
    cents: Mapped[int]
    customer: Mapped[Customer] = relationship(back_populates="orders", lazy="raise")


engine = create_engine("sqlite://")
Base.metadata.create_all(engine)
with Session(engine) as s, s.begin():
    s.add_all([Customer(email=f"c{i}@x.example", orders=[Order(cents=c) for c in (100, 250, 900)]) for i in range(20)])
```

## 2. Loading strategies compared

Each strategy decides when related rows are fetched. Set a safe default on the relationship (`lazy="raise"` or `"selectin"`) and override per query with `options()`.

| Strategy | Queries for 20 customers and their orders | Pick when |
|---|---|---|
| `selectinload(Customer.orders)` | 2: customers, then orders `WHERE customer_id IN (...)` | collections; the general default |
| `joinedload(Order.customer)` | 1, with `LEFT OUTER JOIN` | many-to-one, one row each side |
| `joinedload(Customer.orders)` | 1, but customer columns repeat per order | small collections; needs `.unique()` |
| `subqueryload` | 2, re-running the first query as a subquery | legacy; `selectinload` is better almost always |
| `lazyload` | 1 + 20 (N+1) | scripts touching one object |
| `raiseload("*")` | error on any unloaded relationship | proving a code path loads what it needs |
| `noload` | none: the attribute is empty | never in normal code: silently wrong data |

Strategies chain along a path: `selectinload(Customer.orders).joinedload(Order.customer)` loads orders, then each order's customer.

```python
from sqlalchemy.orm import joinedload, selectinload

with Session(engine) as s:
    customers = s.scalars(select(Customer).options(selectinload(Customer.orders)).order_by(Customer.id)).all()
    sum(o.cents for c in customers for o in c.orders)  # 25000
    rows = s.scalars(select(Customer).options(joinedload(Customer.orders))).unique().all()
    len(rows)                                          # 20: unique() removes the repeated customers
```

## 3. Counting queries to catch N+1

An N+1 problem shows up as a query count that grows with the data. A context manager that counts statements through the engine's events turns that into a test assertion.

```python
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def count_queries(target) -> Iterator[list[str]]:
    statements: list[str] = []

    def before(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    event.listen(target, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(target, "before_cursor_execute", before)


with Session(engine) as s, count_queries(engine) as seen:
    customers = s.scalars(select(Customer).options(selectinload(Customer.orders))).all()
    _ = [o.cents for c in customers for o in c.orders]
len(seen)                                              # 2, however many customers there are
```

- In tests, assert the count against a fixed number, and run the test with more rows than the page size, so an N+1 regression fails.
- `raiseload("*")` in the query options is the stricter alternative: any relationship you did not plan to load raises.
- In development, `echo=True` on the engine or `logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)` prints every statement.

## 4. contains_eager for joins you filter on

When a query joins a relationship to filter or sort by it, `contains_eager` fills the relationship from that same join instead of loading it again. The collection then holds only the rows that matched the filter.

```python
from sqlalchemy.orm import contains_eager

with Session(engine) as s:
    big = s.scalars(
        select(Customer).join(Customer.orders).where(Order.cents >= 900)
        .options(contains_eager(Customer.orders)).order_by(Customer.id)
    ).unique().all()
    [len(c.orders) for c in big][:3]                   # [1, 1, 1]: only the matching orders
```

Use it deliberately: an object whose collection is partially loaded looks complete to later code in the same session. Keep such queries in read-only paths.

## 5. Write-only relationships for large collections

A collection with thousands of rows should never load at once. `WriteOnlyMapped` makes the attribute a handle: you add to it and build queries from it, but it never loads itself.

```python
from sqlalchemy.orm import WriteOnlyMapped


class Account(Base):
    __tablename__ = "account"
    id: Mapped[int] = mapped_column(primary_key=True)
    events: WriteOnlyMapped["Event"] = relationship(passive_deletes=True)


class Event(Base):
    __tablename__ = "event"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str]


Base.metadata.create_all(engine)
with Session(engine) as s, s.begin():
    acct = Account()
    s.add(acct)
    acct.events.add_all([Event(kind="login") for _ in range(5)])
    s.flush()
    s.scalar(select(func.count()).select_from(acct.events.select().subquery()))   # 5
```

`passive_deletes=True` with `ondelete="CASCADE"` lets the database delete the children, instead of the ORM loading them all to delete one by one.

## 6. Streaming large results with yield_per

`session.scalars()` normally buffers all rows. The `yield_per` execution option fetches and builds objects in batches, so memory stays flat while iterating millions of rows.

```python
with Session(engine) as s:
    total = 0
    for order in s.scalars(select(Order).execution_options(yield_per=500)):
        total += order.cents
total                                                  # 25000
```

- With `yield_per`, eager loading of collections through `joinedload` is not allowed; `selectinload` works per batch.
- On PostgreSQL, add `stream_results=True` (implied by `yield_per` on drivers that support server-side cursors) so the driver does not buffer the whole result either.
- For pure reads into dicts or DataFrames, skip the ORM: `s.execute(select(Order.id, Order.cents))` returns light rows.

## 7. Bulk inserts with RETURNING

`session.execute(insert(Model), list_of_dicts)` inserts many rows without creating objects. Adding `.returning(Model)` gives back the new objects; SQLAlchemy batches the statements with "insertmanyvalues" on backends that support `RETURNING` (SQLite 3.35+, PostgreSQL, MariaDB 10.5+).

```python
from sqlalchemy import insert, update

with Session(engine) as s, s.begin():
    new_ids = s.scalars(
        insert(Customer).returning(Customer.id),
        [{"email": f"bulk{i}@x.example"} for i in range(1000)],
    ).all()
    len(new_ids)                                       # 1000
    s.execute(update(Order).where(Order.cents < 200).values(cents=Order.cents + 1))
    s.scalar(select(func.min(Order.cents)))           # 101
```

- Bulk statements skip ORM events such as `before_insert` and do not cascade relationships; put children in their own bulk insert.
- An "ORM bulk UPDATE by primary key" is the same call with a list of dicts that each contain the primary key: `s.execute(update(Order), [{"id": 1, "cents": 5}, ...])`.
- For millions of rows on PostgreSQL, the driver's COPY is faster still (the psycopg-postgres skill); reach the raw connection with `s.connection().connection.driver_connection`.

## 8. Upserts on SQLite and PostgreSQL

An upsert is dialect-specific SQL: `INSERT ... ON CONFLICT DO UPDATE`. SQLite and PostgreSQL share the same construct shape in their dialect modules.

```python
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

rows = [{"email": "c1@x.example"}, {"email": "new@x.example"}]
stmt = sqlite_insert(Customer).values(rows)
stmt = stmt.on_conflict_do_update(index_elements=[Customer.email], set_={"email": stmt.excluded.email})
with Session(engine) as s, s.begin():
    s.execute(stmt)
    s.scalar(select(func.count()).select_from(Customer))   # 1021: one new row, one updated in place
```

- On PostgreSQL import `insert` from `sqlalchemy.dialects.postgresql`; the arguments are the same, and `constraint="uq_name"` targets a named constraint.
- `on_conflict_do_nothing()` skips duplicates; combine with `.returning(...)` to learn which rows were inserted.
- A portable alternative is `session.merge(obj)`, which issues a SELECT per object: fine for a few rows, slow for many.
