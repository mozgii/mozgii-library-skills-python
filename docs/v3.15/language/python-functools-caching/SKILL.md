---
name: python-functools-caching
description: Caches results and composes functions in Python - functools.cache and lru_cache with maxsize, typed and cache_info, cached_property, how cache keys are built, mutable cached values, caching methods without leaking instances, invalidation, memory growth, thread safety and stampedes, async functions, TTL caches with cachetools TTLCache and cachedmethod, disk caches with diskcache, plus partial with Placeholder, reduce, and singledispatch and singledispatchmethod. Use when memoising an expensive function, adding a TTL cache to API calls, caching a method or property, caching to disk between runs, when a cache grows without bound, returns stale or wrong values, or keeps objects alive, or when replacing isinstance chains with singledispatch.
metadata:
  tags:
  - python
  - functools
  - caching
  - lru-cache
  - cachetools
  - diskcache
  - singledispatch
  - memoization
---

# Python caching and functools

A cache trades memory and staleness for speed. `functools` gives in-process memoisation (`cache`, `lru_cache`, `cached_property`) plus function tools (`partial`, `reduce`, `singledispatch`); expiry by time comes from cachetools, and caches that survive a restart from diskcache. Most caching bugs are about keys, invalidation, memory and threads. Measure before caching: profiling is in the `python-performance` skill.

## Step 0: read the versions

Read the versions before choosing an API.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
uv run python -c "import cachetools; print(cachetools.__version__)"    # 7.2.7 in October 2026
uv run python -c "import diskcache; print(diskcache.__version__)"      # 5.6.3 in October 2026
```

| Version | Change that matters |
|---|---|
| Python 3.11 | `singledispatch.register` accepts union types (`int \| float`) |
| Python 3.12 | `cached_property` no longer locks; concurrent first reads may compute twice |
| Python 3.14 | `functools.Placeholder` for positional slots in `partial` |
| cachetools 6.0.0, 7 | 6.0.0: `condition=` on `cached` and `cachedmethod` prevents duplicate concurrent computation. 7: `cachedmethod` wrappers became descriptors, deprecating classmethods and instances without a mutable `__dict__`; `cache(self)` returning `None` no longer supported; `info=` added |

## Choose a cache

Pick the smallest tool that gives the expiry and lifetime you need.

| Need | Use |
|---|---|
| pure function, small fixed input space | `functools.cache` |
| pure function, many possible inputs | `functools.lru_cache(maxsize=N)` |
| computed once per object | `functools.cached_property` |
| results go stale after a time (API data, tokens) | `cachetools.TTLCache` with `@cached` |
| per-object cache with expiry | `cachetools.cachedmethod` |
| survives restarts, shared between processes | `diskcache.Cache`, in a folder only your user can write (it unpickles values) |

## functools.cache and lru_cache

`functools.lru_cache(maxsize=N)` memoises a function: arguments become a key, and the least recently used entries are dropped once `N` are stored. `functools.cache` is the same with no limit.

```python
from functools import cache, lru_cache


@lru_cache(maxsize=1024)
def parse_rule(text: str) -> tuple[str, ...]:
    return tuple(text.split())                 # immutable result: safe to share


@cache
def fib(n: int) -> int:
    return n if n < 2 else fib(n - 1) + fib(n - 2)


parse_rule("a b")            # computed
parse_rule("a b")            # served from the cache
parse_rule.cache_info()      # CacheInfo(hits=1, misses=1, maxsize=1024, currsize=1)
parse_rule.cache_clear()     # drop everything, e.g. between tests
```

- Cache only functions whose result depends on the arguments alone: no clock, no file, no global setting.
- `@lru_cache` without parentheses gives `maxsize=128`; size it from `cache_info()` in a realistic run.
- `@cache` on a function called with ever-new arguments (user ids, timestamps) is a memory leak.

## How cache keys are built

The cache key is made from the positional and keyword arguments, so every argument must be hashable and the same call must be spelled the same way.

- An unhashable argument (`list`, `dict`, `set`) raises `TypeError: unhashable type`. Pass a `tuple`, a `frozenset` or, on 3.15, a `frozendict`.
- `f(1, b=2)`, `f(a=1, b=2)` and `f(b=2, a=1)` are three different keys. Call cached functions one consistent way, or make parameters positional-only (`def f(a, b, /)`).
- Equal values share a key: `1`, `1.0` and `Decimal(1)` are equal with equal hashes, so a cache may return a result computed for another type. `typed=True` keeps types apart.

## Do not mutate cached values

A cached function returns the same object to every caller. If one caller mutates it, every later caller sees the change.

```python
from functools import cache


@cache
def defaults() -> dict[str, int]:
    return {"retries": 3}


settings = defaults()
settings["retries"] = 99         # now defaults() returns {'retries': 99} for everyone
```

Return immutable values (tuples, frozen dataclasses, `frozendict` on 3.15, `MappingProxyType`), or return a copy from a thin wrapper around the cached function.

## Caching methods safely

`@lru_cache` on a method puts `self` into every key, and the cache lives on the class, so it keeps every instance alive until the entry is evicted. With `@cache` that is forever.

Three safe patterns, in order of preference:

```python
import threading
from functools import cached_property, lru_cache

import cachetools


class Report:
    def __init__(self, rows: dict[str, list[float]]) -> None:
        self.rows = rows

    @cached_property                               # 1. no arguments: compute once per instance
    def totals(self) -> dict[str, float]:
        return {name: sum(values) for name, values in self.rows.items()}


class Pricing:
    def __init__(self) -> None:                    # 2. a per-instance lru_cache, freed with the instance
        self.quote = lru_cache(maxsize=256)(self._quote)

    def _quote(self, sku: str) -> float: ...


class Catalog:
    def __init__(self) -> None:                    # 3. per-instance TTL cache with a lock
        self._cache = cachetools.TTLCache(maxsize=500, ttl=30)
        self._lock = threading.Lock()

    @cachetools.cachedmethod(lambda self: self._cache, lock=lambda self: self._lock)
    def price(self, sku: str) -> float: ...
```

Pattern 2 creates a reference cycle, so instances are freed by the cycle collector rather than at once.

## cached_property

`functools.cached_property` computes a value on first access and stores it in the instance `__dict__`, so later reads are plain attribute reads. `del obj.attr` clears it.

```python
from functools import cached_property
from pathlib import Path


class Dataset:
    def __init__(self, path: Path) -> None:
        self.path = path

    @cached_property
    def rows(self) -> list[str]:
        return self.path.read_text(encoding="utf-8").splitlines()


ds = Dataset(Path("data.csv"))
ds.rows          # reads the file
ds.rows          # cached
del ds.rows      # the next access reads again
```

It needs an instance `__dict__`, so it fails on classes with `__slots__`. Since 3.12 it holds no lock: guard a computation that must run only once with your own `threading.Lock`. How it works as a descriptor is in the `python-object-model` skill.

## Invalidation

Every cache needs an answer to "when is this value wrong?", decided before adding it.

| Strategy | How |
|---|---|
| never stale | the function is pure; cache forever with a size bound |
| stale after a time | `TTLCache(ttl=...)` or `diskcache` `expire=` |
| stale when the source changes | put a version in the key: file mtime and size, a row's `updated_at`, a content hash |
| explicit | `cache_clear()`, `del obj.prop`, `cache.pop(key)` after a write |

Putting the version in the key is the most reliable choice for files and records: `load(path, path.stat().st_mtime_ns)` misses automatically when the file changes, and old entries age out of an LRU.

## Memory growth

An unbounded cache grows with the number of distinct keys, and each value may be large. Bound it by count (`maxsize`), by size (cachetools `getsizeof=`, diskcache `size_limit=`) or by time.

Check `cache_info().currsize` in long-running processes. Cache small derived results rather than whole responses or DataFrames. A cache keyed by objects keeps them alive; a `weakref.WeakKeyDictionary` or a per-instance cache lets them go.

## Thread safety and stampedes

`lru_cache` is safe to call from many threads: its internal state never corrupts. It does not stop two threads from computing the same missing key at the same time; both run the function and one result wins. With five threads asking for one slow key at once, the function runs five times.

If duplicate work is only wasted time, accept it. If it is expensive or has side effects (a paid API call), use cachetools with `condition=threading.Condition()`: the first caller computes, the others wait for its result. cachetools caches are not thread-safe on their own: pass `lock=` or `condition=` whenever threads share one.

```python
import threading

import httpx
from cachetools import TTLCache, cached


@cached(cache=TTLCache(maxsize=1024, ttl=300), condition=threading.Condition(), info=True)
def exchange_rate(base: str, quote: str) -> float:
    return httpx.get(f"https://rates.example/{base}/{quote}", timeout=5).raise_for_status().json()["rate"]
```

## Caching async functions

`@cache` on an `async def` caches the coroutine object, and the second `await` fails with `RuntimeError: cannot reuse already awaited coroutine`. Cache a task instead, so concurrent callers share one in-flight call.

```python
import asyncio
import functools
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any


def async_cached[**P, R](fn: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Awaitable[R]]:
    tasks: dict[Any, asyncio.Task[R]] = {}

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Awaitable[R]:
        key = (args, tuple(sorted(kwargs.items())))
        task = tasks.get(key)
        if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
            task = tasks[key] = asyncio.ensure_future(fn(*args, **kwargs))   # failures are retried
        return asyncio.shield(task)                  # one caller's cancel stays with that caller

    return wrapper
```

Without `asyncio.shield`, cancelling one caller cancels the shared task, and every caller gets `CancelledError`; the test is in [cachetools-diskcache.md](references/cachetools-diskcache.md). Tasks belong to one event loop, so this cache must not outlive `asyncio.run`; bound it before using it with many keys.

## TTL caches with cachetools

cachetools provides cache classes that behave like dicts with eviction rules, and `@cached` / `@cachedmethod` decorators that use them. `TTLCache(maxsize, ttl)` drops entries `ttl` seconds after they were stored.

```python
import threading

from cachetools import TTLCache, cached
from cachetools.keys import hashkey

from my_app.api import ApiClient, User


@cached(cache=TTLCache(maxsize=10_000, ttl=60), key=lambda client, user_id: hashkey(user_id),
        lock=threading.Lock())
def load_user(client: ApiClient, user_id: int) -> User:      # the client is left out of the key
    return client.get_user(user_id)


load_user.cache.pop(hashkey(42), None)                       # invalidate one user after an update
```

The cache classes, `TLRUCache` with a lifetime per item, `getsizeof`, `cachedmethod` details and testing with a fake timer are in [cachetools-diskcache.md](references/cachetools-diskcache.md).

## Disk caches with diskcache

diskcache stores values in SQLite plus files in a folder, so the cache survives restarts and several processes can share it. `Cache.memoize` is the decorator form.

```python
from diskcache import Cache
from platformdirs import user_cache_path

from my_app.images import render_thumbnail

cache = Cache(user_cache_path("my-app") / "thumbnails", size_limit=2**30)   # 1 GiB, evicts least recently stored


@cache.memoize(expire=7 * 24 * 3600, tag="thumbnail")
def thumbnail(path: str, width: int) -> bytes:
    return render_thumbnail(path, width)
```

- **Security advisory CVE-2025-69872 (GHSA-w8v5-vhqr-4h9v), with no fixed release.** diskcache through 5.6.3 pickles values by default, so anyone who can write to the cache folder runs code in your process when it reads the cache. Use only a trusted folder that your own user owns and nobody else can write to. `JSONDisk` does not protect a writable folder: it stores JSON (other types raise `TypeError`), yet a row set to `MODE_PICKLE` in SQLite still went through pickle when read. The last release, 5.6.3, dates from August 2023: stable and widely used, so check the project's status before building something long-lived on it.
- Put the cache in the per-user cache folder (`~/Library/Caches` on macOS, `~/.cache` on Linux, `%LOCALAPPDATA%` on Windows), never on a network drive: SQLite locking is unreliable there.

## partial and Placeholder

`functools.partial(fn, *args, **kwargs)` fixes some arguments and returns a callable that takes the rest. It is clearer than a `lambda` for callbacks and keeps a readable `repr`.

```python
from functools import Placeholder, partial

int_from_hex = partial(int, base=16)
int_from_hex("ff")                                   # 255

starts_with_http = partial(str.startswith, Placeholder, "http")   # 3.14: leave the first slot open
starts_with_http("https://example.com")              # True
```

## reduce

`functools.reduce(fn, iterable, initial)` folds a sequence into one value by applying `fn` pairwise. Prefer `sum`, `math.prod`, `max` or `"".join` when they fit.

```python
import operator
from functools import reduce

reduce(operator.getitem, ["a", "b", "c"], {"a": {"b": {"c": 1}}})     # 1: a nested lookup
reduce(operator.or_, [{"a": 1}, {"b": 2}, {"a": 3}], {})               # {'a': 3, 'b': 2}
```

Pass the initial value: without it, an empty input raises `TypeError`.

## singledispatch and singledispatchmethod

`functools.singledispatch` turns a function into a generic one that picks an implementation by the type of its first argument. It replaces long `isinstance` chains and lets other modules add types without editing the original.

A worked `to_json` with `Decimal`, `date` and `list` handlers is in [singledispatch.md](references/singledispatch.md).

- Dispatch follows the MRO: `bool` uses the `int` implementation unless `bool` is registered; `to_json.dispatch(bool)` shows which one wins.
- Only the first argument's type counts. For methods, `functools.singledispatchmethod` dispatches on the first argument after `self`.
- `typing` generics (`list[int]`) cannot be registered; register `list` and check items inside.

`functools.total_ordering`, which fills in comparison methods, is covered in the `python-object-model` skill.

## Verify

Prove a cache with its hit rate, its bound and, for diskcache, who can write to the folder.

```bash
uv run ruff check --select B019 .                 # expect no lru_cache on a method
uv run python - <<'PY'
import os
import stat
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=2)
def square(n: int) -> int:
    return n * n


for n in (1, 2, 1, 3, 1):
    square(n)
info = square.cache_info()
print(info.hits, info.misses, info.currsize)      # expect 2 3 2: bounded, and hits are counted

folder = Path(sys.argv[1] if len(sys.argv) > 1 else ".")   # POSIX: pass your diskcache folder
mode = folder.stat().st_mode
assert folder.stat().st_uid == os.getuid() and not mode & (stat.S_IWGRP | stat.S_IWOTH)
PY
uv pip list 2>/dev/null | grep -iE "^(cachetools|diskcache) "   # diskcache 5.6.3 has the open advisory
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `@cache` on ever-new inputs | `lru_cache(maxsize=N)` or a TTL |
| `@lru_cache` on a method | `cached_property`, or a per-instance cache |
| `@cache` on `async def` | cache tasks |
| returning a shared list or dict | an immutable value, or a copy |
| one cache with no invalidation rule | decide when a value is wrong before adding it |
| diskcache in a shared or synced folder | a private per-user folder |

## Pre-merge checklist

A cache is ready when every item holds.

- [ ] Profiling showed the function is hot; `cache_info()` shows a useful hit rate.
- [ ] The result depends only on the arguments, or the key holds a version or the cache a TTL.
- [ ] The cache is bounded by size, bytes or time; nothing is `@cache` on unbounded inputs.
- [ ] Cached values are immutable or copied; arguments are hashable and passed consistently.
- [ ] No `lru_cache` on methods; per-instance caches or `cached_property` instead.
- [ ] Shared cachetools caches have `lock=` or `condition=`; async functions cache tasks, not coroutines.
- [ ] Disk caches live in the per-user cache folder; tests clear caches between cases.

## Gotchas

These are the caching bugs seen most often.

1. **`TypeError: unhashable type: 'list'` from a cached function.** A list or dict was passed. Pass a tuple or frozenset, or convert inside an uncached wrapper.
2. **Memory grows steadily in a long-running process.** `@cache` or `lru_cache(maxsize=None)` on unbounded keys, or `lru_cache` on a method keeping instances alive. Bound the cache and move it per instance.
3. **A cached value changed for every caller.** A caller mutated the returned dict or list. Return immutable data.
4. **Tests pass alone and fail together.** A cache kept results from an earlier test. Call `cache_clear()` in a fixture.
5. **`RuntimeError: cannot reuse already awaited coroutine`.** `@cache` was put on an `async def`. Cache tasks, as in the `async_cached` pattern.
6. **An expensive call runs several times at start-up.** Concurrent threads missed the cache together. Use cachetools with `condition=threading.Condition()`.
7. **A cache returns a result for the wrong type.** Equal values of different types share a key (`1` and `1.0`). Use `typed=True`.

## References

The reference files hold the detail.

- [singledispatch.md](references/singledispatch.md): a `to_json` generic function with handlers registered by annotation
- [cachetools-diskcache.md](references/cachetools-diskcache.md): cachetools cache classes, `cached` and `cachedmethod` parameters, keys, `TLRUCache`, `getsizeof`, fake timers for tests; diskcache `Cache`, `memoize`, expiry, tags, transactions, `FanoutCache`, sharing between processes and the platform notes

## See also

- `python-performance`: profiling before adding a cache
- `python-object-model`: `cached_property` as a descriptor and `total_ordering`
- `python-decorators`: how wrappers keep signatures
- `python-security`: pickle and other unsafe loads
- `python-concurrency`: locks and duplicate work between threads
