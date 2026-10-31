# Cost of Python container operations

This reference lists the time cost (big-O) of common operations on CPython's containers, the memory each object takes, and how to measure a choice. `n` is the container's size, `k` the size of the other argument or slice. "Average" assumes reasonable hashes; a pathological set of keys that all collide makes dict and set operations O(n).

## list

A `list` is a dynamic array of pointers. Operations at the end are cheap; anything that shifts items is linear.

| Operation | Cost | Note |
|---|---|---|
| `xs[i]`, `xs[i] = v`, `len(xs)` | O(1) | |
| `xs.append(v)`, `xs.pop()` | O(1) amortised | the array grows by over-allocating |
| `xs.pop(0)`, `xs.insert(0, v)`, `xs.insert(i, v)` | O(n) | every later item moves |
| `xs.remove(v)`, `v in xs`, `xs.index(v)`, `xs.count(v)` | O(n) | linear scan |
| `xs[a:b]` | O(k) | copies the slice |
| `xs.extend(ys)`, `xs + ys` | O(k), O(n + k) | `+` builds a new list |
| `xs.sort()`, `sorted(xs)` | O(n log n) | Timsort; O(n) when already sorted or reversed runs |
| `xs.reverse()`, `reversed(xs)` | O(n), O(1) to create | `reversed` is lazy |
| `min(xs)`, `max(xs)`, `sum(xs)` | O(n) | |
| `del xs[i]` | O(n - i) | |
| `xs * m` | O(n·m) | copies references, not objects |

## tuple

A `tuple` has the same costs as a list for reading (`t[i]` O(1), `in` O(n), slicing O(k)) and no mutating operations. Hashing a tuple is O(n) the first time; CPython 3.14 caches the result.

## collections.deque

A `deque` is a doubly linked list of fixed-size blocks: constant time at both ends, linear in the middle.

| Operation | Cost |
|---|---|
| `append`, `appendleft`, `pop`, `popleft` | O(1) |
| `d[0]`, `d[-1]` | O(1) |
| `d[i]` in the middle, `insert`, `remove`, `del d[i]` | O(n) |
| `rotate(k)` | O(k) |
| `v in d` | O(n) |
| `extend(ys)`, `extendleft(ys)` | O(k) |

## dict

A `dict` is a hash table with a separate, ordered entries array, which is why it keeps insertion order.

| Operation | Average | Worst |
|---|---|---|
| `d[k]`, `d[k] = v`, `del d[k]`, `k in d`, `d.get(k)` | O(1) | O(n) |
| `d.pop(k)`, `d.setdefault(k, v)` | O(1) | O(n) |
| `len(d)` | O(1) | |
| iteration over keys, values or items | O(n) | |
| `d.copy()`, `dict(d)`, `d \| e` | O(n), O(n + k) | |
| `d.update(e)`, `d \|= e` | O(k) | |
| `d.keys() & e.keys()` | O(min(n, k)) | |
| `d.popitem()` | O(1) | removes the last inserted item |

A dict that had many deletions keeps its allocated size until it is resized by later inserts; to release memory after removing most keys, build a new dict.

## set and frozenset

A `set` is a hash table of keys only.

| Operation | Average |
|---|---|
| `v in s`, `s.add(v)`, `s.discard(v)`, `s.remove(v)` | O(1) |
| `s \| t` (union) | O(n + k) |
| `s & t` (intersection) | O(min(n, k)) |
| `s - t` (difference) | O(n) |
| `s ^ t` (symmetric difference) | O(n + k) |
| `s <= t` (subset) | O(n) |
| `set(iterable)` | O(k) |

`s & t` iterates over the smaller set and probes the larger, so intersecting a small set with a huge one is fast in either order.

## heapq

`heapq` functions keep a list ordered as a binary heap.

| Operation | Cost |
|---|---|
| `heapify(xs)` | O(n) |
| `heappush`, `heappop`, `heappushpop`, `heapreplace` | O(log n) |
| `heap[0]` (peek at the smallest) | O(1) |
| `nlargest(k, xs)`, `nsmallest(k, xs)` | O(n log k) |
| `merge(*sorted_iterables)` | O(N log m) for N items from m inputs, lazy |
| removing or re-prioritising an arbitrary item | O(n) |

`nlargest` and `nsmallest` with `k = 1` are better written as `max` and `min`; when `k` is close to `n`, `sorted(xs)[:k]` is faster.

## bisect

| Operation | Cost |
|---|---|
| `bisect_left`, `bisect_right` | O(log n) comparisons |
| `insort_left`, `insort_right` | O(log n) search plus O(n) insert |

The `key=` function (3.10) is applied to the list's items during the search, so it runs O(log n) times per lookup; the list must already be sorted by that key.

## Counter and defaultdict

`Counter` and `defaultdict` are dict subclasses with dict costs. `Counter.most_common(k)` is O(n log k) (it uses `heapq.nlargest`), and `most_common()` without `k` sorts everything, O(n log n). Counter arithmetic (`+`, `-`, `&`, `|`) is O(n + k).

## String building

Strings are immutable, so `s += piece` in a loop can copy the whole string each time, O(n²) in total. CPython sometimes optimises it in place, but not reliably and not on other implementations. Collect pieces in a list and `"".join(pieces)` once, O(n), or write to `io.StringIO`.

## Memory per object

Measured with `sys.getsizeof` on 64-bit CPython 3.14. These sizes are the container itself; the objects it refers to are counted separately.

| Object | Bytes |
|---|---|
| `int` (small values) | 28 |
| `float` | 24 |
| `str` of 10 ASCII characters | 51 |
| empty `tuple` / 3-item tuple | 48 / 72 |
| empty `list` / 3-item list | 56 / 88 |
| empty `dict` / 3-item dict | 64 / 224 |
| empty `set` / 3-item set | 216 / 216 |
| empty `deque` | 760 |

Measured per item with `tracemalloc` for 100,000 integers, including the integer objects: about 40 bytes in a `list`, 74 in a `set`, 84 as `dict` keys with a shared value, and 8 per float in `array.array("d")`. For many small records, `__slots__` classes or tuples beat dict-based objects, and columnar storage (arrays, NumPy, Polars) beats both.

## Measuring a choice with timeit

Big-O says how cost grows; constants decide small cases. Measure the real operation with your data sizes before and after a change.

```bash
python -m timeit -s "xs = list(range(100_000)); s = set(xs)" "99_999 in xs"
python -m timeit -s "xs = list(range(100_000)); s = set(xs)" "99_999 in s"
python -m timeit -s "from collections import deque; d = deque(range(100_000))" "d.append(d.popleft())"
```

On one machine running 3.14, the list membership test measured about 0.5 ms and the set test about 30 ns; draining 50,000 items with `list.pop(0)` took about 0.55 s against under 1 ms with `deque.popleft()`. Profiling a whole program to find which operation matters is in the `python-performance` skill.
