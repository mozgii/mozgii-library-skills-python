---
title: Python import machinery for application developers
author: MOZGII, Inc.
lang: en
---

# Python import machinery for application developers

The parts of the import machinery an application developer meets: the `sys.modules` cache, finders and specs, the module attributes `__spec__`, `__file__` and `__package__`, reloading, a small import hook, simulating a missing dependency in tests, and bytecode caches.

## Contents

This reference has 6 sections.

1. The sys.modules cache
2. Checking for a module without importing it
3. Module attributes: __name__, __spec__, __file__
4. Reloading and why it rarely works
5. Import hooks and blocking a module in tests
6. Bytecode caches and __pycache__

## 1. The sys.modules cache

`sys.modules` maps every imported module name to its module object; `import` checks it first, so a module's code runs only once per process, however many files import it.

```python
import sys

import json

print(sys.modules["json"] is json)                       # the cached object
print(len(sys.modules) > 20, "json.decoder" in sys.modules)   # submodules imported by json are cached too
```

- A module that failed halfway through its import is removed from `sys.modules`, so the next import runs it again from the start.
- Deleting an entry (`del sys.modules["x"]`) makes the next import run the module again, creating a second module object while old references still point at the first. Use it only in tests, and clean up.
- Setting `sys.modules["x"] = None` makes `import x` raise `ModuleNotFoundError`; section 5 uses that in tests.

## 2. Checking for a module without importing it

`importlib.util.find_spec(name)` asks the finders whether a module exists and where, without running it; it returns `None` when the module is not found.

```python
import importlib.util


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:                          # the parent package is missing: "a.b" without "a"
        return False


print(has_module("json"), has_module("not_installed_xyz"), has_module("not_installed_xyz.sub"))
print(importlib.util.find_spec("json").origin.endswith("__init__.py"))
```

- For a dotted name, `find_spec` imports the parent packages (running their `__init__.py`) to find the child.
- To check a feature, prefer trying the import where it is used (`try: import x except ImportError`); `find_spec` is for reporting what is available, such as a diagnostics command.

## 3. Module attributes: __name__, __spec__, __file__

Every module carries attributes that say how it was imported; code that computes paths or names from them must handle the cases where they are missing.

| Attribute | Value | Missing or surprising when |
|---|---|---|
| `__name__` | `"shop.billing"` | `"__main__"` for the script or `-m` module |
| `__spec__` | the `ModuleSpec` used to load it | `None` for the script run as a file path |
| `__spec__.name` | the real import name | gives `"shop.billing"` even when `__name__` is `"__main__"` under `-m` |
| `__file__` | path of the source | can be missing for namespace packages, frozen apps and some zip imports |
| `__package__` | the parent package name | `""` for top-level modules |
| `__path__` | search locations of a package | only on packages |

```python
import json
import sys

print(json.__spec__.name, json.__spec__.origin is not None, hasattr(json, "__path__"))
main = sys.modules["__main__"]
print(main.__name__, getattr(main, "__spec__", None))   # '__main__' None when run as a file
```

Use `importlib.resources` for data files instead of building paths from `__file__`; it works when `__file__` does not.

## 4. Reloading and why it rarely works

`importlib.reload(module)` runs a module's code again in the same module object. Objects created before the reload keep the old classes and functions, and every `from module import name` elsewhere keeps the old value, so a reloaded program is a mix of old and new code.

```python
import importlib
import sys
from pathlib import Path

Path("settings_mod.py").write_text("LIMIT = 1\n", encoding="utf-8")
sys.path.insert(0, ".")
import settings_mod
from settings_mod import LIMIT

Path("settings_mod.py").write_text("LIMIT = 20\n", encoding="utf-8")   # a different size: see section 6
importlib.invalidate_caches()
importlib.reload(settings_mod)
print(settings_mod.LIMIT, LIMIT)                          # 20 1: the from-import kept the old value
```

For a development loop, restart the process (watchfiles' `run_process`, or the framework's reloader) instead of reloading modules. Reload is fine in a REPL or notebook for one self-contained module.

## 5. Import hooks and blocking a module in tests

A finder on `sys.meta_path` sees every import before the standard finders. Real hooks are rare in applications; the common need is a test that runs code as if an optional dependency were not installed.

```python
import importlib.abc
import sys


class Blocker(importlib.abc.MetaPathFinder):
    """Make the named top-level modules look uninstalled."""

    def __init__(self, *names: str) -> None:
        self.names = set(names)

    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] in self.names:
            raise ModuleNotFoundError(f"No module named {fullname!r} (blocked)", name=fullname)
        return None                                          # let the normal finders handle it


def optional_speedups() -> str:
    try:
        import sqlite3                                       # stands in for an optional dependency
    except ImportError:
        return "fallback"
    return "fast"


sys.modules.pop("sqlite3", None)
sys.meta_path.insert(0, blocker := Blocker("sqlite3"))
try:
    print(optional_speedups())                               # fallback
finally:
    sys.meta_path.remove(blocker)
print(optional_speedups())                                   # fast
```

With pytest, the shorter form is `monkeypatch.setitem(sys.modules, "sqlite3", None)`: the import raises `ModuleNotFoundError` and pytest restores the entry after the test. Remove already-imported submodules too (`sqlite3.dbapi2`) if the code imports them.

## 6. Bytecode caches and __pycache__

Python caches compiled bytecode in `__pycache__/<module>.cpython-315.pyc` and checks it against the source's modification time and size on each import, so edits are picked up automatically.

- The check compares the source's modification time in whole seconds and its size, so an edit that keeps the size and lands in the same second as the last compile is missed. Tools that generate and import code quickly hit this; `importlib.invalidate_caches()` does not help, a different size or hash-based pycs do.
- A `.pyc` in `__pycache__` is used only when its source exists. Deleting or renaming a module makes it unimportable even if its cache remains; the stale cache is harmless but may confuse searches, so `git clean -X` or `find . -name __pycache__ -exec rm -rf {} +` tidies up.
- Builds that need reproducible output use hash-based pycs (`python -m compileall --invalidation-mode checked-hash`), which do not depend on file times.
- `PYTHONDONTWRITEBYTECODE=1` or `python -B` stops writing caches, useful on read-only or network file systems; imports are then slower on each start.
- `PYTHONPYCACHEPREFIX=/some/folder` keeps all caches out of the source tree, for example in a container with a read-only source mount.
