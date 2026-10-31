---
title: Python profilers in practice
author: MOZGII, Inc.
lang: en
---

# Python profilers in practice

Commands, outputs and limits of the CPU and memory profilers worth knowing in October 2026: the standard library's `profiling` package (Python 3.15), cProfile, py-spy 0.4, pyinstrument 5.1, Scalene 2.4, tracemalloc and memray 1.20.

## Contents

This reference has 9 sections.

1. The Python 3.15 sampling profiler (profiling.sampling)
2. cProfile and profiling.tracing with pstats
3. py-spy for live processes
4. pyinstrument call trees
5. Scalene: CPU, native time and memory per line
6. tracemalloc leak hunting
7. memray native memory
8. Reading a flame graph
9. Permissions for attaching

## 1. The Python 3.15 sampling profiler (profiling.sampling)

`profiling.sampling` (PEP 799, nicknamed Tachyon) is a statistical profiler in the standard library from Python 3.15. It reads the target's stacks from outside the process, so overhead is near zero and it can attach to a program that is already running.

```bash
python -m profiling.sampling run script.py arg1 arg2               # profile a script
python -m profiling.sampling run -m my_app.cli serve               # profile a module
python -m profiling.sampling run --flamegraph -o profile.html script.py
python -m profiling.sampling run -r 20khz -a script.py             # 20 kHz sampling, all threads
python -m profiling.sampling run --mode=cpu script.py              # wall (default), cpu, gil, exception
python -m profiling.sampling run --live script.py                  # live terminal view
python -m profiling.sampling attach 12345                          # a running process
python -m profiling.sampling attach --live 12345
python -m profiling.sampling dump 12345                            # one stack snapshot, like a traceback
```

- Output formats: `--pstats` (default table), `--collapsed` (for flamegraph tools), `--flamegraph` (HTML), `--gecko` (Firefox Profiler), `--heatmap`.
- `--mode=wall` counts time spent waiting too; `--mode=cpu` counts only time on the CPU; `--mode=gil` shows which threads hold the GIL; `--async-aware` reconstructs asyncio task stacks.
- The plain `profile` module is deprecated (removal in 3.17); `cProfile` remains as an alias of `profiling.tracing`.

## 2. cProfile and profiling.tracing with pstats

The deterministic profiler records every call and return, so it gives exact call counts but slows the program and inflates small functions.

```bash
python -m cProfile -s cumtime script.py | head -30      # sort by cumulative time
python -m cProfile -o out.prof script.py                # save, then explore
python -m pstats out.prof                               # interactive: sort tottime, stats 20, callers fn
uvx snakeviz out.prof                                   # icicle chart in the browser
```

```python
import cProfile
import pstats

with cProfile.Profile() as prof:                       # profile one block only
    handle_request(sample)
pstats.Stats(prof).strip_dirs().sort_stats("tottime").print_stats(15)
```

Columns: `ncalls` (calls; `3/1` means recursive), `tottime` (inside the function), `percall`, `cumtime` (with callees).

## 3. py-spy for live processes

py-spy is a sampling profiler written in Rust that reads another process's memory; it needs no code changes and works on production processes.

```bash
py-spy record -o profile.svg -- python script.py        # flame graph SVG
py-spy record -o profile.json --format speedscope --pid 12345 --duration 30
py-spy top --pid 12345                                  # live "top" of functions
py-spy dump --pid 12345                                 # current stack of every thread: why is it stuck?
py-spy record --native -o p.svg -- python script.py     # include C and Rust frames
py-spy record --subprocesses -o p.svg -- python -m my_app   # follow worker processes
```

Install with `uv tool install py-spy` or `uvx py-spy`. New Python minor versions need a py-spy release that knows their internals; check its changelog before relying on it for 3.15.

## 4. pyinstrument call trees

pyinstrument samples the stack and prints a call tree that hides library internals, which makes one request or one script easy to read.

```bash
uvx pyinstrument script.py
uvx pyinstrument -r html -o profile.html script.py
```

```python
from pyinstrument import Profiler

profiler = Profiler(async_mode="enabled")              # follows await chains in asyncio code
profiler.start()
await handle_request(sample)
profiler.stop()
print(profiler.output_text(unicode=True, color=False, show_all=False))
```

## 5. Scalene: CPU, native time and memory per line

Scalene profiles CPU, memory and (optionally) GPU per line, and splits CPU time into Python and native code, which shows whether vectorising helped.

```bash
uvx scalene run script.py                              # writes scalene-profile.json
uvx scalene view                                       # opens that profile as a web report
uvx scalene view --cli --reduced                       # terminal, only lines with activity
uvx scalene run --cpu-only -o cpu.json script.py       # faster, no memory or GPU data
uvx scalene run --memory --profile-only my_app script.py --- --input data.csv   # after ---: the program's arguments
```

Scalene 2 split the command into `run` and `view`, so the old `scalene script.py` form no longer works. A program that ends in a moment gives "did not run for long enough to profile"; give it more work.

## 6. tracemalloc leak hunting

tracemalloc traces Python memory allocations; comparing two snapshots shows what grew.

```python
import gc
import tracemalloc

tracemalloc.start(25)
for _ in range(3):
    run_workload()                     # warm caches first
gc.collect()
baseline = tracemalloc.take_snapshot()
for _ in range(50):
    run_workload()
gc.collect()
current = tracemalloc.take_snapshot()

for stat in current.compare_to(baseline, "traceback")[:5]:
    print(f"{stat.size_diff / 1024:.1f} KiB in {stat.count_diff} blocks")
    for line in stat.traceback.format()[-6:]:
        print("   ", line)
```

Growth that scales with the number of iterations is a leak; a one-time increase is a cache filling up. `python -X tracemalloc=25 script.py` starts tracing from the first line.

## 7. memray native memory

memray tracks every allocation, including those from C extensions, and reports peak usage and leaks. It runs on Linux and macOS.

```bash
uvx memray run -o run.bin script.py
uvx memray flamegraph run.bin            # memory at the peak, by call stack
uvx memray flamegraph --leaks run.bin    # memory never freed (run with PYTHONMALLOC=malloc for accuracy)
uvx memray summary run.bin
uvx memray stats run.bin
uvx memray run --live script.py          # live terminal UI
```

pytest integration: `pytest-memray` with `@pytest.mark.limit_memory("100 MB")` fails a test that allocates more.

## 8. Reading a flame graph

A flame graph stacks call frames vertically and sizes each box by the share of samples in which it appears.

- The width of a box is time (or memory); the vertical position is call depth. Order left to right is alphabetical, not chronological.
- Look for wide boxes near the top: those functions do the work themselves. A wide box with many narrow children spends its time in its callees.
- Compare two flame graphs (before and after) at the same sampling rate and input.

## 9. Permissions for attaching

Profilers that attach to another process need debugger rights on that process.

| OS | Needs |
|---|---|
| Linux | root, `CAP_SYS_PTRACE`, or `/proc/sys/kernel/yama/ptrace_scope` set to 0 (`echo 0 \| sudo tee /proc/sys/kernel/yama/ptrace_scope`); in Docker, `--cap-add SYS_PTRACE` |
| macOS | root (`sudo`); system Python binaries protected by SIP cannot be attached at all |
| Windows | an administrator prompt for processes of other users |

Launching the program under the profiler (`run`, `record -- python ...`) still reads another process's memory, so the answer depends on the OS.

- Linux: the default `ptrace_scope` of 1 lets a parent read its child, so launching needs no extra rights. Not run here; py-spy's README and the `profiling.sampling` docs state it.
- macOS: root is needed for launching too. Without it, on macOS 27 with py-spy 0.4.2, `py-spy record -- python app.py` stops with `This program requires root on OSX`; `python -m profiling.sampling run app.py` on Python 3.15.2 stops with `Permission Error: Unable to access process memory on macOS`. Use `sudo`, or an in-process profiler (pyinstrument, cProfile), which needs no rights.
