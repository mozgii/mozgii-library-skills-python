---
name: python-performance
description: Finds and fixes slow or memory-hungry Python code - measuring with timeit and pyperf, profiling CPU with the Python 3.15 sampling profiler (profiling.sampling), cProfile, py-spy, pyinstrument and Scalene, profiling memory with tracemalloc and memray, import time with -X importtime, then the fixes that matter - better algorithms and data structures, caching, batching I/O, vectorising with NumPy or Polars, slots, processes or free-threaded Python for CPU work, and compiling hot loops with Cython, mypyc or Rust. Use when code is slow, uses too much memory, a service has high latency, start-up is slow, or before optimising anything.
metadata:
  tags:
  - python
  - performance
  - profiling
  - cprofile
  - py-spy
  - memray
  - tracemalloc
  - benchmark
  - optimization
---

# Python performance and profiling

Performance work is measurement first: profile to find where time or memory really goes, change one thing, and measure again. Guesses about hot spots are wrong more often than right. This skill gives the measuring commands and the fixes that usually pay off; tool details are in [profilers.md](references/profilers.md).

## Step 0: read the versions

Which profilers work depends on the Python build, so read it and the tools the project already has. `profiling.sampling` needs 3.15, and memray runs on Linux and macOS only.

```bash
uv run python -c "import sys; print(sys.version); print('GIL on:', getattr(sys, '_is_gil_enabled', lambda: True)())"
uv pip list 2>/dev/null | grep -iE "^(numpy|polars|pyinstrument|py-spy|scalene|memray|pyperf|pytest-benchmark) "
```

Current releases (October 2026): py-spy 0.4.2, pyinstrument 5.1.3, Scalene 2.4.0, memray 1.20.0, pyperf 2.10.0, pytest-benchmark 5.3.0.

## The loop

Every optimisation follows the same five steps, and stops when the target is met.

1. **Set a target**: "the import takes under 2 s", "p95 latency under 100 ms", "peak memory under 1 GB".
2. **Measure the baseline** on realistic input, several runs.
3. **Profile** to find the top few functions or allocations.
4. **Change one thing**, preferably the algorithm or the data structure.
5. **Measure again** with the same input and keep the change only if it helps; add a benchmark so it stays fixed.

## Measure

Measure wall time for the whole program and microbenchmarks for small pieces.

```bash
time uv run python -m my_app process data.csv                 # whole program
uv run python -m timeit -s "items = list(range(10_000))" "9_999 in items"
uv run python -m timeit -s "items = set(range(10_000))" "9_999 in items"
uvx pyperf timeit -s "import json; d = {'a': list(range(100))}" "json.dumps(d)"   # stable: many processes, outlier checks
```

- `timeit` disables garbage collection and repeats the statement; use it to compare two ways of doing one small thing.
- `pyperf` runs worker processes, calibrates loops and warns about unstable machines; use it for numbers you will publish or compare across commits (`pyperf compare_to old.json new.json`).
- Benchmark on the Python version you ship: 3.11 to 3.15 each changed the speed of common operations.

## Profile CPU

A profiler shows which functions take the time. Start with a sampling profiler: it adds little overhead and sees the real program.

| Tool | Command | Best for |
|---|---|---|
| `profiling.sampling` (3.15 stdlib, "Tachyon") | `python -m profiling.sampling run --flamegraph -o profile.html script.py` | any program on 3.15; attach with `attach PID` |
| py-spy | `py-spy record -o profile.svg -- python script.py`; `py-spy top --pid PID` | running processes on any Python version, no code changes |
| pyinstrument | `pyinstrument script.py` | readable call trees for one request or script |
| Scalene | `scalene run script.py`, then `scalene view --cli` | CPU split into Python versus native time, plus memory per line |
| cProfile / `profiling.tracing` | `python -m cProfile -s cumtime script.py` | exact call counts; slows the program, so relative numbers only |

The script `scripts/profile_top.py` wraps the deterministic profiler and prints the top functions:

```bash
python3 scripts/profile_top.py my_script.py arg1               # sorted by cumulative time
python3 scripts/profile_top.py -m my_app.cli --sort tottime --limit 30 -- --input data.csv
python3 scripts/profile_top.py --save out.prof my_script.py    # keep the data for snakeviz or pstats
```

Without `scripts/` on disk (an install that keeps only searchable text), the standard library prints the same table:

```bash
uv run python -m cProfile -s cumtime my_script.py arg1 | head -40
uv run python -m cProfile -s tottime -m my_app.cli --input data.csv | head -40
uv run python -m cProfile -o out.prof my_script.py     # keep the data for snakeviz or pstats
```

Read `tottime` (time inside the function) to find hot loops and `cumulative` (including callees) to find the expensive call path.

## Profile memory

Memory profilers show which lines allocate and what stays alive.

```python
import tracemalloc

tracemalloc.start(25)                                   # keep 25 frames per allocation
before = tracemalloc.take_snapshot()
run_workload()
after = tracemalloc.take_snapshot()
for stat in after.compare_to(before, "lineno")[:10]:    # top 10 lines by growth
    print(stat)
print("peak MiB:", tracemalloc.get_traced_memory()[1] / 2**20)
```

```bash
uvx memray run -o run.bin -m my_app process data.csv   # Linux and macOS
uvx memray flamegraph run.bin                            # HTML flame graph of allocations at peak
uvx memray run --live -m my_app                          # live terminal view
```

- `tracemalloc` is in the standard library and sees Python allocations only; memray also sees native allocations (NumPy, C extensions).
- A slowly growing process usually holds references: a module-level cache without a bound, a list of every result, closures kept by callbacks. `gc.get_referrers(obj)` shows who holds an object.

## Profile start-up

Start-up time is import time for CLIs, serverless functions and workers.

```bash
uv run python -X importtime -c "import my_app" 2> imports.log
sort -t'|' -k2 -n imports.log | tail -15                # slowest cumulative imports, microseconds
```

Fix it by importing heavy packages inside the functions that use them, or with `lazy import pandas as pd` on Python 3.15 (PEP 810), and by removing work done at import time (reading files, compiling large regexes, network calls).

## Fixes that usually matter

The biggest wins come from doing less work, not from doing the same work slightly faster.

| Symptom in the profile | Fix |
|---|---|
| `x in some_list` inside a loop | a `set` or `dict`: O(1) membership instead of O(n) |
| repeated `list.pop(0)` or `insert(0, ...)` | `collections.deque` |
| sorting to get the top k | `heapq.nlargest(k, items)` |
| searching a sorted list | `bisect` |
| the same pure computation repeated | `functools.cache` or `lru_cache(maxsize=...)` |
| building a string with `+=` in a loop | `"".join(parts)` or `io.StringIO` |
| one query or request per item (N+1) | one batched query or request; `executemany`, `IN (...)`, bulk APIs |
| a Python loop over numbers | NumPy or Polars vector operations |
| many small objects | `@dataclass(slots=True)` or tuples; arrays instead of lists of floats |
| reading a big file whole | iterate lines or chunks; `mmap` for random access |
| regex compiled in a loop | compile once at module level (Python caches only recent patterns) |
| JSON encoding is hot | `orjson` or `msgspec` |

## Use more cores

Python code in one process runs one thread at a time on the standard (GIL) build, so CPU-bound work needs processes, a free-threaded build, or native code.

- **Processes**: `concurrent.futures.ProcessPoolExecutor` for CPU-bound Python; send work in chunks, because arguments and results are pickled.
- **Free-threaded Python** (3.14t, officially supported since 3.14 per PEP 779): threads run Python in parallel. Install with `uv python install 3.14t`; check that every C extension you use ships `cp314t` wheels; single-thread speed is somewhat lower.
- **Subinterpreters** (3.14): `concurrent.futures.InterpreterPoolExecutor` runs isolated interpreters in one process; objects are not shared, so it suits independent tasks.
- **I/O-bound work** needs no extra cores: use asyncio or threads (the `python-asyncio` skill).

## Compile the hot spot

When one function dominates and the algorithm is already right, compile that function rather than the whole program.

| Tool | How | Fits |
|---|---|---|
| NumPy / Polars | rewrite as array operations | numeric and tabular data |
| Numba | `@numba.njit` on a numeric function | loops over NumPy arrays |
| mypyc | compiles type-annotated modules to C extensions; enforces annotations at runtime, see [mypyc.md](references/mypyc.md) | strictly typed pure-Python code |
| Cython | `.pyx` or annotated `.py` compiled to C | tight loops, C library bindings |
| Rust with PyO3 and maturin | a Rust function exposed as a Python module | heavy parsing, string processing, safe parallelism |

The CPython JIT (experimental since 3.13, `PYTHON_JIT=1` in builds that include it) speeds up some pure-Python loops; measure before relying on it.

## Worked example: a report that takes 27 seconds

This walkthrough follows the loop on a report that totals orders per region. The target is under one second for 200,000 orders; the first run took 27 s on an Apple silicon laptop with Python 3.15.2.

```python
# report.py
import sys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Customer:
    id: int
    region: str


@dataclass(frozen=True, slots=True)
class Order:
    customer_id: int
    cents: int


def load(count: int) -> tuple[list[Customer], list[Order]]:
    customers = [Customer(i, f"region-{i % 7}") for i in range(count // 10)]
    orders = [Order(i % (count // 10), 100 + i % 900) for i in range(count)]
    return customers, orders


def find_customer(customers: list[Customer], customer_id: int) -> Customer:
    for customer in customers:                      # O(customers) for every order
        if customer.id == customer_id:
            return customer
    raise KeyError(customer_id)


def totals_by_region(customers: list[Customer], orders: list[Order]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for order in orders:
        region = find_customer(customers, order.customer_id).region
        totals[region] = totals.get(region, 0) + order.cents
    return totals


if __name__ == "__main__":
    print(totals_by_region(*load(int(sys.argv[1])))["region-3"])
```

The profile names one function, so there is nothing to guess:

```text
$ uv run python -m cProfile -s tottime report.py 200000 | head -9
   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
   200000   22.259    0.000   22.259    0.000 report.py:23(find_customer)
        1    0.178    0.178   22.486   22.486 report.py:30(totals_by_region)
```

Change one thing, the data structure: delete `find_customer` and build a dict once, which replaces a scan per order with one lookup per order.

```python
def totals_by_region(customers: list[Customer], orders: list[Order]) -> dict[str, int]:
    region_of = {customer.id: customer.region for customer in customers}   # one pass, then O(1) per order
    totals: dict[str, int] = {}
    for order in orders:
        region = region_of[order.customer_id]
        totals[region] = totals.get(region, 0) + order.cents
    return totals
```

The same input now takes 0.09 s, and the output is unchanged (`15689830`). The target is met, so the work stops here: no compiled extension, no process pool. A missing `customer_id` still raises `KeyError`, so the failure mode stayed the same too.

## Keep it fast

A benchmark in CI catches regressions that a one-time optimisation would otherwise lose.

```bash
uv add --dev pytest-benchmark "pytest<9.2"             # 5.3.0 breaks on pytest 9.2.0
uv run pytest tests/bench --benchmark-autosave
uv run pytest tests/bench --benchmark-compare --benchmark-compare-fail=mean:10%   # fail on a 10% slowdown
```

CI machines are noisy; compare against a baseline from the same machine type, and fail only on large changes.

## Verify

Prove the change with the same input before and after, and keep a guard that fails when the slowdown returns.

```bash
time uv run python report.py 200000                                  # three runs: under the target, same printed total
uv run python -m cProfile -s tottime report.py 200000 | head -9      # the old hot spot left the top lines
uv run pytest -q                                                     # behaviour unchanged
uv run pytest tests/bench --benchmark-autosave                       # store the good baseline
uv run pytest tests/bench --benchmark-compare --benchmark-compare-fail=mean:10%; echo "exit $?"   # expect 0
```

Plant the old code once and run the last line again: it must exit 1, or the benchmark guards nothing.

## Anti-patterns

| Instead of | Do |
|---|---|
| optimising before a profile exists | measure, profile, then change the top line of the profile |
| `timeit` on a tiny loop as proof for a program | time the whole program on realistic input |
| several changes in one measurement | one change, one measurement, so each keeps or loses its place |
| compiling or adding cores while the code is O(n²) | fix the algorithm first; a dict lookup beat both here |
| a thread pool for CPU-bound Python on the GIL build | processes, NumPy, or the free-threaded build |
| an unbounded `functools.cache` in a long-lived process | `lru_cache(maxsize=...)`, and watch memory |

## Checklist

An optimisation is done when every item holds.

- [ ] A written target, and a baseline measured on realistic input.
- [ ] The profile identified the hot spot before any change.
- [ ] One change at a time, each measured; tests still pass.
- [ ] The algorithm and data structures were checked before micro-optimising.
- [ ] A benchmark or a test guards the result.

## Gotchas

These are the mistakes that waste optimisation effort.

1. **Optimising the wrong function.** No profile was taken. Profile first; the hot spot is rarely where it seems.
2. **cProfile says the program spends its time in tiny functions.** Deterministic profiling adds a fixed cost per call, which inflates many small calls. Confirm with a sampling profiler.
3. **The benchmark improved, the real program did not.** The microbenchmark ran on warm caches or tiny inputs. Measure the whole program on real data.
4. **Threads made CPU work slower.** The GIL serialises Python bytecode. Use processes, NumPy, or a free-threaded build.
5. **A process pool is slower than a loop.** Pickling arguments and results costs more than the work. Send bigger chunks, or share data through files or shared memory.
6. **py-spy or `profiling.sampling` fails with a permission error.** Both read another process's memory. On Linux, attaching needs root, `CAP_SYS_PTRACE` or `ptrace_scope` 0, and launching the program under the profiler (`run` or `record -- python ...`) avoids that. On macOS launching refuses too (`This program requires root on OSX`, `Unable to access process memory on macOS`): use `sudo`, or an in-process profiler such as pyinstrument or cProfile.
7. **`AttributeError: 'Function' object has no attribute '_nodeid'` from the benchmark fixture.** pytest-benchmark 5.3.0 does not work with pytest 9.2.0. Hold `pytest<9.2` until a newer pytest-benchmark ships; 9.1.1 passes.

## References

The reference files hold the longer recipes and the full tool detail.

- [profilers.md](references/profilers.md): every profiler's commands and output, flame graphs, attaching to live processes, memory leak hunting
- `scripts/profile_top.py`: deterministic profile of a script or module, top functions printed

## See also

- `python-concurrency`: threads, processes and free-threaded Python in depth
- `python-functools-caching`: bounded caches and when they pay off
- `python-native-extensions`: Cython, Rust and building wheels for the compiled path
- `numpy` and `polars`: array and table operations instead of Python loops
