---
name: python-debugging
description: Debugs Python programs systematically - reading tracebacks with chained exceptions and exception groups, reproducing and shrinking a failure, git bisect, breakpoint() and pdb commands, post-mortem debugging, pytest --pdb, attaching pdb to a running process with python -m pdb -p (3.14), faulthandler and py-spy dump for hangs and crashes, asyncio task dumps, development mode and warnings as errors, debugpy for VS Code and remote containers, and the usual causes of ModuleNotFoundError, wrong interpreter, encoding and environment differences. Use when a Python program crashes, hangs, gives wrong results, behaves differently in another environment, or when a test fails for an unclear reason.
metadata:
  tags:
  - python
  - debugging
  - pdb
  - traceback
  - faulthandler
  - debugpy
  - py-spy
  - breakpoint
---

# Debugging Python programs

Debugging is a loop: reproduce the failure, shrink it, form one hypothesis, test it with the debugger or a measurement, fix, and add a test that fails without the fix. This skill gives the tools for each step, from reading the traceback to attaching a debugger to a process that is already stuck.

## Step 0: read the interpreter and what changed

Half of all debugging time goes on the wrong interpreter or the wrong code, so confirm both before touching anything. The version also decides which tools below exist.

```bash
uv run python -c "import sys; print(sys.version); print(sys.executable)"
grep -n "requires-python" pyproject.toml
git status --short; git log --oneline -5
uv pip list 2>/dev/null | grep -iE "^(debugpy|py-spy|ipdb|pytest) "
```

| Version | Tool that arrived |
|---|---|
| 3.11 | fine-grained `~^^^` error locations; `add_note` |
| 3.13 | `exceptions` command in post-mortem pdb |
| 3.14 | `python -m pdb -p`, `python -m asyncio pstree`, `sys.remote_exec`, C stacks in faulthandler |
| 3.15 | `python -m profiling.sampling dump`; pdb's new shell |

## Choose a tool by what you can see

The symptom picks the tool; a debugger is not always the fastest route.

| You see | Start with | Why the others lose here |
|---|---|---|
| a traceback | read it bottom-up, then `pytest --pdb` | a breakpoint before reading guesses at what the traceback already says |
| wrong output, no error | one failing test, then `breakpoint()` at the last good value | prints in many places cost more than one stop with the whole frame |
| a process that is stuck now | `py-spy dump`, or `pdb -p` on 3.14+ | restarting destroys the only evidence |
| a crash with no traceback | `-X faulthandler`, then `lldb` or `gdb` | pdb never gets control in native code |
| failure only in a container or on a server | `debugpy` over a forwarded port, or logs | a local rerun may not reproduce the environment |
| it worked last week | `git bisect run` | reading the diff by hand is slower once it spans many commits |
| timing bugs between threads or tasks | logs with task names and timestamps | a debugger changes the timing and hides the race |

## The loop

Work through these steps in order; skipping to "try a fix" is the slowest path.

1. **Read the whole error**: the last line names the exception, the frames above show the path; chained exceptions show the original cause.
2. **Reproduce** it with one command, ideally a failing test.
3. **Shrink** the input and the code until the failure is small enough to see whole.
4. **Hypothesise** one cause and check it: a breakpoint, a print, an assertion.
5. **Fix** the cause, not the symptom, and keep the failing test as a regression test.

## Read the traceback

A traceback lists frames from the outermost call (top) to the line that raised (bottom); read it bottom-up.

```text
Traceback (most recent call last):
  File "app.py", line 8, in main
    f(data)
  File "app.py", line 2, in f
    x = d["missing"]
        ~^^^^^^^^^^^
KeyError: 'missing'

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "app.py", line 10, in main
    raise RuntimeError("config incomplete") from e
RuntimeError: config incomplete
```

- "The above exception was the direct cause" comes from `raise ... from e`; "During handling of the above exception, another exception occurred" means a second error happened inside an `except` block, often a bug in the handler itself.
- The `~^^^` markers point at the exact sub-expression that failed.
- An `ExceptionGroup` (from `TaskGroup` or `except*`) prints a tree with one branch per sub-exception.
- Notes added with `exc.add_note("while loading user 17")` print under the exception; add them where context is known.
- Python 3.15 suggests nested attributes and common methods from other languages (`Did you mean '.append'` for `.push`).

## Reproduce and shrink

A reliable one-command reproduction turns a debugging session from guessing into checking.

- Write it as a test: `uv run pytest tests/test_bug.py -x` that fails now.
- Shrink the input: halve the data until it stops failing, then step back. For generated inputs, Hypothesis shrinks automatically (the `pytest-testing` skill).
- Pin the environment: same Python, same lock (`uv sync --locked`), same environment variables.
- When it worked before: `git bisect start; git bisect bad; git bisect good v1.4.0; git bisect run uv run pytest tests/test_bug.py -x` finds the commit that broke it.

## Breakpoints and pdb

`breakpoint()` stops the program at that line in the debugger (pdb by default).

```python
from decimal import Decimal

from my_app.orders import Order, lookup_rate


def apply_discount(order: Order) -> Decimal:
    rate = lookup_rate(order.customer)
    breakpoint()                          # stops here; remove before committing
    return order.total * (1 - rate)
```

| Command | Does |
|---|---|
| `l`, `ll` | list code around the line; the whole current function |
| `n`, `s`, `r`, `c` | next line; step into a call; run to return; continue |
| `p expr`, `pp expr` | print a value; pretty-print it |
| `w`, `u`, `d` | where (the stack); up and down a frame |
| `b file.py:42`, `b func, cond` | set a breakpoint, optionally with a condition |
| `display expr` | print `expr` whenever it changes |
| `interact` | a Python shell with the current frame's variables |
| `exceptions` | (3.13+, post-mortem) list chained exceptions and jump between them |
| `q` | quit |

- `PYTHONBREAKPOINT=0` disables every `breakpoint()`; `PYTHONBREAKPOINT=ipdb.set_trace` uses another debugger.
- A Ruff rule (`T100`, in the default set) flags leftover `breakpoint()` and `pdb.set_trace()` calls.
- Since Python 3.15 pdb uses the new interactive shell with colour and completion by default.

## Post-mortem debugging

Post-mortem debugging opens the debugger at the frame that raised, after the crash, with every local variable intact.

```bash
uv run python -m pdb -c continue my_script.py arg1   # runs normally; on an uncaught exception, stops in pdb
uv run pytest --pdb                                  # debugger at the first failing test's exception
uv run pytest --trace tests/test_bug.py::test_x      # debugger at the start of the test
```

In an interactive session, `import pdb; pdb.pm()` inspects the last uncaught exception.

## Attach to a running process

A program that is stuck or misbehaving right now can be inspected without restarting it.

```bash
python -m pdb -p 12345                     # 3.14+: attach pdb to a running Python process (PEP 768)
py-spy dump --pid 12345                    # every thread's current stack, any Python version
python -m asyncio pstree 12345             # 3.14+: asyncio tasks and what each awaits
python -m profiling.sampling dump 12345    # 3.15+: one stack snapshot from the standard library
```

- Attaching needs debugger rights over the process: the same user plus `ptrace_scope` 0 or `CAP_SYS_PTRACE` on Linux, `sudo` on macOS.
- `sys.remote_exec(pid, "script.py")` (3.14) runs a script inside another Python process at its next safe point; it is how `pdb -p` works.
- Production processes that must not be attached disable it with `PYTHON_DISABLE_REMOTE_DEBUG=1` or `-X disable_remote_debug`.

## Hangs and hard crashes: faulthandler

`faulthandler` prints the Python stack of every thread on a segfault, abort or timeout, where a normal traceback never appears.

```bash
uv run python -X faulthandler my_script.py            # stacks on SIGSEGV, SIGFPE, SIGABRT, SIGBUS, SIGILL
```

```python
import faulthandler
import signal
import sys

faulthandler.enable()
faulthandler.register(signal.SIGUSR1, all_threads=True)    # POSIX: `kill -USR1 <pid>` prints all stacks
faulthandler.dump_traceback_later(600, exit=True)          # a watchdog: dump and exit if still running after 10 min
```

pytest has the same watchdog: `faulthandler_timeout = 300` in `[tool.pytest]` dumps stacks of a test that runs longer than 300 seconds.

## Development mode and warnings

Development mode turns on checks that are too costly for production and surfaces problems that otherwise stay silent.

```bash
uv run python -X dev my_script.py              # ResourceWarning for unclosed files, asyncio debug, debug memory hooks
uv run python -W error::DeprecationWarning my_script.py   # deprecations become exceptions with a traceback
uv run python -X tracemalloc=25 -X dev my_script.py       # ResourceWarnings show where the object was allocated
```

## Print debugging that helps

A well-placed print or log line is often the fastest check of a hypothesis.

```python
import sys
from decimal import Decimal
from typing import reveal_type

total, rate, text = Decimal("10"), 0.15, "a\tb "
print(f"{total=} {rate=}")                    # self-documenting f-string: total=Decimal('10') rate=0.15
print(repr(text), file=sys.stderr)            # repr shows hidden whitespace and types
reveal_type(rate)                             # type checkers show the static type; at runtime it prints the runtime type
```

Log instead of print in code that stays, with a logger at `DEBUG` (the `python-logging` skill).

## VS Code and remote debugging with debugpy

debugpy (1.8) is the debugger behind VS Code's Python extension; it also attaches to processes in containers or on servers.

```json
// .vscode/launch.json
{
  "version": "0.2.0",
  "configurations": [
    { "name": "my-app", "type": "debugpy", "request": "launch", "module": "my_app", "args": ["process", "data.csv"], "justMyCode": false },
    { "name": "attach", "type": "debugpy", "request": "attach", "connect": { "host": "127.0.0.1", "port": 5678 },
      "pathMappings": [{ "localRoot": "${workspaceFolder}", "remoteRoot": "/app" }] }
  ]
}
```

```bash
uv run python -m debugpy --listen 127.0.0.1:5678 --wait-for-client -m my_app   # local or over SSH; then start "attach" in VS Code
uv run python -m debugpy --listen 0.0.0.0:5678 --wait-for-client -m my_app     # inside a container only
```

On a machine, bind to `127.0.0.1` and forward the port over SSH. A container has its own loopback, so a listener on `127.0.0.1` inside it cannot be reached through `-p`: listen on `0.0.0.0` inside and keep the host side closed with `docker run -p 127.0.0.1:5678:5678`. A debug port open to a network gives anyone code execution.

## Worked example: from a failing test to the cause

This walkthrough follows the loop on a cart that remembers items from earlier calls. The bug is small, so each step shows what the tool gives you rather than the size of the search.

```python
# shop.py
def add_item(item: str, cart: list[str] = []) -> list[str]:
    cart.append(item)
    return cart
```

```python
# test_shop.py
from shop import add_item


def test_a_new_cart_holds_one_item() -> None:
    add_item("a")
    assert add_item("b") == ["b"]
```

Run it so the debugger opens at the failure, then ask the frame instead of guessing:

```text
$ uv run pytest test_shop.py -x --pdb
E       AssertionError: assert ['a', 'b'] == ['b']
(Pdb) p add_item.__defaults__
(['a', 'b'],)
(Pdb) q
```

`__defaults__` holds a list that is already filled, so the default is shared between calls: the cause is named, and the test exists. Fix the cause, then run the test with and without the fix (see Verify).

```python
def add_item(item: str, cart: list[str] | None = None) -> list[str]:
    cart = [] if cart is None else cart
    cart.append(item)
    return cart
```

## Common causes by symptom

Many bugs repeat; check these first.

| Symptom | Usual cause | Check |
|---|---|---|
| `ModuleNotFoundError` for an installed package | a different interpreter or venv runs the code | `python -c "import sys; print(sys.executable); print(sys.path)"`; run through `uv run` |
| `ImportError: cannot import name ... (most likely due to a circular import)` | two modules import each other at top level | move the import into the function, or the shared code into a third module |
| `AttributeError: 'NoneType' object has no attribute` | a function returned `None` on some path | type-check with Pyright strict; find the path that returns nothing |
| works in the terminal, fails in cron, CI or a service | different `PATH`, cwd, user, env vars or locale | log `os.getcwd()`, `os.environ` keys, `sys.executable` at start |
| `UnicodeDecodeError` reading a file | no `encoding=` and a non-UTF-8 locale (Windows before 3.15) | always pass `encoding="utf-8"` |
| `RecursionError` | unbounded recursion, often `__getattr__` or `__repr__` calling itself | read the repeating frames; make it iterative |
| a number is slightly wrong | float arithmetic | `decimal.Decimal` for money, `math.isclose` for comparisons |
| a default argument remembers old calls | mutable default (`def f(items=[])`) | use `None` and create inside |
| a test passes alone, fails in the suite | shared state between tests | run with random order; isolate with fixtures |

## Verify

Prove a fix by showing the test fails without it and passes with it, then that nothing was left behind.

```bash
uv run pytest tests/test_bug.py -x; echo "exit $?"          # expect exit 0 with the fix
git stash push -- src                                       # take the fix away
uv run pytest tests/test_bug.py -x; echo "exit $?"          # expect exit 1: the test catches the bug
git stash pop
uv run ruff check --select T100,T20,BLE,S110 .              # expect no leftover breakpoint, print or swallowed error
uv run python -X faulthandler -c "import ctypes; ctypes.string_at(0)"; echo "exit $?"   # expect "Fatal Python error" and exit 139 on macOS and Linux
```

The last line proves that faulthandler reaches this interpreter: on a real crash you then know the empty output means something else.

## Anti-patterns

| Instead of | Do |
|---|---|
| changing code until the error goes away | name the cause first, with a measurement |
| several changes at once | one hypothesis, one change, one run |
| `except Exception: pass` to get past a crash | let it raise and read it |
| a fix with no test | a test that fails without the fix |
| restarting a stuck process right away | `py-spy dump` or `pdb -p` first |
| a debug port on `0.0.0.0` | bind to `127.0.0.1` and forward it |

## Pre-merge checklist

A bug is fixed when every item holds.

- [ ] The failure was reproduced with one command before changing code.
- [ ] The cause, not the symptom, was found and named.
- [ ] A test fails without the fix and passes with it.
- [ ] No `breakpoint()`, debug prints or `reveal_type` left behind (Ruff `T100`, `T20`).
- [ ] The same class of bug was searched for elsewhere in the codebase.

## Gotchas

These are the debugging traps that waste the most time.

1. **Debugging the wrong code.** An installed copy runs instead of the source (non-editable install, stale wheel, another venv). Print `module.__file__`.
2. **The bug disappears under the debugger.** Timing changed: a race between threads or tasks. Add logging with timestamps and thread or task names instead.
3. **`breakpoint()` does nothing.** `PYTHONBREAKPOINT=0` is set, or stdin is not a terminal (a service, a pipe). Use `debugpy` or `pdb -p` instead.
4. **`pdb -p` fails with a permission error.** Missing ptrace rights or remote debugging disabled. Run as the same user with `sudo` or adjust `ptrace_scope`.
5. **A segfault with no traceback.** A C extension crashed. Run with `-X faulthandler`; for a native backtrace run it under `gdb` (CPython's gdb extension adds `py-bt` for the Python frames) or `lldb` on macOS.
6. **A swallowed exception.** `except Exception: pass` hid the real error. Search for broad `except` blocks along the failing path (Ruff `BLE001`, `S110`).

## See also

- `python-logging`: log lines that stay in the code
- `python-error-handling`: raising and chaining so tracebacks stay useful
- `pytest-testing`: failing tests, fixtures and Hypothesis shrinking
- `python-native-extensions`: crashes inside C, C++ and Rust code
- `python-asyncio`: task dumps and blocked event loops
- `python-performance`: slowness rather than failure
