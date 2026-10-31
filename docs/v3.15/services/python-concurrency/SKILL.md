---
name: python-concurrency
description: Writes correct concurrent Python with threads, processes and subinterpreters - choosing between threads, processes and asyncio, threading primitives (Lock, RLock, Event, Condition, Semaphore, Barrier, queue.Queue with shutdown), ThreadPoolExecutor and ProcessPoolExecutor with map buffersize and terminate_workers (3.14), multiprocessing start methods per OS and the forkserver default on Linux in 3.14, pickling limits, shared memory, the free-threaded build (3.14t and 3.15t, installed with uv, sys._is_gil_enabled, code that breaks without the GIL), concurrent.interpreters (3.14), thread-safe iterators (3.15), which operations are thread-safe, and finding and avoiding deadlocks. Use when running work in parallel, speeding up CPU-bound Python, sharing data between threads or processes, a program deadlocks or loses updates, or when trying free-threaded Python.
metadata:
  tags:
  - python
  - concurrency
  - threading
  - multiprocessing
  - concurrent-futures
  - free-threading
  - subinterpreters
  - deadlocks
---

# Concurrency in Python: threads, processes and interpreters

Python gives three ways to do several things at once inside one program: threads, processes and, since 3.14, subinterpreters; plus asyncio for many waits on one thread. Which one is right depends on whether the work waits or computes, and on whether the interpreter has a GIL. This skill covers the choice, the primitives and the failure modes. asyncio is the python-asyncio skill; running other programs is the python-subprocess skill.

## Step 0: read the Python build and version

Whether threads can run Python code in parallel depends on the build, so read the version and whether the GIL is on before choosing.

```bash
uv run python -c "import sys, sysconfig, os; print(sys.version_info[:2], 'free-threaded build:', bool(sysconfig.get_config_var('Py_GIL_DISABLED')), 'GIL on:', getattr(sys, '_is_gil_enabled', lambda: True)(), 'cpus:', getattr(os, 'process_cpu_count', os.cpu_count)())"
uv run python -c "import multiprocessing as mp; print(mp.get_start_method())"
```

| Version | Change that matters |
|---|---|
| 3.12 | `DeprecationWarning` when `fork` is used in a process that has threads |
| 3.13 | `queue.Queue.shutdown`; `os.process_cpu_count`; experimental free-threaded build (3.13t) |
| 3.14 | free-threaded build officially supported (PEP 779); `concurrent.interpreters` and `InterpreterPoolExecutor` (PEP 734); `forkserver` is the default start method on Linux; `Executor.map(buffersize=)`; `ProcessPoolExecutor.terminate_workers` and `kill_workers` |
| 3.15 | `threading.serialize_iterator`, `synchronized_iterator` and `concurrent_tee` for sharing iterators between threads |

## Choose threads, processes, interpreters or asyncio

The choice follows from what the work spends its time on.

| The work | Use | Why |
|---|---|---|
| many network calls, async libraries available | asyncio | thousands of waits on one thread |
| blocking I/O with sync libraries (files, a sync HTTP or DB client) | threads, `ThreadPoolExecutor` | the GIL is released while waiting |
| CPU-heavy NumPy, hashing, compression, image decoding | threads | these release the GIL inside C code |
| CPU-heavy pure Python on the default build | processes, or interpreters (3.14) | one GIL each, so real parallelism |
| CPU-heavy pure Python sharing large in-memory state | free-threaded build with threads | no GIL and no copying, if every extension supports it |
| an external program | `subprocess` | separate program |

- Measure before and after: thread and process pools add overhead, and small tasks get slower.
- Do not mix many models in one program; pick one for each layer (asyncio at the edges, a process pool for CPU work behind it).

## Thread pools with ThreadPoolExecutor

`concurrent.futures.ThreadPoolExecutor` runs functions on a fixed set of threads and hands back futures, which carry results and exceptions back to the caller.

```python
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def fetch(n: int) -> str:
    time.sleep(0.1)                                      # stands in for blocking I/O
    if n == 3:
        raise ValueError("item 3 is broken")
    return f"item {n}"


with ThreadPoolExecutor(max_workers=8, thread_name_prefix="fetch") as pool:
    futures = {pool.submit(fetch, n): n for n in range(6)}
    for fut in as_completed(futures):
        try:
            print(fut.result())
        except ValueError as exc:
            print(f"{futures[fut]} failed: {exc}")
```

- `pool.map(fn, items)` returns results in input order and raises the first error when its turn comes; `submit` with `as_completed` handles each item's error.
- `map` submits everything at once; for a very long or endless input, pass `buffersize=` (3.14) so only that many tasks are queued ahead.
- Leaving the `with` block waits for all tasks; `pool.shutdown(wait=False, cancel_futures=True)` drops queued tasks on an early exit.
- The default `max_workers` is `min(32, (os.process_cpu_count() or 1) + 4)`; for I/O, size it by what the remote side tolerates.

## Process pools with ProcessPoolExecutor

`ProcessPoolExecutor` runs functions in worker processes, so pure-Python CPU work uses every core. Functions and arguments are pickled, so the function must be importable at module top level and the script needs a `__main__` guard.

```python
import os
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context


def count_primes(bounds: tuple[int, int]) -> int:
    lo, hi = bounds
    return sum(all(n % d for d in range(2, int(n**0.5) + 1)) for n in range(max(lo, 2), hi))


def main() -> None:
    chunks = [(i, i + 50_000) for i in range(0, 400_000, 50_000)]     # batches, not single numbers
    ctx = get_context("spawn")                                       # same behaviour on every OS
    with ProcessPoolExecutor(max_workers=os.process_cpu_count(), mp_context=ctx) as pool:
        print(sum(pool.map(count_primes, chunks)))


if __name__ == "__main__":
    main()
```

- Send batches: each call pickles its arguments and result, which costs more than tiny work.
- `os.process_cpu_count()` (3.13) respects CPU affinity limits, including those of a container; `os.cpu_count()` does not.
- `max_tasks_per_child=` recycles workers that leak memory; `initializer=` loads a model once per worker.
- A worker killed from outside (out of memory) breaks the pool: later calls raise `BrokenProcessPool`. Recreate the pool and retry. Since 3.15 the traceback names the child's PID and exit code, in the chained cause; `str(exc)` stays the general message. `terminate_workers()` and `kill_workers()` (3.14) stop a pool whose tasks hang.

## Start methods per OS

The start method decides how a worker process begins: `spawn` starts a fresh interpreter, `fork` copies the parent, `forkserver` forks from a clean helper process.

| OS | Default | Notes |
|---|---|---|
| Windows | `spawn` | the only method |
| macOS | `spawn` (since 3.8) | `fork` can crash with system frameworks |
| Linux and other POSIX | `forkserver` (since 3.14), `fork` before | `fork` copies locks held by other threads and can deadlock |

- With `spawn` and `forkserver`, the main module is imported again in each worker: anything at module level runs again, and code that starts processes must sit under `if __name__ == "__main__":`.
- Pass the context explicitly (`get_context("spawn")`) so a program behaves the same on every OS and on every Python version.

## What can cross a process boundary

Everything sent to another process or interpreter is pickled: data, top-level functions and classes by reference. Lambdas, nested functions, open files, sockets, locks and database connections cannot be pickled.

- Pass a path, a URL or an id, and let the worker open the resource itself.
- `functools.partial(fn, config)` replaces a closure.
- Large arrays are shared, not copied, through `multiprocessing.shared_memory`; the pattern is in [processes-and-interpreters.md](references/processes-and-interpreters.md).

## Threading primitives

The `threading` module provides the primitives; prefer the highest-level one that fits: a queue over a condition, a condition over a bare lock.

| Primitive | Use for |
|---|---|
| `queue.Queue` | passing work and results between threads; thread-safe and blocking |
| `Lock` | protecting a read-modify-write of shared state |
| `RLock` | a lock the same thread may take again (recursive code) |
| `Event` | a one-way signal: stop, ready |
| `Condition` | waiting until shared state meets a predicate |
| `Semaphore` / `BoundedSemaphore` | limiting how many threads use a resource at once |
| `Barrier` | making N threads wait for each other at a step |

This producer-consumer pipeline uses `Queue.shutdown` (3.13) to tell consumers there is no more work, instead of sentinel values.

```python
import queue
import threading

jobs: queue.Queue[int] = queue.Queue(maxsize=100)       # bounded: producers wait when consumers lag
results: list[int] = []
results_lock = threading.Lock()


def worker() -> None:
    while True:
        try:
            n = jobs.get()
        except queue.ShutDown:                           # raised once the queue is shut down and empty
            return
        with results_lock:
            results.append(n * n)


threads = [threading.Thread(target=worker) for _ in range(4)]
for t in threads:
    t.start()
for n in range(20):
    jobs.put(n)
jobs.shutdown()                                          # no more puts; getters drain, then get ShutDown
for t in threads:
    t.join()
print(sorted(results)[:5], len(results))
```

## Thread safety of common operations

Single operations on built-in containers do not corrupt them, with or without the GIL; sequences of operations are not atomic, and a thread can run between them.

| Code | Safe without a lock? |
|---|---|
| `lst.append(x)`, `d[k] = v`, `d.get(k)`, `q.put(x)` | yes: one operation |
| `d.setdefault(k, [])`, `dict.pop(k, None)` | yes |
| `counter += 1`, `d[k] = d.get(k, 0) + 1` | no: read, then write |
| `if k not in d: d[k] = make()` | no: check then act |
| iterating a list or dict while another thread changes it | no: wrong results or `RuntimeError` |
| sharing one generator between threads | no: `ValueError: generator already executing`; wrap it with `threading.serialize_iterator` (3.15) |

Lost updates happen on the default build too, only less often; the free-threaded build makes them common. Protect each read-modify-write with a `Lock`, or give each thread its own data and combine at the end.

```python
import threading

lock = threading.Lock()
counts: dict[str, int] = {}


def record(key: str) -> None:
    with lock:                                            # read-modify-write under one lock
        counts[key] = counts.get(key, 0) + 1
```

- Prefer passing data through `queue.Queue` and returning results through futures over sharing objects.
- Check a `threading.Event` in long loops so threads can exit; daemon threads are killed abruptly at interpreter exit, so give workers a stop event and `join()` them.

## Free-threaded Python (3.14t, 3.15t)

The free-threaded build has no GIL, so threads run Python code on several cores at once. It is a separate interpreter (`python3.14t`, `python3.15t`) and is officially supported since 3.14.

```bash
uv python install 3.15t
uv run --python 3.15t python -c "import sys; print(sys._is_gil_enabled())"      # False
uv venv --python 3.15t .venv-ft && uv pip install --python .venv-ft -r requirements.txt
```

- Every C extension must ship `cp314t` or `cp315t` wheels and declare free-threading support; importing one that does not turns the GIL back on with a `RuntimeWarning`. Check `sys._is_gil_enabled()` after your imports, not before.
- `PYTHON_GIL=0` or `-X gil=0` keeps the GIL off even then, at the risk of crashes in that extension; `PYTHON_GIL=1` turns it on for comparison.
- Single-threaded code runs somewhat slower than on the default build; the gain comes only with several busy threads.
- Code that was "safe because of the GIL" (unlocked counters, check-then-act on shared dicts, lazy initialisation without a lock) now races. The race test and the porting list are in [free-threading.md](references/free-threading.md).

## Subinterpreters with concurrent.interpreters (3.14)

A subinterpreter is a separate Python interpreter inside the same process, with its own modules and its own GIL, so several run Python code in parallel on the default build. `InterpreterPoolExecutor` is the easy entry; `concurrent.interpreters` gives direct control.

```python
from concurrent import interpreters
from concurrent.futures import InterpreterPoolExecutor


def count_primes(limit: int) -> int:
    return sum(all(n % d for d in range(2, int(n**0.5) + 1)) for n in range(2, limit))


if __name__ == "__main__":
    with InterpreterPoolExecutor(max_workers=4) as pool:
        print(list(pool.map(count_primes, [20_000] * 4)))
    interp = interpreters.create()
    print(interp.call(count_primes, 10_000))             # runs in the subinterpreter
    interp.close()
```

- Interpreters share no objects; arguments and results are pickled or, for a few types, shared directly. Start-up and memory cost less than processes.
- Extension modules must support multiple interpreters; many do not yet and fail to import there. Test the imports first.

## Deadlocks

A deadlock is two or more threads each waiting for something another holds; the program stops using CPU and never finishes.

- Take several locks always in the same global order, or use one lock for the whole compound operation.
- Never hold a lock while calling out: a callback, a log handler, a blocking `queue.put`, network I/O.
- Put a timeout on waits in production code (`lock.acquire(timeout=5)`, `q.get(timeout=1)`, `fut.result(timeout=30)`) and log when it expires.
- `fork` after threads exist copies locks held by those threads into the child, where nobody will ever release them; use `spawn` or `forkserver`.
- To see where a hung program waits: `py-spy dump --pid <pid>`, or `faulthandler.dump_traceback_later(60)` at start-up (the python-debugging skill).

## Verify

A race passes most runs, so repeat the test, and prove the settings a concurrent program depends on.

```bash
uv run python -X dev -m pytest -q tests/test_concurrency.py   # dev mode shows unclosed resources
for i in 1 2 3 4 5; do uv run pytest -q tests/test_concurrency.py || break; done   # expect no failure
grep -rnE "get_context\(|set_start_method\(" src      # processes state their start method
grep -rnE "\.(result|get|acquire|join)\(\)" src        # each hit is a wait with no timeout: justify it
uv run --python 3.15t python -c "import my_app, sys; print(sys._is_gil_enabled())"   # False on the free-threaded build
```

## Anti-patterns

| Instead of | Do |
|---|---|
| threads for pure-Python CPU work on the default build | processes or interpreters |
| `counter += 1` from several threads | a `Lock`, or per-thread counts combined at the end |
| `time.sleep` to wait for another thread | an `Event`, a `Queue` or `join(timeout=...)` |
| relying on the default start method | `get_context("spawn")` |
| sending a database connection to a worker | a connection string; the worker opens it |
| one task per tiny item in a process pool | batches |
| a lock held across a network call | copy what you need, release, then call |
| `daemon=True` to hide a hang at exit | a shutdown signal and `join` |

## Checklist

Concurrent code is correct when every item holds.

- [ ] The model was chosen from the work (I/O or CPU) and measured.
- [ ] Every read-modify-write of shared state is under a lock, or threads do not share mutable state.
- [ ] Work passes through `queue.Queue` or futures; queues are bounded; shutdown is explicit.
- [ ] Process code has a `__main__` guard, sends picklable batches, and sets the start method explicitly.
- [ ] Every wait has a timeout in production code; locks are taken in a fixed order and never held across calls out.
- [ ] On the free-threaded build, `sys._is_gil_enabled()` is checked after imports, and tests run with several threads.

## Gotchas

These are the concurrency bugs seen most often.

1. **Threads made CPU work no faster.** The GIL serialises Python bytecode. Use processes, interpreters, a C library that releases the GIL, or the free-threaded build.
2. **`AttributeError: Can't get attribute 'work' on <module '__main__'>`.** The worker function is a lambda, nested, or defined in a notebook. Move it to module top level in an importable file.
3. **Every worker re-runs the program's start-up.** Module-level code under `spawn` or `forkserver`. Move it into `main()` behind the `__main__` guard.
4. **Code that worked on Linux deadlocks or crashes elsewhere, or after upgrading to 3.14.** The default start method differs. Pass the context explicitly.
5. **Counts come out low under load.** `x += 1` from several threads. Use a lock or per-thread counts.
6. **The program hangs at exit.** A non-daemon thread waits on a queue forever, or a pool's `shutdown` waits for a stuck task. Shut down the queue or set a stop `Event`, then `join`; give tasks their own timeouts.
7. **The free-threaded build is as slow as the default one.** An extension re-enabled the GIL. Check `sys._is_gil_enabled()` and the import warnings.

## References

The reference files hold deeper material.

- [free-threading.md](references/free-threading.md): installing and testing on the free-threaded build, extension support, a race test, porting checklist, thread-safe iterators in 3.15, and per-thread state
- [processes-and-interpreters.md](references/processes-and-interpreters.md): shared memory, Manager proxies, process pool initializers and timeouts, worker crash handling, subinterpreter queues and sharing rules

## See also

- `python-asyncio`: many waits on one thread
- `python-subprocess`: running other programs
- `python-performance`: measuring before and after
- `python-debugging`: reading a hung program's stacks
- `python-native-extensions`: free-threaded support in C extensions
- `python-services-shutdown`: stopping workers cleanly
