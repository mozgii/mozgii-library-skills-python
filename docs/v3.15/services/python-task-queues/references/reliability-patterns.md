---
title: Reliability patterns for background jobs
author: MOZGII, Inc.
lang: en
---

# Reliability patterns for background jobs

Patterns that keep background jobs correct when messages repeat, workers die and producers crash between two writes: idempotency keys, the transactional outbox, deduplication and locks, poison messages and dead letters, long tasks with checkpoints, routing by latency, and sizing workers. They apply to every queue library; the examples use SQLite from the standard library so they run anywhere, and the same SQL works in PostgreSQL.

## Contents

This reference has 8 sections.

1. Delivery guarantees in one table
2. Idempotent consumers with a processed-messages table
3. The transactional outbox
4. Deduplication and per-resource locks
5. Poison messages and dead letters
6. Long tasks: chunks and checkpoints
7. Queues by latency and sizing workers
8. Gotchas

## 1. Delivery guarantees in one table

A delivery guarantee says how often a message may run. "Exactly once" is not something a queue can promise across a crash; it is at-least-once delivery plus an idempotent consumer.

| Guarantee | How it happens | Risk |
|---|---|---|
| at most once | ack before running (Celery default) | a crash loses the job |
| at least once | ack after running (late ack) | a crash after the work, before the ack, runs it again |
| effectively once | at least once + idempotent consumer | none, if the "done" record and the effect commit together |

## 2. Idempotent consumers with a processed-messages table

An idempotent consumer records each message's key in the same transaction as its effect; a redelivered message finds the key and does nothing. The key is a business identity (`invoice-42-send`), not a random id per delivery.

```python
import sqlite3

db = sqlite3.connect(":memory:", isolation_level=None)         # explicit BEGIN/COMMIT below
db.executescript("""
CREATE TABLE processed(key TEXT PRIMARY KEY, done_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE balances(account TEXT PRIMARY KEY, cents INTEGER NOT NULL);
INSERT INTO balances VALUES ('acme', 0);
""")


def apply_payment(key: str, account: str, cents: int) -> bool:
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("INSERT OR IGNORE INTO processed(key) VALUES (?)", (key,)).rowcount == 0:
            db.execute("ROLLBACK")
            return False                                         # seen before: skip
        db.execute("UPDATE balances SET cents = cents + ? WHERE account = ?", (cents, account))
        db.execute("COMMIT")
        return True
    except BaseException:
        db.execute("ROLLBACK")
        raise


print(apply_payment("payment-981", "acme", 5000), apply_payment("payment-981", "acme", 5000))   # True False
print(db.execute("SELECT cents FROM balances").fetchone()[0])                                    # 5000
```

- In PostgreSQL the same is `INSERT ... ON CONFLICT DO NOTHING` and checking the row count.
- Expire old keys (days or weeks) with a periodic cleanup; keep them longer than the longest possible redelivery delay.
- Side effects outside the database (email, payment API) cannot join the transaction. Send the provider an idempotency key derived from the same business key, or record "sending" first and reconcile on retry.

## 3. The transactional outbox

The outbox pattern makes "save data and enqueue a job" atomic: the job is written as a row in the same transaction as the data, and a relay process moves committed rows to the broker. A crash can delay a job but never lose it or enqueue a job for rolled-back data.

```python
import json
import sqlite3

db = sqlite3.connect(":memory:")
db.executescript("""
CREATE TABLE orders(id INTEGER PRIMARY KEY, total INTEGER);
CREATE TABLE outbox(id INTEGER PRIMARY KEY, task TEXT, args TEXT, sent_at TEXT);
""")


def place_order(total: int) -> int:
    with db:                                                     # one transaction for both rows
        order_id = db.execute("INSERT INTO orders(total) VALUES (?)", (total,)).lastrowid
        db.execute("INSERT INTO outbox(task, args) VALUES (?, ?)", ("send_receipt", json.dumps([order_id])))
    return order_id


def relay(enqueue, batch: int = 100) -> int:
    rows = db.execute("SELECT id, task, args FROM outbox WHERE sent_at IS NULL ORDER BY id LIMIT ?", (batch,)).fetchall()
    for row_id, task, args in rows:
        enqueue(task, json.loads(args))                          # e.g. app.send_task(task, args=...)
        with db:
            db.execute("UPDATE outbox SET sent_at = CURRENT_TIMESTAMP WHERE id = ?", (row_id,))
    return len(rows)


place_order(4900)
sent: list[tuple[str, list]] = []
print(relay(lambda task, args: sent.append((task, args))), sent)   # 1 [('send_receipt', [1])]
```

- The relay enqueues and then marks the row: a crash between the two sends the job twice, so the consumer must be idempotent (section 2).
- With several relay processes on PostgreSQL, select with `FOR UPDATE SKIP LOCKED` so each row goes to one relay.
- Procrastinate makes the outbox unnecessary when Postgres is the broker: defer the job on your own connection inside the transaction.

## 4. Deduplication and per-resource locks

Deduplication stops the same job from being queued twice; a lock stops two jobs from working on the same resource at once. They solve different problems and are often needed together.

| Need | Celery | Dramatiq | RQ | arq | Procrastinate | Huey |
|---|---|---|---|---|---|---|
| refuse a duplicate while one waits | own Redis key | own Redis key | `job_id=` | `_job_id=` | `queueing_lock=` | own storage key |
| one job per resource at a time | own lock | own lock | own lock | own lock | `lock=` | `huey.lock_task(name)` |

A Redis lock is `SET key value NX PX ttl` with a random value, released only if the value still matches; the TTL must exceed the job's time limit, or a slow job loses its lock while still running. Database row locks (`SELECT ... FOR UPDATE`) are simpler when the resource is a row.

## 5. Poison messages and dead letters

A poison message fails every time (bad data, a bug for one input) and, retried forever, blocks a worker and floods logs. Bounded retries move it to a dead-letter place where a person can inspect and replay it.

- Classify exceptions: transient ones retry with backoff; permanent ones (validation, not found, 4xx) fail at once. In Dramatiq list them in `throws=`; in Celery list only transient ones in `autoretry_for`.
- Log the job id, task name, attempt number and the exception type with every failure; never log the full arguments if they may contain personal data.
- Dead letters per library: Celery stores the failure in the result backend and emits `task_failed` events (route them to a table); Dramatiq has a dead-letter queue per queue (kept 7 days by default); RQ has the `FailedJobRegistry` and `rq requeue`; Procrastinate keeps failed jobs in its table with status `failed`; Huey stores the error as the task result.
- After fixing the bug, replay dead letters in small batches and watch the error rate.

## 6. Long tasks: chunks and checkpoints

A long task is hard to stop, retry or monitor; one that restarts from the beginning after 50 minutes of work wastes the work and may run past a visibility timeout. Split it into chunks or record progress.

```python
import sqlite3

db = sqlite3.connect(":memory:")
db.executescript("CREATE TABLE progress(job TEXT PRIMARY KEY, last_id INTEGER);"
                 "CREATE TABLE items(id INTEGER PRIMARY KEY, done INTEGER DEFAULT 0);")
db.executemany("INSERT INTO items(id) VALUES (?)", [(i,) for i in range(1, 1001)])


def reindex(job: str, chunk: int = 200, stop_after: int | None = None) -> int:
    row = db.execute("SELECT last_id FROM progress WHERE job = ?", (job,)).fetchone()
    last = row[0] if row else 0
    chunks = 0
    while ids := [r[0] for r in db.execute("SELECT id FROM items WHERE id > ? ORDER BY id LIMIT ?", (last, chunk))]:
        with db:                                                  # work and checkpoint commit together
            db.executemany("UPDATE items SET done = 1 WHERE id = ?", [(i,) for i in ids])
            last = ids[-1]
            db.execute("INSERT INTO progress VALUES (?, ?) ON CONFLICT(job) DO UPDATE SET last_id = excluded.last_id", (job, last))
        chunks += 1
        if stop_after and chunks == stop_after:
            break                                                 # simulate a worker stopping mid-job
    return last


print(reindex("reindex-2026-10", stop_after=2))   # 400: stopped after two chunks
print(reindex("reindex-2026-10"))                 # 1000: resumed from 400, not from 0
```

- Fan out instead when chunks are independent: one coordinator task enqueues one task per chunk (Celery `group`, Dramatiq `group`, Huey `group`), and a final step runs when all finish.
- Check for a shutdown request between chunks and re-enqueue the task with its checkpoint, so deploys do not wait for an hour-long job.
- Report progress (chunks done, total) to a table or metric so the UI can show it.

## 7. Queues by latency and sizing workers

Queues separated by latency keep a flood of slow jobs from delaying fast ones: an `interactive` queue for jobs a person is waiting for, `default`, and `bulk` for exports and reindexing, each with its own workers.

- Worker type by work: processes (Celery prefork, Dramatiq processes, RQ fork) for CPU-heavy jobs; threads or asyncio (Dramatiq threads, Huey threads, arq) for I/O-bound jobs.
- Database connections: workers times connections per worker must stay under the database's limit; with 8 processes and 8 threads each, a pool of 2 per process already needs 16 connections.
- Prefetch: a worker that reserves many messages hides them from idle workers. Celery `worker_prefetch_multiplier=1` for long tasks; Dramatiq's prefetch is per worker thread.
- Scale on the oldest message's age, not only on queue length: 1,000 one-millisecond jobs are fine; 10 one-hour jobs are not.
- Priorities inside one queue (RabbitMQ priorities, Dramatiq `priority`, Procrastinate `priority`) can starve low-priority work; separate queues with reserved workers are easier to reason about.

## 8. Gotchas

These are the reliability problems that survive the first launch.

1. **A customer is charged twice after a worker restart.** At-least-once delivery without an idempotency key at the payment provider. Send a key derived from the order id.
2. **Jobs appear for orders that do not exist.** The job was enqueued and the transaction rolled back. Use an outbox or transactional deferral.
3. **A lock expires while its job still runs, and a second job starts.** The lock TTL is shorter than the job's run time. Make the TTL exceed the hard time limit, or renew it.
4. **The dead-letter queue grows silently.** Nobody alerts on it. Export its size as a metric and alert above zero.
5. **The database refuses connections when workers scale up.** Each worker process has its own pool. Budget connections per worker, or put a pooler (PgBouncer) in front.
6. **Interactive jobs wait behind an export.** One queue for everything. Split queues by latency and give each its own workers.
