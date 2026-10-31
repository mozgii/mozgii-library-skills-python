# itertools reference and recipes

The `itertools` module provides lazy iterator building blocks implemented in C. They take any iterable, return an iterator, and hold only the state they need, so they work on unbounded streams. This reference shows each common function with its result, the traps in `groupby`, `tee` and `count`, and the official recipes worth copying.

## batched: fixed-size chunks

`itertools.batched(iterable, n)` (3.12) yields tuples of `n` items; the last one may be shorter. With `strict=True` (3.13) a short last batch raises `ValueError`, which suits data that must come in complete records.

```python
from itertools import batched

list(batched("ABCDEFG", 3))              # [('A', 'B', 'C'), ('D', 'E', 'F'), ('G',)]
list(batched([1, 2, 3], 2, strict=True)) # ValueError: batched(): incomplete batch

for chunk in batched(rows, 500):         # bulk insert 500 rows per statement
    insert_many(chunk)
```

On 3.11 and older, the recipe is `while batch := tuple(islice(it, n)): yield batch` with `it = iter(iterable)`.

## pairwise: consecutive pairs

`itertools.pairwise(iterable)` (3.10) yields overlapping pairs `(s0, s1), (s1, s2), ...`. It replaces `zip(xs, xs[1:])`, which copies the list and does not work on iterators.

```python
from itertools import pairwise

[b - a for a, b in pairwise([1, 4, 9, 16])]      # [3, 5, 7]: differences between readings
```

## chain and chain.from_iterable: flatten one level

`chain(a, b, c)` iterates several iterables in turn; `chain.from_iterable(nested)` does the same for an iterable of iterables and is lazy in the outer level too.

```python
from itertools import chain

list(chain([1, 2], (3,), "ab"))                  # [1, 2, 3, 'a', 'b']
list(chain.from_iterable([[1, 2], [3], []]))     # [1, 2, 3]
```

On 3.15 a comprehension can unpack directly: `[*row for row in rows]` gives the same flat list. Neither flattens deeper than one level; recursive flattening needs a small generator, and strings must be excluded or they recurse forever into single characters.

## groupby and its two traps

`itertools.groupby(iterable, key)` yields `(key, group)` for each run of adjacent items with the same key. It does not sort, and each group is a view on the shared underlying iterator.

```python
from itertools import groupby

words = ["apple", "avocado", "banana", "apricot"]

[(k, list(g)) for k, g in groupby(words, key=lambda s: s[0])]
# [('a', ['apple', 'avocado']), ('b', ['banana']), ('a', ['apricot'])]   <- 'a' twice: input not sorted

[(k, list(g)) for k, g in groupby(sorted(words), key=lambda s: s[0])]
# [('a', ['apple', 'apricot', 'avocado']), ('b', ['banana'])]

saved = list(groupby(sorted(words), key=lambda s: s[0]))
[(k, list(g)) for k, g in saved]
# [('a', []), ('b', [])]   <- groups were consumed when groupby advanced
```

- Sort by the same key you group by. Grouping by `s[0]` after sorting by `s` works only because the first letter is the leading sort key.
- Consume or copy each group inside the loop (`{k: list(g) for k, g in ...}`).
- When the data is not sorted and fits in memory, a `defaultdict(list)` grouping in one pass is simpler and does not need the sort.

Summing per group with `groupby` and `itemgetter` looks like this.

```python
import operator
from itertools import groupby

sales = [("eu", 10), ("eu", 5), ("us", 7)]
by_region = {region: sum(amount for _, amount in rows)
             for region, rows in groupby(sorted(sales), key=operator.itemgetter(0))}
# {'eu': 15, 'us': 7}
```

## islice: slicing an iterator

`islice(iterable, stop)` or `islice(iterable, start, stop, step)` slices without building a list, and works on unbounded iterators. Negative indices are not supported because the length is unknown.

```python
from itertools import count, islice

list(islice(count(10), 3))               # [10, 11, 12]
list(islice(range(100), 10, 20, 5))      # [10, 15]
first_ten = list(islice(read_records(path), 10))   # preview a huge file
```

`islice` consumes the items it skips. On a file or generator, `islice(f, 1000, 1010)` reads and discards the first 1000 lines; there is no random access.

## tee: several passes over one iterator

`tee(iterable, n)` returns `n` independent iterators over the same source. It buffers every item that one copy has seen and another has not, so if one copy runs far ahead, the buffer holds that whole gap in memory.

```python
from itertools import tee

src = iter(range(5))
a, b = tee(src)
next(src)                 # never touch the source after tee
list(a)                   # [1, 2, 3, 4]: item 0 was lost to the direct next()
```

- After `tee`, use only the returned iterators.
- When one copy is consumed fully before the other starts, `list()` is simpler and uses the same memory.
- `tee` objects are not thread-safe; do not share them between threads.

## accumulate: running totals

`accumulate(iterable, func=operator.add, initial=None)` yields running results. Any two-argument function works.

```python
from itertools import accumulate

list(accumulate([1, 2, 3, 4]))               # [1, 3, 6, 10]
list(accumulate([3, 1, 4, 1, 5], max))       # [3, 3, 4, 4, 5]: running maximum
list(accumulate([1, 2, 3], initial=100))     # [100, 101, 103, 106]
```

## count, cycle and repeat: infinite iterators

`count(start, step)`, `cycle(iterable)` and `repeat(value, times)` produce endless (or long) sequences; always bound them with `zip`, `islice` or a `break`.

```python
from itertools import count, cycle, islice

for attempt in count(1):                     # 1, 2, 3, ... until break
    if try_once():
        break

colours = cycle(["red", "green", "blue"])    # cycle keeps a copy of every item it has seen
list(islice(count(0, 0.1), 4))               # [0, 0.1, 0.2, 0.30000000000000004]
```

`count` with a float step adds the step repeatedly, so errors accumulate. For evenly spaced floats, compute `start + i * step` from an integer counter.

## Other functions in one table

These functions round out the module.

| Function | Example | Result |
|---|---|---|
| `zip_longest(a, b, fillvalue=x)` | `zip_longest("ab", "xyz", fillvalue="-")` | `('a','x'), ('b','y'), ('-','z')` |
| `takewhile(pred, it)` | `takewhile(lambda x: x < 3, [1, 2, 5, 1])` | `1, 2` |
| `dropwhile(pred, it)` | `dropwhile(lambda x: x < 3, [1, 2, 5, 1])` | `5, 1` |
| `compress(data, selectors)` | `compress("abcd", [1, 0, 1, 0])` | `'a', 'c'` |
| `filterfalse(pred, it)` | `filterfalse(str.isdigit, "a1b2")` | `'a', 'b'` |
| `starmap(fn, it)` | `starmap(pow, [(2, 3), (3, 2)])` | `8, 9` |
| `product(a, b)`, `product(a, repeat=n)` | `product("ab", repeat=3)` | 8 tuples |
| `combinations(it, r)` | `combinations([1, 2, 3], 2)` | `(1,2), (1,3), (2,3)` |
| `permutations(it, r)` | `permutations("abc", 2)` | 6 ordered pairs |

`takewhile` consumes the first item that fails the test and drops it; the rest of the iterator continues after it.

## Official recipes worth copying

The Python documentation lists recipes built from these functions. The ones below are the most useful; each is short enough to copy into a project utility module.

Taking, skipping and finding items in an iterator:

```python
from collections import deque
from itertools import islice


def take(n, iterable):
    return list(islice(iterable, n))


def nth(iterable, n, default=None):
    return next(islice(iterable, n, None), default)


def tail(n, iterable):
    return list(deque(iterable, maxlen=n))          # last n items, constant memory


def consume(iterator, n=None):
    if n is None:
        deque(iterator, maxlen=0)                    # exhaust at C speed
    else:
        next(islice(iterator, n, n), None)


def first_true(iterable, default=None, predicate=None):
    return next(filter(predicate, iterable), default)
```

A sliding window of `n` items, for moving averages or n-grams:

```python
from collections import deque
from itertools import islice

def sliding_window(iterable, n):
    iterator = iter(iterable)
    window = deque(islice(iterator, n - 1), maxlen=n)
    for x in iterator:
        window.append(x)
        yield tuple(window)


list(sliding_window("ABCDE", 3))    # [('A','B','C'), ('B','C','D'), ('C','D','E')]
```

Removing duplicates while keeping order, optionally by a key:

```python
def unique_everseen(iterable, key=None):
    seen = set()
    for element in iterable:
        k = element if key is None else key(element)
        if k not in seen:
            seen.add(k)
            yield element


list(unique_everseen("ABBcCAD", key=str.casefold))   # ['A', 'B', 'c', 'D']
```

For hashable items without a key, `list(dict.fromkeys(items))` does the same in one line.

Interleaving several iterables and splitting one by a predicate:

```python
from itertools import cycle, filterfalse, islice, tee


def roundrobin(*iterables):
    iterators = map(iter, iterables)
    for num_active in range(len(iterables), 0, -1):
        iterators = cycle(islice(iterators, num_active))
        yield from map(next, iterators)


def partition(predicate, iterable):
    """Returns (items where predicate is false, items where it is true)."""
    t1, t2 = tee(iterable)
    return filterfalse(predicate, t1), filter(predicate, t2)


list(roundrobin("ABC", "D", "EF"))   # ['A', 'D', 'E', 'B', 'F', 'C']
```

## When to use more-itertools

`more-itertools` (11.x on PyPI) packages the recipes above and many more: `chunked`, `windowed`, `spy` (peek without consuming), `peekable`, `bucket`, `split_at`, `one` (exactly one item or an error), `ichunked` for lazy chunks, `distribute`. Add it when a project needs several of these; copy the recipe when it needs one. Its functions are typed and tested, which beats a hand-written variant with an off-by-one in the last chunk.
