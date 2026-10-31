---
title: Processes, shared memory and subinterpreters in Python
author: MOZGII, Inc.
lang: en
---

# Processes, shared memory and subinterpreters in Python

Patterns for work spread across processes and subinterpreters: per-worker set-up, timeouts and hung workers, recovering from a crashed worker, shared memory without copying, Manager proxies, plain `multiprocessing.Process` with queues, and the sharing rules of `concurrent.interpreters`.

## Contents

This reference has 7 sections.

1. Per-worker set-up with an initializer
2. Timeouts and hung workers
3. Recovering from a crashed worker
4. Shared memory without copying
5. Manager proxies for small shared state
6. A long-lived worker with multiprocessing.Process
7. Subinterpreters: queues, sharing rules and errors

## 1. Per-worker set-up with an initializer

An initializer runs once in each worker process before it takes tasks; it is where an expensive object (a model, a parser, a connection) is created and kept in a module global for that worker.

```python
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

_tokenizer: dict[str, int] | None = None              # one per worker process


def init_worker(vocabulary: list[str]) -> None:
    global _tokenizer
    _tokenizer = {word: i for i, word in enumerate(vocabulary)}   # the expensive part, once per worker


def encode(text: str) -> list[int]:
    assert _tokenizer is not None
    return [_tokenizer.get(word, -1) for word in text.split()]


if __name__ == "__main__":
    vocab = ["the", "cat", "sat", "on", "mat"]
    with ProcessPoolExecutor(2, mp_context=get_context("spawn"), initializer=init_worker, initargs=(vocab,)) as pool:
        print(list(pool.map(encode, ["the cat sat", "on the mat", "a dog"])))
```

- `initargs` are pickled once per worker, not once per task; pass large read-only data this way.
- An exception in the initializer breaks the pool (`BrokenProcessPool`) on the first task; log inside the initializer to see why.

## 2. Timeouts and hung workers

`future.result(timeout=...)` stops waiting, not the task: the worker keeps running it. To stop work that hangs, terminate the pool's workers (3.14) and build a new pool.

```python
import time
from concurrent.futures import ProcessPoolExecutor, TimeoutError
from multiprocessing import get_context


def slow(seconds: float) -> float:
    time.sleep(seconds)
    return seconds


if __name__ == "__main__":
    pool = ProcessPoolExecutor(2, mp_context=get_context("spawn"))
    future = pool.submit(slow, 30)
    try:
        future.result(timeout=1)
    except TimeoutError:
        print("task timed out; stopping the workers")
        pool.terminate_workers()                         # SIGTERM to each worker (3.14); kill_workers() sends SIGKILL
    pool.shutdown(wait=True, cancel_futures=True)
    print("pool closed")
```

On 3.13 and older, run a task that may hang in its own `multiprocessing.Process` and call `terminate()` on it, or run it as a subprocess with a timeout.

## 3. Recovering from a crashed worker

A worker that dies (out-of-memory killer, a crash in a C extension, `os._exit`) breaks the whole pool: pending and later futures raise `BrokenProcessPool`. Catch it, recreate the pool, and retry or skip the item that caused it.

```python
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context


def risky(n: int) -> int:
    if n == 3:
        os._exit(1)                                      # simulates a hard crash in the worker
    return n * 10


def run_all(items: list[int]) -> dict[int, int | None]:
    results: dict[int, int | None] = {}
    for n in items:                                      # one at a time makes the culprit obvious; batch in real code
        with ProcessPoolExecutor(1, mp_context=get_context("spawn")) as pool:
            try:
                results[n] = pool.submit(risky, n).result()
            except BrokenProcessPool as exc:
                print(f"item {n} crashed its worker: {exc}")
                results[n] = None
    return results


if __name__ == "__main__":
    print(run_all([1, 2, 3, 4]))
```

In a real pipeline, keep one pool for the batch, and on `BrokenProcessPool` retry the unfinished items in a fresh pool one by one to find the item that crashes.

## 4. Shared memory without copying

`multiprocessing.shared_memory.SharedMemory` is a named block of memory that several processes map; data written by one is visible to the others with no pickling. The creator unlinks it once at the end.

```python
import struct
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context, shared_memory

FMT = "d"                                                # one float64 per item
SIZE = struct.calcsize(FMT)


def scale_slice(name: str, start: int, stop: int, factor: float) -> None:
    shm = shared_memory.SharedMemory(name=name, track=False)   # attach; the creator owns cleanup (3.13 track=)
    try:
        view = shm.buf.cast(FMT)
        for i in range(start, stop):
            view[i] *= factor
        view.release()
    finally:
        shm.close()


if __name__ == "__main__":
    n = 1_000
    shm = shared_memory.SharedMemory(create=True, size=n * SIZE)
    try:
        data = shm.buf.cast(FMT)
        for i in range(n):
            data[i] = float(i)
        with ProcessPoolExecutor(4, mp_context=get_context("spawn")) as pool:
            bounds = [(s, min(s + 250, n)) for s in range(0, n, 250)]
            list(pool.map(scale_slice, [shm.name] * 4, *zip(*bounds), [2.0] * 4))
        print(data[0], data[1], data[999])               # 0.0 2.0 1998.0
        data.release()
    finally:
        shm.close()
        shm.unlink()                                     # free it exactly once
```

- With NumPy, build `np.ndarray(shape, dtype, buffer=shm.buf)` in each process instead of the `memoryview` cast.
- `shared_memory.ShareableList` stores a fixed-length list of ints, floats, bools, short strings and bytes.
- Release every `memoryview` before `close()`, or it raises `BufferError: cannot close exported pointers exist`.
- On Windows the block disappears when the last process closes it; on macOS and Linux it lives until `unlink()`, so unlink in a `finally`.

## 5. Manager proxies for small shared state

A `multiprocessing.Manager` runs a server process that owns real Python objects; other processes get proxies, and every operation is a round trip. It suits small shared state such as a progress dict, never hot loops.

```python
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import Manager, get_context


def work(item: int, progress, lock) -> int:
    with lock:
        progress["done"] += 1                            # proxy call: read and write are separate trips
    return item * item


if __name__ == "__main__":
    ctx = get_context("spawn")
    with Manager() as manager:
        progress = manager.dict(done=0)
        lock = manager.Lock()
        with ProcessPoolExecutor(4, mp_context=ctx) as pool:
            results = list(pool.map(work, range(10), [progress] * 10, [lock] * 10))
        print(sum(results), progress["done"])
```

`progress["done"] += 1` on a proxy is a read and a write; without the manager lock, updates from different processes are lost.

## 6. A long-lived worker with multiprocessing.Process

A `multiprocessing.Process` with an input queue and an output queue suits a worker that keeps state for the life of the app (a loaded model, an open file), where a pool's anonymous workers do not fit.

```python
from multiprocessing import get_context


def worker(inbox, outbox) -> None:
    cache: dict[int, int] = {}                           # state kept between requests
    while (n := inbox.get()) is not None:                # None means stop
        cache[n] = cache.get(n) or n ** 3
        outbox.put((n, cache[n]))


if __name__ == "__main__":
    ctx = get_context("spawn")
    inbox, outbox = ctx.Queue(), ctx.Queue()
    proc = ctx.Process(target=worker, args=(inbox, outbox), name="cube-worker")
    proc.start()
    for n in (2, 3, 2):
        inbox.put(n)
        print(outbox.get(timeout=10))
    inbox.put(None)
    proc.join(timeout=10)
    print("exit code", proc.exitcode)                     # 0; negative means killed by a signal on POSIX
```

- Read results before `join()`: a child blocks at exit until its queued data is consumed, so joining first can deadlock.
- For a worker that another program (not Python) starts, see the python-stdio-worker skill.

## 7. Subinterpreters: queues, sharing rules and errors

`concurrent.interpreters` (3.14) creates interpreters in the current process. Only shareable objects cross directly; other objects are pickled by `call`, and a cross-interpreter queue carries values between them.

```python
from concurrent import interpreters

print([interpreters.is_shareable(x) for x in (1, 1.5, "s", b"b", None, (1, "a"), [1])])   # last: False

interp = interpreters.create()
results = interpreters.create_queue()
interp.prepare_main(results=results, factor=3)           # bind names in the interpreter's __main__
interp.exec("results.put(sum(i * factor for i in range(10)))")
print(results.get())                                     # 135

try:
    interp.exec("1 / 0")
except interpreters.ExecutionFailed as exc:              # the error's type and message cross over
    print("failed:", str(exc).splitlines()[0])

thread = interp.call_in_thread(print, "running in a thread of its own")
thread.join()
interp.close()
```

- `is_shareable` objects (`int`, `float`, `str`, `bytes`, `bool`, `None`, tuples of these, queues) pass without pickling; lists and dicts are copied by pickling.
- Each interpreter imports its own modules, so module-level state is not shared and imports run again; keep heavy imports inside the function the interpreter calls.
- Close interpreters you create; `InterpreterPoolExecutor` manages them for you and is the right choice for task-style work.
