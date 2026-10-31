---
name: python-iterators-generators
description: Writes and debugs lazy iteration in Python - the iterator protocol (__iter__ and __next__, iterables versus iterators), generator functions and generator expressions, yield from with return values, send, throw and close, cleanup with finally and contextlib.closing, iter() with a sentinel for chunked reads, generator pipelines that process files larger than memory in constant memory, lazy versus eager evaluation, exhausted-iterator bugs, itertools (batched, pairwise, chain, groupby, islice, tee, accumulate) with their traps, and async generators with aclosing. Use when processing large files or streams lazily, writing a generator or a custom iterator, chaining processing steps, when a loop over the same data yields nothing the second time, groupby gives split groups, or an async generator does not clean up.
metadata:
  tags:
  - python
  - iterators
  - generators
  - itertools
  - lazy-evaluation
  - streaming
  - async-generators
---

# Python iterators and generators

Iteration in Python is a protocol: `for x in obj` calls `iter(obj)` once to get an iterator, then `next()` on it until `StopIteration`. Generators are the short way to write iterators, and chains of them process data far larger than memory one item at a time. The price is that an iterator runs once and runs late: a second loop sees nothing, and work happens when the values are pulled, not where the code is written. This skill covers the protocol, generators, pipelines and their traps; [itertools-recipes.md](references/itertools-recipes.md) has the itertools reference and recipes.

## Step 0: read the Python version

Several helpers are recent, so read the oldest supported Python first.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
grep -n "requires-python" pyproject.toml
```

| Version | Change that matters |
|---|---|
| 3.10 | `itertools.pairwise`, `zip(strict=True)`, `contextlib.aclosing`, built-ins `aiter` and `anext` |
| 3.12 | `itertools.batched` |
| 3.13 | `batched(..., strict=True)` refuses a short last batch; `generator.close()` returns the generator's return value |
| 3.14 | `map(..., strict=True)` checks that all iterables have the same length |
| 3.15 | unpacking in comprehensions: `[*row for row in rows]` flattens one level |

## Iterables and iterators

An iterable is anything `iter()` accepts: it has `__iter__` (or the old `__getitem__` protocol). An iterator is an iterable that also has `__next__` and returns itself from `__iter__`. Lists, dicts and `range` are iterables that hand out a fresh iterator each time; files, generators, `map`, `zip` and `csv.reader` are iterators and can be consumed once.

| Object | Iterable | Iterator (one pass) | `len()` |
|---|---|---|---|
| `list`, `tuple`, `str`, `dict`, `set`, `range` | yes | no | yes |
| generator, `map`, `filter`, `zip`, `enumerate`, `reversed` | yes | yes | no |
| open file, `csv.reader`, `os.scandir()` | yes | yes | no |
| `dict.keys()`, `.items()` views | yes | no | yes |

Test for one-pass objects with `iter(x) is x`. A function that loops over its argument twice should accept `Collection` or call `list()` once; one that loops once can accept any `Iterable`.

## A reusable iterable class

The simplest iterable class makes `__iter__` a generator function. Each `for` loop then gets a new, independent generator, so the object can be iterated many times.

```python
from collections.abc import Iterator


class Countdown:
    def __init__(self, start: int) -> None:
        self.start = start

    def __iter__(self) -> Iterator[int]:
        n = self.start
        while n > 0:
            yield n
            n -= 1


c = Countdown(3)
list(c), list(c)        # ([3, 2, 1], [3, 2, 1])
```

Writing `__next__` by hand (keep a position, raise `StopIteration` at the end, return `self` from `__iter__`) gives a one-pass iterator; it is rarely worth it.

## iter() with a sentinel for chunked reads

The two-argument form `iter(callable, sentinel)` calls `callable()` until it returns `sentinel`. It turns any "read the next piece" function into an iterator, which is the clean way to read a binary file in fixed-size chunks.

```python
import hashlib
from functools import partial
from pathlib import Path


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(partial(f.read, 1 << 16), b""):     # 64 KiB at a time until EOF
            digest.update(chunk)
    return digest.hexdigest()
```

Memory stays at one chunk whatever the file size. `hashlib.file_digest(f, "sha256")` (3.11) does this for hashes; the pattern serves uploads, socket reads and `queue.get` until a stop marker.

## Generator functions and generator expressions

A function containing `yield` returns a generator when called; its body runs only as values are requested, pausing at each `yield`. A generator expression `(f(x) for x in xs)` is the one-line form.

```python
import json
from collections.abc import Iterator
from pathlib import Path


def read_records(path: Path) -> Iterator[dict[str, int]]:
    with path.open(encoding="utf-8") as f:             # the file stays open while the consumer iterates
        for line in f:
            if line.strip():
                yield json.loads(line)


total = sum(r["amount"] for r in read_records(Path("orders.jsonl")))  # no list is ever built
```

- Calling `read_records(path)` runs nothing, not even the `open`; a missing file fails on the first `next()`. Validate early in a plain wrapper that returns the generator.
- Annotate a generator as `Iterator[T]`, or `Generator[Yield, Send, Return]` when it receives values or returns one.
- Raising `StopIteration` inside a generator is converted to `RuntimeError` (PEP 479). End a generator with `return`.

## yield from and delegating

`yield from sub` hands control to a sub-iterator: every value it yields passes straight to the caller, `send` and `throw` go through to it, and the expression's value is the sub-generator's `return` value. Use it to split a long generator into steps.

```python
from collections.abc import Generator, Iterable, Iterator


def read_header(lines: Iterator[str]) -> Generator[str, None, dict[str, str]]:
    meta: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            return meta                             # becomes the value of "yield from"
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
        yield key.strip()
    return meta


def parse(lines: Iterable[str]) -> Iterator[str]:
    it = iter(lines)                                # one iterator shared by both steps
    meta = yield from read_header(it)
    yield f"meta={meta}"
    yield from it                                   # the rest of the lines
```

Passing `it` rather than `lines` matters: the body continues where the header step stopped. With a list, each step would start again from the top.

## send, throw and close

A generator is also a coroutine in the old sense: `gen.send(value)` resumes it and makes the paused `yield` expression evaluate to `value`; `gen.throw(exc)` raises `exc` at the paused `yield`; `gen.close()` raises `GeneratorExit` there so `finally` blocks and `with` blocks run.

```python
from collections.abc import Generator


def averager() -> Generator[float, float, None]:
    total = count = 0
    average = 0.0
    while True:
        value = yield average
        total += value
        count += 1
        average = total / count


avg = averager()
next(avg)                    # prime it: run to the first yield
avg.send(10)                 # 10.0
avg.send(20)                 # 15.0
```

- The first call must be `next(gen)` or `gen.send(None)`; sending a value to a fresh generator raises `TypeError`.
- New code that needs values fed in over time is usually clearer as a class with a method.
- A generator that catches `GeneratorExit` must not `yield` again; it may `return` a value, which `close()` returns since 3.13.

## Cleanup in generators

A `with` block or `try/finally` inside a generator runs its cleanup when the generator finishes, when it is closed, or when it is garbage-collected. Relying on garbage collection is fragile: on PyPy or with a reference kept in a traceback, the file stays open. Close generators you stop early.

```python
from contextlib import closing
from pathlib import Path

with closing(read_records(Path("orders.jsonl"))) as records:    # read_records: the generator above
    for record in records:
        if record["id"] == 17:
            break                       # closing() calls records.close(): the file is closed now
```

The same applies to any generator holding a lock, a database cursor or a temporary file.

## Generator pipelines for large data

A pipeline is a chain of generators where each step takes an iterable and yields results. Each item flows through all steps before the next is read, so memory stays flat for any input size and the first result appears immediately.

```python
import csv
import itertools
from collections.abc import Iterable, Iterator
from pathlib import Path

from my_app.db import insert_many


def read_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def valid(rows: Iterable[dict[str, str]]) -> Iterator[dict[str, str]]:
    for row in rows:
        if row["amount"].strip():
            yield row


def amounts(rows: Iterable[dict[str, str]]) -> Iterator[float]:
    for row in rows:
        yield float(row["amount"])


total = sum(amounts(valid(read_csv(Path("sales.csv")))))       # one row in memory at a time

for chunk in itertools.batched(valid(read_csv(Path("sales.csv"))), 1000):
    insert_many(chunk)                                         # bulk writes, 1000 rows each
```

- Keep each step small and testable; preview a huge input with `itertools.islice(pipeline, n)`.
- Sorting, grouping across the whole input, or `len` need all the data. Put such steps at the end, or switch to a database or Polars when the data does not fit in memory.
- Pipelines run on one core. CPU-heavy steps go to a process pool (`ProcessPoolExecutor.map(fn, items, chunksize=...)`), covered in the `python-concurrency` skill.

## Lazy versus eager evaluation

A lazy expression (a generator, `map`, `filter`, `zip`) computes nothing until consumed; an eager one (a list comprehension, `list()`, `sorted()`) computes everything now. Choose by how the result is used.

| Use lazy when | Use eager when |
|---|---|
| the input is large or unbounded | the result is used more than once |
| the consumer may stop early (`any`, `next`, `islice`) | you need `len`, indexing or slicing |
| results should start flowing before all input is read | the source may change or close before consumption |
| it feeds one aggregate (`sum`, `max`, `"".join`) | errors must surface here, not later in another function |

Laziness also moves the moment of evaluation. A generator over a list sees items appended after it was created; a generator that reads a file fails after the `with` block closed it. Materialise with `list()` at the point where the data must be fixed.

## Exhausted-iterator bugs

A one-pass iterator consumed once is empty forever after, and nothing raises: the second loop just does nothing.

```python
lines = ["1", "2", "3"]
rows = (int(line) for line in lines)
count = sum(1 for _ in rows)         # 3
for row in rows:                     # runs zero times: rows is already exhausted
    print(row)

nums = map(int, ["1", "2", "3"])
2 in nums                            # True, and it consumed 1 and 2 to find it
list(nums)                           # [3]
```

- Convert to a list once if you need two passes, or compute both results in one loop.
- `in` on an iterator consumes up to the match; `zip(it, it)` pairs consecutive items because both arguments are the same iterator.
- A function that receives an iterator and returns a lazy result shares it with the caller; if both advance it, items vanish. Document which function owns the iterator.
- Lists restart, which hides these bugs; test pipeline steps with `iter([...])` inputs too.

## Async generators and aclosing

An `async def` function containing `yield` is an async generator: consume it with `async for`, and inside it you may `await` between values. Pages from an API, rows from an async database driver and lines from a subprocess are natural async generators.

```python
import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing


async def pages(client, url: str) -> AsyncIterator[dict]:
    while url:
        response = await client.get(url)
        body = response.json()
        yield body
        url = body.get("next")


async def first_matching(client, url: str) -> dict | None:
    async with aclosing(pages(client, url)) as stream:      # closes the generator on break or error
        async for page in stream:
            if page["items"]:
                return page
    return None
```

- Breaking out of `async for` leaves the async generator suspended. Its `finally` runs only when the event loop finalises it later, possibly after the connection it uses was closed. `aclosing` closes it at once.
- `asyncio.run` closes leftover async generators at shutdown, but code in their `finally` may then run against resources already gone.
- `yield from` is not allowed in async generators; write `async for item in sub(): yield item`.

## itertools at a glance

The `itertools` module gives fast, lazy building blocks. The ones used most are in the table; behaviour details, traps and the official recipes are in [itertools-recipes.md](references/itertools-recipes.md). `more-itertools` (11.x) packages many more recipes.

| Need | Function |
|---|---|
| fixed-size chunks | `batched(it, n)` |
| consecutive pairs | `pairwise(it)` |
| flatten one level | `chain.from_iterable(nested)` |
| first `n`, or a slice of an iterator | `islice(it, start, stop, step)` |
| group adjacent equal keys | `groupby(sorted(it, key=k), key=k)` |
| running totals or maximums | `accumulate(it, func)` |
| two independent passes over one iterator | `tee(it, 2)` |

## Verify

Prove a pipeline step with one-pass input, flat memory and cleanup on early exit; a test that feeds only lists hides these bugs.

```bash
uv run ruff check --select B031,B905 .             # groupby reuse, zip without strict
uv run python - <<'PY'
import tracemalloc
from contextlib import closing

events: list[str] = []


def squares(numbers):                              # put your pipeline step here
    try:
        for n in numbers:
            yield n * n
    finally:
        events.append("closed")


assert list(squares(iter([1, 2]))) == [1, 4]       # one-pass input works

tracemalloc.start()
total = sum(squares(range(1_000_000)))
peak = tracemalloc.get_traced_memory()[1]
print(total, peak < 100_000)                       # expect True: memory stays flat

events.clear()
with closing(squares([1, 2, 3])) as stream:
    next(stream)
assert events == ["closed"]                        # cleanup ran at the early exit
print("ok")
PY
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `list(generator)` before one loop | iterate the generator directly |
| a second loop over an iterator | `list()` once, or do both jobs in one pass |
| `readlines()` on a large file | iterate the file object |
| `groupby` on unsorted input | sort by the same key first |
| `return (x for x in ...)` inside a `with` block | make the function a generator, with `with` inside it |
| leaving a resource-holding generator early | `closing` or `aclosing` |
| hand-written `__next__` | `__iter__` as a generator function |

## Pre-merge checklist

Iteration code is correct when every item holds.

- [ ] Functions that loop twice take a `Collection` or call `list()` once; one-pass inputs are documented.
- [ ] Large inputs flow through generators; nothing calls `list()` or `readlines()` on them without need.
- [ ] Generators that may be stopped early are closed (`closing`, `aclosing`) or own no resources.
- [ ] No `StopIteration` raised inside a generator; generators end with `return`.
- [ ] `groupby` input is sorted by the same key; `tee` sources are not touched after the split.
- [ ] Pipeline steps have tests with both lists and `iter([...])` inputs.

## Gotchas

These are the iteration bugs seen most often.

1. **The second loop over the data does nothing.** The value was a generator, `map` or file, already consumed. Materialise once with `list()` or compute everything in one pass.
2. **A file-reading generator raises `ValueError: I/O operation on closed file`.** The generator was returned from inside a `with` block in a non-generator function, which closed the file before iteration. Make the function itself a generator, with the `with` inside it.
3. **`groupby` returns the same key several times.** Its input was not sorted by the key; it only groups adjacent items. Sort by the same key first.
4. **Groups from `groupby` are empty.** They were stored with `list(groupby(...))` and read later; each group is a view on the shared iterator. Consume each group inside the loop.
5. **`RuntimeError: generator raised StopIteration`.** A `next()` call inside a generator hit the end. Use `next(it, None)` and `return`, or a `for` loop.
6. **An async generator's cleanup runs late or warns at shutdown.** The consumer broke out of `async for`. Wrap it in `contextlib.aclosing`.
7. **An error is raised far from its cause.** A lazy generator's argument check runs only on the first `next()`. Check arguments in an eager wrapper that returns the generator.

## References

The reference file holds the itertools detail.

- [itertools-recipes.md](references/itertools-recipes.md): every common itertools function with an example, the `groupby` and `tee` traps, `islice` on unbounded iterators, the official recipes (`sliding_window`, `unique_everseen`, `roundrobin`, `partition`, `first_true`, `take`, `nth`, `tail`, `consume`) and when to use more-itertools

## See also

- `python-asyncio`: async iteration inside tasks and timeouts
- `python-data-structures`: `deque`, `heapq.merge` and containers for what you materialise
- `python-concurrency`: process pools for CPU-heavy pipeline steps
- `polars`: lazy pipelines over data that does not fit in memory
- `python-idioms`: everyday loops and comprehensions
