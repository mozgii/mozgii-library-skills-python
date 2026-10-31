---
title: Lazy imports in Python 3.15 (PEP 810)
author: MOZGII, Inc.
lang: en
---

# Lazy imports in Python 3.15 (PEP 810)

How the `lazy` import keyword of Python 3.15 works, what triggers the real import, where it is allowed, how to write code that is lazy on 3.15 and still runs on 3.14, the global mode and its filter, and how it compares with function-level imports and `importlib.util.LazyLoader`. Checked against CPython 3.15.2.

## Contents

This reference has 8 sections.

1. The two forms of the lazy keyword
2. When the real import happens
3. Where lazy imports are allowed
4. Errors move to the first use
5. Code that also runs on 3.14: __lazy_modules__
6. Global lazy mode and the filter
7. Imports that must stay eager
8. Older ways to defer imports

## 1. The two forms of the lazy keyword

`lazy` is a soft keyword placed before an import statement at module level; it binds the name to a lazy proxy instead of running the import. A variable called `lazy` elsewhere keeps working.

```python
import sys

lazy import json                         # binds 'json' to a proxy
lazy from fractions import Fraction      # binds 'Fraction' to a proxy
lazy import xml.dom.minidom              # binds 'xml'; the whole dotted import is deferred

print([m in sys.modules for m in ("json", "fractions", "xml.dom.minidom")])   # [False, False, False]
print(json.dumps({"a": Fraction(1, 3).denominator}))                         # both load here
lazy = "still a normal name"
print(lazy)
```

## 2. When the real import happens

The proxy is replaced by the real module or attribute the first time the name is loaded: used in an expression in its own module, or read as an attribute (`mod.name`) from another module. Only reading the module's `__dict__` directly shows the proxy, whose type is `types.LazyImportType`.

```python
import sys
import types
from pathlib import Path

Path("heavy_mod.py").write_text("print('heavy_mod runs')\nVALUE = 42\n", encoding="utf-8")
Path("uses_heavy.py").write_text("lazy import heavy_mod\ndef value():\n    return heavy_mod.VALUE\n", encoding="utf-8")
sys.path.insert(0, ".")

import uses_heavy

print(isinstance(uses_heavy.__dict__["heavy_mod"], types.LazyImportType))   # True: still a proxy
print("heavy_mod" in sys.lazy_modules, "heavy_mod" in sys.modules)          # True False
print(uses_heavy.value())                                                    # 'heavy_mod runs', then 42
print(type(uses_heavy.__dict__["heavy_mod"]).__name__)                       # module: replaced
```

- `sys.lazy_modules` holds the names of modules imported lazily and not yet loaded.
- Assigning the name to another global (`alias = heavy_mod`) loads it: loading the name for the assignment is a use.
- The module still loads only once, through the normal machinery; `sys.modules` and module-level code behave exactly as with an eager import, only later.

## 3. Where lazy imports are allowed

The keyword works only for imports that run once at module level, where deferring is safe; everywhere else it is a compile-time `SyntaxError`.

| Place | `lazy import` |
|---|---|
| module top level | allowed |
| inside `if`, including `if TYPE_CHECKING:` | allowed |
| inside `with` at module level | allowed |
| inside a function or method | `SyntaxError: lazy import not allowed inside functions` |
| inside a class body | `SyntaxError: lazy import not allowed inside classes` |
| inside `try` / `except` | `SyntaxError: lazy import not allowed inside try/except blocks` |
| `lazy from x import *` | `SyntaxError: lazy from ... import * is not allowed` |

Inside functions a plain import is already lazy in effect: it runs only when the function runs.

## 4. Errors move to the first use

A lazy import of a missing module succeeds; the error comes at the first use, as `ModuleNotFoundError` (an `ImportError`) raised where the name is touched, chained from an `ImportError` whose traceback points at the `lazy import` line.

```python
lazy import module_that_is_not_installed


def report() -> str:
    try:
        return module_that_is_not_installed.render()
    except ImportError as exc:                       # ModuleNotFoundError is a subclass
        return f"optional feature unavailable: {exc}; cause: {exc.__cause__}"


print(report())
```

- Optional dependencies keep the eager pattern `try: import x / except ImportError: x = None`; a `try` block cannot contain a lazy import anyway.
- A broken plugin or a typo in a module name now fails later and further from its cause; run the test suite with lazy imports, and keep a smoke test that touches each lazily imported module.

## 5. Code that also runs on 3.14: __lazy_modules__

A module that must run on 3.14 or older cannot use the new syntax. It lists module names in `__lazy_modules__` instead and keeps ordinary import statements: Python 3.15 treats those imports as lazy, older versions ignore the list.

```python
import sys

__lazy_modules__ = ["decimal", "statistics"]

import decimal                                       # lazy on 3.15, eager on 3.14
from statistics import fmean                         # lazy on 3.15, eager on 3.14

print(sys.version_info[:2], "decimal" in sys.modules)
print(fmean([1, 2, 3]), decimal.Decimal("0.1"))
```

The list must be assigned at module level before the imports it names. Libraries that support several Python versions use this form until 3.15 is their minimum.

## 6. Global lazy mode and the filter

Global mode makes every eligible top-level import lazy without changing code; it is the quickest way to measure what lazy imports would save. Imports in `try` blocks, functions and classes stay eager.

| Setting | Effect |
|---|---|
| `python -X lazy_imports=all` or `PYTHON_LAZY_IMPORTS=all` | all eligible imports lazy |
| `-X lazy_imports=normal` (default) | only `lazy` imports and `__lazy_modules__` |
| `sys.set_lazy_imports("all")` / `sys.get_lazy_imports()` | the same at run time, for imports that run after the call |
| `sys.set_lazy_imports_filter(fn)` | `fn(importer, name, fromlist)` returns `False` to keep an import eager |

```python
import sys


def keep_eager(importer: str, name: str, fromlist: object) -> bool:
    return name not in {"readline", "my_app.plugins"}    # modules imported for their side effects


sys.set_lazy_imports_filter(keep_eager)
sys.set_lazy_imports("all")
import readline                                          # eager: the filter said so
import csv                                               # lazy
print("readline" in sys.modules, "csv" in sys.modules)  # True False
sys.set_lazy_imports("normal")
sys.set_lazy_imports_filter(None)
```

Global mode is for applications and experiments; a library should never switch it on, because it changes the behaviour of every other module in the process.

## 7. Imports that must stay eager

Some imports are made for what they do when they run, not for the name they bind; deferring them changes behaviour silently.

- Plugin registration: a module whose import registers handlers, codecs, converters or entry points with a registry.
- Monkeypatching and instrumentation: `gevent.monkey`, OpenTelemetry auto-instrumentation, `faulthandler` set-up modules.
- `readline` (it changes `input()`), `sitecustomize`-style modules, and modules that read environment variables at import to configure themselves.
- Imports that must fail at start-up: a required dependency should crash the program at launch, not on the first click.

Leave these as plain imports, and exclude them with a filter when global mode is used.

## 8. Older ways to defer imports

Before 3.15, imports were deferred with a function-level import, with `importlib.util.LazyLoader`, or with a module-level `__getattr__` (PEP 562). They still work and are the way to support older Pythons.

```python
import importlib.util
import sys


def lazy_module(name: str):
    """Return a module object that loads on first attribute access (any Python 3)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.find_spec(name)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(name)
    loader = importlib.util.LazyLoader(spec.loader)
    spec.loader = loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


statistics = lazy_module("statistics")
print(statistics.median([3, 1, 2]))                      # the module body runs here
```

| Way | Works on | Trade-off |
|---|---|---|
| `lazy import` | 3.15+ | clean syntax; module level only |
| `__lazy_modules__` | 3.15 lazy, older eager | no syntax change; a string list to keep in sync |
| import inside the function | every version | repeated in each function; hides dependencies |
| `importlib.util.LazyLoader` | every version | no `from x import y`; a missing module fails at `find_spec`, a broken one at first use |
| module `__getattr__` | 3.7+ | for a package's own submodules and deprecations; written by hand |

When 3.15 becomes the minimum, replace function-level imports of heavy modules with `lazy import` at the top, so the dependencies are visible again.
