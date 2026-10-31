---
name: nox-sessions
description: Automates a Python project's checks with nox 2026.10 and uv - a noxfile with the uv venv backend, installing the locked project into each session with uv sync, sessions for tests, lint, type checking and docs, a test matrix across Python versions read from the classifiers including free-threaded builds, parametrized sessions (oldest and newest dependency versions), passing posargs, reusing environments with -R, session dependencies with requires, tags and default sessions, parallel sessions with -j, running the same sessions locally and in CI, and choosing between nox and tox 4 with tox-uv. Use when writing or fixing a noxfile, running tests on several Python versions locally, making CI and local checks identical, or deciding between nox and tox.
metadata:
  tags:
  - python
  - nox
  - tox
  - tox-uv
  - uv
  - test-matrix
  - ci
  - automation
---

# Project sessions with nox and uv

nox runs named sessions, each in its own virtual environment, from a `noxfile.py` written in plain Python. One file defines how to run the tests on every supported Python, lint, type-check and build the docs, and the same commands run on a laptop and in CI. With the uv backend, environments build in a second or two. This skill covers the noxfile, the session patterns that matter, and when tox fits better. Tests themselves are the pytest-testing skill; uv projects and lock files are the uv-projects skill.

## Step 0: read the installed versions

nox changed a lot in 2026 (parallel sessions, Python discovery, free-threaded versions from classifiers), so read the versions first.

```bash
uv run python -c "import sys; print(sys.version)"
uvx nox --version                        # 2026.10.20
uv --version                             # the uv that the uv backend will call
uvx nox -l                               # sessions in this project's noxfile
grep -n "needs_version" noxfile.py
```

Current releases (October 2026): nox 2026.10.20 (runs on Python 3.10+), tox 4.67.5 with tox-uv 1.37.2, nox-uv 0.8.0, uv 0.13.6. nox versions are dates; pin a minimum with `nox.needs_version = ">=2026.8.10"` when you use parallel sessions or other recent features.

## Why nox

nox is a task runner whose configuration is Python: sessions are functions, the matrix is a list, conditions are `if` statements.

- Run nox as a uv tool (`uvx nox` or `uv tool install nox`), not as a project dependency; it manages environments for the project, so it sits outside them.
- Each session gets a fresh environment under `.nox/<session>`; nothing leaks between tests on 3.12 and 3.14.
- A noxfile is code you can test and refactor; a helper like `uv_sync()` below replaces repeated blocks.
- Use nox when sessions need logic (conditional steps, generated matrices, a release script). Use plain `uv run` when one Python and one command are all you need.

## A noxfile with the uv backend

This noxfile defines tests on every supported Python, a coverage report that combines them, lint, type checking on the oldest Python, and a docs build. It installs exactly the versions in `uv.lock`.

```python
import os

import nox

nox.needs_version = ">=2026.8.10"
nox.options.default_venv_backend = "uv"
nox.options.reuse_venv = "yes"
nox.options.error_on_missing_interpreters = "CI" in os.environ

PYPROJECT = nox.project.load_toml("pyproject.toml")
PYTHONS = nox.project.python_versions(PYPROJECT)


def uv_sync(session: nox.Session, *groups: str) -> None:
    """Install the locked project and the named dependency groups into the session's venv."""
    args = [f"--group={g}" for g in groups]
    session.run_install(
        "uv",
        "sync",
        "--locked",
        "--no-default-groups",
        *args,
        f"--python={session.virtualenv.location}",
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )


@nox.session(python=PYTHONS, tags=["test"], allow_parallel=True)
def tests(session: nox.Session) -> None:
    """Run the test suite under coverage. Extra arguments go to pytest."""
    uv_sync(session, "test")
    session.run("coverage", "run", "--parallel-mode", "-m", "pytest", *session.posargs)


@nox.session(default=False, requires=["tests"])
def coverage(session: nox.Session) -> None:
    """Combine the data files from every tests session and report."""
    session.install("coverage>=7.17")
    session.run("coverage", "combine")
    session.run("coverage", "report", "--fail-under=100")


@nox.session(tags=["check"])
def lint(session: nox.Session) -> None:
    """Lint and check formatting without changing files."""
    uv_sync(session, "lint")
    session.run("ruff", "check", ".")
    session.run("ruff", "format", "--check", ".")


@nox.session(python=PYTHONS[0], tags=["check"])
def typecheck(session: nox.Session) -> None:
    """Type-check against the oldest supported Python."""
    uv_sync(session, "typecheck")
    session.run("pyright", "src", "tests", *session.posargs)
```

The matching `pyproject.toml` declares the groups each session syncs:

```toml
[dependency-groups]
test = ["pytest>=9", "coverage>=7.17"]
lint = ["ruff==0.17.3"]
typecheck = ["pyright==1.1.414", {include-group = "test"}]
docs = ["mkdocs-material>=9.8", "mkdocstrings-python>=2.0"]
```

- `nox -l` lists the sessions with their docstrings; `*` marks the ones a bare `nox` runs.
- Write session docstrings: they are the help text.
- Add `.nox/` to `.gitignore`.

## Install the locked project with uv sync

`session.install()` with the uv backend runs `uv pip install`, which resolves fresh and ignores `uv.lock`. For a locked, reproducible session, run `uv sync` into the session's environment, as the `uv_sync()` helper does.

- `UV_PROJECT_ENVIRONMENT` points `uv sync` at the session's venv instead of the project's `.venv`, and `--python` keeps the session's interpreter.
- `--locked` fails when `uv.lock` is stale, so a forgotten `uv lock` breaks the session, not a later release.
- `--no-default-groups` plus `--group=<name>` gives each session only what it needs: lint does not install pytest.
- `run_install` (not `run`) marks the step as installation, so `nox -R` skips it.
- The nox-uv package wraps this pattern in a decorator: `@nox_uv.session(uv_groups=["test"])` runs `uv sync --locked` with those groups.

## Sessions for tests, lint, type checking and docs

Each session does one job and fails on the first failing command.

| Session | Runs | Notes |
|---|---|---|
| `tests` | `coverage run -m pytest` on each Python | parallel coverage files, combined by `coverage` |
| `lint` | `ruff check .`, `ruff format --check .` | check only; fixing is the editor's or pre-commit's job |
| `typecheck` | Pyright or mypy on the oldest supported Python | the oldest version catches use of newer stdlib APIs |
| `docs` | `mkdocs build --strict` or `sphinx-build -W` | treats warnings as errors |

This docs session builds the site, or serves it with `nox -s docs -- --serve`:

```python
@nox.session(default=False)
def docs(session: nox.Session) -> None:
    """Build the documentation; fail on any warning. Pass --serve to preview."""
    uv_sync(session, "docs")
    if "--serve" in session.posargs:
        session.run("mkdocs", "serve")
    else:
        session.run("mkdocs", "build", "--strict", "--site-dir", "site")
```

`session.run` warns when a program comes from outside the session's environment ("git is not installed into the virtualenv ... Pass external=True into run() to silence this message"); the warning catches a tool missing from a dependency group. `--error-on-external-run` turns it into an error. `uv` itself raises no warning under the uv backend.

## Test across Python versions

`nox.project.python_versions(pyproject)` reads the `Programming Language :: Python :: 3.X` classifiers, so the matrix follows the package metadata instead of a second list.

```bash
uvx nox -s tests                 # tests-3.12, tests-3.13, tests-3.14
uvx nox -s tests-3.14            # one version
uvx nox -p 3.13                  # every session that runs on 3.13
uvx nox -s tests -P 3.15         # force a version the noxfile does not list
```

- With the classifier `Programming Language :: Python :: Free Threading :: 3 - Stable`, nox 2026.10 adds `3.14t` and newer free-threaded versions; pass `free_threaded=False` to leave them out.
- `--download-python auto` (the default) downloads a standalone build when the version is not installed; `never` turns that off.
- A missing interpreter skips the session locally and, with `error_on_missing_interpreters` set from `CI`, fails it in CI.

## Parametrize sessions

`@nox.parametrize` runs one session several times with different arguments. This session tests against both the newest and the oldest dependency versions your `pyproject.toml` allows, on the oldest and newest Python.

```python
@nox.session(python=[PYTHONS[0], PYTHONS[-1]], default=False)
@nox.parametrize("resolution", ["highest", "lowest-direct"])
def deps(session: nox.Session, resolution: str) -> None:
    """Test against the newest and the oldest allowed versions of direct dependencies."""
    session.install(f"--resolution={resolution}", "-e", ".", "--group=test")
    session.run("pytest", "-q", *session.posargs)
```

It creates four sessions, `deps-3.12(resolution='highest')` through `deps-3.14(resolution='lowest-direct')`; select one with `-s "deps-3.12(resolution='lowest-direct')"` or `-k lowest`. Here `session.install` is right: the point is to resolve fresh, ignoring the lock.

## Pass arguments with posargs

Everything after `--` on the command line arrives in `session.posargs`; pass it on to the tool.

```bash
uvx nox -s tests-3.14 -- -x -k parser --lf      # pytest flags
uvx nox -s typecheck -- --verbose
```

A session can also read posargs as its own switches, like `--serve` in the docs session. Keep defaults sensible when posargs is empty, since CI runs sessions without them.

## Reuse environments for speed

By default nox rebuilds each environment on every run. Reuse saves time on repeated local runs.

| Option | Effect |
|---|---|
| `nox.options.reuse_venv = "yes"` | reuse an environment whose interpreter and backend still match |
| `--reuse-venv=no` | rebuild; the command-line value beats the noxfile |
| `-R` | reuse and skip every `run_install` and `install` step: fastest edit-test loop |
| `--no-install` | skip installs, but create missing environments |

With `-R`, a dependency change in `uv.lock` is not picked up; drop `-R` after changing dependencies.

## Select and order sessions

Sessions can depend on others, be skipped by default, and carry tags.

| Feature | Meaning |
|---|---|
| `default=False` | `nox` alone skips it; `nox -s name` runs it |
| `requires=["tests"]` | runs every `tests-*` session first; `--no-dependencies` turns this off |
| `session.notify("coverage")` | queues another session after this one, if it was not already selected |
| `tags=["check"]` | `nox -t check` runs every session with the tag |
| `-k "not lint"` | keyword expression over session names, like pytest's `-k` |
| `-x` | stop at the first failing session |

## Run sessions in parallel

Since nox 2026.8, `-j N` runs independent sessions concurrently, each in its own process with buffered output. Only sessions marked `allow_parallel=True` (or every session with `--allow-parallel`) run concurrently; sessions linked by `requires` keep their order.

```bash
uvx nox -j auto -s coverage      # the three tests sessions in parallel, then coverage
```

Parallel sessions must not share files: give coverage parallel data files (as above), and write per-session output under `session.create_tmp()` or names that include `session.name`. A session can check `session.parallel` to see whether it runs in a parallel worker.

## Run the same sessions locally and in CI

CI calls the same sessions a developer runs, so a passing local run means a passing CI run.

```yaml
      - uses: astral-sh/setup-uv@v10.3.3
      - run: uvx nox -s tests-${{ matrix.python }}
```

- In CI set `CI=true` (GitHub Actions does) so a missing interpreter fails the job instead of skipping.
- `uvx nox -s lint typecheck docs` in one job and `tests-<python>` per matrix job keep the matrix in the noxfile.
- GitHub Actions workflows, coverage across jobs and the `wntrblm/nox` action: [ci-recipes.md](references/ci-recipes.md).

## nox or tox

tox 4 is the other mature runner. Its configuration is declarative (`tox.toml`, `tox.ini` or `[tool.tox]`), and tox-uv swaps virtualenv and pip for uv.

| | nox | tox 4 with tox-uv |
|---|---|---|
| Configuration | Python functions | TOML or INI settings |
| Logic (conditions, loops, generated matrices) | ordinary Python | factor conditions and substitutions |
| Lock file | `uv sync` in the session (helper or nox-uv) | `runner = "uv-venv-lock-runner"`, built in |
| Parallel | `-j`, opt-in per session | `tox run-parallel` / `-p` |
| Strength | custom workflows, releases, anything scripted | uniform environments, long-standing plugin ecosystem |

Choose nox when sessions need logic or the team prefers Python; choose tox when the project already uses it or a plugin you need exists only for tox. Both can run the same `uv.lock`. tox configuration with tox-uv, and its traps: [tox-with-uv.md](references/tox-with-uv.md).

## Verify

Run the sessions the way CI will, and prove that a stale lock stops them.

```bash
uvx nox -l                              # lists each session with its docstring
CI=true uvx nox -s lint typecheck       # a missing interpreter fails here instead of skipping
CI=true uvx nox -s tests-3.14 -- -x     # pick a Python from the list above
uv lock --check; echo "exit $?"         # expect 0; a stale lock also fails every uv sync --locked session
grep -n "session.install" noxfile.py    # only sessions meant to resolve fresh
git status --short                      # expect nothing new: .nox/ is ignored
```

## Anti-patterns

| Instead of | Do |
|---|---|
| nox in the project's own dependencies | `uvx nox`; it manages the environments from outside |
| `session.install(".")` in a test session | `uv sync --locked` into the session |
| a Python list beside the classifiers | `nox.project.python_versions(pyproject)` |
| CI steps that differ from the local commands | CI calls `uvx nox -s <session>` |
| `external=True` to silence the warning | add the tool to the session's dependency group |
| `-R` left on after changing dependencies | a run without `-R` |
| sessions that swallow `posargs` | pass `*session.posargs` to the main tool |

## Checklist

A project's nox setup is sound when every item holds.

- [ ] nox runs as a uv tool; `nox.needs_version` states the minimum version the noxfile needs.
- [ ] `default_venv_backend = "uv"`, and sessions sync from `uv.lock` with `--locked`, except sessions meant to resolve fresh.
- [ ] The Python list comes from the classifiers; CI fails on a missing interpreter.
- [ ] Every session has a docstring; slow or special sessions are `default=False`.
- [ ] Sessions pass `session.posargs` to their main tool.
- [ ] CI calls the same sessions developers run.

## Gotchas

These are the nox problems seen most.

1. **A session installs different versions than `uv.lock`.** It used `session.install`, which resolves fresh. Use `uv sync --locked` into the session (the helper above or nox-uv).
2. **`uv sync` in a session changed the project's `.venv` instead.** `UV_PROJECT_ENVIRONMENT` was not set. Pass it in `env=` together with `--python=`.
3. **A session warns "X is not installed into the virtualenv" and still works.** The program was found outside the venv, so the result depends on the machine. Add it to the session's dependency group, or pass `external=True` deliberately.
4. **A dependency change is not seen.** The run used `-R`, which skips installs. Run once without `-R`.
5. **`tests-3.15` is silently skipped.** The interpreter is missing and downloads were off. Allow `--download-python auto`, install it with `uv python install 3.15`, or set `error_on_missing_interpreters`.
6. **Parallel sessions overwrite each other's output.** They write to the same file. Use per-session names, or leave the session without `allow_parallel`.
7. **`uv sync` fails with "expected directory but found a file" for `.venv`.** tox 4.64+ wrote a `.venv` redirect file. Set `venv_redirect = false` in the tox config and delete the file.

## References

The reference files hold the CI workflows and the tox alternative.

- [ci-recipes.md](references/ci-recipes.md): GitHub Actions with a Python and OS matrix calling nox, the `wntrblm/nox` action, combining coverage from sessions, a noxfile with inline script metadata, release and build sessions, Windows and macOS notes
- [tox-with-uv.md](references/tox-with-uv.md): tox 4 with tox-uv, `tox.toml` settings, the uv lock runner and dependency groups, posargs, labels, `depends` and parallel runs, the `.venv` redirect file, and moving between tox and nox

## See also

- `pytest-testing`: what the tests session runs
- `coverage-py`: combining data from every session
- `uv-projects`: dependency groups and `uv.lock`
- `ruff`: the lint session's tool
- `pre-commit`: the checks that run before a commit rather than on demand
