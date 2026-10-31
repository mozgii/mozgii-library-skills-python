---
title: Retries and resilience in Python
author: MOZGII, Inc.
lang: en
---

# Retries and resilience in Python

Recipes for calls to things that fail sometimes: other services, databases, queues, files on network drives. Each section stands on its own; the code uses the standard library, stamina (26.1) or tenacity (9.2).

## Contents

This reference has 9 sections.

1. Which failures are transient
2. Idempotency and idempotency keys
3. Honouring Retry-After
4. One retry layer, one deadline
5. Async retries
6. A circuit breaker
7. Logging retries
8. Testing code that retries
9. The same policy with tenacity or a plain loop

## 1. Which failures are transient

A transient failure is one that a later identical attempt can succeed at; only those are worth retrying.

| Failure | Transient | Retry |
|---|---|---|
| connect timeout, read timeout, connection reset | usually | yes, with backoff |
| HTTP 429 Too Many Requests | yes | yes, after `Retry-After` |
| HTTP 502, 503, 504 | usually | yes, with backoff |
| HTTP 500 | sometimes | a few times at most; it is often a bug |
| HTTP 400, 401, 403, 404, 409, 422 | no | never: fix the request, the credentials or the state |
| database serialization failure, deadlock detected | yes | yes: rerun the whole transaction |
| unique violation, check violation | no | never |
| `ValidationError`, `TypeError`, `KeyError` in your code | no | never |
| DNS failure | sometimes | a few times; a typo in the host name never resolves |

Postgres serialization failures (`SQLSTATE 40001`) and deadlocks (`40P01`) are retried by rerunning the whole transaction, never only the failed statement.

## 2. Idempotency and idempotency keys

An operation is idempotent when doing it twice leaves the same state as doing it once; only idempotent operations are safe to retry after a timeout, because a timeout does not tell you whether the server did the work.

- Reads, `PUT` of a full resource, `DELETE` of a known id and "set status to X" are idempotent.
- `POST /payments`, "append a row", "increment a counter" and "send an email" are not.
- Make a write retryable with an idempotency key: the client generates one id per logical operation and sends it with every attempt; the server stores the key with the result and returns the stored result for a repeated key.

```python
import uuid

import httpx
import stamina


def create_payment(client: httpx.Client, amount_cents: int, customer_id: str) -> dict[str, object]:
    key = str(uuid.uuid4())                                # one key for all attempts of this payment

    @stamina.retry(on=httpx.TransportError, attempts=4, timeout=20.0)
    def attempt() -> dict[str, object]:
        r = client.post("/payments", json={"amount": amount_cents, "customer": customer_id},
                        headers={"Idempotency-Key": key})
        r.raise_for_status()
        return r.json()

    return attempt()
```

On the server side, a unique constraint on the key column makes the second insert fail cleanly, and the handler returns the first result.

## 3. Honouring Retry-After

A `Retry-After` header on 429 or 503 says how long the server wants you to wait, either in seconds or as an HTTP date; it beats any backoff formula. stamina lets the `on` callable return a wait time instead of `True`.

```python
import datetime as dt
from email.utils import parsedate_to_datetime

import httpx


def retry_after(exc: Exception) -> bool | float:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {429, 503}:
        value = exc.response.headers.get("Retry-After", "")
        if value.isdigit():
            return min(float(value), 60.0)                 # cap: never sleep for an hour on a bad header
        try:
            when = parsedate_to_datetime(value)
            return max(0.0, min((when - dt.datetime.now(dt.UTC)).total_seconds(), 60.0))
        except (TypeError, ValueError):
            return True                                    # no usable header: normal backoff
    return isinstance(exc, httpx.TransportError)
```

Use it as `@stamina.retry(on=retry_after, attempts=5, timeout=120.0)`. Returning a float overrides the backoff for that attempt (stamina 25.2 and later).

## 4. One retry layer, one deadline

Retries at several layers multiply, so pick one layer that retries and give the whole operation one deadline.

- An HTTP client with transport retries (`httpx.HTTPTransport(retries=2)` retries connection failures only), inside a function with `@stamina.retry(attempts=3)`, inside a job runner that retries a job 3 times, makes up to 27 requests per job.
- Retry at the layer that knows whether the operation is idempotent, usually the function that makes the call.
- Compute one deadline at the top and pass the remaining time down:

```python
import time


class Deadline:
    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + seconds

    def remaining(self) -> float:
        left = self._end - time.monotonic()
        if left <= 0:
            raise TimeoutError("deadline exceeded")
        return left


deadline = Deadline(30)
user = client.get("/user", timeout=deadline.remaining())
orders = client.get("/orders", timeout=deadline.remaining())
```

## 5. Async retries

Async code retries the same way, but sleeps with `await` so other tasks keep running, and a cancellation stops the retry loop at once.

```python
import asyncio

import httpx
import stamina


@stamina.retry(on=httpx.TransportError, attempts=5, timeout=30.0)   # works on async def too
async def fetch(client: httpx.AsyncClient, url: str) -> bytes:
    r = await client.get(url)
    r.raise_for_status()
    return r.content


async def fetch_all(urls: list[str]) -> list[bytes]:
    async with httpx.AsyncClient(timeout=10.0) as client, asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(fetch(client, u)) for u in urls]
    return [t.result() for t in tasks]
```

- Never retry `asyncio.CancelledError`; stamina and tenacity do not, and a hand-written loop must not catch `BaseException`.
- Bound concurrency (`asyncio.Semaphore`) as well; retries under unbounded concurrency hammer a struggling server.
- `stamina.retry_context(on=..., attempts=...)` gives an `async for attempt in ...: with attempt:` loop when a decorator does not fit.

## 6. A circuit breaker

A circuit breaker stops calling a dependency that keeps failing, so callers fail fast instead of each waiting for a timeout, and the dependency gets time to recover.

```python
import threading
import time
from collections.abc import Callable


class CircuitOpen(Exception):
    """The dependency failed repeatedly; calls are refused until the cool-down ends."""


class CircuitBreaker:
    def __init__(self, failures: int = 5, cooldown: float = 30.0) -> None:
        self._limit, self._cooldown = failures, cooldown
        self._failures, self._opened_at, self._trial_running = 0, 0.0, False
        self._lock = threading.Lock()

    def call[T](self, fn: Callable[[], T]) -> T:
        is_trial = False
        with self._lock:
            if self._failures >= self._limit:
                if self._trial_running or time.monotonic() - self._opened_at < self._cooldown:
                    raise CircuitOpen(f"circuit open for {self._cooldown:.0f} s after {self._failures} failures")
                self._trial_running = is_trial = True      # this caller alone tests the dependency
        try:
            result = fn()
        except Exception:
            with self._lock:
                self._failures += 1
                if self._failures >= self._limit:
                    self._opened_at = time.monotonic()     # a failed trial starts the cool-down again
            raise
        else:
            with self._lock:
                self._failures = 0
            return result
        finally:
            if is_trial:                                   # also after KeyboardInterrupt, or the breaker sticks
                with self._lock:
                    self._trial_running = False
```

The trial is claimed under the lock, so after the cool-down five concurrent callers send one call to the dependency and the other four get `CircuitOpen`.

Put the breaker outside the retry (`breaker.call(lambda: fetch_with_retries(...))`), so one logical call counts as one failure. Libraries such as `pybreaker` add half-open limits and listeners when a service needs them.

## 7. Logging retries

Each retry should leave one log line with the attempt number, the wait and the reason, so an operator can tell a slow dependency from a broken one.

- stamina logs every scheduled retry through `logging` (and structlog or Prometheus when they are installed); configure the `stamina` logger's level like any other library logger.
- tenacity logs when told to: `before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING)`.
- Log the final failure once, at the layer that reports it, not on every attempt at `ERROR`.

## 8. Testing code that retries

Tests check that transient errors are retried, permanent ones are not, and that the test itself never sleeps.

```python
import httpx
import pytest
import stamina


@pytest.fixture(autouse=True)
def no_retry_waits():
    stamina.set_testing(True, attempts=3)                  # no sleeping; at most 3 attempts
    yield
    stamina.set_testing(False)


def test_retries_503_then_succeeds() -> None:
    replies = iter([httpx.Response(503), httpx.Response(200, content=b"ok")])
    client = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(lambda req: next(replies)))
    assert fetch_report(client, "r1") == b"ok"


def test_does_not_retry_404() -> None:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(404)

    client = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_report(client, "missing")
    assert len(calls) == 1
```

For tenacity, patch the wait: `fetch_report_t.retry.wait = tenacity.wait_none()` in a fixture, or build the decorator from a function that takes the wait strategy as a parameter.

## 9. The same policy with tenacity or a plain loop

A retry policy written with stamina translates directly to tenacity or to a short standard-library loop. `is_transient` is the predicate from the main skill: 429, 502, 503, 504 and transport errors.

tenacity (9.2) is common in existing code:

```python
import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, stop_after_delay, wait_exponential_jitter


@retry(
    retry=retry_if_exception(is_transient),
    stop=stop_after_attempt(5) | stop_after_delay(30),
    wait=wait_exponential_jitter(multiplier=0.5, max=10),  # tenacity 9.2 renamed initial= to multiplier=
    reraise=True,                                           # raise the last error, not tenacity.RetryError
)
def fetch_report_t(client: httpx.Client, report_id: str) -> bytes: ...
```

Without a dependency, a short loop does the same job:

```python
import random
import time
from collections.abc import Callable


def call_with_retries[T](fn: Callable[[], T], *, attempts: int = 5, base: float = 0.5, cap: float = 10.0,
                         retry_on: tuple[type[Exception], ...] = (TimeoutError, ConnectionError)) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on:
            if attempt == attempts:
                raise
            time.sleep(random.uniform(0, min(cap, base * 2 ** (attempt - 1))))   # "full jitter"
    raise AssertionError("unreachable")
```
