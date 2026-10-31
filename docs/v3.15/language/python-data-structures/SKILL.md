---
name: python-data-structures
description: Chooses and uses Python's built-in and standard-library data structures by their cost - list, tuple, dict, set and frozenset, collections.deque, Counter, defaultdict, OrderedDict and ChainMap, heapq priority queues and the 3.14 max-heap functions, bisect on sorted lists with key=, array and memoryview for compact numbers and zero-copy bytes, the big-O cost of common operations, sorting with key functions and stability, dict insertion order, and graphs and trees in plain Python with BFS, Dijkstra and graphlib. Use when picking a container, a loop is slow because of list membership or pop(0), building a queue, priority queue, LRU or counter, sorting by several keys, keeping a list sorted, representing a graph or tree, or when memory is high because of many small objects.
metadata:
  tags:
  - python
  - data-structures
  - collections
  - heapq
  - bisect
  - big-o
  - sorting
  - graphs
---

# Python data structures

Most slow Python code is slow because of the container, not the interpreter: `x in some_list` inside a loop, `list.pop(0)` as a queue, a sort repeated to get the top item. Python ships fast structures for these jobs in the built-ins, `collections`, `heapq`, `bisect` and `array`. This skill says which to pick by the operations you need and what each operation costs. Classes that hold records (dataclasses, pydantic) are in the `python-data-models` skill; measuring is in the `python-performance` skill.

## Step 0: read the Python version

A few helpers are recent, so read the oldest Python the code supports first.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
grep -n "requires-python" pyproject.toml
```

| Version | Change that matters |
|---|---|
| 3.7 | `dict` keeps insertion order as a language guarantee |
| 3.9 | `graphlib.TopologicalSorter`; `dict \| dict` merge |
| 3.10 | `bisect` functions take `key=`; `Counter.total()` and `<=` comparisons between counters |
| 3.14 | `heapq` max-heap functions: `heapify_max`, `heappush_max`, `heappop_max`, `heappushpop_max`, `heapreplace_max` |
| 3.15 | built-in `frozendict`: a hashable, immutable mapping |

## Choose by the operations you need

Pick the container from the operations that run most often, not from habit.

| You mostly | Use | Cost of that operation |
|---|---|---|
| append, iterate, index by position | `list` | O(1) amortised append, O(1) index |
| test membership, remove duplicates | `set` | O(1) average |
| look up by key | `dict` | O(1) average |
| add or remove at both ends (queue, sliding window) | `collections.deque` | O(1) at either end |
| take the smallest (or largest) item repeatedly | `heapq` on a list | O(log n) push and pop |
| keep items sorted and search them | a sorted `list` with `bisect` | O(log n) search, O(n) insert |
| count occurrences | `collections.Counter` | O(1) per update |
| group values by key | `collections.defaultdict(list)` | O(1) per append |
| store an immutable record or a dict key | `tuple`, `frozenset`, `frozendict` (3.15) | hashable |
| store millions of numbers compactly | `array.array` or NumPy | 8 bytes per float instead of about 32 |

The full cost table for each type is in [complexity.md](references/complexity.md).

## list, tuple, set and frozenset

A `list` is a dynamic array: fast at the end, slow at the front, linear for `in`. A `tuple` is an immutable list: hashable when its items are, slightly smaller, and a clear signal that the length is fixed. Sets and frozensets hold unique hashable items with constant-time membership.

```python
emails = ["ann@example.com", "bob@example.com", "ann@example.com"]
seen: set[str] = set()
for email in emails:
    if email in seen:                     # O(1); the same test on a list is O(n)
        continue
    seen.add(email)

a, b = {1, 2, 3}, {2, 3, 4}
a & b, a | b, a - b, a ^ b                # {2, 3} {1, 2, 3, 4} {1} {1, 4}

pair_scores = {frozenset({"ann", "bob"}): 3}   # order-free pair as a dict key
```

- Converting a list to a set once and testing many times beats testing the list: on 100,000 items, one `in` on a list measured about 0.5 ms against well under a microsecond on a set.
- `list(dict.fromkeys(items))` removes duplicates and keeps the first-seen order; `set(items)` loses the order.
- Sets of mutable objects are impossible (`unhashable type`); use a tuple of the identifying fields, or the object's id field.

## dict and its ordering

A `dict` maps hashable keys to values with O(1) average lookup, insert and delete. Since 3.7 it keeps insertion order.

```python
d = {"b": 1, "a": 2}
d["b"] = 3                 # updating a key keeps its position: ['b', 'a']
del d["b"]; d["b"] = 4     # deleting and re-inserting moves it to the end: ['a', 'b']

a, b = {"x": 1, "y": 2}, {"y": 0, "z": 3}
a | b                      # {'x': 1, 'y': 0, 'z': 3}: the right side wins
a.keys() & b.keys()        # {'y'}: key views support set operations
```

- `dict` equality ignores order (`{"a": 1, "b": 2} == {"b": 2, "a": 1}`); `OrderedDict` equality does not.
- Changing a dict's size while iterating over it raises `RuntimeError`. Iterate over `list(d)` when deleting.
- `d.get(k, default)` reads without inserting; `d.setdefault(k, [])` inserts and returns; `defaultdict` when every missing key needs a default.

## collections.deque for queues and windows

`collections.deque` is a double-ended queue with O(1) `append`, `appendleft`, `pop` and `popleft`. Use it for FIFO queues, BFS and "last N items". `deque(maxlen=n)` drops items from the other end automatically.

```python
from collections import deque

queue = deque(["a", "b"])
queue.append("c")
first = queue.popleft()            # 'a', O(1); list.pop(0) is O(n)

recent: deque[int] = deque(maxlen=3)     # keeps the last three readings
for value in [3, 5, 8, 13]:
    recent.append(value)                 # deque([5, 8, 13], maxlen=3)
```

Indexing into the middle of a deque is O(n); use a list when random access dominates. `deque` appends and pops are thread-safe; for producer-consumer work that must block or wait, use `queue.Queue`.

## Counter, defaultdict, OrderedDict and ChainMap

The `collections` module adds specialised dicts for counting, grouping, ordering and layering.

```python
from collections import ChainMap, Counter, OrderedDict, defaultdict

counts = Counter("the cat and the hat".split())
counts.most_common(1)              # [('the', 2)]
counts["dog"]                      # 0: missing keys count as zero, without being inserted
Counter(apples=2) <= Counter(apples=3, pears=1)   # True: "is a sub-multiset of" (3.10)

by_owner: defaultdict[str, list[str]] = defaultdict(list)
by_owner["ann"].append("order-1")  # no "if key not in" dance

lru: OrderedDict[str, bytes] = OrderedDict(key=b"v")
lru.move_to_end("key")             # mark as recently used (raises KeyError if absent)
lru.popitem(last=False)            # drop the oldest

cli_args, env_vars, defaults = {}, {"user": "ann"}, {"user": "guest", "debug": False}
settings = ChainMap(cli_args, env_vars, defaults)   # lookups try each mapping in order
settings["user"]                   # the first mapping that has "user" wins
```

- `Counter` arithmetic (`+`, `-`, `&`, `|`) drops zero and negative counts; `subtract()` keeps them. `counts.total()` sums all counts.
- Reading a missing key from a `defaultdict` inserts it. Use `key in d` or `d.get(key)` to test without inserting.
- `ChainMap` writes go only to the first mapping, which suits "defaults, then config, then command-line" layering. `new_child()` adds a scope on top.
- For a cache, `functools.lru_cache` or cachetools is usually better than a hand-written `OrderedDict` LRU (the `python-functools-caching` skill).

## heapq for priority queues

`heapq` keeps a plain list arranged as a binary min-heap: `heap[0]` is always the smallest item, push and pop are O(log n), and `heapify` turns a list into a heap in O(n). It is the standard priority queue.

```python
import heapq
import itertools

order = itertools.count()                     # tie-breaker: equal priorities keep insertion order
tasks: list[tuple[int, int, dict[str, str]]] = []
heapq.heappush(tasks, (2, next(order), {"job": "write"}))
heapq.heappush(tasks, (1, next(order), {"job": "read"}))
priority, _, task = heapq.heappop(tasks)       # the lowest priority number first

scores = [70, 95, 88, 60]
heapq.nlargest(3, scores)                      # top 3 without sorting everything: O(n log 3)
list(heapq.merge([1, 4], [2, 3]))              # lazily merge already-sorted inputs: [1, 2, 3, 4]
```

- Items are compared as tuples. Without the counter, two equal priorities compare the payloads, and objects that do not support `<` raise `TypeError`.
- On 3.14, `heapq.heappush_max`, `heappop_max` and `heapify_max` give a max-heap directly. On older versions, push negated priorities.
- Changing an item's priority means marking the old entry as removed and pushing a new one; a heap has no fast "update".
- `queue.PriorityQueue` wraps `heapq` with a lock for threads; `asyncio.PriorityQueue` for coroutines.

## bisect on sorted lists

`bisect` finds positions in a sorted list in O(log n); `insort` inserts while keeping the order, which costs O(n) because later items shift. It fits data that is searched often and changed rarely.

```python
import bisect
from operator import itemgetter

grades = [60, 70, 80, 90]


def grade(score: int) -> str:
    return "FDCBA"[bisect.bisect_right(grades, score)]     # 70 -> 'C', 90 -> 'A'


def index_of(items: list[int], x: int) -> int:
    i = bisect.bisect_left(items, x)
    if i != len(items) and items[i] == x:
        return i
    raise ValueError(x)


people = [("ann", 41), ("bob", 25), ("eve", 33)]
people.sort(key=itemgetter(1))                                   # sorted by age
first_30_plus = bisect.bisect_left(people, 30, key=itemgetter(1))   # key= since 3.10
```

`bisect_left` gives the first position where `x` could go, `bisect_right` the position after any equal items. For many inserts and deletes in a large sorted collection, the third-party `sortedcontainers` package gives O(log n) for all of them.

## array and memoryview for compact data

`array.array` stores numbers of one C type contiguously: 8 bytes per float, where a list stores an 8-byte pointer plus a 24-byte float object. `memoryview` gives zero-copy slices of anything that exposes a buffer (`bytes`, `bytearray`, `array`, NumPy arrays, `mmap`).

```python
import array

readings = array.array("d", [0.0]) * 1_000_000      # 8 MB, contiguous doubles

buffer = bytearray(b"header:payload")
body = memoryview(buffer)[7:]                        # no copy
body[0:3] = b"PAY"                                   # writes through to buffer
bytes(buffer)                                        # b'header:PAYload'
```

- Slicing `bytes` copies; slicing a `memoryview` does not. Parse large binary files or network buffers through memoryviews.
- `memoryview.cast("B")` reinterprets the memory as bytes; `.tolist()` converts back to Python numbers.
- For arithmetic on many numbers, NumPy is faster than `array`; `array` is for storage, I/O and the standard library only.

## Sorting with key functions and stability

`sorted(iterable, key=...)` returns a new list; `list.sort(key=...)` sorts in place and returns `None`. Both are stable: items with equal keys keep their original order, which makes multi-key sorts reliable.

```python
from operator import itemgetter

rows = [
    {"name": "ann", "age": 41, "country": "FR", "city": "Lyon", "created": "2026-03-01"},
    {"name": "bob", "age": 25, "country": "DE", "city": "Bonn", "created": "2026-01-09"},
]
rows.sort(key=itemgetter("country", "city"))                 # several keys, all ascending
rows.sort(key=lambda r: (-r["age"], r["name"]))              # age descending, then name ascending

rows.sort(key=itemgetter("name"))                            # mixed directions on non-numeric keys:
rows.sort(key=itemgetter("created"), reverse=True)           # sort by the secondary key first

sorted(["bob", "Ann"], key=str.casefold)                     # case-insensitive
sorted([3, None, 1], key=lambda v: (v is None, v or 0))      # None values last
```

- The key function is called once per item, so an expensive key is computed n times, not n log n.
- `reverse=True` keeps stability: equal items stay in their original order.
- Mixed types do not sort: `sorted([1, "a"])` raises `TypeError`. Normalise first.
- Plain string sorting is by code point (`"Zoe" < "bob"`, accented letters last). For human-language order, use `locale.strxfrm` as the key or PyICU.

## Graphs and trees in plain Python

A graph is usually a `dict` mapping each node to a `set` (or a dict of weights) of neighbours, and a tree is a node object with a list of children. Breadth-first search uses a `deque`, Dijkstra uses `heapq`, and dependency ordering uses `graphlib.TopologicalSorter`.

```python
from collections import defaultdict, deque
from graphlib import TopologicalSorter

graph: defaultdict[str, set[str]] = defaultdict(set)
for u, v in [("a", "b"), ("b", "c")]:
    graph[u].add(v)
    graph[v].add(u)                              # omit for a directed graph


def bfs_path(graph: dict[str, set[str]], start: str, goal: str) -> list[str] | None:
    parent: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == goal:
            path = []
            step: str | None = node
            while step is not None:
                path.append(step)
                step = parent[step]
            return path[::-1]
        for nxt in graph.get(node, ()):
            if nxt not in parent:
                parent[nxt] = node
                queue.append(nxt)
    return None


deps = {"app": {"db", "cache"}, "db": {"config"}, "cache": {"config"}, "config": set()}
list(TopologicalSorter(deps).static_order())    # ['config', 'db', 'cache', 'app']
```

Write graph and tree traversals with an explicit stack or queue rather than recursion: the default recursion limit is 1000, and a tree 5000 levels deep fails with `RecursionError`. Dijkstra, union-find, tries and tree walks are in [graphs-trees.md](references/graphs-trees.md); for large graph algorithms, NetworkX or rustworkx.

## Worked example: the most frequent errors in a sliding window

A monitor that reports the top error codes of the last N events combines three structures: a `deque` remembers the order, a `Counter` holds the counts, and `heapq.nsmallest` picks the top k without sorting everything. Each event costs O(1), so the window size does not slow the stream down.

```python
import heapq
from collections import Counter, deque


class RecentTop:
    def __init__(self, window: int) -> None:
        if window < 1:
            raise ValueError(f"window must be at least 1, got {window}")
        self._window = window
        self._events: deque[str] = deque()
        self._counts: Counter[str] = Counter()

    def add(self, code: str) -> None:
        self._events.append(code)
        self._counts[code] += 1
        if len(self._events) > self._window:
            oldest = self._events.popleft()
            self._counts[oldest] -= 1
            if not self._counts[oldest]:
                del self._counts[oldest]                  # the Counter stays as small as the window

    def top(self, k: int) -> list[tuple[str, int]]:
        """Return up to k (code, count) pairs, highest count first, ties by code; [] when empty."""
        return heapq.nsmallest(k, self._counts.items(), key=lambda item: (-item[1], item[0]))


recent = RecentTop(window=3)
assert recent.top(2) == []
for code in ["E1", "E2", "E2", "E3"]:
    recent.add(code)
assert recent.top(2) == [("E2", 2), ("E3", 1)]            # E1 left the window
```

A plain `list` here would cost O(n) per event at `pop(0)`, and `sorted(counts.items())` would pay O(n log n) on every report.

## Verify

Prove a container choice by measuring how the cost grows, because a loop that looks fine on ten items can be quadratic.

```bash
uv run python -m timeit -s "items = list(range(100_000))" "99_999 in items"    # about 0.5 ms per test
uv run python -m timeit -s "items = set(range(100_000))" "99_999 in items"     # well under a microsecond
uv run python - <<'PY'
import time


def cost(n: int) -> float:
    items = list(range(n))
    start = time.perf_counter()
    while items:
        items.pop(0)                                 # put the suspect loop here
    return time.perf_counter() - start


print(round(cost(400_000) / cost(200_000), 1))       # about 2 is linear; about 4 is quadratic
PY
uv run ruff check --select PERF,C4,B031 .            # expect no finding
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `if x in items` on a list inside a loop | build a `set` once, then test it |
| `items.pop(0)` or `items.insert(0, x)` | `collections.deque` |
| `sorted(items)[0]` on every iteration | `heapq`, or `min` for one pass |
| `(priority, payload)` tuples in a heap | `(priority, next(counter), payload)` |
| a hand-written LRU on `OrderedDict` | `functools.lru_cache` or cachetools |
| recursion over data of unknown depth | an explicit stack or `deque` |

## Pre-merge checklist

A choice of data structure is sound when every item holds.

- [ ] Membership tests inside loops run against a `set` or `dict`, not a list.
- [ ] Queues use `deque`; priority queues use `heapq` with a tie-breaking counter.
- [ ] Repeated "top k" uses `heapq.nlargest`/`nsmallest`; repeated searches of sorted data use `bisect`.
- [ ] Counting and grouping use `Counter` and `defaultdict`; lookups that must not insert use `get`.
- [ ] Sorts use `key=` functions, rely on stability for multi-key order, and never compare mixed types.
- [ ] Large numeric data lives in `array` or NumPy; large binary data is sliced through `memoryview`.
- [ ] Graph and tree traversals are iterative.

## Gotchas

These are the container bugs seen most often.

1. **A loop gets quadratically slower as data grows.** It does `x in list`, `list.pop(0)`, `list.insert(0, x)` or `list.remove(x)` per item. Use a set, a deque or a dict.
2. **`TypeError: '<' not supported` from `heapq`.** Two entries had equal priorities and the payloads were compared. Add a counter as the second tuple element.
3. **A `defaultdict` grows keys nobody set.** Reading `d[key]` for a missing key inserts it. Test with `in` or `get`.
4. **`RuntimeError: dictionary changed size during iteration`.** The loop deleted or added keys. Iterate over `list(d)` or build a new dict.
5. **A multi-key sort comes out in the wrong order.** Successive sorts were run primary-key first. Sort by the least significant key first, or use one tuple key.
6. **`list.sort()` returned `None` and the variable is now `None`.** `sort` works in place. Use `sorted()` for a new list.
7. **`RecursionError` walking a deep tree or graph.** Recursion depth follows the data. Use an explicit stack.

## References

The reference files hold the detail.

- [complexity.md](references/complexity.md): big-O tables for list, deque, dict, set, heapq and bisect operations, amortised costs, memory per object with `sys.getsizeof`, and measuring a choice with `timeit`
- [graphs-trees.md](references/graphs-trees.md): adjacency representations, BFS and iterative DFS, Dijkstra with heapq, topological order and parallel batches with graphlib, cycle detection, union-find, tries, tree nodes and iterative traversal, and when to use NetworkX

## See also

- `python-performance`: measuring and profiling before and after a change
- `python-functools-caching`: caches instead of hand-written LRU dicts
- `python-data-models`: dataclasses and pydantic for records
- `numpy`: arrays when arithmetic on many numbers dominates
