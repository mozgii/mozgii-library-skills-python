---
name: python-error-handling
description: Designs and handles errors in Python code - where to catch and where to let exceptions rise, narrow except clauses, a small exception hierarchy for a library or service, raise from and exception chaining, add_note for context, ExceptionGroup and except*, cleanup with with, finally, contextmanager and ExitStack, retries with exponential backoff and jitter (stamina, tenacity or a stdlib loop) only for transient and idempotent work, timeouts and deadlines, warnings and deprecations, and error messages people can act on. Use when writing try/except blocks, defining custom exceptions, adding retries to a flaky call, cleaning up resources reliably, or when errors are swallowed, a traceback hides the real cause or a retry loop makes an outage worse.
metadata:
  tags:
  - python
  - exceptions
  - error-handling
  - retries
  - backoff
  - resilience
  - context-managers
  - exceptiongroup
---

# Python error handling

An exception is how Python code says "I could not do what you asked". Good error handling decides, for each kind of failure, the one place that can do something about it, and lets the exception travel there untouched. Most bad error handling catches too much, too early, and turns a clear traceback into silence or a vague message. This skill covers the decisions and the patterns; retry details are in [retries-and-resilience.md](references/retries-and-resilience.md).

## Step 0: read the Python version

Several error-handling features are recent, so check the oldest Python the code supports.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
grep -n "requires-python" pyproject.toml
```

| Version | Feature |
|---|---|
| 3.11 | `ExceptionGroup`, `except*`, `BaseException.add_note()`, fine-grained error locations in tracebacks |
| 3.13 | `warnings.deprecated` decorator |
| 3.14 | `except A, B:` without parentheses when there is no `as` (PEP 758); `return`, `break` or `continue` leaving a `finally` block is a `SyntaxWarning` (PEP 765) |

## Decide where each error is handled

An exception should be caught only where the code can do one of four things with it; everywhere else it propagates.

| The code can | Example | Do |
|---|---|---|
| recover | a cache miss, a missing optional config file | catch the specific exception, use the fallback |
| retry | a timeout talking to another service | retry with backoff, then let it rise |
| translate | a driver's `psycopg.errors.UniqueViolation` at your repository boundary | `raise EmailTaken(email) from exc` |
| report | the top of a request handler, a CLI's `main`, a job runner | log with the traceback, return an error response or exit code |

Code in the middle (a service function, a helper) usually does none of these and has no `try` at all. A `try` that only logs and re-raises adds a duplicate log line and nothing else.

## Catch narrowly

The `except` clause names the exceptions you expect and wraps only the lines that raise them.

```python
import json
from pathlib import Path


class SettingsError(Exception):
    """The settings file exists but cannot be used."""


def load_settings(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}                                          # recover: no file means defaults
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SettingsError(f"{path} is not valid JSON (line {exc.lineno}): {exc.msg}") from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must contain a JSON object, got {type(data).__name__}")
    return data
```

- Never write a bare `except:` or `except BaseException:` outside the very top of a program: they catch `KeyboardInterrupt`, `SystemExit` and `asyncio.CancelledError`, so Ctrl+C and shutdown stop working.
- `except Exception:` belongs only at a reporting boundary, and always with `log.exception(...)`; elsewhere it hides `NameError`, `TypeError` and `AttributeError` from your own typos.
- Keep the `try` block small. A long block catches the same exception type from lines you never meant to guard.
- `else:` runs only when the `try` succeeded; put code there that must not be guarded.
- Ruff checks these: `E722` (bare except), `BLE001` (blind `except Exception`), `S110` (`except: pass`), `TRY` rules (raise inside try, long messages, `raise ... from`).

## A small exception hierarchy

A library or service defines one base exception and a few subclasses named for what the caller does differently, not for every place that can fail.

```python
class ShopError(Exception):
    """Base class for every error this package raises on purpose."""


class NotFound(ShopError, LookupError):                    # also a LookupError for generic callers
    def __init__(self, kind: str, key: object) -> None:
        super().__init__(kind, key)                        # args rebuild the exception when unpickled
        self.kind = kind
        self.key = key

    def __str__(self) -> str:
        return f"{self.kind} {self.key!r} not found"


class Conflict(ShopError):
    """The request clashes with the current state; retrying the same request will not help."""


class Unavailable(ShopError):
    """A dependency is down or timed out; retrying later may help."""
```

- Callers catch `ShopError` to handle "anything from this package", or one subclass when they act on it. Three to six subclasses are usually enough.
- Store the data a handler needs as attributes (`exc.key`), so nobody parses the message.
- Also inherit from the matching built-in (`LookupError`, `ValueError`, `PermissionError`, `TimeoutError`) when the meaning really matches.
- Raise built-ins for programming errors: a wrong argument type is `TypeError`, an out-of-range argument is `ValueError`.
- Pass every `__init__` argument to `super().__init__`, in order, and build the text in `__str__`. Pickle recreates an exception by calling its class with `exc.args`, so `super().__init__(message)` on a two-argument class fails with `TypeError: missing 1 required positional argument` in multiprocessing workers and task queues.

## Chaining with raise from

`raise NewError(...) from exc` records the original exception as the cause, so the traceback shows both: what failed underneath and what it meant to your code.

```python
import psycopg


async def register(conn: psycopg.AsyncConnection, email: str) -> None:
    try:
        await conn.execute("insert into users (email) values (%s)", (email,))
    except psycopg.errors.UniqueViolation as exc:
        raise Conflict(f"email {email!r} is already registered") from exc
```

- Without `from`, Python still links them, but the traceback says "During handling of the above exception, another exception occurred", which reads like a bug in the handler.
- `from None` hides the original; use it only when the lower exception is noise to every reader (a `KeyError` inside a lookup you translate to `NotFound`).
- Re-raise the same exception with a bare `raise`, which keeps its traceback.

## Add context with notes

`exc.add_note(text)` (3.11+) attaches a line of context to an exception without changing its type, and the traceback prints it under the message.

```python
for line_no, line in enumerate(lines, start=1):
    try:
        records.append(parse_record(line))
    except ValueError as exc:
        exc.add_note(f"while parsing {path}:{line_no}")
        raise
```

Use notes where the context is known (which file, which row, which user id) instead of wrapping the exception in a new type only to carry text.

## Several errors at once: ExceptionGroup and except*

An `ExceptionGroup` carries several exceptions together; `TaskGroup` raises one when tasks fail, and code that validates many items can raise one to report every problem at once.

```python
def validate_rows(rows: list[dict[str, str]]) -> None:
    problems: list[Exception] = []
    for i, row in enumerate(rows):
        if not row.get("email"):
            problems.append(ValueError(f"row {i}: email is empty"))
    if problems:
        raise ExceptionGroup("invalid rows", problems)


try:
    validate_rows(rows)
except* ValueError as group:                               # matches the ValueErrors inside the group
    for problem in group.exceptions:
        print(problem)
```

A plain `except ValueError:` does not catch an `ExceptionGroup` that contains one; use `except*`, which can appear several times to split a group by type. Prefer a list of error records in a normal return value when the caller is not exception-driven (a form, an import report).

## Cleanup that always runs

Resources (files, locks, connections, temporary directories) are released with `with`, which runs the cleanup on success, on an exception and on cancellation.

```python
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path


@contextmanager
def locked(lock_path: Path) -> Iterator[None]:
    fd = acquire_lock(lock_path)
    try:
        yield
    finally:
        release_lock(fd)                                   # runs even when the body raises


def merge(paths: list[Path], out: Path) -> None:
    with ExitStack() as stack, out.open("w", encoding="utf-8") as dst:
        sources = [stack.enter_context(p.open(encoding="utf-8")) for p in paths]   # all closed at the end
        for src in sources:
            dst.write(src.read())
```

- `finally` runs on every exit; never `return` from it, because that swallows the exception (a `SyntaxWarning` since 3.14).
- An exception raised inside `finally` or `__exit__` replaces the original one; keep cleanup code simple and guard it if it can fail.
- `contextlib.closing(obj)` wraps objects that have `close()` but no context manager; `contextlib.suppress(FileNotFoundError)` replaces `try/except/pass` for one expected error.
- `ExitStack.callback(fn, *args)` registers cleanup for things that are not context managers; `AsyncExitStack` does the same in async code.
- Do not rely on `__del__` for cleanup: when and whether it runs depends on the garbage collector.

## Ask forgiveness or look first

Python's usual style is to try the operation and handle the failure (EAFP), because checking first races with other processes and duplicates work.

```python
try:
    text = path.read_text(encoding="utf-8")                # correct even if the file vanishes right now
except FileNotFoundError:
    text = ""

if path.exists():                                          # race: the file can disappear after this line
    text = path.read_text(encoding="utf-8")
```

Look first (LBYL) when the check is cheap and the failure would be expensive or hard to undo: validate all inputs before writing anything, check free disk space before a long download.

## Retry only what is transient

A retry helps only when the failure is temporary (a timeout, a dropped connection, HTTP 429 or 503) and the operation is safe to repeat. Retrying a permanent error (HTTP 400, a validation error, a bug) only delays it and adds load. stamina is the default.

```python
import httpx
import stamina                                             # uv add stamina (26.1)


def is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 502, 503, 504}
    return isinstance(exc, httpx.TransportError)          # timeouts, connection errors


@stamina.retry(on=is_transient, attempts=5, timeout=30.0)   # exponential backoff with jitter, 30 s in total
def fetch_report(client: httpx.Client, report_id: str) -> bytes:
    response = client.get(f"/reports/{report_id}")
    response.raise_for_status()
    return response.content
```

- Backoff with jitter spreads retries out, so a thousand clients do not all retry in the same second after an outage.
- Cap the total time (`timeout=`) as well as the attempts; a caller waiting for a reply has its own deadline.
- Retry at one layer only. A retrying HTTP client inside a retrying service inside a retrying job turns 3 attempts into 27.
- Writes are retried only when they are idempotent: a `PUT` of the same state, or a `POST` with an idempotency key the server deduplicates.
- In tests, `stamina.set_testing(True, attempts=1)` turns off waiting and extra attempts.

The same policy with tenacity, and a dependency-free loop with full jitter, are in [retries-and-resilience.md](references/retries-and-resilience.md).

`Retry-After` headers, circuit breakers, async retries and testing: [retries-and-resilience.md](references/retries-and-resilience.md).

## Timeouts and deadlines

Every call that waits on something outside the process gets a timeout, because a call without one can wait forever and hold a worker, a connection and a lock while it does.

- HTTP: `httpx.Client(timeout=10.0)`; requests: `requests.get(url, timeout=(3.05, 30))`. requests has no default timeout.
- Sockets: `socket.create_connection((host, port), timeout=5)`; subprocesses: `subprocess.run(..., timeout=60)`; queues and locks: `queue.get(timeout=1)`, `lock.acquire(timeout=5)`.
- asyncio: `async with asyncio.timeout(10):` around the whole operation, retries included.
- Pass a deadline down rather than a fresh timeout at each layer: compute `deadline = time.monotonic() + 30` once and give each call `max(0, deadline - time.monotonic())`.

## Warnings and deprecations

A warning tells the caller about something that still works but should change; it is not an error and does not stop the program.

```python
import warnings


def connect(url: str, *, timeout: float | None = None, retries: int | None = None) -> None:
    if retries is not None:
        warnings.warn("connect(retries=...) is ignored and will be removed in 3.0; configure a retry policy",
                      DeprecationWarning, stacklevel=2)    # stacklevel=2 points at the caller's line


@warnings.deprecated("use load_v2()")                      # 3.13+; typing_extensions.deprecated before that
def load() -> None: ...
```

- `DeprecationWarning` is hidden by default except in `__main__` and in test runners; `FutureWarning` is always shown and suits behaviour changes aimed at end users.
- Run tests with warnings as errors (`filterwarnings = ["error"]` in pytest), so deprecations surface before the removal release.

## Error messages people can act on

An error message names what failed, the value that caused it, and what to do, in one sentence.

| Vague | Actionable |
|---|---|
| `Invalid input` | `price must be a positive number, got '-3' (row 12 of prices.csv)` |
| `Connection failed` | `could not reach db.internal:5432 within 5 s; is the database running?` |
| `KeyError: 'region'` | `config.toml has no 'region' key; add region = "eu-west-1"` |

- Include the offending value with `!r`, so empty strings and stray spaces are visible; never include secrets or whole request bodies.
- Return or show a short message to users and log the full exception with its traceback for developers.

## Exceptions or return values

Python signals failure with exceptions; a return value signals an expected outcome.

- Return `None` (typed as `X | None`) when "not there" is a normal answer: `dict.get`, `find_user(email)`. Raise when the caller had a right to expect success: `get_user(user_id)` with an id from your own database.
- Do not return error codes or `(ok, value)` tuples; callers forget to check them, and the type checker cannot help.
- Never use `assert` for input validation or access checks: `python -O` removes asserts.

## Verify

Prove the error paths with Ruff, a pickle round trip for each exception with its own `__init__`, and a check that a permanent failure is never retried.

```bash
uv run ruff check --select E722,BLE,S110,TRY,B012,B904 .       # expect no finding
uv run python - <<'PY'
import pickle

import httpx

from shop.errors import NotFound, is_transient               # your exceptions and retry predicate

copy = pickle.loads(pickle.dumps(NotFound("user", 7)))
assert (copy.kind, copy.key, str(copy)) == ("user", 7, "user 7 not found")

request = httpx.Request("GET", "https://api.test/")
bad = httpx.HTTPStatusError("400", request=request, response=httpx.Response(400, request=request))
busy = httpx.HTTPStatusError("503", request=request, response=httpx.Response(503, request=request))
assert not is_transient(bad) and is_transient(busy)
print("ok")
PY
uv run pytest -W error::DeprecationWarning                     # deprecations fail while there is time to act
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `except Exception: pass` | catch the named error, or log it at a reporting boundary |
| `try` around a whole function | `try` around the lines that raise |
| log and re-raise at every layer | one report at the boundary |
| retries in the client, the service and the job | retries at one layer, with a total time cap |
| `(ok, value)` tuples | exceptions, or `X \| None` for "not there" |
| `assert` to validate input | `raise ValueError`; `python -O` removes asserts |
| cleanup in `__del__` | `with`, `finally` or `ExitStack` |

## Pre-merge checklist

Error handling is in good shape when every item holds.

- [ ] Each `try` catches named exceptions around the few lines that raise them; no bare `except` below the top level.
- [ ] `except Exception` appears only at reporting boundaries, with `log.exception`.
- [ ] The package raises its own small hierarchy; translations use `raise ... from exc`.
- [ ] Resources are released with `with` or `finally`; no `return` inside `finally`.
- [ ] Retries cover only transient, idempotent operations, with jitter, an attempt cap and a total time cap, at one layer.
- [ ] Every outside call has a timeout.
- [ ] Messages name the value and the fix; no secrets in messages or logs.
- [ ] Ruff `E722`, `BLE`, `S110` and `TRY` rules pass.

## Gotchas

These are the error-handling mistakes people ask about most.

1. **"My program fails silently."** An `except Exception: pass` (or a handler that only returns `None`) along the path swallowed the error. Search for broad handlers; log or re-raise.
2. **"Ctrl+C does not stop my script."** A bare `except:` or `except BaseException:` caught `KeyboardInterrupt` inside a loop. Catch `Exception` or narrower.
3. **"The traceback shows the wrong error."** An exception raised inside `except` or `finally` replaced the original. Use `raise ... from exc`, and keep cleanup code from raising.
4. **"The retries made the outage worse."** Every client retried immediately and without a cap. Add exponential backoff with jitter and a total timeout, and stop retrying 4xx errors.
5. **"A payment was charged twice."** A non-idempotent `POST` was retried after a timeout that happened after the server processed it. Send an idempotency key, or do not retry writes.
6. **`except ValueError` does not catch the error from my `TaskGroup`.** The task's error arrives inside an `ExceptionGroup`. Use `except* ValueError`.
7. **Custom exception fails to unpickle in a worker process with `TypeError: ... missing 1 required positional argument`.** Its `__init__` takes arguments that it does not pass to `super().__init__`. Pass them all through and build the text in `__str__`, or define `__reduce__`.
8. **`except (A, B) as exc` versus `except A, B:`.** On 3.14 both are valid without `as`; with `as` the parentheses are still required, and on 3.13 or older `except A, B:` is a `SyntaxError`.

## References

The reference file holds the longer resilience recipes.

- [retries-and-resilience.md](references/retries-and-resilience.md): what is transient, idempotency keys, `Retry-After`, retry layers, async retries, a circuit breaker, logging and testing retries

## See also

- `python-logging`: `log.exception` and what reaches a reporting boundary
- `python-http-clients`: timeouts and retries on HTTP calls
- `python-asyncio`: `TaskGroup`, cancellation and `except*`
- `python-services-shutdown`: cleanup when a process is told to stop
- `python-library-maintenance`: exceptions as public API
