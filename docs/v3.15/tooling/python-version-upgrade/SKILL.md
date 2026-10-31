---
name: python-version-upgrade
description: Upgrades a project to a newer Python version (3.11 through 3.15) and drops old ones safely - reading the support calendar, a CI matrix that adds the new version first, checking that every dependency has wheels for it, running tests with deprecation warnings as errors, the removed modules and changed defaults that break code in each release (distutils, dead batteries, asyncio get_event_loop, forkserver on Linux, lazy annotations, UTF-8 default, strptime without a year), rewriting syntax with Ruff UP rules after raising requires-python, free-threaded builds, and the order of steps for a library versus an application. Use when moving to Python 3.13, 3.14 or 3.15, when a Python version reaches end of life, when code breaks after a Python upgrade, or before raising requires-python.
metadata:
  tags:
  - python
  - upgrade
  - migration
  - python-version
  - deprecation
  - compatibility
  - requires-python
  - free-threading
---

# Upgrading the Python version

A Python upgrade has two halves: making the code run on the new version (removed modules, changed defaults, dependencies without wheels), and later dropping the oldest version (raising `requires-python`, deleting compatibility code, using new syntax). Done in that order, each half is a small, testable change. This skill gives the order, the commands and the breaking changes per release; the full list is in [changes-by-version.md](references/changes-by-version.md).

## Step 0: read where the project stands

Upgrade planning starts from three facts: which versions the project claims, which it tests, and which are still supported upstream.

```bash
grep -n "requires-python" pyproject.toml; cat .python-version 2>/dev/null
grep -rn "python-version" .github/workflows/ 2>/dev/null | head
uv python list                       # installed and downloadable versions
```

| Version | Released | Security support ends | Status in October 2026 |
|---|---|---|---|
| 3.10 | Oct 2021 | Oct 2026 | end of life: drop it |
| 3.11 | Oct 2022 | Oct 2027 | security fixes only |
| 3.12 | Oct 2023 | Oct 2028 | security fixes only |
| 3.13 | Oct 2024 | Oct 2029 | bug fixes until Oct 2026, then security only |
| 3.14 | Oct 2025 | Oct 2030 | bug fixes |
| 3.15 | Oct 2026 | Oct 2031 | newest, 3.15.2 |

A new minor release comes every October and each gets five years. Applications usually run the newest version their dependencies support; libraries support every version that still gets security fixes.

## The order of work

Adding the new version and dropping the old one are separate changes, each merged on its own.

1. **Read the release notes** for every version you skip: the "Removed" and "Porting to" sections of What's New, plus [changes-by-version.md](references/changes-by-version.md).
2. **Check the dependencies** have wheels for the new version (next section).
3. **Add the new version to CI** next to the old ones, allowed to fail at first.
4. **Run the tests with warnings as errors** on the current version, fix every `DeprecationWarning`; most of them are next version's removals.
5. **Fix what breaks** on the new version until CI is green, then make the new job required.
6. **Switch the default**: `.python-version`, Docker base image, the deployed runtime. Watch it in production.
7. **Later, drop the oldest version**: raise `requires-python`, remove its CI job, run Ruff's `UP` fixes, delete compatibility branches.

## Check the dependencies first

A new Python release is blocked most often by a dependency with compiled code that has no wheel for it yet.

```bash
uv python install 3.15
uv sync --python 3.15 --all-extras --all-groups      # tries the real resolution and install in .venv
uv run --python 3.15 python -c "import my_app"        # imports every C extension the package uses
uv tree --outdated --depth 1                          # direct dependencies with newer releases
```

- A failure such as `Failed to build numpy==2.1.0` or `No solution found ... only has wheels for cp310-cp313` means that version has no wheel for the new Python: upgrade the dependency (`uv lock --upgrade-package numpy`), then re-run.
- When the newest release still has no wheel, check its issue tracker for the new Python, and wait or replace it; do not ship a build from source of a large C package.
- `uv lock` already resolves for every version in `requires-python`; raising the upper end of a project's tested range rarely needs lock changes, but a new `cp315` wheel may.
- Free-threaded builds need separate `cp314t` / `cp315t` wheels (see the last section).

## A CI matrix with the new version

The new Python joins the CI matrix before anything else changes, and starts as allowed to fail so the main branch stays green.

```yaml
# .github/workflows/test.yml
jobs:
  test:
    runs-on: ${{ matrix.os }}
    continue-on-error: ${{ matrix.experimental }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python: ["3.11", "3.12", "3.13", "3.14"]
        experimental: [false]
        include:
          - { os: ubuntu-latest, python: "3.15", experimental: true }
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
        with: { version: "0.13.6" }
      - run: uv sync --locked --python ${{ matrix.python }}
      - run: uv run --python ${{ matrix.python }} pytest -q -W error::DeprecationWarning
```

Run one job with the lowest dependency versions as well (`uv sync --resolution lowest-direct`) for a library; old dependency versions are the ones most likely to lack new wheels.

## Turn deprecations into errors

Almost every removal is announced by a `DeprecationWarning` one or two releases earlier, so a test run that fails on those warnings lists most of the next upgrade's work.

```toml
[tool.pytest]
filterwarnings = [
  "error",                                       # every warning fails the test
  "ignore::DeprecationWarning:some_third_party.*",  # one you cannot fix yet, with a ticket
]
```

```bash
uv run python -W error::DeprecationWarning -X dev -m my_app --self-test   # outside pytest
```

Deprecations raised inside a third-party package point at that package: upgrade it, or ignore that one module's warnings with a dated comment.

## What breaks in each release

Each release removes some modules and changes some defaults. The changes below are the ones real projects hit; the reference file has the complete lists with replacements.

| Upgrading to | What breaks most often | Fix |
|---|---|---|
| 3.12 | `distutils`, `imp`, `asyncore`, `asynchat` removed; `setuptools` no longer in new venvs; `"\d"` in a normal string is a `SyntaxWarning` | `setuptools`/`packaging` or `sysconfig`; `importlib`; asyncio; raw strings `r"\d"` |
| 3.13 | 19 "dead battery" modules removed (`cgi`, `crypt`, `telnetlib`, `imghdr`, `pipes`, `audioop`, ...), `lib2to3` and `2to3` removed | PyPI replacements (`legacy-cgi`, `standard-imghdr`) or rewrite with `email`, `shlex`, `hashlib` |
| 3.14 | `asyncio.get_event_loop()` raises without a running loop; Linux `multiprocessing` defaults to `forkserver`; annotations are evaluated lazily; `ast.Num`/`ast.Str` removed; `return` in `finally` warns | `asyncio.run`; picklable top-level worker functions; `annotationlib`; `ast.Constant` |
| 3.15 | UTF-8 is the default encoding everywhere; `strptime("%d")` without a year raises; `sqlite3.connect()` arguments after the path are keyword-only; `sre_compile`/`sre_parse` removed; CGI handler removed from `http.server` | pass `encoding=` on purpose; add the year to the format; keyword arguments; `re` |

### The two changes that bite silently

Two default changes do not raise at import time and show up as wrong behaviour instead, so test for them explicitly.

- **3.14, Linux: `forkserver` instead of `fork`.** Process pools no longer inherit module globals set at run time, and work functions and their arguments must pickle. Symptom: `NameError`, `AttributeError: Can't get local object` or `PicklingError` from workers. Fix: top-level functions, pass state as arguments, or opt back in with `multiprocessing.get_context("fork")` where the program has no threads.
- **3.15: UTF-8 by default (PEP 686).** `open()`, `subprocess` text mode and `Path.read_text()` without `encoding=` now use UTF-8 even on a Windows machine with a legacy code page. Files written by older versions in cp1252 decode wrongly or raise `UnicodeDecodeError`. Fix: pass `encoding="utf-8"` (or `"cp1252"` for old data) everywhere; `PYTHONUTF8=0` restores the old default while you migrate.

## Raise requires-python and modernise

Dropping the oldest version is a separate change: raise `requires-python`, then let Ruff rewrite the code for the new minimum, because its `UP` rules read that field.

```toml
[project]
requires-python = ">=3.12"
```

```bash
uv lock                                              # re-resolve: newer dependency versions may now fit
uv run ruff check --select UP --fix .                # safe fixes, e.g. Optional[X] to X | None
uv run ruff check --select UP --unsafe-fixes --diff . # read these first: TypeVar to PEP 695 generics is an unsafe fix
uv run ruff check --select UP --fix --unsafe-fixes .  # then apply them
uv run pytest -q
```

Then delete what only existed for the old version:

- `if sys.version_info < (3, 12):` branches and their `else` indentation (Ruff `UP036` removes outdated version blocks).
- `typing_extensions` imports of names the new minimum has (`Self`, `override`, `TypeIs`); keep the dependency only for newer names.
- Backport packages: `tomli` (use `tomllib`, 3.11), `exceptiongroup` (3.11), `backports.zoneinfo` (3.9), `importlib_metadata`/`importlib_resources` for their stdlib parts.
- `from __future__ import annotations` can stay; it is harmless, and removing it is only worthwhile once the minimum is 3.14.

## Libraries and applications differ

The same upgrade has a different shape for code others install than for code you deploy.

| | Library | Application |
|---|---|---|
| supported versions | every version with security support (3.11 to 3.15 in late 2026) | one: the version you deploy |
| when to add a new version | at its first release candidate in CI, a release with the classifier soon after the final | when its dependencies have wheels, usually a few months after release |
| when to drop one | at or after its end of life, in a minor release noted in the changelog | as soon as production no longer runs it |
| `requires-python` | the lowest supported, no upper bound | the deployed version, e.g. `>=3.14,<3.15` is acceptable |

Never put an upper bound such as `<3.15` in a library's `requires-python`: installers then pick an ancient release of your library on the new Python instead of failing clearly.

## Free-threaded Python

The free-threaded build (`3.14t`, `3.15t`) runs Python threads in parallel without the GIL; it is officially supported since 3.14 but still a separate build with its own wheels.

```bash
uv python install 3.15t
uv run --python 3.15t python -c "import sys; print(sys._is_gil_enabled())"   # False when truly free-threaded
uv run --python 3.15t --with pytest-run-parallel pytest -q --parallel-threads=4   # each test runs in 4 threads at once
```

- Every compiled dependency needs a `cp315t` wheel; when one is missing, uv tries to build it from source.
- An extension that does not declare free-threading support re-enables the GIL at import and prints a `RuntimeWarning`; `sys._is_gil_enabled()` then returns `True`.
- Code that relied on the GIL for safety (shared dicts and lists updated from several threads, lazy initialisation without a lock) now races: add locks, or keep such state per thread.
- pytest-xdist's `-n 4` starts processes, each with one thread, so it cannot find a thread race; pytest-run-parallel (0.10) can.
- Single-threaded code runs somewhat slower on the free-threaded build; adopt it only for CPU-bound work in threads, and measure.

## Verify

Prove the new version from a clean install rather than from the old `.venv`, and confirm the tree holds none of the removed names.

```bash
uv sync --python 3.15 --all-groups --reinstall                 # wheels for every dependency, or a clear failure
uv run --python 3.15 python -c "import sys, my_app; print(sys.version_info[:2])"
uv run --python 3.15 python -W error::DeprecationWarning -m pytest -q
uv run ruff check --select UP .                                # after raising requires-python: expect nothing
uv lock --check                                                # expect exit 0
grep -rnE '^\s*(import|from) (distutils|imp|asyncore|asynchat|cgi|crypt|telnetlib|imghdr|pipes|audioop|sre_compile|sre_parse)\b' src
```

The last line prints nothing when no removed module is left. Run the new version in production-like load before making it the default, because forkserver and encoding changes fail at run time, not at import.

## Anti-patterns

| Instead of | Do |
|---|---|
| adding the new version and dropping the old in one change | two changes, each merged alone |
| `<3.15` in a library's `requires-python` | no upper bound; test the new version in CI |
| ignoring `DeprecationWarning` | errors in tests: they list the next release's removals |
| `pytest -n 4` as a free-threading test | pytest-run-parallel, which runs threads |
| `--unsafe-fixes` without reading the diff | `--diff` first, on a clean tree |
| building a large C package from source to get ahead of its wheels | wait for the wheel, or replace the package |

## Checklist

An upgrade is done when every item holds.

- [ ] Release notes for every skipped version were read; the reference table was checked against the code (`grep` for each removed name).
- [ ] Every dependency installs on the new version from wheels.
- [ ] CI runs the new version on every supported OS, required to pass.
- [ ] Tests pass with `DeprecationWarning` as an error on the newest version.
- [ ] Process pools, file encodings and `asyncio` start-up were checked explicitly.
- [ ] `.python-version`, Docker images and the deployed runtime were switched after CI went green.
- [ ] Dropping a version happened in its own change, with `requires-python` raised, Ruff `UP` applied and compatibility code removed.

## Gotchas

These are the upgrade problems people ask about.

1. **"No module named 'distutils'" after moving to 3.12.** It was removed. Replace `distutils.version` with `packaging.version`, `distutils.util.strtobool` with a small function, build code with setuptools; add `setuptools` explicitly if an old tool imports it.
2. **"ModuleNotFoundError: No module named 'cgi'" (or `imghdr`, `telnetlib`, `crypt`) on 3.13.** Dead batteries were removed. Use the replacement listed in the reference, or the `legacy-cgi`/`standard-*` packages as a stopgap.
3. **"There is no current event loop" on 3.14.** `asyncio.get_event_loop()` no longer creates a loop. Use `asyncio.run(main())` or `asyncio.Runner`.
4. **Process pool workers fail with pickling or name errors on Linux after 3.14.** The default start method became `forkserver`. Make worker functions top-level and their arguments picklable.
5. **"UnicodeDecodeError: 'utf-8' codec can't decode" on Windows after 3.15.** Old files are in a legacy code page and the default is now UTF-8. Read them with `encoding="cp1252"`, write everything as UTF-8.
6. **`pip install` picks a two-year-old version of a package on the new Python.** The newest releases have no wheel or exclude the version, so the resolver backtracks. Pin a lower bound and read the resolver's output.
7. **Ruff rewrote `Optional[X]` but the code broke on the old Python.** `requires-python` (or `target-version`) was raised before the old version was dropped from CI. Keep the field honest; it drives the rewrites.
8. **A library dropped 3.10 and users on 3.10 get errors.** `requires-python` was not raised, so installers still chose the new release. Raise it in the same release that drops the version.
9. **pytest stops with `INTERNALERROR` and `The configuration option "asyncio_default_fixture_loop_scope" is unset`, before any test runs.** `filterwarnings = ["error"]` turns the warning pytest-asyncio 1.4.1 raises while pytest starts into a crash (exit code 3). Set `asyncio_default_fixture_loop_scope = "function"` beside `filterwarnings`.

## References

The reference file has the complete version-by-version lists.

- [changes-by-version.md](references/changes-by-version.md): removed modules and APIs with replacements, changed defaults and pending removals, release by release from 3.12 to 3.16

## See also

- `uv-projects`: installing Python versions and re-locking
- `ruff`: the `UP` rules behind the rewrite
- `python-containers`: moving base images to the new version
- `python-concurrency`: free-threaded Python and start methods
- `python-library-maintenance`: dropping a version in a library release
