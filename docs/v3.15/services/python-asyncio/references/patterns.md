---
title: asyncio patterns and recipes
author: MOZGII, Inc.
lang: en
---

# asyncio patterns and recipes

Tested recipes for recurring asyncio problems: a worker pool over a queue, retries with backoff, rate limiting, graceful shutdown, async generators and context managers, and racing for the first result. All run on Python 3.13+ unless noted.

## Contents

This reference has 8 sections.

1. Worker pool over a queue
2. Retries with exponential backoff and jitter
3. Rate limiting
4. Graceful shutdown on SIGTERM
5. Async context managers and generators
6. First result wins
7. Progress over as_completed
8. Gotchas

## 1. Worker pool over a queue

A worker pool is a fixed number of tasks that take items from an `asyncio.Queue`; memory stays flat however many items there are, and `maxsize` gives backpressure to the producer.

```python
import asyncio


async def worker(queue: asyncio.Queue[int], results: list[int]) -> None:
    while True:
        try:
            item = await queue.get()
        except asyncio.QueueShutDown:          # 3.13+: the producer is done and the queue is empty
            return
        try:
            results.append(await process(item))
        finally:
            queue.task_done()


async def run_pool(items: list[int], workers: int = 4) -> list[int]:
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=100)
    results: list[int] = []
    async with asyncio.TaskGroup() as tg:
        for _ in range(workers):
            tg.create_task(worker(queue, results))
        for item in items:
            await queue.put(item)              # waits while the queue is full: backpressure
        await queue.join()                     # every item processed
        queue.shutdown()                       # wake the workers so they exit
    return results
```

Before 3.13, send one sentinel (`None`) per worker instead of `shutdown()`. A worker that raises fails the whole group, which is usually right; catch per-item errors inside the loop when one bad item must not stop the rest.

## 2. Retries with exponential backoff and jitter

A retry helper waits longer after each failure and adds randomness (jitter) so many clients do not retry in lockstep.

```python
import asyncio
import random
from collections.abc import Awaitable, Callable


async def retry[R](
    fn: Callable[[], Awaitable[R]],
    *,
    attempts: int = 5,
    base: float = 0.2,
    cap: float = 10.0,
    retry_on: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError),
) -> R:
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except retry_on:
            if attempt == attempts:
                raise
            await asyncio.sleep(random.uniform(0, min(cap, base * 2**attempt)))   # "full jitter"
    raise AssertionError("unreachable")


status = await retry(lambda: client.get_status(job_id))
```

- Retry only errors that can succeed next time (timeouts, connection resets, HTTP 429 and 503), never validation errors.
- Retried operations must be idempotent, or carry an idempotency key.
- Wrap the whole retry loop in `asyncio.timeout()` to bound total time. The `tenacity` and `stamina` packages offer the same with decorators.

## 3. Rate limiting

A rate limiter spaces calls so that no more than N start per second; a semaphore limits how many run at once. APIs often need both.

```python
import asyncio
import time


class RateLimiter:
    """At most `rate` acquisitions per second, evenly spaced."""

    def __init__(self, rate: float) -> None:
        self._interval = 1.0 / rate
        self._next = time.monotonic()
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            self._next = max(now, self._next) + self._interval
        if wait > 0:
            await asyncio.sleep(wait)

    async def __aexit__(self, *exc: object) -> None:
        return None


limit = RateLimiter(rate=5)              # 5 requests per second
async with limit:
    await client.get("/items")
```

## 4. Graceful shutdown on SIGTERM

Graceful shutdown stops accepting work, lets in-flight work finish within a deadline, then exits; containers and process managers send SIGTERM first.

```python
import asyncio
import signal
import sys


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":                       # add_signal_handler is POSIX-only
        loop.add_signal_handler(signal.SIGTERM, stop.set)
        loop.add_signal_handler(signal.SIGINT, stop.set)

    async with asyncio.TaskGroup() as tg:
        server = tg.create_task(serve_forever())
        await stop.wait()
        server.cancel()                               # serve_forever's finally closes listeners
        async with asyncio.timeout(10):               # bounded drain
            await drain_in_flight()
```

On Windows, Ctrl+C arrives as `KeyboardInterrupt` in `asyncio.run`, which cancels `main()`; put cleanup in `finally` blocks so both paths run it.

## 5. Async context managers and generators

Async context managers own resources with `async with`; async generators stream results with `async for`.

```python
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator


@contextlib.asynccontextmanager
async def connection(url: str) -> AsyncIterator[Connection]:
    conn = await connect(url)
    try:
        yield conn
    finally:
        await conn.close()                    # runs on success, error and cancellation


async def pages(client: Client, url: str) -> AsyncGenerator[list[Item], None]:
    while url:
        page = await client.get_json(url)
        yield page["items"]
        url = page.get("next", "")


async with contextlib.aclosing(pages(client, "/items")) as stream:   # closes the generator on early exit
    async for batch in stream:
        if done(batch):
            break
```

`contextlib.aclosing` makes an early `break` run the generator's `finally` at once instead of whenever the garbage collector gets to it. It needs an object with `aclose()`, so `pages` is annotated `AsyncGenerator`: `AsyncIterator` has no `aclose`, and pyright refuses it there. `contextlib.AsyncExitStack` manages a variable number of async resources.

## 6. First result wins

Racing several equivalent sources and keeping the fastest answer needs the losers cancelled.

```python
async def first_success[R](*fns: Callable[[], Awaitable[R]]) -> R:
    tasks = [asyncio.create_task(fn()) for fn in fns]
    try:
        for next_done in asyncio.as_completed(tasks):
            try:
                return await next_done
            except Exception:
                continue                      # try the next one to finish
        raise RuntimeError("every source failed")
    finally:
        for t in tasks:
            t.cancel()
```

On Python 3.15, a `TaskGroup` plus `tg.cancel()` from the first successful task expresses the same race with structured cleanup.

## 7. Progress over as_completed

`asyncio.as_completed` yields awaitables in completion order, which suits progress reporting; since 3.13 it is also an async iterator that yields the original tasks.

```python
async def download_all(urls: list[str]) -> None:
    tasks = [asyncio.create_task(download(u)) for u in urls]
    async for task in asyncio.as_completed(tasks):        # 3.13+
        result = await task
        print(f"{result.url} done")
```

## 8. Gotchas

These are the mistakes these patterns usually prevent.

1. **The producer finishes but workers never exit.** No sentinel or `queue.shutdown()`. Shut the queue down after `join()`.
2. **Retries make an outage worse.** No jitter and no cap: every client retries at the same moment. Use full jitter and a total timeout.
3. **SIGTERM kills the process mid-write.** No handler was installed, so Python's default terminates at once. Install the handler and drain with a deadline.
4. **An async generator's cleanup runs late or not at all.** The loop broke out early. Wrap it in `contextlib.aclosing`.
5. **Losing tasks keep running after the race.** They were never cancelled. Cancel in `finally`.
