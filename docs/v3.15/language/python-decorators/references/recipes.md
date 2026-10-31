# Python decorator recipes

These are tested patterns for decorators beyond the basic wrapper: optional arguments, class-based decorators that work on methods, locks, deprecation, registration, argument validation, wrapping a whole class, state that can be reset, generator functions, and a `ContextDecorator` timer. Each one keeps the wrapped function's name and docstring with `functools.wraps`.

## A decorator with optional arguments

A decorator that works both bare (`@traced`) and with keyword arguments (`@traced(level=logging.INFO)`) checks whether it received the function as its first positional argument. Making the settings keyword-only removes the ambiguity, and two overloads keep it typed.

```python
import functools
import logging
from collections.abc import Callable
from typing import Any, overload

log = logging.getLogger(__name__)


@overload
def traced[**P, R](fn: Callable[P, R], /) -> Callable[P, R]: ...
@overload
def traced[**P, R](*, level: int = logging.DEBUG) -> Callable[[Callable[P, R]], Callable[P, R]]: ...
def traced(fn: Callable[..., Any] | None = None, /, *, level: int = logging.DEBUG) -> Any:
    def decorate(f: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            log.log(level, "call %s", f.__qualname__)
            return f(*args, **kwargs)
        return wrapper
    return decorate if fn is None else decorate(fn)


@traced
def load() -> None: ...


@traced(level=logging.INFO)
def save() -> None: ...
```

Only use this form for decorators that are usually bare. For ones that always take settings, a plain factory is clearer, and `@factory()` with empty parentheses is fine.

## A class-based decorator that works on methods

A decorator written as a class is useful when it carries state and methods of its own (a counter, a `reset()`). An instance with `__call__` does not bind to instances like a function does, so on a method it never receives `self`. Adding `__get__` that returns a bound method fixes that.

```python
import functools
import threading
from collections.abc import Callable
from types import MethodType
from typing import Any


class CountCalls:
    def __init__(self, fn: Callable[..., Any]) -> None:
        functools.update_wrapper(self, fn)        # the wraps() equivalent for an object
        self.fn = fn
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            self.calls += 1
        return self.fn(*args, **kwargs)

    def __get__(self, obj: object, objtype: type | None = None) -> Any:
        if obj is None:
            return self                           # Repo.save is the CountCalls object
        return MethodType(self, obj)              # repo.save binds like a method


class Repo:
    @CountCalls
    def save(self, item: str) -> None: ...


Repo().save("a")
Repo.save.calls          # 1: one counter for the class, shared by every instance
```

## A per-instance lock decorator

A lock created inside the decorator is one lock for every instance of the class, which serialises unrelated objects. For a lock per object, keep it on `self` and have the decorator use it.

```python
import functools
import threading

def locked(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class Account:
    def __init__(self) -> None:
        self._lock = threading.RLock()            # re-entrant: a locked method may call another
        self.balance = 0

    @locked
    def deposit(self, amount: int) -> None:
        self.balance += amount
```

An `RLock` lets one locked method call another on the same object without deadlocking. For asyncio code, use `asyncio.Lock` with an `async def` wrapper; a `threading.Lock` held across an `await` blocks the whole event loop.

## Deprecating a function with warnings.deprecated

`warnings.deprecated` (3.13) marks a function, class or overload as deprecated. Calls emit a `DeprecationWarning` at runtime, and type checkers report every use. On older Pythons, the same decorator comes from `typing_extensions`.

```python
from warnings import deprecated


@deprecated("use load_config() instead")
def read_config() -> dict[str, str]:
    return load_config()


read_config.__deprecated__      # 'use load_config() instead'
```

`DeprecationWarning` is hidden by default outside `__main__` and test runners. Run tests with `-W error::DeprecationWarning` so callers see it; pass `category=FutureWarning` when end users of an application must see it too.

## A registration decorator for dispatch tables

A registration decorator records the function in a table and returns it unchanged, so it adds no call overhead. Command routers, event handlers and serializers by type are built this way.

```python
from collections.abc import Callable
from typing import Any

Handler = Callable[[dict[str, Any]], str]
HANDLERS: dict[str, Handler] = {}


def handles(event: str) -> Callable[[Handler], Handler]:
    def decorate(fn: Handler) -> Handler:
        if event in HANDLERS:
            raise ValueError(f"{event!r} already handled by {HANDLERS[event].__qualname__}")
        HANDLERS[event] = fn
        return fn                                 # unchanged: no wrapper, no wraps() needed
    return decorate


@handles("user.created")
def on_user_created(payload: dict[str, Any]) -> str:
    return f"welcome {payload['name']}"


def dispatch(event: str, payload: dict[str, Any]) -> str:
    try:
        handler = HANDLERS[event]
    except KeyError:
        raise LookupError(f"no handler for {event!r}") from None
    return handler(payload)
```

- Refuse duplicates at registration; a silent overwrite is a bug that shows up far from its cause.
- The table fills only when the modules with the handlers are imported. Import them explicitly at start-up.
- For dispatch on the type of the first argument, `functools.singledispatch` already does this (the `python-functools-caching` skill).

## Argument validation with inspect.signature

A validating decorator that uses `inspect.signature(fn).bind(*args, **kwargs)` sees every argument by name, whether the caller passed it by position, by keyword or left the default. Compute the signature once, at decoration time, and check that the named parameters exist.

```python
import functools
import inspect


def validate_positive(*names: str):
    def decorate(fn):
        sig = inspect.signature(fn)
        unknown = set(names) - sig.parameters.keys()
        if unknown:                                           # fails at import time, not on a call
            raise TypeError(f"{fn.__qualname__} has no parameters {sorted(unknown)}")

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            for name in names:
                if bound.arguments[name] <= 0:
                    raise ValueError(f"{name} must be > 0, got {bound.arguments[name]!r}")
            return fn(*args, **kwargs)
        return wrapper
    return decorate


@validate_positive("qty", "price")
def order(item: str, qty: int, price: float = 1.0) -> float:
    return qty * price
```

`bind` costs a few microseconds per call. For data coming from outside the program, validate once at the boundary with a model (the `python-data-models` skill) rather than on every internal call.

## Wrapping every method of a class

A class decorator can apply a function decorator to every method. Unwrap `staticmethod` and `classmethod` objects first, wrap their `__func__`, and rebuild them; skip dunder methods.

```python
import inspect

def trace_methods(cls):
    for name, attr in list(vars(cls).items()):
        if name.startswith("__"):
            continue
        if isinstance(attr, (staticmethod, classmethod)):
            setattr(cls, name, type(attr)(traced(attr.__func__)))
        elif inspect.isfunction(attr):
            setattr(cls, name, traced(attr))
    return cls


@trace_methods
class Store:
    def get(self, key: str) -> str: ...

    @classmethod
    def make(cls) -> "Store": ...
```

Inherited methods are not wrapped because `vars(cls)` holds only the class's own attributes. Properties are skipped as written; wrap `attr.fget` and rebuild the property if they need it too.

## Decorator state that tests can reset

A decorator that keeps state (a rate limiter, a counter) needs a lock for threads and a way to reset it between tests. Attach a `reset` function to the wrapper and accept an injectable clock.

```python
import functools
import threading
import time
from collections.abc import Callable


def min_interval(seconds: float, clock: Callable[[], float] = time.monotonic):
    """Drop calls that come sooner than `seconds` after the last accepted one."""
    def decorate(fn):
        last = float("-inf")
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            nonlocal last
            with lock:
                now = clock()
                if now - last < seconds:
                    return None
                last = now
            return fn(*args, **kwargs)

        def reset() -> None:
            nonlocal last
            last = float("-inf")

        wrapper.reset = reset
        return wrapper
    return decorate
```

Type checkers do not know about attributes added to a function. For a typed public API, make the decorator return a small class with `__call__` and `reset` (and `__get__` if it decorates methods).

## Wrapping a generator function

A wrapper around a generator function that just calls it returns the generator immediately, before any of its code runs. To time or guard the iteration itself, make the wrapper a generator that delegates with `yield from`; it also passes `send`, `throw` and the return value through.

```python
import functools
import logging
import time

log = logging.getLogger(__name__)


def timed_gen(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return (yield from fn(*args, **kwargs))
        finally:
            log.debug("%s ran %.3fs", fn.__qualname__, time.perf_counter() - start)
    return wrapper
```

`inspect.isgeneratorfunction(wrapper)` stays `True`, so code that checks for generators keeps working. For async generator functions, write an `async def` wrapper with `async for item in fn(...): yield item`; `yield from` is not allowed there.

## A timer that works as a decorator and a with block

`contextlib.ContextDecorator` gives a context manager class the decorator form as well, so one class times a whole function or a single block. `__enter__` and `__exit__` run once per call of the decorated function.

```python
import logging
import time
from contextlib import ContextDecorator

log = logging.getLogger(__name__)


class timer(ContextDecorator):
    def __init__(self, label: str) -> None:
        self.label = label

    def __enter__(self) -> "timer":
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        log.debug("%s: %.3fs", self.label, time.perf_counter() - self.start)


@timer("nightly report")
def report() -> None: ...


with timer("load config"):
    ...
```
