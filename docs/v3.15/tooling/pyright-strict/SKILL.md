---
name: pyright-strict
description: Sets up and enforces Pyright strict mode in a Python project and fixes its errors - configuration in pyproject.toml or pyrightconfig.json for uv and pip environments, run and CI commands, Pylance and basedpyright, TYPE_CHECKING imports, each strict-mode error code and its fix, Any versus Unknown, typed boundaries for JSON and untyped libraries, local stubs, ignore comments, and a root-first method with a grouping script for hundreds of cascading reportUnknown errors. Use when adding type checking to a project, writing code that must pass pyright or Pylance strict, when pyright reports reportUnknownMemberType, reportMissingTypeArgument, reportReturnType or reportArgumentType, or when a type error survives two fixes or floods many files.
metadata:
  tags:
  - python
  - pyright
  - pylance
  - basedpyright
  - typing
  - strict
  - static-analysis
  - uv
---

# Pyright strict mode

Pyright strict mode (`typeCheckingMode = "strict"`) turns every unknown type into an error, so type information flows through the whole codebase instead of fading into `Unknown`. This skill sets it up, runs it, maps each common error code to its fix, and fixes errors that cascade across files from their root.

## When to use strict mode

Strict mode pays off in code that lives long and is read by many people or agents.

| Project | Mode |
|---|---|
| a new library, service, worker or CLI | `strict` from the first commit |
| an existing untyped codebase | `standard` globally, `strict` per directory through `strict = ["src/my_app/core"]`, widened over time |
| notebooks, one-off scripts | `standard` or none |

Never lower the mode to make errors go away. Fix the types, or narrow the scope with `strict = [...]` while migrating.

## Step 0: read the installed versions

Pyright adds checks often, so read the version the project runs before trusting any rule description.

```bash
uv run pyright --version                 # e.g. pyright 1.1.414
uv run python -c "import sys; print(sys.version)"
grep -nA8 "tool.pyright" pyproject.toml 2>/dev/null; cat pyrightconfig.json 2>/dev/null
```

The PyPI `pyright` package is a wrapper that downloads the matching Node-based release on first run; it needs network once, then caches. `basedpyright` (PyPI, no Node needed) is a fork with extra rules and the same config keys under `[tool.basedpyright]`.

## Configuration

Pyright reads `pyrightconfig.json` first, then `[tool.pyright]` in `pyproject.toml`. Use one of them, never both.

```toml
# pyproject.toml
[tool.pyright]
include = ["src", "tests"]
exclude = ["**/__pycache__", "build", "dist"]
pythonVersion = "3.12"                 # the lowest version you support
typeCheckingMode = "strict"
venvPath = "."
venv = ".venv"                         # where uv sync puts the environment
stubPath = "typings"                   # local stubs for untyped packages
reportUnnecessaryTypeIgnoreComment = "error"
reportImplicitOverride = "error"       # require @override on overriding methods
```

The same settings as `pyrightconfig.json`:

```json
{
  "include": ["src", "tests"],
  "pythonVersion": "3.12",
  "typeCheckingMode": "strict",
  "venvPath": ".",
  "venv": ".venv",
  "stubPath": "typings",
  "reportUnnecessaryTypeIgnoreComment": "error"
}
```

- `pythonVersion` is the minimum supported version, not the one installed: pyright then flags syntax and stdlib APIs newer than that.
- `venvPath` + `venv` tell pyright where installed packages live. Without them it uses whatever `python` is on `PATH` and reports `reportMissingImports` for packages that are installed in `.venv`.
- Rules can be raised above strict (`reportImplicitOverride`, `reportUnnecessaryTypeIgnoreComment`, `reportPropertyTypeMismatch`) but strict mode itself is never lowered.
- Per-folder settings go in `executionEnvironments`, for example a `scripts/` folder with its own `extraPaths`.

## Running pyright

Run pyright through the project environment so it sees the same packages as the code.

```bash
uv add --dev pyright                     # or: pip install pyright inside the venv
uv run pyright                           # whole project: catches cross-file type flow
uv run pyright src/my_app/models.py      # one file, fast iteration
uv run pyright --outputjson > pyright.json   # machine-readable, for scripts/pyright_summary.py
uv run pyright --verifytypes my_app --ignoreexternal   # a library's public API completeness score
uv run pyright --warnings                # CI: exit non-zero on warnings too
```

Each diagnostic line reads `file:line:col - error: message (ruleName)`. The rule name in parentheses is what to look up below.

### The loop

Write code, run pyright, fix every error, run again, until it prints `0 errors`. Show the final output. When the same error survives two fix attempts, or more than about 20 `reportUnknown*` errors appear at once, stop fixing symptoms and find the root (next section).

### Many errors at once: fix the root first

A cascade is one missing type deep in a helper that shows up as dozens of errors in every file using it. Casting each symptom hides it; typing the root clears them all.

1. Group the errors: `uv run pyright --outputjson > pyright.json && python3 scripts/pyright_summary.py pyright.json --limit 5`. Without the script (a search-only install has no `scripts/` folder), this command gives the same two rankings:

   ```bash
   uv run pyright --outputjson | python3 -c '
   import collections, json, sys
   diags = json.load(sys.stdin)["generalDiagnostics"]
   for key, label in (("rule", "by rule"), ("file", "by file")):
       print(label, collections.Counter(d.get(key, "-") for d in diags).most_common(10))'
   ```

2. Take one error from the largest group and follow the `Unknown` back through definitions (`reveal_type(x)`, go to definition) to the first symbol with a missing annotation or an untyped package.
3. Write the chain down, fix that leaf (annotation, `TypedDict`, stub), run pyright again, and only then fix what remains one level up.

The full method, the root-cause table and the notes file: [cascading-errors.md](references/cascading-errors.md).

### Pylance in VS Code

Pylance uses the same engine. Make the editor agree with CI:

```json
// .vscode/settings.json
{
  "python.analysis.typeCheckingMode": "strict",
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python"
}
```

A `pyrightconfig.json` or `[tool.pyright]` in the workspace overrides these settings, which is the reason to keep the config in the repo.

## Imports for types only

Type-only imports keep runtime imports light and break import cycles.

```python
from __future__ import annotations      # only needed when supporting Python 3.13 or older

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    import numpy as np                   # heavy, only for annotations
    from my_app.models import Order      # would be a cycle at runtime


def first(orders: Iterator[Order]) -> Order | None:
    return next(orders, None)


def mean_total(totals: np.ndarray) -> float:
    return float(totals.mean())
```

- Python 3.14 evaluates annotations lazily (PEP 649), so names imported under `TYPE_CHECKING` work in annotations without the `__future__` import. Keep the future import while you support 3.13 or older.
- Anything used at runtime (`isinstance`, a default value, a base class, pydantic and dataclass field types that are inspected at runtime) must be a real import.
- Ruff's `TC` rules move imports into or out of `TYPE_CHECKING` automatically (`ruff check --select TC --fix`).

## Any versus Unknown

`Unknown` is pyright's name for a type it could not infer; strict mode reports it. `Any` is a type you wrote, or one a library declared, and strict mode accepts it silently.

```python
import json

data = json.load(open("config.json"))     # json.load returns Any: no error, and no checking either
port = data["port"] + 1                   # also Any; a typo in "port" is never caught
```

This makes `Any` from `json`, `pickle`, `yaml.safe_load`, `requests.Response.json()` and dynamic APIs the real gap in a strict codebase. Close it at the boundary: validate into a `TypedDict`, a dataclass or a pydantic model right where the data enters (the `python-data-models` skill), and pass typed values inward.

## Strict-mode errors and their fixes

These are the error codes strict mode reports most, each with the fix that keeps types honest.

### reportReturnType: all paths must return

`Function with declared return type "str" must return value on all code paths` means one branch falls through to an implicit `None`.

```python
def get_name(item: dict[str, str], key: str) -> str:
    if key in item:
        return item[key]
    raise KeyError(key)                   # or declare -> str | None and return None
```

### reportMissingTypeArgument and reportUnknownVariableType: type every generic

An empty `list` or `dict` has no element type to infer, so pyright reports `list[Unknown]`.

```python
from collections.abc import Callable

clips: list[str] = []
index: dict[str, int] = {}
handlers: dict[str, Callable[[bytes], None]] = {}
```

### reportUnknownParameterType and reportMissingParameterType: annotate every parameter

Every parameter and every return type of a function you write needs an annotation; defaults do not count.

```python
def scale(value: float, factor: float = 1.0) -> float:
    return value * factor
```

### reportUnknownMemberType: a library returns Unknown

When a third-party function has no annotations, its result is `Unknown`. Fix it at the call site once, not everywhere it spreads:

```python
from typing import cast

import legacy_client                       # an untyped package

raw = cast("list[dict[str, object]]", legacy_client.fetch_rows())   # one cast at the boundary
```

Better still, add a stub (see "Untyped libraries") so every call site is typed.

### reportArgumentType: the value may not fit the parameter

An `int | None` passed where `int` is required needs narrowing, not a cast:

```python
def total(xs: list[int | None]) -> int:
    return sum(x for x in xs if x is not None)
```

### reportOptionalMemberAccess and reportOptionalSubscript: guard None first

```python
import re


def read_version(text: str) -> str:
    match = re.search(r"version: (\S+)", text)
    if match is None:
        raise ValueError(f"no version in {text!r}")
    return match.group(1)
```

### reportAttributeAccessIssue: the attribute is not declared

Declare attributes in the class body or `__init__`, or narrow a union with `isinstance` before reaching for an attribute only one member has.

### reportPrivateUsage, reportUnusedImport, reportUnusedVariable

Strict mode also reports `_private` names used from another module, unused imports and unused locals. Rename to public, delete, or prefix an intentionally unused variable with `_`.

### reportIncompatibleMethodOverride: overrides must accept what the base accepts

An override may widen parameter types and narrow the return type, never the reverse. Mark overrides with `@override` (`typing.override`, Python 3.12+) so renaming the base method breaks loudly.

## Structured data: TypedDict, dataclass, Protocol

Strict mode works best when structured values have declared shapes instead of `dict[str, Any]`.

```python
from typing import NotRequired, Protocol, TypedDict


class RenderJob(TypedDict):
    preset: str
    output_dir: str
    chunk_size: int
    note: NotRequired[str]                # optional key


class SupportsRead(Protocol):             # structural: any object with this method fits
    def read(self, n: int = -1, /) -> bytes: ...
```

- `TypedDict` for JSON-shaped dicts that cross an API; `@dataclass(slots=True, frozen=True)` for values your own code creates; `Protocol` for duck-typed parameters.
- `total=False` or `NotRequired[...]` for optional keys; `ReadOnly[...]` (3.13+, or `typing_extensions`) for keys a function must not change.
- Choose between them with the `python-data-models` skill; the full type system is in the `python-typing` skill.

## cast() discipline

`cast()` tells the checker to trust you; it does nothing at runtime. Use it rarely and on purpose.

| Use cast | Do not use cast |
|---|---|
| a library returns `Unknown` or `object` and the type is documented | pyright already narrows after `isinstance`, `is None`, `in` or `assert` |
| after a runtime check pyright cannot follow | to silence an error you do not understand |
| once, at a boundary function | at every call site |

Prefer `isinstance` narrowing or a `TypeIs` function (the `python-typing` skill), which check at runtime too.

## Untyped libraries: stubs

A package without type hints makes everything it returns `Unknown`. Find or write stubs once instead of casting everywhere.

1. Look for published stubs: `types-<package>` (typeshed) or `<package>-stubs` on PyPI, then `uv add --dev types-<package>`.
2. Generate a draft: `uv run pyright --createstub <package>` writes `typings/<package>/`; trim it to what you use and fix the types.
3. Or write the minimum by hand:

```python
# typings/legacy_client/__init__.pyi
class Client:
    def __init__(self, url: str, timeout: float = ...) -> None: ...
    def fetch_rows(self) -> list[dict[str, object]]: ...
```

4. Point `stubPath` at `typings` (the default) and run pyright again.

Stubs for a package that is only present in some environments (a plugin host's embedded module, an optional extra) still work: pyright reads the `.pyi` even when the module is not installed.

## Ignore comments

An ignore comment is a recorded exception, so it names the rule and explains itself.

```python
from my_app.plugins import registry   # filled by a plugin loader, typed as an untyped dict

value = registry["csv"]  # pyright: ignore[reportUnknownVariableType]  # the loader decides the value type
```

- Use `# pyright: ignore[ruleName]`, never a bare `# type: ignore`; set `reportUnnecessaryTypeIgnoreComment = "error"` so stale ignores fail.
- `# pyright: basic` at the top of a file lowers one generated or vendored file; never add it to hand-written code.
- Never add `"reportUnknownVariableType": false` or similar to the config to clear a backlog.

## Verify

Prove that the gate catches a planted bug, then that the project is clean and every suppression names its rule.

```bash
uv run pyright --version                            # matches the pin in the dev group
grep -n "typeCheckingMode" pyproject.toml pyrightconfig.json 2>/dev/null    # strict
printf 'def f(x: int) -> str:\n    return x\n' > src/probe_bug.py
uv run pyright src/probe_bug.py; echo "exit $?"     # expect reportReturnType and exit 1
rm src/probe_bug.py
uv run pyright; echo "exit $?"                      # expect "0 errors" and exit 0
grep -rnE "#\s*(type|pyright): ignore\s*($|#)" src tests   # expect nothing: every ignore names a rule
```

## Anti-patterns

| Instead of | Do |
|---|---|
| lowering `typeCheckingMode` to clear a backlog | `strict = ["src/my_app/core"]`, widened over time |
| a bare `# type: ignore` | `# pyright: ignore[ruleName]` with the reason |
| `cast()` at every call site | one typed boundary, or a stub |
| `dict[str, Any]` across an API | `TypedDict`, a dataclass or a pydantic model |
| fixing symptoms in a cascade | the root first, from the grouped report |
| `pyright` from a global install | `uv run pyright`, pinned in the dev group |
| `"reportUnknownVariableType": false` in config | type the source of the `Unknown` |

## Checklist

A change is done when every item holds.

- [ ] `[tool.pyright]` or `pyrightconfig.json` exists with `typeCheckingMode = "strict"`, never lowered.
- [ ] `uv run pyright` prints `0 errors`; the output was shown.
- [ ] Every new function has annotated parameters and return type.
- [ ] Every empty container and generic carries type arguments.
- [ ] Data from JSON, files or untyped libraries is validated or cast once, at the boundary.
- [ ] Type-only imports sit under `TYPE_CHECKING`; runtime-used names are real imports.
- [ ] Every ignore names its rule and says why.

## Gotchas

These are the traps that make pyright output confusing.

1. **Everything is `reportMissingImports`.** pyright is not looking at the project's venv. Set `venvPath`/`venv`, or run it with `uv run`.
2. **Clean in the editor, red in CI** (or the reverse). Different pyright versions or a different interpreter. Pin pyright in the dev group and keep the config in the repo.
3. **No errors on code that is obviously wrong.** The value is `Any`, not `Unknown` (JSON, dynamic APIs). Validate at the boundary.
4. **A runtime `NameError` for an annotation.** A name imported under `TYPE_CHECKING` is used at runtime (`isinstance`, pydantic field type, dataclass default). Make it a real import.
5. **Hundreds of errors after upgrading pyright.** New checks landed. Pin the version, fix in batches by rule (`--outputjson` and `scripts/pyright_summary.py`, or the inline grouping command above), then move the pin.
6. **`pyright` first run hangs in CI.** The PyPI wrapper downloads the engine. Cache `~/.cache/pyright-python` or use `basedpyright`, which ships the engine in the wheel.

## References

The reference file and script cover large backlogs of errors.

- [cascading-errors.md](references/cascading-errors.md): mapping a chain of `Unknown` types to its root, fixing bottom-up, common roots, a notes file
- `scripts/pyright_summary.py`: groups `pyright --outputjson` output by rule and file (`--rule`, `--limit`, `-` for stdin)

## See also

- `python-typing`: what to write in annotations, and mypy
- `ty`: Astral's checker, and when it fits instead
- `python-data-models`: typed shapes for data from outside
- `pre-commit`: pyright as a local pre-push hook
- `ruff`: the `TC` and `ANN` rules beside the checker
