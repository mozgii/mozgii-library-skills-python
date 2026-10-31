---
name: python-task-queues
description: Runs background jobs in Python with a task queue - choosing among Celery, Dramatiq, RQ, arq, Procrastinate (Postgres) and Huey, task arguments as ids not objects, idempotent tasks for at-least-once delivery, late acknowledgement and visibility timeouts, retries with exponential backoff, enqueueing after the database commit, periodic tasks with one scheduler, result backends, testing tasks eagerly or with in-memory brokers, graceful worker shutdown, and monitoring queue depth and failures. Use when sending email or generating reports in the background, moving slow work out of a request or a UI thread, retrying flaky calls later, scheduling recurring jobs, or picking between Celery, Dramatiq, RQ, arq, Procrastinate and Huey.
metadata:
  tags:
  - python
  - task-queue
  - background-jobs
  - celery
  - dramatiq
  - rq
  - procrastinate
  - huey
---

# Background task queues in Python

A task queue moves work out of the process that asked for it: the producer stores a message in a broker (Redis, RabbitMQ, Postgres, SQLite), and separate worker processes run it, retry it and record the outcome. The libraries differ in brokers and features, but the rules that keep jobs correct are the same everywhere: small arguments, idempotent tasks, late acknowledgement, bounded retries and a clean shutdown. In-process concurrency is the `python-asyncio` and `python-concurrency` skills.

## Step 0: read the installed versions

Read the library and broker client versions before writing configuration; defaults changed in recent major releases (Dramatiq 2.0, Huey 3.0).

```bash
uv run python -c "import sys; print(sys.version_info[:3])"
uv pip list 2>/dev/null | grep -iE "^(celery|kombu|dramatiq|rq|arq|procrastinate|huey|redis|flower) "
```

Current releases (October 2026): Celery 5.6.3 (5.7 in release candidates), Dramatiq 2.2.1, RQ 3.0.0, arq 0.28.0, Procrastinate 3.10.0, Huey 3.5.3, redis-py 8.1.0, Flower 2.2.0.

## When a task queue is the right tool

A task queue pays off when work must survive a restart, run on other machines, or be retried later. For work that may die with the process, simpler tools are enough.

| Need | Use |
|---|---|
| run something concurrently inside one process | `asyncio.TaskGroup`, a thread or process pool |
| a job that must not be lost if the app restarts | a task queue |
| retry a failing call minutes or hours later | a task queue with retries |
| spread CPU work over several machines | a task queue (or a batch system) |
| a desktop app's background work | an in-process queue, or Huey with SQLite: no server to install |

## Choosing a library

The library choice follows from the broker you already run and whether your code is sync or async.

| Library | Broker | Style | Pick when |
|---|---|---|---|
| Celery 5.6 | RabbitMQ, Redis, SQS | sync; prefork, threads, gevent pools | many features (workflows, rate limits, routing), a team that knows it |
| Dramatiq 2.2 | RabbitMQ, Redis | sync and async actors | a simpler Celery: safe defaults (late acks, retries on by default) |
| RQ 3.0 | Redis | sync; a fork per job by default | simple Redis jobs with a small API, concurrency limits and a built-in cron scheduler |
| arq 0.28 | Redis | asyncio only | async code calling async APIs; needs `redis<6`, so not beside redis-py 8.1 |
| Procrastinate 3.10 | PostgreSQL | sync and async | the app already uses Postgres; jobs deferred in the same transaction as the data |
| Huey 3.5 | Redis, SQLite, Postgres, file, memory | sync; threads, processes or greenlets | small apps and desktop tools; SQLite needs no server |

- Windows: Celery's prefork pool is not supported there (run `--pool=solo` or `--pool=threads` for development), and RQ's default worker forks, while `rq.SpawnWorker`, which avoids `fork()`, needs POSIX since 3.0 (`os.posix_spawn`). Huey thread workers, arq and Procrastinate are plain Python and run on all three OSes.
- Kafka, NATS and cloud queues (SQS, Pub/Sub) are message brokers, not task queues; they need their own consumer loop with the same rules.

## Task arguments are ids, not objects

A task argument is serialized into the broker and read later, maybe hours later on another machine. Pass ids and small values, and load current data inside the task.

```python
# WRONG: pickles a whole ORM object; the task works on stale data and breaks when the class changes
send_invoice.delay(invoice)

# RIGHT: a JSON-serializable id; the task loads the current row
send_invoice.delay(invoice.id)
```

- Celery, Dramatiq and Procrastinate serialize to JSON; RQ, arq and Huey default to pickle. Anyone who can write to a pickle broker can run code in your workers: use `rq.serializers.JSONSerializer`, arq's `job_serializer`/`job_deserializer`, or a private broker.
- Large inputs (files, datasets) go to storage first; the task gets a path or object key.
- Keep argument lists stable: add new parameters with defaults so messages already in the queue still run after a deploy.

## Idempotent tasks for at-least-once delivery

Every reliable task queue delivers at least once: a worker can finish the work and die before acknowledging, and the message runs again. Design each task so running it twice has the same effect as running it once.

```python
import sqlite3


def charge_order(db: sqlite3.Connection, order_id: int, idempotency_key: str) -> None:
    with db:                                                    # one transaction
        cur = db.execute("INSERT OR IGNORE INTO charges(key, order_id) VALUES (?, ?)", (idempotency_key, order_id))
        if cur.rowcount == 0:
            return                                              # already done by an earlier delivery
        db.execute("UPDATE orders SET status = 'charged' WHERE id = ?", (order_id,))


db = sqlite3.connect(":memory:")
db.executescript("CREATE TABLE charges(key TEXT PRIMARY KEY, order_id INT);"
                 "CREATE TABLE orders(id INT PRIMARY KEY, status TEXT); INSERT INTO orders VALUES (7, 'new');")
charge_order(db, 7, "order-7-charge")
charge_order(db, 7, "order-7-charge")                           # redelivery: no second charge
print(db.execute("SELECT count(*) FROM charges").fetchone()[0])   # 1
```

- Record "done" in the same transaction as the effect, keyed by a unique idempotency key.
- External APIs: send the provider's idempotency key header (payment and email APIs support one) built from the task's business id, not a random value per attempt.
- "Set to value X" is idempotent; "add 1" is not. Prefer absolute updates.

## Acknowledgement and visibility timeouts

Acknowledgement (ack) tells the broker a message is done. Ack after the task finishes (late ack) so a crash re-delivers the message; ack on receipt loses the job when the worker dies.

- Celery acks early by default: set `task_acks_late=True` and `task_reject_on_worker_lost=True`, and `worker_prefetch_multiplier=1` for long tasks.
- Dramatiq and Procrastinate acknowledge after the run by design; RQ moves a job whose worker died to the failed registry, or retries it when the job has retries left.
- Huey is at-most-once: it removes the message from storage when a worker dequeues it, before the task runs, so a crash in the middle of a task loses that job. Where a job must survive a dead worker, record it in your own table too, or choose another library.
- Redis-based brokers emulate acks with a visibility timeout: a message not acked within it is given to another worker. Celery on Redis defaults to one hour; a task running longer runs twice. Set `broker_transport_options={"visibility_timeout": ...}` above the longest task, or split the task.
- Late ack plus a time limit: give every task a hard time limit (Celery `task_time_limit`, Dramatiq `time_limit`, RQ `job_timeout`) so a stuck job cannot hold a worker forever.

## Retries with exponential backoff

A retry re-runs a task after a transient failure (timeout, 503, lock conflict) with growing delays and jitter, and stops after a limit. Retry only errors that can succeed later; a validation error retried ten times is ten failures.

```python
from celery import Celery

app = Celery("billing", broker="redis://localhost:6379/0")
app.conf.update(task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
                task_time_limit=300, task_soft_time_limit=240,
                broker_transport_options={"visibility_timeout": 3600})


class TransientError(Exception):
    pass


@app.task(bind=True, autoretry_for=(TransientError,), retry_backoff=2, retry_backoff_max=600,
          retry_jitter=True, max_retries=8)
def send_invoice(self, invoice_id: int) -> None:
    ...                                                         # raise TransientError on 5xx or timeouts
```

- Dramatiq retries every exception by default (`max_retries=20`, exponential backoff); narrow it with `throws=(PermanentError,)` or `retry_when=`. Since 2.0 the first backoff is never shorter than `min_backoff`, so average delays doubled compared with 1.x.
- RQ: `queue.enqueue(fn, arg, retry=Retry(max=3, interval=[10, 60, 300]))`. arq: `raise Retry(defer=seconds)`. Procrastinate: `retry=RetryStrategy(max_attempts=5, exponential_wait=2)`. Huey: `@huey.task(retries=3, retry_delay=10)`.
- After the last attempt, the job lands in a dead-letter place (Celery: the result backend and logs; Dramatiq: the dead-letter queue; RQ: the failed registry); alert on it.

## Enqueue after the database commit

A task enqueued before the transaction commits can run before the data exists, or for data that is rolled back. Enqueue after commit, or store the job in the same database transaction.

- With Procrastinate the job row can be inserted in your own transaction: `task.configure(connection=conn).defer(...)` with a psycopg connection, so job and data commit together.
- With other brokers, write an outbox row in the transaction and have a relay enqueue it after commit; a crash between commit and enqueue then only delays the job.
- An in-memory "enqueue after commit" hook (Django's `on_commit`) is simpler but loses the job if the process dies right after the commit.

## Periodic tasks with exactly one scheduler

A periodic task is enqueued by a scheduler process on a timetable; run exactly one scheduler, or every replica enqueues its own copy.

| Library | Scheduler |
|---|---|
| Celery | `celery beat` with `beat_schedule` (one process) |
| Dramatiq | none built in; APScheduler or a cron job that enqueues |
| RQ | `rq cron` with a config file of `cron.register(...)` calls (still beta); since 3.0 cron strings use the scheduler's local time zone, not UTC |
| arq | `cron_jobs=[cron(fn, hour=3, minute=0)]` in `WorkerSettings` (unique by default) |
| Procrastinate | `@app.periodic(cron="*/10 * * * *")`; workers coordinate through Postgres |
| Huey | `@huey.periodic_task(crontab(minute="0", hour="3"))`; the consumer schedules |

Schedules in UTC avoid daylight-saving gaps and doubled runs; periodic tasks must be idempotent too, since a scheduler restart can fire a run twice.

## Results and result backends

A result backend stores return values so the producer can wait for them. Most background work needs no result: the task writes to the database and the UI reads that.

- Celery: set `task_ignore_result=True` globally and enable results only where a caller waits; results otherwise accumulate (set `result_expires`).
- Dramatiq 2.0 requires an explicit backend in the `Results` middleware; RQ keeps results for `result_ttl` (500 s by default); arq for `keep_result` (one hour).
- Never block one task waiting for another task's result inside a worker; it can deadlock the pool. Use chains, groups or callbacks.

## Testing tasks

Test the task function directly as plain Python, then test the wiring with the library's in-memory or eager mode. Eager modes skip serialization, so also test that arguments survive a JSON round trip.

| Library | Test mode |
|---|---|
| Celery | `celery.contrib.testing.worker.start_worker(app)` with `memory://` broker, or `task_always_eager=True` (skips serialization and routing) |
| Dramatiq | `StubBroker` plus `Worker`; `broker.join(queue)` re-raises actor errors since 2.0 |
| RQ | `Queue(is_async=False)` runs jobs inline, or `SimpleWorker(...).work(burst=True)` against fakeredis |
| Procrastinate | `procrastinate.testing.InMemoryConnector`, then `run_worker_async(wait=False)` |
| Huey | `MemoryHuey(immediate=True)` |

A full Dramatiq, Huey, Procrastinate and RQ test is in [library-recipes.md](references/library-recipes.md).

## Graceful worker shutdown

A worker stopping for a deploy should finish or hand back its current jobs. Celery, Dramatiq, RQ and Procrastinate treat SIGTERM as "stop taking work and finish what is running". Two do not by default: Huey interrupts running tasks on SIGTERM unless the consumer runs with `--graceful-signal TERM`, and arq cancels them unless `job_completion_wait` is set.

- Celery: SIGTERM is a warm shutdown (finish running tasks); `worker_soft_shutdown_timeout` (5.5+) bounds the wait before a cold shutdown; SIGQUIT is cold and loses unacked work unless `acks_late`.
- Set the container or service stop timeout (`docker stop -t`, Kubernetes `terminationGracePeriodSeconds`, systemd `TimeoutStopSec`) longer than the longest task, or make long tasks checkpoint and re-enqueue themselves.
- Signals and process supervision in general are the `python-services-shutdown` skill.

## Monitoring

A queue is healthy when its depth stays bounded, the age of the oldest message stays low, and failures are visible. Export these as metrics (the `python-observability-otel` skill) and alert on them.

- Queue depth and oldest-message age per queue; a growing backlog means too few workers or a stuck task.
- Failure and retry counts per task name; the dead-letter queue size.
- Celery: Flower 2.2 for live workers and tasks; Dramatiq's `Prometheus` middleware (no longer on by default in 2.0); RQ's dashboard and `rq info`.

## Verify

Prove the three promises every queue depends on: arguments survive the broker, a redelivery changes nothing, and failures retry. This test runs RQ on fakeredis with no server; the same three asserts apply to any library in [library-recipes.md](references/library-recipes.md). The job must be importable by the worker, so it lives in its own module.

```python
# tests/test_jobs.py
import json

from fakeredis import FakeStrictRedis
from rq import Queue, Retry, SimpleWorker
from rq.serializers import JSONSerializer

from my_app.jobs import flaky_export                 # raises on its first call, returns "ok" after


def test_arguments_survive_json() -> None:
    args = {"invoice_id": 42, "attempt_key": "invoice-42"}
    assert json.loads(json.dumps(args)) == args


def test_flaky_job_retries_then_finishes() -> None:
    redis = FakeStrictRedis()
    queue = Queue("exports", connection=redis, serializer=JSONSerializer)
    job = queue.enqueue(flaky_export, 9, retry=Retry(max=2))
    SimpleWorker([queue], connection=redis, serializer=JSONSerializer).work(burst=True)
    job.refresh()
    assert job.get_status().value == "finished" and job.return_value() == "ok"
```

Call each task twice with one key in a test of its own, as `charge_order` does above, and assert a single effect. On a staging broker, kill a worker with `SIGKILL` in the middle of a job and confirm the job runs again; then send `SIGTERM` and confirm it finishes.

## Anti-patterns

| Instead of | Do |
|---|---|
| an ORM object as an argument | its id; load the row inside the task |
| retrying every exception forever | transient errors, backoff with jitter, a maximum |
| enqueueing inside an open transaction | after commit, an outbox, or Procrastinate's shared transaction |
| a scheduler in every replica | exactly one scheduler process |
| one task waiting on another task's result | a chain, a group or a callback |
| pickle on a broker others can reach | JSON serialization, or a private broker |

## Checklist

A task queue setup is ready when every item holds.

- [ ] Task arguments are ids and small JSON values; no pickle from shared brokers.
- [ ] Every task is idempotent; effects are recorded with a unique key in one transaction.
- [ ] Late acks are on; the visibility timeout exceeds the longest task; every task has a time limit.
- [ ] Retries cover transient errors only, with backoff, jitter and a maximum; failed jobs are alerted on.
- [ ] Jobs are enqueued after commit, or in the same transaction (Procrastinate) or through an outbox.
- [ ] Exactly one scheduler runs periodic tasks, in UTC.
- [ ] Results are ignored unless a caller needs them, with an expiry.
- [ ] Tests run tasks through an in-memory broker with JSON serialization.
- [ ] Workers get SIGTERM and enough time to finish; queue depth and failures are monitored.

## Gotchas

These are the task-queue failures seen most.

1. **A long task runs twice on Redis.** It outlived the visibility timeout and was redelivered. Raise `visibility_timeout` or split the task.
2. **The task cannot find the row it was given.** It was enqueued before the transaction committed. Enqueue after commit or use an outbox.
3. **Every replica sends the daily email.** Each started its own scheduler. Run one scheduler.
4. **Jobs vanish when a worker is killed.** Early acknowledgement (Celery's default). Set `task_acks_late=True` and `task_reject_on_worker_lost=True`.
5. **Tests pass, production fails with `TypeError: Object of type Decimal is not JSON serializable`.** Eager mode skipped serialization. Test through a real or in-memory broker, and pass plain types.
6. **One slow task blocks many short ones.** Prefetching gave the busy worker a batch. Set `worker_prefetch_multiplier=1` and route long tasks to their own queue.
7. **A Dramatiq test raises an actor's exception from `broker.join`.** Dramatiq 2.0 made `fail_fast` the default. Assert with `pytest.raises`, or pass `fail_fast=False`.
8. **The broker's memory fills with results.** A result backend with no expiry. Ignore results or set an expiry.

## References

The reference files hold the per-library setup and the reliability patterns.

- [library-recipes.md](references/library-recipes.md): a runnable setup, retry, periodic task and test for Celery, Dramatiq, RQ, arq, Procrastinate and Huey
- [reliability-patterns.md](references/reliability-patterns.md): idempotency keys, the transactional outbox, deduplication and locks, poison messages and dead letters, long tasks with checkpoints, priorities and routing, and scaling workers

## See also

- `python-asyncio`: concurrency inside one process
- `python-concurrency`: threads and process pools inside one process
- `python-services-shutdown`: signals and stop timeouts for workers
- `python-observability-otel`: queue depth and failure metrics
- `psycopg-postgres`: the database behind Procrastinate
