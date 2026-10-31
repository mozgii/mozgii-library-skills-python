---
title: Task queue recipes for Celery, Dramatiq, RQ, arq, Procrastinate and Huey
author: MOZGII, Inc.
lang: en
---

# Task queue recipes for Celery, Dramatiq, RQ, arq, Procrastinate and Huey

One working setup per library: the app and broker, a task with retries and a time limit, a periodic task, the worker command, and a test that runs without a broker server. Versions as of October 2026: Celery 5.6.3, Dramatiq 2.2.1, RQ 3.0.0, arq 0.28.0, Procrastinate 3.10.0, Huey 3.5.3. The examples marked as tests run with only the library installed (and fakeredis for RQ).

## Contents

This reference has 7 sections.

1. Celery
2. Dramatiq
3. RQ
4. arq
5. Procrastinate
6. Huey
7. Worker commands side by side

## 1. Celery

Celery is configured on an app object; tasks are decorated functions, and `celery beat` is the separate scheduler process. The settings below make delivery safe for long tasks on Redis.

```python
# billing/tasks.py
from celery import Celery
from celery.schedules import crontab
from celery.utils.log import get_task_logger

app = Celery("billing", broker="redis://localhost:6379/0", backend="redis://localhost:6379/1")
app.conf.update(
    task_acks_late=True,                  # ack after the task finishes, not when it is received
    task_reject_on_worker_lost=True,      # requeue if the worker process dies mid-task
    worker_prefetch_multiplier=1,         # one message at a time per process
    task_time_limit=300,                  # hard kill after 5 minutes
    task_soft_time_limit=240,             # SoftTimeLimitExceeded raised inside the task first
    broker_transport_options={"visibility_timeout": 3600},   # Redis: must exceed the longest task
    task_ignore_result=True,              # opt in per task when a caller waits
    result_expires=86400,
    timezone="UTC",
    beat_schedule={"nightly-cleanup": {"task": "billing.tasks.cleanup", "schedule": crontab(minute=0, hour=3)}},
)
log = get_task_logger(__name__)


class TransientError(Exception):
    pass


@app.task(bind=True, autoretry_for=(TransientError,), retry_backoff=2, retry_backoff_max=600,
          retry_jitter=True, max_retries=8, ignore_result=False)
def send_invoice(self, invoice_id: int) -> str:
    log.info("sending invoice %s (attempt %s)", invoice_id, self.request.retries + 1)
    return f"sent {invoice_id}"


@app.task
def cleanup() -> None:
    pass
```

A test runs a real worker thread against the in-memory broker, so messages go through serialization and routing like in production.

```python
from celery.contrib.testing.worker import start_worker

from billing.tasks import app, send_invoice

app.conf.update(broker_url="memory://", result_backend="cache+memory://")
with start_worker(app, perform_ping_check=False, pool="solo"):
    assert send_invoice.delay(42).get(timeout=10) == "sent 42"
```

- `SoftTimeLimitExceeded` (from `celery.exceptions`) lets the task clean up; the hard limit kills the child process.
- `celery.contrib.pytest` provides `celery_app` and `celery_worker` fixtures when `pytest` is installed with the plugin enabled.
- Routing: `app.conf.task_routes = {"billing.tasks.render_pdf": {"queue": "slow"}}`, and start a worker with `-Q slow` for long jobs.

## 2. Dramatiq

Dramatiq actors are functions decorated with `@dramatiq.actor`; the broker is set once, and middleware provides retries, time limits and age limits. Messages are acknowledged after the actor returns.

```python
import dramatiq
from dramatiq.brokers.stub import StubBroker

broker = StubBroker()                     # production: RedisBroker(url=...) or RabbitmqBroker(url=...)
dramatiq.set_broker(broker)


class PermanentError(Exception):
    pass


calls: list[int] = []


@dramatiq.actor(max_retries=5, min_backoff=1_000, max_backoff=300_000, time_limit=60_000,
                throws=(PermanentError,), queue_name="images")
def resize_image(image_id: int) -> None:
    if image_id < 0:
        raise PermanentError("no such image")       # not retried
    calls.append(image_id)


worker = dramatiq.Worker(broker, worker_timeout=100)
worker.start()
resize_image.send(17)
broker.join(resize_image.queue_name)                # waits; re-raises actor errors (fail_fast, 2.0+)
worker.join()
worker.stop()
print(calls)                                        # [17]
```

- Backoff values are milliseconds. Dramatiq 2.0 fixed the backoff so the first delay is at least `min_backoff`; average delays doubled compared with 1.x.
- Results need the `Results` middleware with an explicit backend since 2.0; the Prometheus middleware must also be added by hand since 2.0 (`dramatiq[prometheus]`).
- Dramatiq has no scheduler; enqueue periodic jobs from APScheduler or a cron entry that calls `actor.send()`.
- Async actors (`async def`) need the `AsyncIO` middleware.

## 3. RQ

RQ queues live in Redis; a job is any importable function. Workers fork a child per job by default, which isolates jobs but needs `fork()`.

```python
from fakeredis import FakeStrictRedis          # tests only; production: redis.Redis.from_url(...)
from rq import Queue, Retry, SimpleWorker
from rq.serializers import JSONSerializer

from exports import export_csv                 # jobs must be importable by the worker: def export_csv(export_id): ...

redis = FakeStrictRedis()
q = Queue("exports", connection=redis, serializer=JSONSerializer)

job = q.enqueue(export_csv, 9, job_id="export-9",                     # a stable id deduplicates
                retry=Retry(max=3, interval=[10, 60, 300]),
                job_timeout=600, result_ttl=86400, failure_ttl=7 * 86400)
SimpleWorker([q], connection=redis, serializer=JSONSerializer).work(burst=True)   # in-process, then stop
job.refresh()
print(job.get_status(), job.return_value())                            # JobStatus.FINISHED export 9
```

- `Queue(is_async=False)` runs jobs synchronously at enqueue time, for unit tests that do not care about the worker.
- Periodic jobs: a config file with `cron.register(fn, queue_name="maintenance", cron="0 3 * * *")` (or `interval=` seconds) run by `rq cron cron_config.py`; the docs still mark it beta. Since 3.0 cron strings use the scheduler's local time zone, like system cron, so set `TZ=UTC` on the scheduler process if your schedules assumed UTC.
- Concurrency limit (3.0): `queue.enqueue(send, rate_limit=RateLimit(key="smtp", concurrency=5))` caps how many jobs with that key run at once. `RateLimit` comes from `rq`. It runs a Lua script, so a test on fakeredis needs `fakeredis[lua]`, or `enqueue` fails with `unknown command 'evalsha'`.
- `enqueue_in(timedelta(minutes=5), fn, ...)` and `enqueue_at(dt, ...)` need a worker started with `--with-scheduler`.
- A job whose worker died is moved to the failed registry by cleanup, or retried when it has retries left.

### Upgrading to RQ 3.0

RQ 3.0 (October 2026) needs redis-py 5.0.1 or newer and officially supports Python 3.15. Most apps that only enqueue and run jobs keep working; check these first.

| Change | Who it affects | Do |
|---|---|---|
| cron strings evaluate in local time, not UTC | anyone using `rq cron` or `CronScheduler` | set `TZ=UTC`, or rewrite the schedules |
| `rate_limit` is reserved by `queue.enqueue()` | job functions with their own `rate_limit` argument | pass it through `kwargs=` or rename it |
| dependents pass through `READY_TO_ENQUEUE` and `ReadyJobRegistry` | code that reads job status or registries for dependent jobs | add the new status to your checks |
| RQ stops propagation on the loggers it configures | apps that expected RQ's lines in their root logger | attach your handler to RQ's loggers |
| `SpawnWorker` uses `os.posix_spawn` | anyone relying on it where POSIX is missing | run workers on Linux or macOS |
| abandoned jobs give failure callbacks and exception handlers `None` as the traceback | handlers that format the traceback | handle `None` |
| `perform_job()`, `handle_job_success()`, `monitor_work_horse()` and `maintain_heartbeats()` need an `execution` argument; `set_current_job_id()`, `current_job_working_time` and `set_current_job_working_time()` are gone; `get_current_job_id()` takes no `pipeline` | custom `Worker` subclasses | use `worker.get_current_executions()` |
| `Queue.enqueue_dependents()` takes no `pipeline`; `job.cancel(enqueue_dependents=True, pipeline=...)` now requires a watched pipeline and leaves dependents to you | code managing dependents by hand | call them outside your pipeline |
| `get_current_job()` uses `contextvars` | gevent-based custom workers | use `greenlet` 0.4.17 or newer |

## 4. arq

arq is asyncio-only: tasks are `async def` functions taking a `ctx` dict, and workers run many jobs concurrently in one event loop. It needs a real Redis, so the recipe below is configuration rather than a self-contained test. arq 0.28 requires `redis<6`, so it cannot share an environment with redis-py 8.1: give its workers their own.

```python
# app/worker.py
from arq import Retry, cron
from arq.connections import RedisSettings

import httpx


async def startup(ctx: dict) -> None:
    ctx["http"] = httpx.AsyncClient(timeout=10)


async def shutdown(ctx: dict) -> None:
    await ctx["http"].aclose()


async def fetch_rates(ctx: dict, currency: str) -> float:
    r = await ctx["http"].get(f"https://rates.example/{currency}")
    if r.status_code in (429, 503):
        raise Retry(defer=ctx["job_try"] * 10)          # 10 s, 20 s, 30 s ...
    r.raise_for_status()
    return float(r.json()["rate"])


async def nightly(ctx: dict) -> None:
    pass


class WorkerSettings:
    functions = [fetch_rates]
    cron_jobs = [cron(nightly, hour=3, minute=0)]       # unique across workers by default
    on_startup, on_shutdown = startup, shutdown
    redis_settings = RedisSettings(host="localhost")
    max_jobs = 20                                       # concurrent jobs per worker
    job_timeout = 300
    max_tries = 5
    job_completion_wait = 60                            # on SIGTERM, let running jobs finish for up to 60 s
```

```python
# enqueue from async code
from arq import create_pool
from arq.connections import RedisSettings


async def enqueue() -> None:
    redis = await create_pool(RedisSettings())
    await redis.enqueue_job("fetch_rates", "EUR", _job_id="rates-EUR-2026-10-28")   # same id: not enqueued twice
```

- arq pickles jobs by default; pass `job_serializer` and `job_deserializer` (for example `msgpack`) to the pool and the worker together.
- Blocking calls inside an arq job stall every other job in that worker; use `asyncio.to_thread` for them.
- On SIGTERM arq cancels running jobs immediately by default (`job_completion_wait=0`); set it to the time your jobs need, and keep the platform's stop timeout above it.

## 5. Procrastinate

Procrastinate stores jobs in PostgreSQL tables and uses `LISTEN/NOTIFY` to wake workers, so there is no separate broker and jobs can be deferred in the same transaction as the data that needs them.

```python
import asyncio

from procrastinate import App, RetryStrategy
from procrastinate.testing import InMemoryConnector

app = App(connector=InMemoryConnector())        # production: PsycopgConnector(conninfo="postgresql://...")
sent: list[int] = []


@app.task(queue="mail", retry=RetryStrategy(max_attempts=5, exponential_wait=2),
          queueing_lock="welcome-mail")           # at most one waiting job with this lock
async def send_welcome(user_id: int) -> None:
    sent.append(user_id)


@app.periodic(cron="*/10 * * * *")
@app.task(queue="maintenance")
async def purge_sessions(timestamp: int) -> None:
    pass


async def main() -> None:
    async with app.open_async():
        await send_welcome.defer_async(user_id=7)
        await app.run_worker_async(queues=["mail"], wait=False)   # process what is queued, then return
    print(sent)                                                   # [7]

asyncio.run(main())
```

- Atomic deferral: `send_welcome.configure(connection=conn).defer(user_id=7)` inserts the job on your psycopg connection inside your transaction; you commit both (supported by the psycopg connectors).
- `procrastinate --app=myapp.app schema --apply` creates the tables once; `procrastinate --app=myapp.app worker -q mail` runs a worker.
- `lock="..."` serialises jobs sharing a resource; `queueing_lock` refuses a second waiting job with the same key.
- Procrastinate warns when the app object is defined in the `__main__` module; define it in an importable module.

## 6. Huey

Huey is a small queue with Redis, SQLite, file and in-memory storage. With `SqliteHuey` a desktop or single-server app gets durable background jobs without running any server.

```python
from huey import MemoryHuey, SqliteHuey, crontab

huey = MemoryHuey("reports", immediate=True)       # tests: tasks run inline
# huey = SqliteHuey("reports", filename="/path/to/jobs.db")   # production on one machine


@huey.task(retries=3, retry_delay=10)
def build_report(report_id: int) -> str:
    return f"report {report_id}"


@huey.periodic_task(crontab(minute="0", hour="3"))
def nightly_cleanup() -> None:
    pass


result = build_report(5)                           # calling a task enqueues it and returns a Result
print(result())                                    # 'report 5'
```

- Run the consumer with `huey_consumer myapp.huey -w 4 -k thread`; `-k process` uses processes, `-k greenlet` gevent.
- By default SIGINT is the graceful signal and SIGTERM interrupts running tasks. Under systemd, Docker or Kubernetes (which send SIGTERM) add `--graceful-signal TERM` and a `--shutdown-timeout`.
- Huey 3.0 added `group()` and `chord()`, per-task `timeout`, and `rate_limit()`.
- The consumer runs periodic tasks itself; run one consumer per storage, or enable periodic tasks on only one.

## 7. Worker commands side by side

Each library has its own worker command; these are the forms used in service units and containers.

| Library | Worker | Scheduler |
|---|---|---|
| Celery | `celery -A billing.tasks worker -Q default,slow --concurrency 4 --loglevel INFO` | `celery -A billing.tasks beat` |
| Dramatiq | `dramatiq app.tasks --processes 2 --threads 8 --queues images` | external |
| RQ | `rq worker exports --with-scheduler --url redis://localhost:6379/0` | `rq cron cron_config.py` |
| arq | `arq app.worker.WorkerSettings` | built into the worker (`cron_jobs`) |
| Procrastinate | `procrastinate --app=myapp.app worker -q mail` | built into the worker (`@app.periodic`) |
| Huey | `huey_consumer myapp.huey -w 4 -k thread -g TERM` | built into the consumer |

All of them stop taking new work on SIGTERM. Celery, Dramatiq, RQ and Procrastinate also finish running jobs first; Huey does so only with `-g TERM`, and arq only with `job_completion_wait`. Give them a stop timeout longer than the longest job.
