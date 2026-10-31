---
name: python-import-system
description: Explains and fixes how Python finds and loads code - modules, packages, __init__.py and namespace packages, absolute versus relative imports, sys.path and why not to edit it, python -m, circular imports, import-time cost, explicit lazy imports with the lazy keyword (PEP 810, Python 3.15) and __lazy_modules__, importlib.import_module, importlib.resources for package data, importlib.metadata for versions and entry points, plugin systems on entry points, and __all__. Use when an import fails with ModuleNotFoundError or "partially initialized module", a script works from one folder but not another, start-up is slow because of imports, a package needs data files or its own version, or an app should load plugins.
metadata:
  tags:
  - python
  - imports
  - modules
  - packages
  - importlib
  - lazy-imports
  - entry-points
  - plugins
---

# The Python import system

`import x` looks for `x` along a search path, runs its code once, and caches the module object in `sys.modules`. Most import errors come from the search path (the wrong folder is on it, or a local file shadows a real module), from cycles, or from code that runs at import time. This skill explains the mechanics and the fixes. How to lay out packages and which way imports should point is in the python-code-design skill; building wheels with package data is in the python-packaging skill.

## Step 0: read the version and how the code runs

Import behaviour depends on the Python version and on how the program is started, so read both first.

```bash
uv run python -c "import sys; print(sys.version_info[:2]); print(*sys.path, sep='\n')"
grep -n "requires-python" pyproject.toml
```

| Version | Change that matters |
|---|---|
| 3.11 | `python -P` and `PYTHONSAFEPATH` keep the script's folder off `sys.path` |
| 3.12 | `imp` removed; `importlib.resources.files()` is the main resources API |
| 3.13 | `importlib.resources.read_text(anchor, *path)` and friends return; the error for a file that shadows a standard module names the file |
| 3.15 | `lazy import` and `lazy from ... import` (PEP 810); `-X lazy_imports=all` |

## Modules and packages

A module is one `.py` file (or a compiled extension); a package is a folder of modules. A regular package has an `__init__.py`, which runs when the package is first imported; a namespace package has none and may be spread across several folders on `sys.path`.

```python
import importlib
import sys
from pathlib import Path

root = Path("src_demo")
(root / "shop" / "billing").mkdir(parents=True, exist_ok=True)
(root / "shop" / "__init__.py").write_text("print('shop init runs once')\n", encoding="utf-8")
(root / "shop" / "billing" / "__init__.py").write_text("", encoding="utf-8")
(root / "shop" / "billing" / "invoice.py").write_text("TOTAL = 42\n", encoding="utf-8")
sys.path.insert(0, str(root))                      # only for this demo; see the sys.path section

import shop.billing.invoice                        # runs shop/__init__, billing/__init__, invoice
import shop.billing.invoice as again               # cached: nothing runs twice
print(again.TOTAL, "shop" in sys.modules, importlib.import_module("shop.billing.invoice") is again)
```

- Importing `a.b.c` imports `a`, then `a.b`, then `a.b.c`, running each `__init__.py` on the way.
- Keep `__init__.py` small: everything it imports is paid for by every user of any submodule.
- A folder without `__init__.py` silently becomes a namespace package. That is what you want for plugin namespaces shared by several distributions, and a source of confusing errors everywhere else (two folders with the same name merge). Give every regular package an `__init__.py`.

## Absolute and relative imports

An absolute import names the full path from the top package (`from shop.billing import invoice`); a relative import names it from the current package (`from . import invoice`, `from ..catalog import product`).

- Prefer absolute imports in applications; relative imports suit a library's internals, where the top-level name may be vendored.
- Relative imports work only inside a package: running a file directly (`python shop/billing/invoice.py`) gives `ImportError: attempted relative import with no known parent package`. Run it as a module instead.

## sys.path and why not to edit it

`sys.path` is the list of folders searched in order: first the script's folder (or the current folder for `-m` and `-c`), then `PYTHONPATH`, then the standard library, then `site-packages` of the active environment.

- Do not append to `sys.path` in code or set `PYTHONPATH` to make imports work. Install the project into its environment instead (`uv sync` installs it editable when `pyproject.toml` has `[build-system]`, which `uv init` writes since 0.12), so imports work the same from every folder, in tests and in production.
- A local file with a standard-library or package name (`random.py`, `json.py`, `requests.py`, `test.py`) shadows the real module because the script folder comes first. Since 3.13 the `AttributeError` says "consider renaming" and names the file.
- `python -P` (3.11+) leaves the script's folder off `sys.path`, which removes the shadowing risk for tools.

## Run code with python -m

`python -m package.module` runs a module by its import name: the current folder goes on `sys.path`, the package is imported properly, and relative imports work. A package runs as `python -m package` when it has a `__main__.py`.

```bash
uv run python -m shop.billing.invoice     # not: python shop/billing/invoice.py
uv run python -m shop                      # runs shop/__main__.py
```

- The module run with `-m` is named `__main__`; if other code imports it by its real name, it is imported a second time as a separate module with its own globals. Keep the entry logic in `__main__.py` or a `main()` function that the console script calls.

## Circular imports

A cycle (`a` imports `b` while `b` imports `a`) fails when the second module reads a name from the first before the first has finished running: `ImportError: cannot import name 'X' from partially initialized module 'a' (most likely due to a circular import)`.

```python
import sys
from pathlib import Path

pkg = Path("cycle_demo") / "app"
pkg.mkdir(parents=True, exist_ok=True)
(pkg / "__init__.py").write_text("", encoding="utf-8")
(pkg / "orders.py").write_text("from app.customers import Customer\nclass Order: ...\n", encoding="utf-8")
(pkg / "customers.py").write_text("from app.orders import Order\nclass Customer: ...\n", encoding="utf-8")
sys.path.insert(0, "cycle_demo")
try:
    import app.orders
except ImportError as exc:
    print(exc)
```

| Fix | When |
|---|---|
| move the shared type into a third module both import | the real fix: the cycle means a missing lower layer |
| `if TYPE_CHECKING:` import, and on 3.14+ plain annotations (they are lazy) | the import exists only for type hints |
| `import app.orders` and use `app.orders.Order` at call time | a stopgap: attribute access happens after both modules finished |
| `lazy from app.orders import Order` (3.15) | the name is used only inside functions; the import resolves on first use |

The python-code-design skill covers restructuring so cycles do not come back.

## Import-time cost

Everything at module level runs on import, so heavy imports and work at the top of a module slow down every command, test and worker start. Measure with `-X importtime`, which prints each module's own and cumulative microseconds.

```bash
uv run python -X importtime -c "import my_app.cli" 2> imports.log
sort -t'|' -k2 -n imports.log | tail -10      # biggest cumulative times last
```

- No work at import: no network, file or database access, no reading settings, no logging configuration. Do it in `main()`.
- Defer heavy optional imports (pandas, torch, matplotlib) to where they are used, with a function-level import or, on 3.15, a `lazy import`.
- The indented tree in the log shows which import pulled a heavy module in; fix the importer.

## Explicit lazy imports (Python 3.15)

`lazy import json` and `lazy from json import dumps` (PEP 810) bind the name to a lazy proxy; the module loads the first time the name is used, and is then replaced by the real object. Start-up pays only for what the run actually touches.

```python
import sys

lazy import tomllib                                  # nothing loaded yet
lazy from decimal import Decimal

print("tomllib" in sys.modules)                     # False
print(tomllib.loads("x = 1")["x"])                  # loads tomllib here
print("tomllib" in sys.modules, Decimal("1.10"))    # True 1.10
```

- Allowed only at module level: a `lazy import` inside a function, class, or `try`/`except` is a `SyntaxError`, and so is `lazy from x import *`. Inside `if` and `with` blocks it is allowed.
- An import error moves to the first use: `ModuleNotFoundError` is raised where the name is touched, chained to an `ImportError` that points at the `lazy import` line. Optional-dependency checks with `try: import x` stay eager.
- Code that must run on 3.14 too lists modules in `__lazy_modules__ = ["tomllib"]` and keeps plain `import tomllib`: 3.15 treats those imports as lazy, older versions ignore the list.
- `python -X lazy_imports=all` (or `PYTHON_LAZY_IMPORTS=all`) makes every eligible top-level import lazy, a quick way to measure the gain; imports in `try` blocks and functions stay eager.

Filters, migration from function-level imports and the traps are in [lazy-imports.md](references/lazy-imports.md).

## importlib.import_module for names known at run time

`importlib.import_module("pkg.mod")` imports a module whose name is a string, such as a backend chosen in config; never `__import__` or `exec`.

```python
import importlib

BACKENDS = {"json": "json", "toml": "tomllib"}      # allow-list: never import arbitrary user input


def load_backend(name: str):
    try:
        return importlib.import_module(BACKENDS[name])
    except KeyError:
        raise ValueError(f"unknown backend {name!r}; choose from {sorted(BACKENDS)}") from None


print(load_backend("toml").loads('a = "b"'))
```

## importlib.resources for package data

Data files shipped inside a package (templates, SQL, JSON schemas, icons) are read with `importlib.resources`, which works from a folder, a wheel, a zip and a frozen app; `Path(__file__).parent / "x"` breaks in the last two.

```python
from importlib.resources import as_file, files

schema = files("email") / "__init__.py"              # any package; use your own, e.g. files("my_app") / "schemas" / "order.json"
print(schema.name, len(schema.read_text(encoding="utf-8")) > 0)
with as_file(schema) as real_path:                   # a real file path for APIs that need one
    print(real_path.exists())
```

The data files must be included in the wheel; the build backend's include rules are in the python-packaging skill.

## importlib.metadata for versions and entry points

`importlib.metadata` reads the metadata of installed distributions: versions, dependencies and entry points, without importing the package.

```python
from importlib.metadata import PackageNotFoundError, entry_points, version

try:
    print("pip", version("pip"))
except PackageNotFoundError:
    print("pip is not installed in this environment")
print(sorted(ep.name for ep in entry_points(group="console_scripts"))[:5])
```

- A package's own `__version__` is best set from `version("my-app")` in `__init__.py` (one source of truth: `pyproject.toml`).
- The distribution name (`"pyyaml"`) can differ from the import name (`yaml`); `packages_distributions()` maps import names to distributions.

## Plugin systems with entry points

An entry point is a named reference (`module:attribute`) that a distribution declares in its metadata; an app discovers every installed plugin by group name, with no folder scanning and no config.

```toml
# the plugin's pyproject.toml
[project.entry-points."my_app.exporters"]
pdf = "my_app_pdf.exporter:PdfExporter"
```

```python
from importlib.metadata import entry_points


def load_exporters() -> dict[str, type]:
    found = {}
    for ep in entry_points(group="my_app.exporters"):
        try:
            found[ep.name] = ep.load()              # imports the plugin module only now
        except Exception as exc:                    # one broken plugin must not stop the app
            print(f"plugin {ep.name} from {ep.dist.name if ep.dist else '?'} failed: {exc}")
    return found


print(load_exporters())                             # {} until a plugin is installed
```

A complete runnable example with a fake installed plugin, and a plugin protocol check, is in [plugins-and-resources.md](references/plugins-and-resources.md).

## __all__ and the public names of a module

`__all__` lists the names `from module import *` exports, and tells type checkers and readers which names are public. Names starting with `_` are private by convention.

```python
# shop/billing/__init__.py: re-export the public API from submodules
from shop.billing.invoice import Invoice, issue
from shop.billing.tax import vat_for

__all__ = ["Invoice", "issue", "vat_for"]
```

Never use `import *` in application code; it hides where names come from and breaks when the other module adds a name.

## Verify

Prove imports by importing every module alone, from another folder, because a cycle can hide behind the order a test suite happens to use.

```bash
uv run python -c "import my_app, sys; print(my_app.__file__); print(sys.executable)"   # both under this project
uv run ruff check --select I,A005,TID .                # A005: a module named after a standard one
grep -rnE "sys\.path\.(insert|append)" src tests         # expect nothing
uv run python - <<'PY'
import pkgutil
import subprocess
import sys

import my_app

for info in pkgutil.walk_packages(my_app.__path__, "my_app."):
    done = subprocess.run([sys.executable, "-c", f"import {info.name}"], capture_output=True, text=True)
    if done.returncode:
        print(info.name, done.stderr.splitlines()[-1])  # expect nothing: each module imports on its own
PY
uv run python -X importtime -c "import my_app" 2>&1 | sort -t'|' -k2 -n | tail -3   # the slowest imports
```

For run-time names, test the allow-list with an attack case: `load_backend("os")` must raise `ValueError`.

## Anti-patterns

| Instead of | Do |
|---|---|
| `sys.path.append(...)` to fix an import | install the project into its environment |
| `python shop/billing/invoice.py` | `python -m shop.billing.invoice` |
| `Path(__file__).parent / "data.json"` | `importlib.resources.files` |
| a function-level import to hide a cycle | a lower module both sides import |
| `importlib.import_module(user_input)` | an allow-list of module names |
| `from module import *` | explicit names, or `__all__` for re-exports |
| a folder scan to find plugins | entry points |

## Pre-merge checklist

Imports are healthy when every item holds.

- [ ] The project is installed into its environment; no `sys.path` edits or `PYTHONPATH` to make imports work.
- [ ] Every regular package has `__init__.py`; no local file shadows a standard or third-party module name.
- [ ] Entry points run with `python -m` or an installed command, not as file paths inside the package.
- [ ] No import cycles; type-only imports sit under `TYPE_CHECKING`.
- [ ] Module level does no I/O; heavy optional imports are deferred (`lazy import` on 3.15).
- [ ] Package data is read with `importlib.resources`; versions come from `importlib.metadata`.
- [ ] Plugins are discovered by entry-point group, and a failing plugin is reported, not fatal.

## Gotchas

These are the import failures seen most often.

1. **`ModuleNotFoundError: No module named 'my_app'` in tests or CI, fine in the editor.** The project is not installed in that environment, or a different interpreter runs. `uv sync` installs the project only with `[build-system]`: a `uv init --no-package` or `--bare` project has none and gets its dependencies alone. Add one, or set `package = true` under `[tool.uv]`, then `uv sync` and `uv run pytest`.
2. **`AttributeError: module 'random' has no attribute 'randint'`.** A local `random.py` shadows the standard module. Rename it and delete its `__pycache__`.
3. **`ImportError: cannot import name 'X' from partially initialized module`.** A circular import. Move the shared piece to a lower module.
4. **`ImportError: attempted relative import with no known parent package`.** A module inside a package was run as a file. Use `python -m package.module`.
5. **A module's globals are different from what another module sees.** It was run as `__main__` and imported again by name. Keep entry logic out of importable modules.
6. **`FileNotFoundError` for a data file only in the built app.** The code used `__file__` paths, or the file is missing from the wheel. Use `importlib.resources` and check the wheel's contents.
7. **A `lazy import` raises `SyntaxError`.** It is inside a function, class or `try` block. Move it to module level, or use a function-level import there.

## References

The reference files hold the deeper material.

- [lazy-imports.md](references/lazy-imports.md): PEP 810 semantics, what triggers loading, `__lazy_modules__` for code that also runs on 3.14, global mode and filters, testing, and how it compares with function-level imports and `importlib.util.LazyLoader`
- [plugins-and-resources.md](references/plugins-and-resources.md): a runnable entry-point plugin system with a protocol check, resource folders and binary files, `importlib.metadata` queries, and module `__getattr__` for deprecations
- [import-machinery.md](references/import-machinery.md): `sys.modules`, finders and loaders, `__spec__`, reloading, import hooks, and isolating imports in tests

## See also

- `python-code-design`: package layout and the direction imports point
- `python-packaging`: package data, wheels and entry points
- `python-performance`: start-up time and profiling
- `uv-projects`: installing the project into its environment
