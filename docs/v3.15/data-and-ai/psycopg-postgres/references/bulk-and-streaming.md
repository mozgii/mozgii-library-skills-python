---
title: Bulk loads, streaming and pipelines with psycopg
author: MOZGII, Inc.
lang: en
---

# Bulk loads, streaming and pipelines with psycopg

How to move many rows between Python and PostgreSQL quickly with psycopg 3: COPY in and out, `executemany` with `returning`, pipeline mode, server-side cursors and `stream()` for results larger than memory, and prepared statements, including when to turn them off.

## Contents

This reference has 8 sections.

1. Choosing a bulk method
2. COPY FROM STDIN with write_row
3. Binary COPY and set_types
4. COPY TO STDOUT for exports
5. executemany with returning and results()
6. Pipeline mode
7. Server-side cursors and stream()
8. Prepared statements and PgBouncer

## 1. Choosing a bulk method

Each method trades speed against flexibility. Every one of them runs inside one transaction; committing per row costs a network round trip and a disk flush per row.

| Rows | Need | Method |
|---|---|---|
| up to a few hundred | plain inserts | `executemany` (pipelined automatically) |
| thousands, need generated ids back | inserts with `RETURNING` | `executemany(..., returning=True)` |
| thousands to millions | fastest load | `COPY ... FROM STDIN` |
| upsert of many rows | `ON CONFLICT` logic | COPY into a temporary table, then one `INSERT ... SELECT ... ON CONFLICT` |
| reading more rows than fit in memory | iteration | named (server-side) cursor or `stream()` |

## 2. COPY FROM STDIN with write_row

`cursor.copy()` opens a COPY operation; `write_row` sends one Python tuple at a time, adapted like query parameters. The data streams, so memory stays flat however many rows go in.

```python
from collections.abc import Iterable, Iterator
from decimal import Decimal

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb


def load_events(conn: psycopg.Connection, rows: Iterable[tuple[int, str, dict[str, object]]]) -> int:
    count = 0
    with conn.transaction(), conn.cursor() as cur:
        with cur.copy("COPY event (account_id, kind, payload) FROM STDIN") as copy:
            for account_id, kind, payload in rows:
                copy.write_row((account_id, kind, Jsonb(payload)))
                count += 1
    return count
```

- Name the columns in the `COPY` statement; the tuple order must match them.
- A failure anywhere aborts the whole COPY: no row is loaded. Validate before, or load into a staging table and clean there.
- For a dynamic table name compose the statement: `sql.SQL("COPY {} (id, name) FROM STDIN").format(sql.Identifier(table))`.
- `copy.write(data)` sends pre-formatted text or CSV bytes, for example a file read in chunks: `COPY t FROM STDIN (FORMAT csv, HEADER true)`.

### Upsert many rows through a staging table

`COPY` cannot handle conflicts, so the bulk upsert loads into a temporary table first and merges with one statement.

```python
def upsert_prices(conn: psycopg.Connection, prices: Iterable[tuple[str, Decimal]]) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE price_in (sku text, cents numeric) ON COMMIT DROP")
        with cur.copy("COPY price_in (sku, cents) FROM STDIN") as copy:
            for row in prices:
                copy.write_row(row)
        cur.execute("""
            INSERT INTO price (sku, cents) SELECT sku, cents FROM price_in
            ON CONFLICT (sku) DO UPDATE SET cents = excluded.cents
        """)
```

`MERGE` (PostgreSQL 15+) is the alternative when rows must also be deleted or matched on several conditions.

## 3. Binary COPY and set_types

Binary COPY skips text parsing on the server and is faster for numbers, timestamps and `bytea`. psycopg needs the column types, because binary data has no type of its own.

```python
from datetime import datetime


def load_readings(conn: psycopg.Connection, rows: Iterable[tuple[int, datetime, float]]) -> None:
    with conn.transaction(), conn.cursor() as cur:
        with cur.copy("COPY reading (sensor_id, at, value) FROM STDIN (FORMAT BINARY)") as copy:
            copy.set_types(["int4", "timestamptz", "float8"])
            for row in rows:
                copy.write_row(row)
```

The type names must match the columns exactly (`int4` for `integer`, `int8` for `bigint`); a mismatch fails with a server error about the binary format.

## 4. COPY TO STDOUT for exports

`COPY ... TO STDOUT` reads a table or query out in a stream. `copy.rows()` yields Python tuples; iterating the copy object yields raw text or CSV chunks for writing to a file.

```python
from pathlib import Path


def export_csv(conn: psycopg.Connection, path: Path) -> None:
    query = "COPY (SELECT id, owner, cents FROM account ORDER BY id) TO STDOUT (FORMAT csv, HEADER true)"
    with conn.cursor() as cur, path.open("wb") as out:
        with cur.copy(query) as copy:
            for chunk in copy:
                out.write(chunk)


def iter_accounts(conn: psycopg.Connection) -> Iterator[tuple[int, str, int]]:
    with conn.cursor() as cur:
        with cur.copy("COPY account (id, owner, cents) TO STDOUT") as copy:
            copy.set_types(["int4", "text", "int8"])         # without it, every value is a str
            yield from copy.rows()
```

## 5. executemany with returning and results()

`executemany` runs one statement per parameter set and, since psycopg 3.1, sends them in pipeline (batch) mode, so it costs one round trip per batch rather than per row. With `returning=True` each statement's result is kept, and `cursor.results()` (3.3) walks them.

```python
def insert_returning_ids(conn: psycopg.Connection, names: list[str]) -> list[int]:
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("INSERT INTO tag (name) VALUES (%s) RETURNING id", [(n,) for n in names], returning=True)
        return [row[0] for result in cur.results() for row in result.fetchall()]
```

For tens of thousands of rows with ids, a single `INSERT ... SELECT * FROM unnest(%s::text[]) RETURNING id` with one array parameter is faster than any per-row method.

## 6. Pipeline mode

Pipeline mode sends several statements without waiting for each reply, which removes network latency between independent statements. It matters most when the server is remote.

```python
def bump_counters(conn: psycopg.Connection, ids: list[int]) -> None:
    with conn.transaction(), conn.pipeline():
        for counter_id in ids:
            conn.execute("UPDATE counter SET n = n + 1 WHERE id = %s", (counter_id,))
```

- Results are fetched when the pipeline syncs (at the end of the block, or when you call `fetchone()` on a cursor inside it).
- An error stops the rest of the pipeline; the transaction block rolls back as usual.
- The async connection has the same `async with aconn.pipeline():`.

## 7. Server-side cursors and stream()

A normal cursor fetches the whole result into client memory at `execute`. A named cursor (`conn.cursor(name=...)`) keeps the result on the server and fetches `itersize` rows at a time.

```python
def scan_events(conn: psycopg.Connection) -> Iterator[tuple[int, str]]:
    with conn.transaction():                                  # named cursors live inside a transaction
        with conn.cursor(name="scan_events") as cur:
            cur.itersize = 5_000                              # rows per network fetch
            cur.execute("SELECT id, kind FROM event ORDER BY id")
            yield from cur
```

- Without `withhold=True`, the cursor exists only until the transaction ends. `withhold=True` keeps it after commit, at the cost of the server materialising the result.
- A long scan holds a snapshot open, which delays vacuum on busy tables. Page by key (`WHERE id > %s ORDER BY id LIMIT 5000`) for jobs that run for minutes.
- `cur.stream(query, params, size=1000)` streams rows from an ordinary cursor without a server-side cursor; it cannot be mixed with other queries on that connection until it finishes.

## 8. Prepared statements and PgBouncer

psycopg prepares a statement on the server automatically after it has run `prepare_threshold` times (default 5) on a connection, which saves parsing and planning on repeated queries. `execute(..., prepare=True)` prepares at once; `prepare=False` never.

| Setting | Effect |
|---|---|
| `prepare_threshold=5` (default) | prepare after the fifth run of the same query text |
| `prepare_threshold=0` | prepare every query on first use |
| `prepare_threshold=None` | never prepare; needed behind PgBouncer in transaction mode before 1.21, or any pooler that moves sessions |
| `conn.prepared_max` | how many prepared statements a connection keeps (LRU) |

PgBouncer 1.21+ can track prepared statements when `max_prepared_statements` is set in its config; without that, a statement prepared on one server connection is missing on the next one and fails with `prepared statement "_pg3_0" does not exist`.
