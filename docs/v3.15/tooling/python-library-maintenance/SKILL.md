---
name: python-library-maintenance
description: Maintains a published Python library without breaking its users - knowing the public API surface, semantic versioning rules for Python code (what is breaking, what is not), deprecating functions, classes, arguments and module attributes with warnings.deprecated (3.13) or typing_extensions and DeprecationWarning, stacklevel and skip_file_prefixes so the warning points at the caller, DeprecationWarning versus FutureWarning visibility, a written deprecation policy, supporting a range of Python versions with compatibility shims, testing against the oldest and newest allowed dependencies with uv --resolution lowest-direct, catching accidental API breaks with griffe check, a changelog with Keep a Changelog and towncrier fragments, typing with py.typed and stubs checked by stubtest, and a release checklist. Use when deprecating or removing a public name, deciding the next version number, keeping old Python or dependency versions working, writing a changelog, or preparing a library release.
metadata:
  tags:
  - python
  - library
  - deprecation
  - semver
  - changelog
  - towncrier
  - compatibility
  - release
---

# Maintaining a published Python library

Once a library is on PyPI, every public name, argument, default and exception type is something another project's code depends on. Maintenance is the craft of changing all of that without surprising them: deprecate first, remove on a schedule, say what changed, and prove that the oldest and newest supported versions of everything still work. Building and uploading the package is the python-packaging skill; choosing which Python versions to support is the python-version-upgrade skill.

## Step 0: read where the library stands

Maintenance decisions start from the current version, the supported range and the tools in place.

```bash
uv run python -c "import sys; print(sys.version)"
uv version                                         # the project's own version
grep -nE "requires-python|^version|typing.extensions|Programming Language" pyproject.toml
git tag --sort=-v:refname | head -5                # recent releases
ls CHANGELOG.md changes/ newsfragments/ src/*/py.typed 2>/dev/null
```

Current tools (October 2026): typing_extensions 4.16.0, griffe 2.4.1, towncrier 26.9.0, mypy 2.4.1 (for `stubtest`), uv 0.13.6.

## Know the public API surface

The public API is every name a user can import without a leading underscore, plus everything those names expose: parameters and their defaults, return types, raised exception types, attributes of returned objects, and documented behaviour. The python-code-design skill covers declaring it with `__init__` re-exports and `__all__`; here the point is that everything public is a promise.

- Name internals with a leading underscore; re-export public names from the package root so moving code between private modules never breaks an import.
- Exceptions are API: changing `ValueError` to `TypeError` breaks every `except ValueError`.

## Semantic versioning for Python libraries

Semantic versioning is `MAJOR.MINOR.PATCH`: a patch fixes bugs, a minor adds features compatibly, a major breaks something. The hard part is knowing what counts as breaking in Python.

| Change | Version bump |
|---|---|
| Remove or rename a public function, class, module, attribute or parameter | major |
| Make an optional parameter required, or reorder positional parameters | major |
| Change a default value that changes behaviour | major (warn with `FutureWarning` first) |
| Raise a different exception type, or stop accepting an input that used to work | major |
| Narrow a return type in a way callers can observe (list to tuple, dict to custom object) | major |
| Add a function, class, or an optional keyword parameter with a default | minor |
| Deprecate something (warning only) | minor |
| Drop an end-of-life Python version, with `requires-python` raised | minor, noted in the changelog |
| Fix a bug, even when someone relied on it | patch, and say so in the changelog |

- Before 1.0, many projects treat `0.MINOR` as the breaking digit; with calendar versions (nox's `2026.10.20`) the deprecation period carries the promise. State your rule in the README.

## Deprecate before removing with warnings.deprecated

A deprecation keeps the old name working for at least one release while telling callers to move. On Python 3.13+, the `warnings.deprecated` decorator (PEP 702) does both jobs: it emits a `DeprecationWarning` at runtime and type checkers flag every call site. For older Pythons, import the same decorator from `typing_extensions`.

```python
# src/oldlib/_compat.py
import sys

if sys.version_info >= (3, 13):
    from warnings import deprecated
else:
    from typing_extensions import deprecated
```

```python
from oldlib._compat import deprecated


def read(path: str, *, encoding: str = "utf-8") -> str:
    with open(path, encoding=encoding) as f:
        return f.read()


@deprecated("load() is deprecated since 2.3 and will be removed in 3.0; use read()")
def load(path: str) -> str:
    return read(path)
```

A caller sees `caller.py:7: DeprecationWarning: load() is deprecated since 2.3 ...` with their own file and line.

- The message names the version that deprecated it, the version that removes it, and the replacement.
- Pyright (`reportDeprecated`) and mypy (`--enable-error-code deprecated`; off by default in mypy 2.4) report every use.
- It also works on classes (warns on instantiation) and on single `@overload` signatures; the object keeps the message in `__deprecated__`.
- Add `typing_extensions>=4.12; python_version < '3.13'` to dependencies when you support 3.11 or 3.12.

## Point the warning at the caller: stacklevel and skip_file_prefixes

A warning shows the file and line it is attributed to. Attributed to your library, it tells the user nothing; attributed to their call, it tells them what to change.

- `warnings.warn(msg, DeprecationWarning, stacklevel=2)` blames the caller of the function that calls `warn`. Each extra layer of your own code between the user and `warn` needs one more level.
- `skip_file_prefixes=(os.path.dirname(__file__),)` (Python 3.12+) skips frames inside your package while counting `stacklevel` (which becomes at least 2), so the first frame outside it is blamed however deep the call chain. No fragile counting.
- `@deprecated` already sets the right level for a direct call; pass `stacklevel=` to it only when wrapping.

```python
import os
import warnings

_HERE = os.path.dirname(__file__)


def _warn_renamed(old: str, new: str) -> None:
    warnings.warn(f"{old} is deprecated since 2.3; use {new}", DeprecationWarning,
                  skip_file_prefixes=(_HERE,))
```

## DeprecationWarning, FutureWarning and who sees them

Python's default warning filters decide which users see which category.

| Category | Shown by default | Use for |
|---|---|---|
| `DeprecationWarning` | only when triggered by code in `__main__`; always under pytest and `-X dev` | API changes aimed at developers who call your library |
| `PendingDeprecationWarning` | never by default | early notice before the real deprecation |
| `FutureWarning` | always | behaviour changes that end users of an application should see, such as a changing default |

A `DeprecationWarning` triggered from another library's module stays hidden from that library's users but shows in its developers' test runs (pytest lists it in the summary), which is the audience that can act. Recipes for every kind of deprecation: [deprecation-patterns.md](references/deprecation-patterns.md).

## A deprecation policy

Write the policy down (in the README or contributing guide) so users can plan upgrades.

- A public name is deprecated for at least one minor release, or six months, whichever is longer, before removal in the next major.
- Every deprecation has a changelog entry under "Deprecated" and a docs note with the replacement.
- Removals happen only in major releases, all listed under "Removed" with their replacements.
- The test suite runs with warnings as errors, so the library never calls its own deprecated APIs.

## Support a range of Python versions

A library supports every Python version that still gets security fixes, unless a dependency forces otherwise: 3.11 to 3.15 in late 2026. `requires-python` states the lowest, with no upper bound; classifiers list each version; CI tests each one. When and how to add and drop versions is the python-version-upgrade skill.

- Test the oldest supported version with the oldest allowed dependencies (next section); most breakage hides there.
- Run the type checker with `pythonVersion` (Pyright) or `python_version` (mypy) set to the oldest supported version, so newer stdlib APIs are caught.
- Free-threaded builds (3.14t, 3.15t) are separate interpreters: test them in CI before claiming support with the Free Threading classifier.

## Compatibility shims

A shim lets one code base use new features where available and fall back elsewhere. Keep them in one private module (`_compat.py`), each with the version that lets you delete it.

```python
# src/oldlib/_compat.py
import sys

if sys.version_info >= (3, 13):
    from warnings import deprecated
else:                                      # remove when requires-python >= 3.13
    from typing_extensions import deprecated

if sys.version_info >= (3, 12):
    from itertools import batched
else:                                      # remove when requires-python >= 3.12
    from oldlib._backports import batched
```

- Compare `sys.version_info` with a tuple: type checkers check each branch against the configured version, which they cannot do with `try`/`except ImportError`. That form is for optional dependencies, which depend on what is installed.
- With `target-version` raised, Ruff's `UP036` flags version blocks you can delete.

## Test against the oldest and newest dependencies

Lower bounds in `dependencies` (`packaging>=24.0`) are promises; untested, they are guesses. uv can resolve every direct dependency to its lowest allowed version.

```bash
uv run --isolated --resolution lowest-direct --python 3.11 pytest
```

- `lowest-direct` takes the lowest version of your direct dependencies and the highest of their own dependencies; `lowest` lowers everything and often fails on ancient transitive packages.
- `--isolated` runs in a temporary environment. `uv sync --resolution lowest-direct` and a plain `uv run --resolution lowest-direct` rewrite `uv.lock` with `resolution-mode = "lowest-direct"`; never commit that.
- In CI, add a job for the oldest Python with lowest-direct and one for the newest Python with `--resolution highest`; a nox session parametrized over both is in the nox-sessions skill.
- When lowest-direct fails, the bound was wrong: raise it.

## Catch accidental API breaks with griffe check

griffe compares the public API of two git references by reading the source, and reports changes that break callers.

```bash
uvx griffe check oldlib --search src --against v2.3.0
```

```
src/oldlib/__init__.py:14: read(encoding): Parameter is now required
src/oldlib/__init__.py:0: load: Public object was removed
```

- Run it in CI on pull requests against the last release tag; it exits 1 on breaking changes, and `-f github` annotates the pull request.
- It sees signatures, changed defaults and removals (`read(encoding): Parameter default was changed: 'utf-8' -> 'latin-1'`), not behaviour: a changed exception type or a new meaning for the same value still needs review.

## Changelog

The changelog is for users deciding whether and how to upgrade. Keep a Changelog's format groups each release's entries under fixed headings: Added, Changed, Deprecated, Removed, Fixed, Security.

- Write entries for users: what changed and what to do ("`load()` is deprecated; use `read()`"), not commit messages; breaking changes lead, each with its migration step.
- towncrier avoids merge conflicts on `CHANGELOG.md`: each pull request adds a fragment file (`changes/142.deprecated.md`), and the release step assembles them. Configuration, CI check and release flow: [changelog-and-release.md](references/changelog-and-release.md).

## Typing: py.typed, stubs and stubtest

A library with inline annotations ships an empty `py.typed` file in the package (PEP 561) so type checkers use them; the python-packaging skill shows where it goes. Stubs (`.pyi`) are for code without inline annotations, such as a compiled extension.

- Ship stubs next to the module (`_core.pyi` beside the compiled `_core`); for someone else's untyped package, publish a `<name>-stubs` distribution.
- Without `py.typed`, mypy skips the package and reports that it found the module but neither stubs nor the marker file (error code `import-untyped`).
- Check stubs against the runtime with mypy's stubtest. It reports drift such as "shapes.area is inconsistent, stub does not have parameter "unit"".

```bash
uv run --with mypy python -m mypy.stubtest oldlib       # compare stubs with the importable module
uv run --with mypy stubgen -m oldlib._core -o stubs/    # draft stubs to edit by hand
```

- Narrowing a parameter's annotation breaks type-checked callers even when runtime behaviour is unchanged.

## Release checklist

A release is a routine, written down and done the same way each time. The full version with commands is in [changelog-and-release.md](references/changelog-and-release.md).

1. Main is green on every supported Python and OS, and in the lowest-direct job.
2. `griffe check` against the last tag shows only intended changes; the version bump matches them.
3. Changelog assembled; deprecations and removals have migration notes.
4. Version bumped (`uv version --bump minor`), committed, tagged `vX.Y.Z`; CI builds and publishes from the tag (python-packaging skill).
5. The published package installs and imports in a fresh environment; versioned docs deployed.

## Verify

Prove a release candidate from the last tag: the API check, the oldest dependencies, warnings as errors, and the deprecation warning itself.

```bash
uvx griffe check oldlib --search src --against "$(git describe --tags --abbrev=0)"; echo "exit $?"   # expect 0, or only intended breaks
uv run --isolated --resolution lowest-direct --python 3.11 pytest   # the oldest allowed dependencies
uv run pytest -W error::DeprecationWarning                # the library never calls its own deprecated names
uv run python -W error::DeprecationWarning -c "import oldlib; oldlib.load('x')"; echo "exit $?"   # expect exit 1: a caller is warned
uv build && python3 -m zipfile -l dist/*.whl | grep py.typed   # expect the marker in the wheel
uv run --with mypy python -m mypy.stubtest oldlib         # stubs, if any, match the runtime
```

## Anti-patterns

| Instead of | Do |
|---|---|
| removing a public name in a minor release | deprecate, wait, remove in the next major |
| a deprecation with no replacement named | name the since version, the removal version and the replacement |
| `stacklevel` counted by hand across layers | `skip_file_prefixes` |
| `DeprecationWarning` for a changed default end users must see | `FutureWarning` |
| lower bounds nobody tested | a `lowest-direct` CI job |
| a changelog pasted from commit messages | entries written for users |
| `try: import` to pick a Python feature | a `sys.version_info` check |

## Pre-merge checklist

A library is maintained well when every item holds.

- [ ] The public API is declared, and everything else is underscored.
- [ ] Removals happen only in major releases, after a deprecation of at least one minor release.
- [ ] Deprecations use `@deprecated` or `DeprecationWarning` with `skip_file_prefixes` or the right `stacklevel`, and name the replacement and removal version.
- [ ] The test suite runs with warnings as errors.
- [ ] CI tests the oldest Python with the oldest allowed dependencies and the newest with the newest.
- [ ] `griffe check` runs on pull requests against the last release.
- [ ] Every user-visible change has a changelog entry.
- [ ] `py.typed` ships; stubs, if any, pass stubtest.

## Gotchas

These are the maintenance mistakes seen most.

1. **Users never saw the deprecation.** `DeprecationWarning` triggered inside another library is hidden by default. It shows in their test runs; also list it in the changelog and docs. Use `FutureWarning` for behaviour changes end users must see.
2. **The warning points at the library's own file.** `stacklevel` is wrong after a refactor added a layer. Use `skip_file_prefixes` (3.12+).
3. **A lower bound in `dependencies` was never true.** Nothing tested it. Add the lowest-direct CI job.
4. **`uv.lock` suddenly says `resolution-mode = "lowest-direct"`.** Someone ran `uv sync --resolution lowest-direct`. Restore the lock; use `uv run --isolated` for those runs.
5. **A patch release broke users.** A "harmless" change altered an exception type or a default. Run `griffe check`, which reports changed defaults, and review exceptions and behaviour by hand.
6. **`from warnings import deprecated` fails on 3.12.** It is new in 3.13. Import it through `_compat.py` from `typing_extensions` on older versions.
7. **The library's own tests emit its deprecation warnings.** Internal code still calls the old name. Turn warnings into errors in pytest and migrate the internal calls.

## References

The reference files hold the full recipes.

- [deprecation-patterns.md](references/deprecation-patterns.md): recipes for deprecating functions, classes, methods, overloads, renamed and removed parameters, positional-to-keyword changes, changed defaults with a sentinel, module attributes and moved modules, and testing deprecations with pytest
- [changelog-and-release.md](references/changelog-and-release.md): Keep a Changelog structure, towncrier configuration and fragments, the fragment check in CI, version support policies such as SPEC 0, maintenance branches and backports, yanking a bad release, and the full release checklist with commands

## See also

- `python-packaging`: building, metadata and publishing
- `python-version-upgrade`: adding and dropping Python versions
- `python-code-design`: declaring the public API
- `python-typing`: annotations, stubs and `py.typed`
- `nox-sessions`: a session per Python and dependency bound
