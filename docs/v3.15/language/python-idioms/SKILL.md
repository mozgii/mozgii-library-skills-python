---
name: python-idioms
description: Writes and reviews idiomatic modern Python (3.11 to 3.15) and catches the classic traps - mutable default arguments, late-binding closures in loops, is versus ==, changing a list while iterating it, shadowed builtins, float equality, quadratic string building, import-time side effects - plus the modern replacements to prefer (pathlib, f-strings, zip strict, itertools.batched and pairwise, dict merge, match, walrus, copy.replace, Path.copy, lazy import), iteration with generators and itertools, files read and written safely, and a review checklist. Use when writing new Python, reviewing or refactoring Python code, asking what the Pythonic way is, or when code behaves strangely because of a language gotcha.
metadata:
  tags:
  - python
  - idioms
  - pythonic
  - code-review
  - anti-patterns
  - refactoring
  - best-practices
  - gotchas
---

# Idiomatic modern Python

Idiomatic Python is code that uses the language's own tools for a job, so a reader recognises the pattern at once and the obvious version is also the correct one. This skill lists the traps that make Python code wrong, the modern replacements for old patterns, and a review checklist. Each rule says what to write, what to avoid, and why.

## Step 0: read the minimum Python

Which idioms are available depends on the oldest Python the code supports, so read it before suggesting newer syntax.

```bash
grep -n "requires-python" pyproject.toml
uv run python -c "import sys; print(sys.version_info[:2])"
```

Ruff applies many of the rules below automatically from that minimum (`UP`, `SIM`, `C4`, `PERF`, `FURB`, `B`, `PTH`): run `uv run ruff check --fix .` before reviewing by hand.

## Traps that make code wrong

These language behaviours produce bugs that look correct on reading, so check for them first in every review.

| Trap | Wrong | Right | Why |
|---|---|---|---|
| mutable default argument | `def add(item, items=[])` | `def add(item, items: list[str] \| None = None)`, then `items = [] if items is None else items` | the default list is created once and shared by every call |
| late-binding closure in a loop | `[lambda: i for i in range(3)]` returns `2, 2, 2` | `[lambda i=i: i for i in range(3)]` or `functools.partial` | the closure reads `i` when called, after the loop ended |
| `is` with values | `if count is 0`, `if name is "x"` | `==`; keep `is` for `None`, `True`, `False` and sentinels | small ints and some strings are cached, so `is` works by accident and then fails |
| changing a collection while iterating | `for x in items: items.remove(x)` | `items = [x for x in items if keep(x)]`, or iterate over `list(d)` to delete dict keys | the iterator skips elements; a dict raises `RuntimeError` |
| shadowed builtins | `list = load()`, `id = row[0]`, `type = "a"` | `rows`, `row_id`, `kind` | later calls to `list(...)` fail far from the cause; Ruff `A` rules |
| float equality | `0.1 + 0.2 == 0.3` is `False` | `math.isclose(a, b)`, or `decimal.Decimal` for money | binary floats cannot hold most decimals exactly |
| `x == None`, `if x:` for "set" | `if timeout:` treats `0` as missing | `if timeout is not None:` | `0`, `""` and `[]` are falsy but valid values |
| string building in a loop | `s += part` inside a loop | `"".join(parts)` | each `+=` may copy the whole string: quadratic time |
| side effects at import | connecting to a database or reading files at module level | do it in a function called from `main()` | imports run in tests, tools and spawn-based worker processes |
| `except Exception: pass` | silent failures | catch the specific error and handle it | the real bug disappears |
| class attribute as instance state | `class C: items = []` mutated as `self.items.append` | assign in `__init__` or use a dataclass `field(default_factory=list)` | every instance shares the class's list |
| tuple of one | `("a")` is a string | `("a",)` | the comma makes the tuple, not the parentheses |

## Modern replacements

Newer Python versions added a direct tool for many hand-written patterns; use them once the minimum version allows.

| Instead of | Write | Since |
|---|---|---|
| `os.path.join`, `open(os.path...)`, `glob.glob` | `pathlib.Path`: `root / "data" / name`, `p.read_text(encoding="utf-8")`, `p.glob("*.csv")` | 3.4 |
| `"%s" % x`, `"{}".format(x)` | f-strings: `f"{name!r} took {secs:.2f}s"`, `f"{value=}"` for debugging | 3.6 |
| `{**a, **b}` | `a \| b`, `a \|= b` | 3.9 |
| `s[len(prefix):] if s.startswith(prefix) else s` | `s.removeprefix(prefix)`, `s.removesuffix(suffix)` | 3.9 |
| `zip(a, b)` when lengths must match | `zip(a, b, strict=True)` | 3.10 |
| `if/elif` chains over shapes of data | `match` with class and mapping patterns | 3.10 |
| `[items[i:i + n] for i in range(0, len(items), n)]` | `itertools.batched(items, n)` (`strict=True` since 3.13 to refuse a short last batch) | 3.12 |
| `zip(xs, xs[1:])` | `itertools.pairwise(xs)` | 3.10 |
| `sum(a * b for a, b in zip(x, y))` | `math.sumprod(x, y)` | 3.12 |
| `dataclasses.replace(obj, ...)`, `nt._replace(...)` | `copy.replace(obj, field=value)` for dataclasses, named tuples and more | 3.13 |
| `shutil.copy2(src, dst)`, `shutil.move` with paths | `src.copy(dst, preserve_metadata=True)`, `src.move(dst)` on `Path`; a plain `src.copy(dst)` drops the mtime and turns mode 0o640 into 0o644 | 3.14 |
| `old = os.getcwd(); os.chdir(d); ...; os.chdir(old)` | `with contextlib.chdir(d):` | 3.11 |
| `import heavy` inside every function to defer it | `lazy import heavy` at the top of the module | 3.15 |
| `itertools.chain.from_iterable(rows)` in a comprehension | `[*row for row in rows]` | 3.15 |
| a module-level `_MISSING = object()` | `MISSING = sentinel("MISSING")`, usable in type hints | 3.15 |

## Iteration

Python loops read best when they iterate over the values directly and let the standard library handle counting, pairing and grouping.

```python
from collections import Counter, defaultdict
from itertools import batched, groupby
from operator import itemgetter

for i, line in enumerate(lines, start=1):                  # not range(len(lines))
    ...

for name, score in zip(names, scores, strict=True):        # parallel lists, same length enforced
    ...

counts = Counter(word.lower() for word in words)           # counting
counts.most_common(3)

by_owner: defaultdict[str, list[Order]] = defaultdict(list)
for order in orders:
    by_owner[order.owner].append(order)                    # grouping without "if key not in d"

for chunk in batched(rows, 500):                           # bulk insert 500 at a time
    insert_many(chunk)

rows.sort(key=itemgetter("country", "city"))               # sort, then groupby needs sorted input
for country, group in groupby(rows, key=itemgetter("country")):
    ...
```

- `any(...)` and `all(...)` with a generator stop at the first decisive item; `next((x for x in xs if pred(x)), None)` finds the first match.
- `dict.get(key, default)` reads; `dict.setdefault(key, [])` inserts and returns; `defaultdict` when every key needs a default.
- `sorted(xs, key=...)` returns a new list; `xs.sort(key=...)` sorts in place and returns `None`.
- `reversed(xs)` and `enumerate` are lazy; wrap in `list()` only when you need a list.

## Comprehensions and generators

A comprehension builds a collection from one expression and at most two loops or conditions; past that, a plain loop reads better.

```python
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class User:
    id: int
    email: str
    active: bool


users = [User(1, "ann@example.com", True), User(2, "bob@example.org", False)]
active_emails = [u.email for u in users if u.active]              # list
by_id = {u.id: u for u in users}                                  # dict
domains = {email.split("@", 1)[1] for email in active_emails}     # set
total = sum(u.id for u in users)                                  # generator: no list in memory


def read_lines(path: Path) -> Iterator[str]:                      # a generator function streams a big file
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield line.rstrip("\n")
```

- Use a generator when the consumer iterates once (sums, writes, `any`); use a list when it needs `len`, indexing or several passes.
- A generator can only be consumed once; a second loop over it sees nothing.
- Never use a comprehension for side effects (`[print(x) for x in xs]`); write a loop.
- The walrus operator avoids computing twice: `if (m := pattern.search(line)) is not None: use(m.group(1))`, and `[y for x in xs if (y := f(x)) > 0]`.

## Files and paths

Files are opened with a context manager and an explicit encoding, and replaced atomically when another process might read them.

```python
import json
import os
import tempfile
from pathlib import Path


def write_json_atomic(path: Path, data: object) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())                                  # on disk before the rename
        os.replace(tmp, path)                                     # atomic on POSIX and Windows
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
```

- Always pass `encoding="utf-8"` to text I/O; before 3.15 the default was the locale's encoding, so the same code read files differently on Windows.
- Iterate a file object line by line for large files; `read_text()` loads the whole file.
- `Path.exists()` then `open()` is a race; open and catch `FileNotFoundError`.
- `tomllib` reads TOML, `json` reads JSON, `csv` needs `newline=""` when opening the file.

## Functions and signatures

A function signature is an API: make the call sites readable and hard to get wrong.

```python
def resize(image: bytes, /, width: int, height: int, *, keep_aspect: bool = True, quality: int = 85) -> bytes:
    ...

resize(b"...", 800, 600, quality=70)       # image is positional-only; options are keyword-only
```

- Put `*` before boolean flags and options, so `resize(img, 800, 600, True)` cannot happen.
- Return one type; a function that returns a list or `None` or `False` forces every caller to check three things.
- Prefer a few explicit parameters to `**kwargs` passed through several layers; type them with `Unpack[TypedDict]` when pass-through is needed.
- Keep functions short enough to name; a name with "and" in it is two functions.

## Classes only when they earn it

A class is worth it when it holds state that several operations share, or when it is a data record; a class with one method and an `__init__` is a function.

- Data records: `@dataclass(frozen=True, slots=True)` instead of a hand-written `__init__`, `__repr__` and `__eq__`.
- Behaviour without state: module-level functions. A module is already a namespace; a class of `@staticmethod`s is not needed.
- `@property` for computed attributes that are cheap; a method (`load_items()`) when the work is slow or has side effects.
- Implement `__repr__` for every class you debug; dataclasses do it for you.
- Prefer composition (an object holds another) over inheritance for reuse; inherit to express "is a kind of".

## Module hygiene

A module is easiest to read and import when it has no side effects at import and a clear public surface.

```python
"""Price parsing and currency conversion."""

from __future__ import annotations

import logging
from decimal import Decimal

__all__ = ["convert", "parse_price"]

log = logging.getLogger(__name__)


def parse_price(raw: str) -> Decimal: ...


def convert(amount: Decimal, rate: Decimal) -> Decimal: ...


def _round_cents(value: Decimal) -> Decimal: ...   # leading underscore: private to this module


if __name__ == "__main__":
    print(parse_price("$3.50"))
```

- Never `from module import *` outside an `__init__.py` re-export; it hides where names come from.
- Constants in `UPPER_CASE` at module level; anything computed expensively goes in a function with `functools.cache`.

## Worked example: from a working script to idiomatic code

This walkthrough takes code that passes a quick test and fixes each idiom it breaks. It adds up amounts per owner from a CSV file.

```python
def summarise(path, totals={}):
    f = open(path)
    for line in f.readlines():
        parts = line.strip().split(",")
        if len(parts) == 2:
            if parts[0] not in totals:
                totals[parts[0]] = 0
            totals[parts[0]] += float(parts[1])
    return totals
```

It has six problems: the mutable default keeps totals between calls, the file never closes, no encoding is set, `readlines` loads the whole file, `float` loses cents, and a bad row disappears without a word. The rewrite separates reading from adding up, so each part has one job.

```python
import csv
from collections import defaultdict
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path


def read_amounts(path: Path) -> Iterator[tuple[str, Decimal]]:
    with path.open(newline="", encoding="utf-8") as f:
        for line_no, row in enumerate(csv.reader(f), start=1):
            if not row:                                       # a blank line holds nothing
                continue
            try:
                owner, amount = row
                yield owner, Decimal(amount)
            except (ValueError, InvalidOperation) as exc:
                raise ValueError(f"{path}:{line_no}: expected owner,amount, got {row!r}") from exc


def totals_by_owner(path: Path) -> dict[str, Decimal]:
    totals: defaultdict[str, Decimal] = defaultdict(Decimal)
    for owner, amount in read_amounts(path):
        totals[owner] += amount
    return dict(totals)
```

For `ann,1.10`, `bob,2` and `ann,0.20`, `totals_by_owner` returns `{'ann': Decimal('1.30'), 'bob': Decimal('2')}`. An empty file returns `{}`, and `ann,oops` raises `ValueError: data.csv:1: expected owner,amount, got ['ann', 'oops']`.

## Verify

Prove the review with Ruff, which catches most of these traps by rule, and an import that does no work.

```bash
uv run ruff check --select A,B,C4,E711,E712,F632,FURB,PERF,PTH,SIM,UP --ignore PTH105 .    # B006 mutable default, B023 late binding
uv run ruff check --select UP --diff .                 # what the minimum Python lets you modernise
uv run python -X dev -W error -c "import my_app"       # expect no output: imports do no work and raise no warning
grep -nE "^(connect|client|session|engine) *= " -r src # expect nothing at module level
```

## Anti-patterns

The traps above make code wrong; these habits make it hard to read while it still works.

| Instead of | Do |
|---|---|
| a clever one-liner past two conditions | a plain loop with a named result |
| `range(len(items))` | `enumerate(items)` |
| a class with one method and `__init__` | a function |
| `**kwargs` passed down several layers | explicit parameters, or `Unpack[TypedDict]` |
| a boolean flag as a positional argument | a keyword-only option |
| new syntax above the minimum Python | what `requires-python` allows |

## Pre-merge checklist

Go through these points when reviewing Python code, after Ruff has run.

- [ ] No mutable defaults, late-binding closures, `is` on values or mutation during iteration.
- [ ] Every `open()` and text I/O has `encoding=`; files are closed by `with`; replacements are atomic.
- [ ] `None` checks use `is None`, not truthiness, wherever `0` or `""` are valid.
- [ ] Loops use `enumerate`, `zip(strict=True)`, `itertools`, `Counter` and `defaultdict` rather than index juggling.
- [ ] Options are keyword-only; functions return one type.
- [ ] Classes hold shared state or data; stateless helpers are functions.
- [ ] Modules have no import-time side effects; `__all__` lists the public names of library modules.
- [ ] New syntax matches the minimum Python (`match` 3.10, `type` aliases 3.12, `lazy import` 3.15).

## Gotchas

These are the Python behaviours people ask about after a confusing bug.

1. **"My function remembers values from the previous call."** A mutable default argument. Use `None` and create the list inside.
2. **"All my callbacks use the last loop value."** Late binding in closures. Bind the value as a default argument or use `functools.partial`.
3. **"`a = b` then changing `a` changed `b`."** Assignment never copies; both names point at one list. Copy with `list(b)`, `b.copy()` or `copy.deepcopy(b)` for nested data.
4. **"`[[0] * 3] * 3` changes every row."** The outer `*` repeats a reference to one inner list. Use `[[0] * 3 for _ in range(3)]`.
5. **"`UnboundLocalError: cannot access local variable`."** An assignment anywhere in a function makes that name local for the whole function, so reading it earlier fails. Pass it in, or use `nonlocal`/`global` on purpose.
6. **"`round(2.5)` gives 2."** Python rounds half to even. Use `Decimal.quantize(..., rounding=ROUND_HALF_UP)` when a rule says otherwise.
7. **"Sorting strings puts 'Z' before 'a' and '10' before '9'."** Sorting compares code points. Use `key=str.casefold`, or a natural-sort key that converts digit runs to `int`.
8. **"The generator is empty the second time."** Generators are consumed once. Store a list if you need two passes.

## See also

- `ruff`: the rules that apply this skill automatically
- `python-code-design`: modules, layers and public APIs
- `python-typing`: annotations for the signatures shown here
- `python-iterators-generators`: laziness and `itertools` in depth
- `python-files-pathlib`: file handling beyond the basics
