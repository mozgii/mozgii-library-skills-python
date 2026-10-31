---
name: python-decorators
description: Writes Python decorators that keep the wrapped function's name, signature and types - function decorators with functools.wraps, decorator factories that take arguments, decorators usable with and without parentheses, typing them with ParamSpec and Concatenate, class decorators, decorating methods, classmethods and staticmethods, async-aware decorators that work on coroutine functions, contextlib.ContextDecorator for a decorator that is also a with block, stacking order, and testing decorated code through __wrapped__. Use when writing or reviewing a decorator, adding timing, logging, locking, registration or validation around functions, when a decorated function loses its name, docstring or type hints, breaks on methods or async functions, or runs in the wrong order.
metadata:
  tags:
  - python
  - decorators
  - functools
  - paramspec
  - contextlib
  - async
  - metaprogramming
---

# Python decorators

A decorator is a callable that takes a function (or a class) and returns a replacement; `@timed` above `def run()` is `run = timed(run)`. Decorators are the right tool for behaviour that wraps many functions the same way: timing, logging, locking, registration, access checks. A careless one hides the function's name and signature, loses type information, breaks on methods or silently stops awaiting a coroutine. This skill gives the patterns that avoid those failures. Retry decorators are in the `python-error-handling` skill and caching decorators in the `python-functools-caching` skill.

## Step 0: read the Python version

Decorator typing syntax depends on the oldest Python the code supports, so read it first.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
grep -n "requires-python" pyproject.toml
uv run python -c "import functools; print(functools.WRAPPER_ASSIGNMENTS)"
```

| Version | Change that matters |
|---|---|
| 3.10 | `ParamSpec` and `Concatenate` in `typing`; `contextlib.AsyncContextDecorator` |
| 3.12 | PEP 695 syntax `def deco[**P, R](...)`; `inspect.markcoroutinefunction`; `functools.wraps` copies `__type_params__` |
| 3.13 | `warnings.deprecated` decorator, understood by type checkers |
| 3.14 | `asyncio.iscoroutinefunction` deprecated (use `inspect.iscoroutinefunction`); `functools.wraps` copies `__annotate__` for lazy annotations |

On 3.11 and older, write `P = ParamSpec("P")` and `R = TypeVar("R")` at module level instead of the bracket syntax.

## Choose the kind of decorator

Pick the smallest form that does the job; a decorator also hides control flow, so it has to earn its place.

| You need | Use | Why the others lose here |
|---|---|---|
| the same behaviour around many functions | a function decorator with `functools.wraps` | copying the code into each function drifts |
| settings given at the `@` line | a decorator factory | a plain decorator has nowhere to take them |
| one setup and teardown around blocks and functions | `contextlib.ContextDecorator` | a plain decorator cannot serve a `with` block |
| to change every subclass of a base | `__init_subclass__` | a class decorator runs on one class only |
| to change one function | plain code in that function | a decorator with one user only adds a layer |
| retries or memoisation | stamina, tenacity, `functools.cache` | a home-made wrapper repeats solved problems |

## A function decorator with functools.wraps

`functools.wraps(fn)` copies the wrapped function's `__name__`, `__qualname__`, `__doc__`, `__module__`, `__dict__` and annotations onto the wrapper and sets `wrapper.__wrapped__ = fn`. Without it, every decorated function is called `wrapper` in tracebacks, logs and documentation, and `inspect.signature` shows `(*args, **kwargs)`.

```python
import functools
import inspect
import logging
import time
from collections.abc import Callable

log = logging.getLogger(__name__)


def timed[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            log.debug("%s took %.3fs", fn.__qualname__, time.perf_counter() - start)
    return wrapper


@timed
def add(a: int, b: int = 0) -> int:
    """Add two numbers."""
    return a + b


add.__name__                  # 'add'
inspect.signature(add)        # (a: int, b: int = 0) -> int, followed through __wrapped__
```

- The wrapper passes `*args, **kwargs` through unchanged and returns the result. Forgetting `return` is the most common decorator bug: the function returns `None`.
- `try`/`finally` makes the timing run even when the function raises; exceptions pass through untouched.
- Work done in the decorator body (outside `wrapper`) runs once, at import time, when the `def` is executed. Work inside `wrapper` runs on every call.

## Typing decorators with ParamSpec

A `ParamSpec` (`**P`) captures the wrapped function's whole parameter list, so a type checker still checks every call to the decorated function. Typed as `Callable[..., Any]`, the decorator turns each decorated function into an unchecked one.

| Decorator shape | Signature |
|---|---|
| keeps the signature | `def deco[**P, R](fn: Callable[P, R]) -> Callable[P, R]` |
| changes the return type | `def deco[**P, R](fn: Callable[P, R]) -> Callable[P, R \| None]` |
| supplies the first argument | `def deco[**P, R](fn: Callable[Concatenate[Conn, P], R]) -> Callable[P, R]` |
| wraps a coroutine function | `def deco[**P, R](fn: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]` |

`Concatenate` types a decorator that injects an argument, such as a database connection, so callers no longer pass it.

```python
import functools
import sqlite3
from collections.abc import Callable
from typing import Concatenate


def with_conn[**P, R](fn: Callable[Concatenate[sqlite3.Connection, P], R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        conn = sqlite3.connect("app.db")
        try:
            return fn(conn, *args, **kwargs)
        finally:
            conn.close()
    return wrapper


@with_conn
def count_users(conn: sqlite3.Connection, active: bool = True) -> int:
    return int(conn.execute("select count(*) from users where active = ?", (active,)).fetchone()[0])


count_users(active=False)     # the checker sees (active: bool = True) -> int
```

More on `ParamSpec`, overloads and generics is in the `python-typing` skill.

## Decorator factories with arguments

A decorator that takes arguments, `@retry_on(ConnectionError, times=3)`, is a factory: a function that returns the decorator. That makes three nested levels: the factory holds the settings, the decorator receives the function, the wrapper runs on each call.

```python
import functools
from collections.abc import Callable

from my_app.auth import current_user


def require_role[**P, R](role: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if role not in current_user().roles:
                raise PermissionError(f"{fn.__qualname__} needs role {role!r}")
            return fn(*args, **kwargs)
        return wrapper
    return decorate


@require_role("admin")
def delete_account(account_id: int) -> None: ...
```

Validate the factory's arguments in the outer function, so a bad value fails at import time instead of on the first call. A decorator that works both bare (`@traced`) and with arguments (`@traced(level=...)`) is in [recipes.md](references/recipes.md).

## Class decorators

A class decorator receives the finished class and returns it (usually the same object, changed) or a replacement. Use it to register a class, add methods or check its shape; it is simpler than a metaclass and does not affect subclasses.

```python
COMMANDS: dict[str, type] = {}


def command(cls: type) -> type:
    COMMANDS[cls.__name__.lower()] = cls        # registry lookup by name
    return cls                                  # return the class, or the name becomes None


@command
class Sync: ...
```

- A class decorator runs once, on the class it decorates; subclasses are not decorated. When every subclass must be processed, use `__init_subclass__` (the `python-object-model` skill).
- `@dataclass` is the best-known class decorator. Put your own decorator above `@dataclass` if it needs the generated `__init__` or fields.
- A type checker does not see methods a decorator adds at runtime; declare them on the class or use a base class when typing matters.

## Decorating methods, classmethods and staticmethods

A function-based decorator works on methods unchanged: the wrapper is a function, functions bind as methods, and `self` arrives as the first item of `args`.

```python
class Service:                      # timed: the decorator from the first example
    @timed
    def ping(self, n: int) -> int: ...

    @classmethod
    @timed                          # timed must sit below classmethod and staticmethod
    def build(cls) -> "Service": ...

    @staticmethod
    @timed
    def helper() -> int: ...
```

- Put your decorator under `@classmethod`, `@staticmethod` and `@property`, so it wraps the plain function they then turn into a descriptor.
- A decorator written as a class with `__call__` does not bind: on a method it fails with `missing 1 required positional argument: 'self'`. Give it a `__get__` method, or write it as a function ([recipes.md](references/recipes.md)).
- State kept in the decorator's closure is shared by every instance of the class. Per-instance state (a lock per object) belongs on `self`.
- To decorate every method of a class, loop over `vars(cls)` in a class decorator and wrap the plain functions; skip dunders.

## Async-aware decorators

A plain synchronous wrapper around an `async def` function returns the coroutine without awaiting it. Timing then measures only the creation of the coroutine, a `try`/`except` never sees its exceptions, and a lock is released before the work starts. Check the function at decoration time and return an `async def` wrapper for coroutine functions.

```python
import functools
import inspect
import logging
import time

log = logging.getLogger(__name__)


def timed_any(fn):
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return await fn(*args, **kwargs)
            finally:
                log.debug("%s took %.3fs", fn.__qualname__, time.perf_counter() - start)
        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            log.debug("%s took %.3fs", fn.__qualname__, time.perf_counter() - start)
    return wrapper
```

- Use `inspect.iscoroutinefunction`; `asyncio.iscoroutinefunction` is deprecated since 3.14 and slated for removal in 3.16.
- A sync wrapper also hides the async nature from frameworks that check it: `inspect.iscoroutinefunction(decorated)` becomes `False`. If a sync wrapper really returns an awaitable, mark it with `inspect.markcoroutinefunction(wrapper)` (3.12+).
- For type checkers, write two typed decorators (`timed` and `timed_async`) or one with `@overload`; the runtime branch above is untyped.
- Generator functions have the same problem: a wrapper around a generator function returns the generator at once, so timing and `try` cover nothing. Wrap with a generator (`yield from fn(*args, **kwargs)`) if the work must be covered.

## ContextDecorator: one helper for with blocks and decorators

`contextlib.ContextDecorator` turns a context manager class into something that also works as a decorator: `@timer("report")` wraps the whole function in `with timer("report"):`. Use it when the same setup and teardown is needed both around a block and around a whole function.

The class form and its use on a function and a block are in [recipes.md](references/recipes.md).

- Functions made with `@contextlib.contextmanager` are already usable as decorators; each call of the decorated function re-enters a fresh generator.
- `contextlib.AsyncContextDecorator` does the same for `async with` and `async def` functions.
- The decorator form cannot give the function the value `__enter__` returns. When the function needs it (a connection, a temporary folder), use the `Concatenate` injection pattern instead.

## Stacking order

Stacked decorators apply from the bottom up and run from the top down: the decorator nearest the `def` wraps the function first, and the top one is the outermost wrapper, which runs first on each call.

```python
@outer          # applied second; its wrapper runs first
@inner          # applied first; its wrapper runs second, just before the body
def run() -> None: ...

# run = outer(inner(run))
# call order: outer wrapper -> inner wrapper -> body
```

| Put on top (outermost) | Put underneath |
|---|---|
| `@classmethod`, `@staticmethod`, `@property` | your function decorators |
| access checks (`@require_role`) | caching, so an unauthorised caller never reads the cache |
| timing that should include the retries | retries |
| registration that must record the final callable | anything that wraps |

## Testing decorated code

Test the decorator once, on its own, with a small function defined in the test; test the business function through its undecorated original via `__wrapped__`.

```python
import logging

from my_app.accounts import delete_account
from my_app.timing import timed


def test_timed_keeps_metadata_and_result(caplog) -> None:
    @timed
    def double(x: int) -> int:
        """Doubles."""
        return x * 2

    with caplog.at_level(logging.DEBUG):
        assert double(4) == 8
    assert double.__name__ == "double" and double.__doc__ == "Doubles."
    assert "double took" in caplog.text


def test_delete_account_logic() -> None:
    delete_account.__wrapped__(42)              # skips @require_role: tests the body alone
```

- `inspect.unwrap(fn)` follows the whole `__wrapped__` chain to the original function.
- A decorator that keeps state (a counter, a cache, a rate limiter) leaks it between tests; expose a reset (`cache_clear()`) and call it in a fixture.
- Patch what the decorator calls (the clock, `current_user`) where the decorator module looks it up, not where the decorated function lives.

## Verify

Prove a decorator keeps what it wraps with a probe that decorates a plain function and a coroutine function, then let a type checker read the result.

```bash
uv run python - <<'PY'
import asyncio
import inspect

from my_app.timing import timed_any as deco          # the decorator under test


@deco
def plain(a: int, b: int = 0) -> int:
    """Add."""
    return a + b


@deco
async def coro(a: int) -> int:
    return a


assert plain.__name__ == "plain" and plain.__doc__ == "Add."
assert str(inspect.signature(plain)) == "(a: int, b: int = 0) -> int"
assert plain(1, 2) == 3                              # the wrapper returns the result
assert inspect.iscoroutinefunction(coro)             # async stays async
assert asyncio.run(coro(3)) == 3
print("ok")
PY
uv run mypy --strict src                             # or pyright: calls with wrong arguments still fail
uv run ruff check --select B,ANN401 .                # expect no Any in a public signature
```

## Anti-patterns

| Instead of | Do |
|---|---|
| a wrapper without `functools.wraps` | `@functools.wraps(fn)` on every wrapper |
| `Callable[..., Any]` in a decorator's signature | `ParamSpec`, so calls stay checked |
| a sync wrapper around `async def` | an `async def` wrapper chosen at decoration time |
| a decorator above `@classmethod` | your decorator underneath it |
| closure state meant per instance | state on `self` |
| heavy work in the decorator body | work inside the wrapper, or an explicit setup call |

## Pre-merge checklist

A decorator is ready when every item holds.

- [ ] `functools.wraps(fn)` on every wrapper; the wrapper returns the function's result.
- [ ] Typed with `ParamSpec` (and `Concatenate` when it injects an argument); no `Callable[..., Any]` in the public signature.
- [ ] Coroutine functions get an `async def` wrapper; generator functions are not silently wrapped.
- [ ] Works on methods: function-based, or a class-based decorator with `__get__`; placed under `classmethod`, `staticmethod` and `property`.
- [ ] Factory arguments are validated when the decorator is created.
- [ ] Stacking order is deliberate and written down where it matters.
- [ ] The decorator has its own tests; decorated functions are tested through `__wrapped__` where useful.

## Gotchas

These are the decorator bugs seen most often.

1. **Every decorated function is called `wrapper` in logs and tracebacks.** `functools.wraps` is missing. Add `@functools.wraps(fn)` to the inner function.
2. **The decorated function returns `None`.** The wrapper calls `fn(...)` but does not return its result. Write `return fn(*args, **kwargs)`.
3. **`TypeError: ... missing 1 required positional argument: 'self'`.** A class-based decorator on a method has no `__get__`. Add `__get__` or rewrite it as a function.
4. **A timed or locked `async def` reports zero time or races.** The sync wrapper returned the coroutine unawaited. Branch on `inspect.iscoroutinefunction` and await in an `async def` wrapper.
5. **`@my_decorator(...)` raises `TypeError` about a function argument, or `@my_decorator` without parentheses does nothing.** Factory and plain decorator were mixed up. Call factories with parentheses, or support both forms explicitly.
6. **Type checkers show the decorated function as `(...) -> Any`.** The decorator is untyped. Type it with `ParamSpec`.
7. **`TypeError: 'classmethod' object is not callable`, or a static method suddenly receives `self`.** Your decorator sits above `@classmethod` or `@staticmethod` and wrapped the descriptor, not the function. Put `@classmethod` and `@staticmethod` on top.

## References

The reference file holds longer recipes.

- [recipes.md](references/recipes.md): decorators with optional arguments, class-based decorators with `__get__`, per-instance locks, deprecation with `warnings.deprecated`, registration and dispatch tables, argument validation, wrapping every method of a class, and decorators that keep state safely

## See also

- `python-typing`: `ParamSpec`, overloads and generics in depth
- `python-functools-caching`: caching decorators and `singledispatch`
- `python-error-handling`: retry decorators
- `python-object-model`: descriptors and `__init_subclass__`
- `python-library-maintenance`: `warnings.deprecated` on a public API
