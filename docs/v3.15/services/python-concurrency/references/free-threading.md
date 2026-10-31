---
title: Free-threaded Python in practice
author: MOZGII, Inc.
lang: en
---

# Free-threaded Python in practice

How to run, test and port code for the free-threaded build of CPython (3.14t, 3.15t), where threads execute Python code in parallel: checking extension support, what turns the GIL back on, a race test that finds unsafe code, a porting checklist, thread-safe iterators from 3.15, and per-thread state. Checked against CPython 3.15.2t on macOS.

## Contents

This reference has 7 sections.

1. Install and check the build
2. Extensions that turn the GIL back on
3. A race test
4. Porting checklist
5. Sharing iterators between threads (3.15)
6. Per-thread state and context variables
7. Measuring whether it pays off

## 1. Install and check the build

The free-threaded interpreter is a separate build with a `t` suffix; uv installs it next to the default one, and a project tests on both.

```bash
uv python install 3.15t
uv run --python 3.15t python -VV                       # "... free-threading build ..."
uv run --python 3.15t --with pytest pytest -q          # the whole test suite on the free-threaded build
```

Two facts describe the running interpreter: whether it was built without the GIL, and whether the GIL is off right now.

```python
import sys
import sysconfig

built_free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
gil_on = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True   # the function exists since 3.13
print(f"free-threaded build: {built_free_threaded}, GIL currently on: {gil_on}")
```

- On a default build `Py_GIL_DISABLED` is `0` and the GIL is always on.
- On a free-threaded build the GIL starts off, but importing an extension that does not declare support turns it on for the rest of the process.
- Wheels for the free-threaded build carry the ABI tag `cp314t` or `cp315t`; a package with only `cp315` wheels builds from source or fails to install there.

## 2. Extensions that turn the GIL back on

An extension module declares that it runs safely without the GIL (the `Py_mod_gil` slot). Importing one that does not re-enables the GIL and prints a warning such as `RuntimeWarning: The global interpreter lock (GIL) has been enabled to load module 'oldext', which has not declared that it can run safely without the GIL`.

| Setting | Effect |
|---|---|
| default | GIL turned on at that import, with a `RuntimeWarning` |
| `python -W error::RuntimeWarning` | the import raises instead: find every such module in CI |
| `PYTHON_GIL=0` or `-X gil=0` | GIL stays off anyway, at the risk of crashes inside that extension |
| `PYTHON_GIL=1` or `-X gil=1` | GIL always on: compare behaviour and speed on the same build |

Add a start-up assertion in services that rely on parallel threads, so a new dependency cannot silently serialise them.

```python
import sys
import warnings


def require_free_threading() -> None:
    if hasattr(sys, "_is_gil_enabled") and sys._is_gil_enabled():
        warnings.warn("GIL is enabled: threads will not run Python code in parallel", RuntimeWarning, stacklevel=2)


import json                                            # import the app's dependencies first
require_free_threading()
print("checked")
```

## 3. A race test

A race test starts many threads at the same instant with a `Barrier` and hammers one operation; on the free-threaded build unsafe code fails within a few runs. Write one for each shared object your threads touch.

```python
import threading
from collections.abc import Callable


def hammer(op: Callable[[], None], threads: int = 8, repeats: int = 20_000) -> None:
    start = threading.Barrier(threads)

    def run() -> None:
        start.wait()                                   # release every thread together
        for _ in range(repeats):
            op()

    workers = [threading.Thread(target=run) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()


class UnsafeCounter:
    def __init__(self) -> None:
        self.value = 0

    def add(self) -> None:
        self.value += 1                                # read, add, write: not atomic


class SafeCounter(UnsafeCounter):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()

    def add(self) -> None:
        with self._lock:
            self.value += 1


for counter in (UnsafeCounter(), SafeCounter()):
    hammer(counter.add)
    print(type(counter).__name__, 8 * 20_000 - counter.value, "updates lost")
```

On the free-threaded build the unsafe counter loses a large share of its updates; on the default build it usually loses few or none, which is why such bugs hide until the switch.

## 4. Porting checklist

Code that ran under the GIL for years may rely on it without saying so. Look for these patterns before trusting threads on the free-threaded build.

- **Read-modify-write on shared state**: counters, `dict[k] += 1`, appending to a shared list and then reading its length. Add a lock or keep per-thread data and merge.
- **Check-then-act**: `if key not in cache: cache[key] = build()` builds twice and may keep the wrong one. Use `dict.setdefault`, a lock, or `functools.cache` (thread-safe; the function may still run twice for one key).
- **Lazy initialisation of globals**: a module-level `_client = None` created on first use. Guard with a lock, or create it at start-up.
- **Mutating a collection another thread iterates**: copy under a lock before iterating, or hand over through a `queue.Queue`.
- **Shared iterators and generators**: wrap with `threading.serialize_iterator` (section 5).
- **C extensions and Cython code**: they need free-threading support declared and tested; check each dependency's wheels and changelog.
- **Tests**: run the suite with several threads hammering the public API (section 3), on the free-threaded build in CI.

## 5. Sharing iterators between threads (3.15)

A generator or most iterators cannot be advanced by two threads at once: a generator raises `ValueError: generator already executing`, and other iterators may skip or repeat items. Python 3.15 adds three helpers to `threading`.

```python
import itertools
import threading

squares = threading.serialize_iterator(i * i for i in range(1000))   # one lock around __next__
seen: list[int] = []
seen_lock = threading.Lock()


def consume() -> None:
    for value in squares:
        with seen_lock:
            seen.append(value)


workers = [threading.Thread(target=consume) for _ in range(4)]
for w in workers:
    w.start()
for w in workers:
    w.join()
print(len(seen), sorted(seen) == [i * i for i in range(1000)])        # 1000 True: each item once


@threading.synchronized_iterator                       # every iterator this function returns is serialised
def ticket_numbers():
    yield from itertools.count(1)


tickets = ticket_numbers()
print(next(tickets), next(tickets))
left, right = threading.concurrent_tee(iter("abc"))    # independent copies for two consumer threads
print(list(left), list(right))
```

- `serialize_iterator(it)` wraps one iterator; `synchronized_iterator(func)` wraps a function or generator function so each iterator it returns is serialised; `concurrent_tee(it, n)` is a thread-safe `itertools.tee`.
- Each `concurrent_tee` copy is itself safe only in one thread; wrap a copy with `serialize_iterator` to share it.

## 6. Per-thread state and context variables

State that belongs to one thread goes in `threading.local()` or, better, a `contextvars.ContextVar`, which also works across asyncio tasks. On the free-threaded build, a new thread starts with a copy of the creating thread's context (`sys.flags.thread_inherit_context` is 1); on the default build it starts with an empty context unless you pass `context=` to `Thread` (3.14) or run Python with `-X thread_inherit_context=1`.

```python
import contextvars
import sys
import threading

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
request_id.set("req-42")
seen: list[str] = []
t = threading.Thread(target=lambda: seen.append(request_id.get()))
t.start()
t.join()
print(sys.flags.thread_inherit_context, seen)            # 1 ['req-42'] on 3.15t, 0 ['-'] on the default build

t = threading.Thread(target=lambda: seen.append(request_id.get()), context=contextvars.copy_context())
t.start()
t.join()
print(seen[-1])                                          # 'req-42' on every build
```

Pass `context=` explicitly when a thread must see a request id or a logging context, so behaviour is the same on both builds.

## 7. Measuring whether it pays off

The free-threaded build pays off only for CPU-bound Python code spread across several threads; I/O-bound code gains little, and single-threaded code runs somewhat slower.

```python
import os
import time
from concurrent.futures import ThreadPoolExecutor


def burn(n: int) -> int:
    return sum(i * i for i in range(n))


workers = min(4, os.process_cpu_count() or 1)
start = time.perf_counter()
for _ in range(workers):
    burn(1_000_000)
serial = time.perf_counter() - start
start = time.perf_counter()
with ThreadPoolExecutor(workers) as pool:
    list(pool.map(burn, [1_000_000] * workers))
threaded = time.perf_counter() - start
print(f"speed-up with {workers} threads: {serial / threaded:.1f}x")   # about 1x with a GIL, near N without
```

Run the same script with `python` and `python3.15t` and compare; then repeat with the real workload, since object sharing and reference counting on hot shared objects can eat the gain.
