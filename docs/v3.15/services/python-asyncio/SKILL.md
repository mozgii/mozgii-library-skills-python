---
name: python-asyncio
description: Writes correct asyncio code in Python 3.11 to 3.15 - asyncio.run and Runner, structured concurrency with TaskGroup and except*, timeouts with asyncio.timeout, cancellation that cleans up, bounded concurrency with Semaphore, queues with shutdown, keeping background tasks alive, moving blocking and CPU work off the loop with to_thread and executors, bridging threads and sync code, and debugging with debug mode and python -m asyncio ps. Use when writing async def code, running many network or I/O operations concurrently, fixing hangs, never-awaited coroutines, lost tasks or slow event loops, or choosing between asyncio, threads and processes.
metadata:
  tags:
  - python
  - asyncio
  - async
  - concurrency
  - taskgroup
  - cancellation
  - timeout
  - anyio
---

# Python asyncio

asyncio runs many I/O-bound operations concurrently on one thread: while one coroutine waits on the network or a pipe, the event loop runs another. It does nothing for CPU-bound work, and one blocking call stalls every coroutine. This skill covers the patterns that keep async code correct; longer recipes are in [patterns.md](references/patterns.md).

## When asyncio fits

asyncio is the right tool when the program waits on many I/O operations at once.

| Workload | Use |
|---|---|
| many HTTP calls, sockets, database queries, subprocess pipes | asyncio |
| a few blocking calls in an otherwise simple script | threads (`concurrent.futures.ThreadPoolExecutor`) or plain sequential code |
| CPU-bound Python (parsing, number crunching) | processes (`ProcessPoolExecutor`), or a free-threaded 3.14t build; see the `python-concurrency` skill |
| a library that must work under asyncio and trio | anyio |

Async is contagious: a function that awaits must be `async def`, and so must its callers. Keep the async layer at the I/O edges and keep pure logic synchronous.

## Step 0: read the Python version

asyncio gained important APIs in every recent release, so check what the minimum supported version offers.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
grep -n "requires-python" pyproject.toml
```

| Version | Added |
|---|---|
| 3.11 | `TaskGroup`, `asyncio.timeout()`, `Runner`, `ExceptionGroup` and `except*` |
| 3.12 | eager task factory, faster tasks |
| 3.13 | `Queue.shutdown()`, `as_completed()` as an async iterator |
| 3.14 | `python -m asyncio ps PID` and `pstree PID`; `get_event_loop()` raises without a running loop; policy API deprecated (removal in 3.16) |
| 3.15 | `TaskGroup.cancel()` to stop a group early |

## Run the program

`asyncio.run()` creates the event loop, runs one coroutine to completion, cancels leftovers and closes the loop.

```python
import asyncio


async def main() -> int:
    ...
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- Call `asyncio.run` once, at the entry point. Several calls from sync code share a loop through `with asyncio.Runner() as runner: runner.run(a()); runner.run(b())`.
- Never call `asyncio.get_event_loop()` to start work: since 3.14 it raises `RuntimeError` when no loop runs. Inside a coroutine use `asyncio.get_running_loop()`.
- A different loop (uvloop, or the selector loop on Windows for libraries that need it) goes through `asyncio.run(main(), loop_factory=...)`, not the deprecated policy API.

## Structured concurrency with TaskGroup

A `TaskGroup` runs tasks concurrently and does not exit until all of them finish; if one fails, it cancels the rest and raises an `ExceptionGroup`.

```python
import asyncio


async def fetch(item_id: int) -> dict[str, int]:
    await asyncio.sleep(0.01)                    # stands in for a network call
    return {"id": item_id}


async def fetch_many(ids: list[int]) -> list[dict[str, int]]:
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(fetch(i)) for i in ids]
    return [t.result() for t in tasks]           # all done here, in input order


async def main() -> None:
    try:
        await fetch_many([1, 2, 3])
    except* TimeoutError as group:               # handle one kind, let others propagate
        print(f"{len(group.exceptions)} requests timed out")
```

- Prefer `TaskGroup` to `asyncio.gather`: a failing task in `gather` leaves the others running unless you cancel them yourself.
- `except*` matches inside an `ExceptionGroup`; a plain `except ValueError` does not catch a group that contains one.
- Python 3.15: `tg.cancel()` stops the whole group from inside (for example, once the first result is good enough).

## Timeouts

`asyncio.timeout()` cancels the block when the deadline passes and raises `TimeoutError` at the `async with`.

```python
import asyncio


async def fetch_status() -> int:
    await asyncio.sleep(0.01)                    # stands in for a network call
    return 200


async def get_status() -> int:
    try:
        async with asyncio.timeout(5):
            return await fetch_status()
    except TimeoutError:
        return -1
```

- Put the timeout around the whole operation, including retries, so the total time is bounded.
- `asyncio.timeout_at(loop.time() + 5)` takes an absolute deadline; `cm.reschedule(...)` moves it.
- `asyncio.wait_for(coro, 5)` still works for one awaitable; `timeout()` composes better.
- `TimeoutError` is the builtin since 3.11; `asyncio.TimeoutError` is an alias.

## Cancellation

Cancelling a task raises `asyncio.CancelledError` at its current `await`; the task must let it propagate after cleaning up.

```python
import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable


async def stream_to_file(download: Callable[[str, str], Awaitable[None]], url: str, path: str) -> None:
    tmp = path + ".part"
    try:
        await download(url, tmp)
        os.replace(tmp, path)
    except asyncio.CancelledError:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)                   # clean up, then re-raise
        raise
```

- `CancelledError` derives from `BaseException`, so `except Exception` does not catch it, but a bare `except:` or `except BaseException:` does. Never swallow it: the task then looks finished while its caller waits for a cancellation that never completes.
- Put cleanup in `finally` or `async with`; both run on cancellation.
- `asyncio.shield(aw)` protects an inner operation from the outer cancel, but the outer `await` still raises; use it rarely (a commit that must complete).

## Bounded concurrency

Unbounded concurrency (10,000 tasks at once) exhausts sockets, file descriptors and the remote server's patience; a semaphore caps it.

```python
import asyncio
from collections.abc import Awaitable, Callable, Iterable


async def bounded_map[T, R](fn: Callable[[T], Awaitable[R]], items: Iterable[T], limit: int = 10) -> list[R]:
    sem = asyncio.Semaphore(limit)

    async def one(item: T) -> R:
        async with sem:
            return await fn(item)

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(one(i)) for i in items]
    return [t.result() for t in tasks]
```

For millions of items, do not create a task per item up front: a fixed pool of workers reading an `asyncio.Queue` keeps memory flat ([patterns.md](references/patterns.md) §1).

## Background tasks must be referenced

The event loop keeps only a weak reference to tasks, so a task created and forgotten can be garbage-collected before it finishes.

```python
import asyncio
from collections.abc import Coroutine
from typing import Any

background: set[asyncio.Task[None]] = set()


def spawn(coro: Coroutine[Any, Any, None]) -> None:
    task = asyncio.create_task(coro)
    background.add(task)                          # strong reference until it finishes
    task.add_done_callback(background.discard)
```

Better still, create long-lived tasks inside a `TaskGroup` that lives as long as the service, so their errors surface instead of being logged as "Task exception was never retrieved".

## Blocking and CPU work

Any call that blocks (file I/O on slow disks, `time.sleep`, `requests`, a sync database driver, heavy computation) freezes the loop for every coroutine. Move it off the loop.

```python
import asyncio
import hashlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path


def hash_bytes(data: bytes) -> str:                            # top level: a process pool pickles it by name
    return hashlib.sha256(data).hexdigest()


async def digest_of(path: Path) -> str:
    data = await asyncio.to_thread(path.read_bytes)            # blocking I/O: default thread pool
    with ProcessPoolExecutor() as pool:                        # CPU-bound: separate processes
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(pool, hash_bytes, data)
```

- Use async libraries where they exist: `httpx.AsyncClient` or `aiohttp` instead of `requests`, `psycopg`'s async mode or `asyncpg`, `asyncio.create_subprocess_exec` instead of `subprocess.run`.
- Ruff's `ASYNC` rules (on by default since 0.16) flag `time.sleep`, `open()`, `requests` and `subprocess` calls inside `async def`.
- A thread cannot be cancelled; `to_thread` work keeps running after the awaiting task is cancelled.

## Threads and sync code

Crossing between threads and the loop needs the thread-safe entry points; asyncio objects are not thread-safe.

```python
import asyncio


async def fetch(item_id: int) -> dict[str, int]:
    await asyncio.sleep(0.01)                                  # stands in for a network call
    return {"id": item_id}


async def main() -> None:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def on_message_from_thread(msg: bytes) -> None:            # runs in a foreign thread
        loop.call_soon_threadsafe(queue.put_nowait, msg)

    def fetch_from_thread() -> dict[str, int]:                 # from a thread: run a coroutine on the loop
        future = asyncio.run_coroutine_threadsafe(fetch(1), loop)
        return future.result(timeout=5)

    await asyncio.to_thread(on_message_from_thread, b"ping")
    print(await queue.get(), await asyncio.to_thread(fetch_from_thread))


asyncio.run(main())
```

Calling `asyncio.run()` inside a running loop raises `RuntimeError`; there is no supported way to block on a coroutine from inside the loop. Restructure so the sync caller runs in a thread (`to_thread`) or becomes async.

## Debugging

asyncio's debug mode reports slow callbacks, unawaited coroutines and non-thread-safe calls.

```bash
PYTHONASYNCIODEBUG=1 uv run python -m my_app      # or asyncio.run(main(), debug=True)
uv run python -X dev -m my_app                    # dev mode: asyncio debug plus more warnings
python -m asyncio ps 12345                        # 3.14+: every pending task in a running process
python -m asyncio pstree 12345                    # 3.14+: tasks as an await tree, shows where each waits
```

- `RuntimeWarning: coroutine 'fetch' was never awaited` means a call without `await`; the coroutine never ran.
- Debug mode logs callbacks slower than 100 ms (`loop.slow_callback_duration`), which points straight at blocking calls.
- `ps` and `pstree` attach to a live process (same permissions as a debugger) and are the fastest way to see why a service hangs.

## Worked example: a bounded batch that stops cleanly

This program joins the pieces above: a `TaskGroup` owns the tasks, a semaphore bounds them, a timeout covers each item and another the whole batch, and `SIGTERM` or Ctrl+C cancels the group under a drain budget. It runs with the standard library alone.

```python
import asyncio
import random
import signal
import sys
from collections.abc import Iterable


async def fetch(item: int) -> None:
    await asyncio.sleep(random.uniform(0.01, 0.2))       # stands in for a network call
    if item == 7:
        raise ConnectionError("refused")


async def one(item: int, sem: asyncio.Semaphore, per_item: float, results: dict[int, str]) -> None:
    async with sem:                                      # at most `limit` calls at once
        try:
            async with asyncio.timeout(per_item):
                await fetch(item)
            results[item] = "ok"                         # filled as items end, so a stop keeps what finished
        except TimeoutError:
            results[item] = "timeout"
        except ConnectionError:                          # expected: one bad item must not cancel the group
            results[item] = "failed"


async def run_batch(items: Iterable[int], results: dict[int, str], limit: int, per_item: float, total: float) -> None:
    sem = asyncio.Semaphore(limit)
    async with asyncio.timeout(total), asyncio.TaskGroup() as tg:
        for item in items:
            tg.create_task(one(item, sem, per_item, results))


def install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name)
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:                      # Windows
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def main() -> int:
    stop = asyncio.Event()
    install_stop_handlers(stop)
    results: dict[int, str] = {}
    batch = asyncio.create_task(run_batch(range(40), results, limit=5, per_item=0.15, total=30.0))
    stopping = asyncio.create_task(stop.wait())
    await asyncio.wait({batch, stopping}, return_when=asyncio.FIRST_COMPLETED)
    stopping.cancel()
    if not batch.done():
        batch.cancel()                                   # the TaskGroup cancels every child
        await asyncio.wait({batch}, timeout=2.0)         # the drain budget
    finished = batch.done() and not batch.cancelled() and batch.exception() is None
    print(f"{len(results)} of 40 ended, finished={finished}", flush=True)
    return 0 if finished else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

A failed or timed-out item only records its status, so it never cancels its neighbours; only the deadline and a stop end the batch. It exits 0 with 40 items ended. A `SIGTERM` in the middle, or a shorter `total`, cancels the rest and exits 1 with the finished items kept. `add_signal_handler` is Unix-only, so the Windows branch hands the event to the loop with `call_soon_threadsafe`.

## Verify

Static rules find blocking calls and lost tasks; debug mode finds the ones that only show at run time.

```bash
uv run ruff check --select ASYNC,RUF006 src         # blocking calls in async def, tasks nobody keeps
uv run python -X dev -m pytest -q -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning   # a never-awaited coroutine fails its test
grep -rn "get_event_loop()" src                     # expect nothing
```

A hung service is read from outside: `python -m asyncio pstree <pid>` (3.14+) prints what every task waits on.

## Anti-patterns

| Instead of | Do |
|---|---|
| `time.sleep` in a coroutine | `await asyncio.sleep(...)` |
| `requests` or a sync driver in a coroutine | an async client, or `asyncio.to_thread` |
| `asyncio.create_task(job())` with the result dropped | a `TaskGroup`, or a set that holds the task |
| `gather` for work that must stop on failure | `TaskGroup` |
| `except BaseException` that carries on | re-raise `CancelledError` after cleanup |
| one task per item for millions of items | a fixed pool of workers on a bounded queue |
| `asyncio.run` from inside a running loop | restructure, or move the sync caller to a thread |

## Checklist

Async code is ready when every item holds.

- [ ] One `asyncio.run()` at the entry point; no `get_event_loop()`.
- [ ] Concurrent work uses `TaskGroup`; every created task is owned by a group or a referenced set.
- [ ] Every network call has a timeout; concurrency is bounded.
- [ ] No blocking call inside `async def` (Ruff `ASYNC` rules clean).
- [ ] `CancelledError` is never swallowed; cleanup is in `finally` or `async with`.
- [ ] Tests run under pytest-asyncio or anyio with debug mode on in CI.

## Gotchas

These are the asyncio bugs seen most in real code.

1. **Everything is slow, one request at a time.** A blocking call inside a coroutine (`requests.get`, `time.sleep`). Use the async library or `to_thread`.
2. **A task vanished without an error.** It was created without keeping a reference and was garbage-collected, or its exception was never retrieved. Use a `TaskGroup` or the reference set.
3. **The program hangs on shutdown.** A task catches `CancelledError` (or `BaseException`) and keeps looping. Re-raise it.
4. **`gather` keeps running after one failure.** Without `return_exceptions`, the first error propagates but the other tasks continue. Use `TaskGroup`.
5. **`RuntimeError: Event loop is closed` at exit on Windows.** Transports finalized after `asyncio.run` closed the loop. Close clients (`async with`) inside `main()`.
6. **`Task was destroyed but it is pending!`** The loop closed while a task still ran, usually a forgotten background task. Await or cancel it before returning from `main()`.
7. **`add_signal_handler` raises `NotImplementedError` on Windows.** Signal handlers on the loop are POSIX-only; use `signal.signal` with `loop.call_soon_threadsafe`, or rely on `KeyboardInterrupt`.

## References

The reference files hold the longer recipes and the full tool detail.

- [patterns.md](references/patterns.md): worker pools with queues, retries with backoff and jitter, rate limits, graceful shutdown, async generators and context managers, first-result races

## See also

- `python-concurrency`: threads, processes and interpreters
- `python-http-clients`: `httpx.AsyncClient`
- `python-subprocess`: child processes from async code
- `python-services-shutdown`: stopping a service cleanly
- `pytest-testing`: async tests with pytest-asyncio or anyio
