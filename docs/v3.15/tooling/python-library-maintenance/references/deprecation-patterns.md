---
title: Deprecation patterns for Python libraries
author: MOZGII, Inc.
lang: en
---

# Deprecation patterns for Python libraries

Recipes for deprecating every kind of public API change in a library: functions, classes, methods, one overload, renamed and positional parameters, changed defaults, module attributes and moved modules, plus testing them. Every recipe keeps the old form working, warns at the caller's line, and names the replacement and the removal version. All of them were run on Python 3.12 (with typing_extensions) and 3.14.

## Contents

This reference has 10 sections.

1. A helper that blames the caller
2. Functions and classes with @deprecated
3. Methods and one overload
4. A renamed keyword parameter
5. A parameter that becomes keyword-only
6. A default that will change
7. A module attribute
8. A moved module
9. Testing deprecations
10. Gotchas

## 1. A helper that blames the caller

Hand-written warnings go through one helper, so every deprecation in the package points at user code. `skip_file_prefixes` (Python 3.12+) skips every frame inside the package while counting the stack level.

```python
# src/geolib/_deprecate.py
import os
import warnings

_PKG = os.path.dirname(__file__)


def warn_deprecated(message: str, category: type[Warning] = DeprecationWarning) -> None:
    """Warn, blaming the first frame outside this package."""
    warnings.warn(message, category, skip_file_prefixes=(_PKG,))
```

For Python 3.11, which lacks `skip_file_prefixes`, pass an explicit `stacklevel` that counts the frames between the user and `warnings.warn`, and test it.

## 2. Functions and classes with @deprecated

`warnings.deprecated` (Python 3.13; `typing_extensions.deprecated` before) wraps a function or class: calling the function or instantiating the class warns, and type checkers flag each use.

```python
from geolib._compat import deprecated   # warnings.deprecated or typing_extensions.deprecated


@deprecated("load() is deprecated since 2.3 and will be removed in 3.0; use read()")
def load(path: str) -> str:
    return read(path)


@deprecated("Session is deprecated since 2.3; use Client")
class Session:
    def __init__(self, url: str) -> None:
        self.url = url
```

- The class form warns on instantiation and also when someone subclasses the class; `isinstance` and pickling keep working.
- `category=FutureWarning` or `category=None` (type checkers only, no runtime warning) are accepted.
- Keep the old object a thin wrapper over the new one, so both behave the same until removal.

## 3. Methods and one overload

`@deprecated` works on methods as on functions. On an `@overload`, it marks one call form for type checkers only, because overload stubs never run; the implementation must emit the runtime warning itself.

```python
from typing import overload


class Route:
    def __init__(self, stops: list[str]) -> None:
        self.stops = stops

    @deprecated("Route.length() is deprecated since 2.3; use len(route)")
    def length(self) -> int:
        return len(self)

    def __len__(self) -> int:
        return len(self.stops)

    @overload
    def stop(self, index: int) -> str: ...
    @overload
    @deprecated("Route.stop(name) is deprecated since 2.3; use Route.index(name)")
    def stop(self, index: str) -> str: ...
    def stop(self, index: int | str) -> str:
        if isinstance(index, str):
            warn_deprecated("Route.stop(name) is deprecated since 2.3; use Route.index(name)")
            return self.stops[self.stops.index(index)]
        return self.stops[index]
```

Pyright with `reportDeprecated` reports `The method "length" in class "Route" is deprecated` and the same for `stop("x")`, while `stop(0)` stays clean.

## 4. A renamed keyword parameter

A decorator accepts the old keyword name, warns, and passes the value on under the new name. `ParamSpec` keeps the wrapped signature for type checkers.

```python
import functools
from collections.abc import Callable


def renamed_parameter[**P, R](old: str, new: str, since: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Accept the old keyword name for a while, with a DeprecationWarning."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if old in kwargs:
                if new in kwargs:
                    raise TypeError(f"{func.__name__}() got both {old!r} and {new!r}")
                warn_deprecated(f"{func.__name__}({old}=...) is deprecated since {since}; use {new}=")
                kwargs[new] = kwargs.pop(old)
            return func(*args, **kwargs)

        return wrapper

    return decorator


@renamed_parameter("tol", "tolerance", since="2.3")
def simplify(points: list[tuple[float, float]], *, tolerance: float = 0.1) -> list[tuple[float, float]]:
    ...
```

- Untyped callers get the runtime warning. Type-checked callers get an error at once (`No parameter named "tol"`), because the visible signature has only the new name; mention that in the changelog.
- The PEP 695 syntax (`def f[**P, R]`) needs Python 3.12; on 3.11 declare `P = ParamSpec("P")` and `R = TypeVar("R")`.

## 5. A parameter that becomes keyword-only

Moving a parameter behind `*` breaks positional callers. Accept extra positional arguments for one release and warn.

```python
def distance(a: tuple[float, float], b: tuple[float, float], *args: object, unit: str = "km") -> float:
    if args:                                   # 2.3: unit becomes keyword-only
        warn_deprecated("passing unit positionally to distance() is deprecated since 2.3; use unit=")
        (unit,) = args
    scale = {"km": 1.0, "mi": 0.621371}[unit]
    return scale * ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
```

In the release that removes it, replace `*args: object` with `*` and delete the branch. Too many positional arguments then raise `TypeError` as usual.

## 6. A default that will change

Changing a default changes behaviour for every caller who relies on it, silently. Use a sentinel to detect "not passed", warn with `FutureWarning` (shown to everyone by default), and keep the old behaviour until the major release.

```python
from typing import Any

_MISSING: Any = object()


def parse(text: str, *, strict: bool = _MISSING) -> list[str]:
    if strict is _MISSING:                     # 3.0: the default becomes True
        warn_deprecated("parse() will default to strict=True in 3.0; pass strict= explicitly", FutureWarning)
        strict = False
    parts = text.split(",")
    if strict and any(not p for p in parts):
        raise ValueError("empty field")
    return parts
```

- Callers silence the warning by passing the argument explicitly, which is the migration.
- Typing the sentinel as `Any` keeps the public annotation `bool`. Python 3.15's built-in `sentinel` gives a nicer repr where available.

## 7. A module attribute

A module-level `__getattr__` (PEP 562) runs only for names that are not found normally, so a removed constant can warn on access.

```python
import warnings


def __getattr__(name: str) -> object:
    if name == "DEFAULT_TIMEOUT":                     # removed from the public API in 2.3
        warnings.warn("geolib.DEFAULT_TIMEOUT is deprecated since 2.3; pass timeout= explicitly",
                      DeprecationWarning, stacklevel=2)
        return 30.0
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

`stacklevel=2` is right here because the caller's attribute access calls `__getattr__` directly. `from geolib import DEFAULT_TIMEOUT` also triggers it.

## 8. A moved module

When a module moves, leave the old one as a shim that re-exports everything and warns on import.

```python
# src/geolib/geometry.py
"""Moved to geolib.shapes in 2.3; this module only re-exports."""

import warnings

from geolib.shapes import *  # noqa: F403
from geolib.shapes import __all__ as __all__

warnings.warn("geolib.geometry is deprecated since 2.3; import from geolib.shapes",
              DeprecationWarning, stacklevel=2)
```

With `stacklevel=2` the warning points at the user's `import geolib.geometry` line; the import system's own frames are skipped automatically. The warning fires once per process, at first import, because later imports come from `sys.modules`.

## 9. Testing deprecations

Each deprecation gets a test that the old form still works and warns, and the whole suite runs with warnings as errors so the library never uses its own deprecated API.

```toml
[tool.pytest]
filterwarnings = ["error"]
```

```python
import importlib
import sys
import warnings

import pytest

import geolib


def test_renamed_parameter() -> None:
    pts = [(0.0, 0.0)] * 10
    with pytest.deprecated_call(match=r"tol=\.\.\.\) is deprecated"):
        old = geolib.simplify(pts, tol=0.5)
    assert old == geolib.simplify(pts, tolerance=0.5)


def test_changing_default_warns_only_when_omitted() -> None:
    with pytest.warns(FutureWarning, match="strict=True in 3.0"):
        geolib.parse("a,,b")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert geolib.parse("a,b", strict=True) == ["a", "b"]


def test_moved_module() -> None:
    sys.modules.pop("geolib.geometry", None)          # import-time warnings fire only once
    with pytest.deprecated_call(match="import from geolib.shapes"):
        mod = importlib.import_module("geolib.geometry")
    assert mod.square(3) == 9
```

- `pytest.deprecated_call()` accepts `DeprecationWarning`, `PendingDeprecationWarning` and `FutureWarning`; `pytest.warns(FutureWarning)` states the category exactly.
- Check where the warning points at least once by hand: run a small caller script with `python -W default caller.py` and read the file and line in each warning.
- Remove the tests together with the deprecated code in the major release; a test asserting the old name is gone (`assert not hasattr(geolib, "load")`) documents the removal.

## 10. Gotchas

These are the deprecation mistakes seen most.

1. **The warning names a file inside the library.** `stacklevel` counts the wrong number of frames. Route warnings through the `skip_file_prefixes` helper.
2. **A deprecated overload never warns at runtime.** Overload stubs do not run. Emit the warning in the implementation as well.
3. **The moved-module test passes alone and fails in the full suite.** Another test imported the module first, so the import-time warning had already fired. Remove it from `sys.modules` before the test imports it.
4. **A user's subclass suddenly warns at import time.** `@deprecated` on a class also warns when it is subclassed. That is intended: the subclass must move too. Say so in the changelog entry.
5. **A typed caller fails type checking the day a parameter is renamed.** The decorator hides the old name from the signature. Accept that, or keep both parameters in the signature with an overload for one release.
