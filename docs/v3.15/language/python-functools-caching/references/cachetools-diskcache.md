# cachetools and diskcache in practice

cachetools (7.2.7 in October 2026) gives in-memory caches with eviction by count, size and time, and decorators that use them. diskcache (5.6.3) gives a persistent cache in a folder, backed by SQLite, that several processes can share. This reference covers the APIs as the installed versions expose them, with tested examples.

## cachetools cache classes

Each cachetools cache is a mutable mapping with a `maxsize` and an eviction rule. They can be used directly like a dict or through the `@cached` and `@cachedmethod` decorators.

| Class | Evicts | Typical use |
|---|---|---|
| `LRUCache(maxsize)` | least recently used | general memoisation with a bound |
| `LFUCache(maxsize)` | least frequently used | a few very hot keys among many cold ones |
| `FIFOCache(maxsize)` | oldest inserted | simple, predictable |
| `RRCache(maxsize)` | a random item | cheap eviction when access patterns do not matter |
| `TTLCache(maxsize, ttl)` | expired items first, then least recently used | API responses, lookups that go stale |
| `TLRUCache(maxsize, ttu)` | items past their own expiry time, then least recently used | tokens and records that carry their own lifetime |

All accept `getsizeof=` and `TTLCache`/`TLRUCache` accept `timer=` (default `time.monotonic`).

## The cached decorator

`cachetools.cached(cache, key=hashkey, lock=None, condition=None, info=False)` memoises a function into the cache object you pass. The cache object is shared by every call, so you can inspect, clear or edit it.

```python
import threading

import httpx
from cachetools import TTLCache, cached


@cached(cache=TTLCache(maxsize=1024, ttl=300), lock=threading.Lock(), info=True)
def exchange_rate(base: str, quote: str) -> float:
    return httpx.get(f"https://rates.example/{base}/{quote}", timeout=5).raise_for_status().json()["rate"]


exchange_rate("EUR", "USD")
exchange_rate.cache_info()      # CacheInfo(hits=0, misses=1, maxsize=1024, currsize=1)
exchange_rate.cache             # the TTLCache itself
exchange_rate.cache_key("EUR", "USD")   # the key that call uses
exchange_rate.cache_clear()
```

- `info=True` adds `cache_info()` with the same fields as `functools.lru_cache`; without it, `cache_info` is `None`.
- `lock=` makes access to the cache thread-safe. The function itself runs outside the lock, so two threads that miss the same key both compute it; the first stored value wins.
- `condition=threading.Condition()` also serialises computations per key: the first caller computes, the others wait and read its result. Pass only `condition=`, or build the condition on the same lock (`threading.Condition(lock)` with `lock=lock`). Two unrelated objects fail with `RuntimeError: cannot notify on un-acquired lock`.
- An item larger than `maxsize` under `getsizeof` is not stored; the decorated call still returns it.

## Cache keys in cachetools

The `key=` function receives the call's arguments and returns a hashable key. `cachetools.keys.hashkey` (the default) builds it from all arguments; `typedkey` also includes their types; `methodkey` ignores `self`.

```python
from cachetools import LRUCache, cached
from cachetools.keys import hashkey


def key_without_client(client: object, user_id: int) -> tuple:
    return hashkey(user_id)                       # the client object must not be part of the key


@cached(cache=LRUCache(maxsize=10_000), key=key_without_client)
def load_user(client: object, user_id: int) -> dict:
    return client.get_user(user_id)


load_user.cache.pop(hashkey(42), None)            # invalidate one user after an update
```

Leaving an argument out of the key is right only when it cannot change the result (a client, a logger, a timeout). Leaving out a real input returns wrong data.

## Per-object caches with cachedmethod

`cachetools.cachedmethod(cache, key=methodkey, lock=None, condition=None, info=False)` caches a method in a cache that belongs to the instance. `cache` and `lock` are callables that receive `self` and return the instance's cache and lock.

```python
import threading

from cachetools import TTLCache, cachedmethod


class Catalog:
    def __init__(self) -> None:
        self._cache: TTLCache[object, float] = TTLCache(maxsize=500, ttl=30)
        self._lock = threading.Lock()

    @cachedmethod(lambda self: self._cache, lock=lambda self: self._lock)
    def price(self, sku: str) -> float:
        return query_price(sku)


catalog = Catalog()
catalog.price("A-1")
catalog.price.cache_clear()       # clears this instance's cache only
```

- The cache lives and dies with the instance, so no instance is kept alive by a class-level cache.
- `@cachedmethod` on a `classmethod` is deprecated in cachetools 7 and warns; cache class-level data in a module-level `@cached` function.
- The decorator stores a bound wrapper in the instance `__dict__`. A `__slots__`-only class still works, but it builds a new wrapper on every access and warns `DeprecationWarning: No '__dict__' attribute on 'Catalog' instance to cache 'price'`, so add `"__dict__"` to the slots.

## Per-item lifetimes with TLRUCache

`TLRUCache(maxsize, ttu, timer)` calls `ttu(key, value, now)` when an item is stored and expires it at the time returned. It suits values that carry their own lifetime, such as OAuth tokens with `expires_in`.

```python
from cachetools import TLRUCache


def token_expiry(key: str, value: dict, now: float) -> float:
    return now + value["expires_in"] - 30          # refresh 30 s before the server's expiry


tokens: TLRUCache[str, dict] = TLRUCache(maxsize=100, ttu=token_expiry)
```

## Bounding by size with getsizeof

With `getsizeof=`, `maxsize` counts the sum of `getsizeof(value)` instead of the number of items. `len` works for strings and bytes; for other objects, measure what dominates (the length of a payload, the row count of a result).

```python
from cachetools import LRUCache

pages = LRUCache(maxsize=64 * 1024 * 1024, getsizeof=len)    # about 64 MiB of bytes values
pages["a"] = body                                              # ValueError: value too large if over the limit
```

## Testing with a fake timer

TTL caches take a `timer` callable, so tests can move time without sleeping.

```python
from cachetools import TTLCache


def test_entries_expire_after_ttl() -> None:
    now = [0.0]
    cache = TTLCache(maxsize=10, ttl=60, timer=lambda: now[0])
    cache["k"] = 1
    now[0] = 59
    assert "k" in cache
    now[0] = 61
    assert "k" not in cache
```

For decorated functions, clear the cache in a fixture (`fn.cache_clear()`) so tests do not see each other's results.

## cachetools.func: drop-in lru_cache replacements

`cachetools.func` offers `lru_cache`, `lfu_cache`, `fifo_cache`, `rr_cache` and `ttl_cache(maxsize=128, ttl=600, timer=time.monotonic, typed=False)` with the same interface as `functools.lru_cache`, including `cache_info()` and `cache_clear()`. They are thread-safe. `ttl_cache` is the quickest way to add expiry to an existing `@lru_cache`.

```python
from cachetools.func import ttl_cache


@ttl_cache(maxsize=256, ttl=60)
def feature_flags(account_id: int) -> frozenset[str]:
    return frozenset(load_flags(account_id))
```

## diskcache Cache basics

`diskcache.Cache(directory, timeout=60, **settings)` opens or creates a cache in a folder. It behaves like a dict with extras: per-item expiry, tags, atomic counters and transactions. Values are stored with pickle, small ones inside SQLite and large ones as separate files.

```python
from diskcache import Cache

with Cache(cache_dir / "app", size_limit=2**30) as cache:
    cache.set("token", "abc", expire=3600, tag="auth")
    cache.get("token")                    # 'abc', or None when missing or expired
    cache.incr("runs")                    # atomic counter, starts at 0
    with cache.transact():                # several operations as one transaction
        cache.incr("a")
        cache.incr("b")
    cache.evict("auth")                   # remove every item tagged "auth"
    cache.expire()                        # remove expired items now, returns the count
```

| Setting | Default | Meaning |
|---|---|---|
| `size_limit` | `2**30` (1 GiB) | approximate total size before eviction |
| `eviction_policy` | `"least-recently-stored"` | also `"least-recently-used"`, `"least-frequently-used"`, `"none"` |
| `cull_limit` | `10` | items evicted per write when over the limit |
| `disk_min_file_size` | `32768` | values larger than this go to separate files |
| `sqlite_journal_mode` | `"wal"` | write-ahead log for concurrent readers |

Expired items are not removed until they are read, evicted or `expire()` runs, so the folder can stay large after a burst; call `cache.expire()` from a periodic job if that matters.

## diskcache memoize

`Cache.memoize(name=None, typed=False, expire=None, tag=None, ignore=())` caches a function's results on disk. The key includes the function's qualified module name, so two functions never share entries. `ignore=` lists argument positions or names to leave out of the key.

```python
@cache.memoize(expire=7 * 24 * 3600, tag="thumbnail")
def thumbnail(path: str, width: int) -> bytes:
    return render_thumbnail(path, width)


key = thumbnail.__cache_key__("a.jpg", 200)     # ('__main__.thumbnail', 'a.jpg', 200, None)
cache.pop(key, None)                             # invalidate one result
thumbnail.__wrapped__("a.jpg", 200)              # bypass the cache
```

Because the key contains the module path, renaming or moving the function orphans its old entries; they age out through eviction. Put a version in `name=` (`name="thumbnail-v2"`) when the output format changes.

`diskcache.memoize_stampede(cache, expire, beta=1)` recomputes a value early, with a probability that rises near expiry, so many processes do not all miss at once when a popular entry expires.

## Sharing a disk cache between processes

A `Cache` folder can be opened by several processes at once; SQLite's locking keeps it consistent. A `Cache` object opens a new database connection when it notices it is in a forked child, and it can be pickled into a `multiprocessing` worker, where it reopens the same folder.

- `FanoutCache(directory, shards=8, timeout=0.01)` splits the data into several SQLite files to reduce write contention. On a lock timeout it gives up instead of raising: `set` returns `False` and `get` returns the default. That suits a cache, where a miss is acceptable.
- The plain `Cache` waits up to `timeout` seconds (default 60) and then raises `diskcache.Timeout`; pass `retry=True` to `set` to retry instead.
- `diskcache.Lock`, `RLock`, `BoundedSemaphore`, `throttle` and `barrier` build cross-process coordination on the same folder.

## Testing that a cancelled caller leaves the shared task alone

`async_cached` from the skill shares one task between callers, so one test must prove that cancelling a caller reaches neither the task nor the other callers. Delete `asyncio.shield` and this test fails with `CancelledError`.

```python
import asyncio

from my_app.cache import async_cached


def test_cancelling_one_caller_keeps_the_shared_call_running() -> None:
    calls = 0

    @async_cached
    async def load(key: str) -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return key.upper()

    async def call() -> str:
        return await load("a")

    async def scenario() -> None:
        first, second = asyncio.create_task(call()), asyncio.create_task(call())
        await asyncio.sleep(0)                # both callers now wait on the shared task
        first.cancel()
        assert await second == "A"
        assert first.cancelled()
        assert await call() == "A"            # a later caller gets the finished result
        assert calls == 1

    asyncio.run(scenario())
```

## Platform and security notes for diskcache

The cache folder determines both reliability and safety.

- Use the per-user cache directory: `~/Library/Caches/<app>` on macOS, `$XDG_CACHE_HOME` or `~/.cache/<app>` on Linux, `%LOCALAPPDATA%\<app>\Cache` on Windows. `platformdirs.user_cache_dir(app)` returns the right one.
- Never place it on NFS, SMB or a synced folder (Dropbox, OneDrive, iCloud): SQLite locking is unreliable there and the database can be corrupted.
- Values are unpickled on read. Advisory CVE-2025-69872 (GHSA-w8v5-vhqr-4h9v) covers every release through 5.6.3 and no release fixes it: anyone who can write to the folder can run code in your process, so use a trusted folder that only your user can write and never a shared or world-writable directory. `Cache(path, disk=diskcache.JSONDisk)` stores keys and values as JSON and supports only JSON types (`TypeError: Object of type set is not JSON serializable` otherwise), but it does not protect a writable folder: the `mode` column decides how a row is read, and a row set to `MODE_PICKLE` in SQLite ran pickle through `JSONDisk`. Only the folder's permissions stop this.
- On Windows, antivirus scanners can hold the database file briefly; keep the default `timeout` rather than lowering it.
- diskcache has had no release since 5.6.3 (August 2023), so the advisory above stays open. It works on current Python versions; pin it and watch the project before relying on it for years.
