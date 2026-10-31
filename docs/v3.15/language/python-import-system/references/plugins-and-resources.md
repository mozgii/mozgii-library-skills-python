---
title: Plugins, package data and metadata in Python
author: MOZGII, Inc.
lang: en
---

# Plugins, package data and metadata in Python

A runnable plugin system built on entry points, package data read with `importlib.resources`, distribution metadata from `importlib.metadata`, and a module-level `__getattr__` for deprecated names and lazy submodules.

## Contents

This reference has 5 sections.

1. A plugin system on entry points
2. Declaring and testing plugins
3. Package data with importlib.resources
4. Querying installed distributions
5. Module __getattr__ for deprecations and lazy names

## 1. A plugin system on entry points

Entry points are read from the `entry_points.txt` file in each installed distribution's `.dist-info` folder. This example creates one by hand on a temporary search path, so the discovery code runs exactly as it would with a real installed plugin.

```python
import sys
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol, runtime_checkable

site = Path("fake_site")
(site / "acme_pdf").mkdir(parents=True, exist_ok=True)
(site / "acme_pdf" / "__init__.py").write_text(
    "class PdfExporter:\n    suffix = '.pdf'\n    def export(self, doc: str) -> bytes:\n        return doc.encode()\n",
    encoding="utf-8",
)
info = site / "acme_pdf-1.0.dist-info"
info.mkdir(exist_ok=True)
(info / "METADATA").write_text("Metadata-Version: 2.4\nName: acme-pdf\nVersion: 1.0\n", encoding="utf-8")
(info / "entry_points.txt").write_text("[my_app.exporters]\npdf = acme_pdf:PdfExporter\n", encoding="utf-8")
sys.path.insert(0, str(site))                     # a real plugin is installed into the environment


@runtime_checkable
class Exporter(Protocol):
    suffix: str

    def export(self, doc: str) -> bytes: ...


def load_exporters(group: str = "my_app.exporters") -> dict[str, Exporter]:
    found: dict[str, Exporter] = {}
    for ep in entry_points(group=group):
        try:
            plugin = ep.load()()                   # import the module, get the class, instantiate it
        except Exception as exc:
            print(f"skipping plugin {ep.name!r} ({ep.value}): {exc}")
            continue
        if not isinstance(plugin, Exporter):
            print(f"skipping plugin {ep.name!r}: does not implement Exporter")
            continue
        found[ep.name] = plugin
    return found


exporters = load_exporters()
print({name: (p.suffix, p.export("hi")) for name, p in exporters.items()})
print([(ep.name, ep.dist.name if ep.dist else None) for ep in entry_points(group="my_app.exporters")])
```

- `entry_points(group=...)` reads metadata only; plugin code runs at `ep.load()`, so listing plugins in a settings screen costs nothing.
- Two distributions may declare the same name in a group; decide which wins (first, last, or an error) and say so in the docs.
- Name groups after your package (`my_app.exporters`), never a generic word another project might use.

## 2. Declaring and testing plugins

A plugin distribution declares its entry points in `pyproject.toml`; the build backend writes them to `entry_points.txt` in the wheel.

```toml
[project]
name = "acme-pdf"
version = "1.0.0"
dependencies = ["my-app>=2.3"]

[project.entry-points."my_app.exporters"]
pdf = "acme_pdf:PdfExporter"
```

- Entry points change only when the distribution is reinstalled; after editing them in a development checkout, run `uv sync` (or `uv pip install -e .`) again.
- Test discovery in the host app with a fake distribution like the one above, or by installing a tiny plugin package from the test suite's fixtures folder.
- Console commands are entry points too: `[project.scripts]` is the group `console_scripts`.

## 3. Package data with importlib.resources

`importlib.resources.files(anchor)` returns a `Traversable` for a package's folder; join names with `/` and read with `read_text` or `read_bytes`. Subfolders of data do not need to be packages.

```python
import sys
from importlib.resources import as_file, files, read_text
from pathlib import Path

pkg = Path("res_demo") / "reports"
(pkg / "templates").mkdir(parents=True, exist_ok=True)
(pkg / "__init__.py").write_text("", encoding="utf-8")
(pkg / "templates" / "invoice.txt").write_text("Invoice {number}\n", encoding="utf-8")
(pkg / "templates" / "logo.bin").write_bytes(b"\x89PNG")
sys.path.insert(0, "res_demo")

templates = files("reports") / "templates"
print(sorted(item.name for item in templates.iterdir()))
print((templates / "invoice.txt").read_text(encoding="utf-8").format(number=7), end="")
print((templates / "logo.bin").read_bytes()[:4])
print(read_text("reports", "templates", "invoice.txt", encoding="utf-8"), end="")   # 3.13 functional form
with as_file(templates / "logo.bin") as real:            # a temporary copy if the package is zipped
    print(real.suffix, real.exists())
```

- `as_file` gives a real file system path for APIs that need one (a C library, a subprocess); the path is valid only inside the `with` block.
- Never write into package data: an installed package may be read-only. Copy defaults to the user's data folder (platformdirs) on first run.
- The files must be in the wheel. Build it and list it (`unzip -l dist/*.whl`) before trusting the code.

## 4. Querying installed distributions

`importlib.metadata` answers questions about installed distributions: their version, requirements, files and which import names they provide.

```python
from importlib.metadata import distribution, packages_distributions, requires, version

print(version("pip"))
dist = distribution("pip")
print(dist.metadata["Name"], dist.metadata["Requires-Python"])
print(requires("pip"))                                   # None or a list of requirement strings
print(packages_distributions().get("pip"))               # import name -> distribution names
print(sum(1 for f in (dist.files or []) if f.suffix == ".py"), "python files")
```

- Use the distribution name (`"PyYAML"`, case and `-`/`_` insensitive) for `version()`, not the import name (`yaml`).
- `version()` raises `PackageNotFoundError` when a package runs from a source folder that was never installed; catch it and fall back to `"0+unknown"`.

## 5. Module __getattr__ for deprecations and lazy names

A module-level `__getattr__(name)` (PEP 562) is called for names the module does not define. It renames things without breaking callers, and loads heavy submodules on first access on any Python version.

```python
import importlib
import sys
import warnings
from pathlib import Path

pkg = Path("getattr_demo") / "geo"
pkg.mkdir(parents=True, exist_ok=True)
(pkg / "heavy.py").write_text("print('geo.heavy loaded')\nRADIUS_KM = 6371\n", encoding="utf-8")
(pkg / "__init__.py").write_text('''
import importlib
import warnings

def distance_km(a, b):
    return abs(a - b)

_RENAMED = {"dist": "distance_km"}
_LAZY = {"heavy"}

def __getattr__(name):
    if name in _RENAMED:
        warnings.warn(f"geo.{name} is deprecated; use geo.{_RENAMED[name]}", DeprecationWarning, stacklevel=2)
        return globals()[_RENAMED[name]]
    if name in _LAZY:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def __dir__():
    return sorted([*globals(), *_RENAMED, *_LAZY])
''', encoding="utf-8")
sys.path.insert(0, "getattr_demo")

import geo

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    print(geo.dist(3, 10), caught[0].message)
print(geo.heavy.RADIUS_KM)
```

- Always end `__getattr__` with `AttributeError`, or `hasattr` and `getattr(mod, name, default)` break.
- `stacklevel=2` points the warning at the caller's line, which is what the person must change.
