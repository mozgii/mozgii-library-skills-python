---
title: nox in CI and other noxfile recipes
author: MOZGII, Inc.
lang: en
---

# nox in CI and other noxfile recipes

Recipes for running nox sessions in GitHub Actions on several Pythons and operating systems, combining coverage from session runs, a self-contained noxfile with inline script metadata, a build-and-check session, and the platform differences that break noxfiles on Windows.

## Contents

This reference has 8 sections.

1. A GitHub Actions matrix that calls nox
2. The wntrblm/nox action
3. Coverage from many sessions and jobs
4. A noxfile with inline script metadata
5. A build-and-check session
6. Sessions without a virtual environment
7. Windows, macOS and Linux differences
8. Gotchas

## 1. A GitHub Actions matrix that calls nox

The matrix lives in the workflow only as far as runners and Python versions; the commands come from the noxfile. Each job runs one `tests-<python>` session, and one job runs the checks.

```yaml
name: ci
on: [push, pull_request]

jobs:
  tests:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python: ["3.12", "3.13", "3.14"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
      - run: uvx nox -s tests-${{ matrix.python }}

  checks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
      - run: uvx nox -t check            # lint and typecheck by tag
      - run: uvx nox -s docs
```

- The uv backend downloads a missing Python with uv, so the job needs no `setup-python` step.
- GitHub Actions sets `CI=true`; with `error_on_missing_interpreters = "CI" in os.environ` in the noxfile, a missing interpreter fails the job instead of skipping it.
- Pin nox in CI (`uvx nox@2026.10.20`) when the noxfile depends on recent behaviour, and keep `nox.needs_version` in step.
- To read the matrix from the noxfile instead of repeating it, add a job that runs `uvx nox -l --json -s tests`; it prints a JSON list of objects with `session` (`tests-3.12`), `python` and `tags`, which a later job can turn into its matrix with `fromJSON`.

## 2. The wntrblm/nox action

`wntrblm/nox` (tagged with nox's version, `2026.10.20`) installs several Pythons with `actions/setup-python` and installs nox. It suits the virtualenv backend; with the uv backend, `setup-uv` plus `uvx nox` is lighter.

```yaml
      - uses: actions/checkout@v7
      - uses: wntrblm/nox@2026.10.20
        with:
          python-versions: "3.12, 3.13, 3.14"
      - run: nox -s tests
```

The default `python-versions` in this release is `3.11, 3.12, 3.13, 3.14, 3.15`. One job then runs every `tests-*` session in sequence, which is slower than a matrix but simpler for small projects.

## 3. Coverage from many sessions and jobs

Each `tests` session writes parallel coverage files (`coverage run --parallel-mode`); a `coverage` session with `requires=["tests"]` combines them locally. In CI, where each session runs in its own job, upload the files and combine them in a final job.

```yaml
      - run: uvx nox -s tests-${{ matrix.python }}
      - uses: actions/upload-artifact@v7
        with:
          name: coverage-${{ matrix.os }}-${{ matrix.python }}
          path: .coverage.*
          include-hidden-files: true
```

```yaml
  coverage:
    needs: tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
      - uses: actions/download-artifact@v8
        with:
          pattern: coverage-*
          merge-multiple: true
      - run: uvx nox -s coverage --no-dependencies     # combine and report; do not rerun the tests
```

`--no-dependencies` stops `requires=["tests"]` from running every test session again in the coverage job. Path remapping for data from Windows runners and `site-packages` is in the coverage-py skill.

## 4. A noxfile with inline script metadata

A noxfile can declare its own dependencies in a PEP 723 block. nox reads the block, builds a small environment for the noxfile itself (`.nox/_nox_script_mode`), and runs it there, so helpers like nox-uv need no global install.

```python
#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["nox>=2026.8.10", "nox-uv>=0.8"]
# ///
import nox
import nox_uv

nox.options.default_venv_backend = "uv"


@nox_uv.session(uv_groups=["test"])
def tests(s: nox.Session) -> None:
    s.run("pytest", "-q", *s.posargs)


if __name__ == "__main__":
    nox.main()
```

- `uvx nox` and `./noxfile.py` both work: the shebang runs the file through `uv run --script`, and `nox.main()` then loads `noxfile.py` from the current directory. Name the file `noxfile.py`; `nox.main()` does not load the file it was called from under another name.
- nox-uv's session runs `uv sync --no-default-groups --locked --group=test` into the session, the same as the `uv_sync()` helper in the main skill.
- `--script-mode=none` ignores the block; `reuse` keeps the noxfile's environment between runs.

## 5. A build-and-check session

Release steps belong in a session too, so they run the same way on every machine. This one builds the sdist and wheel and checks the metadata that PyPI will render.

```python
from pathlib import Path


@nox.session(default=False, venv_backend="none")
def build(session: nox.Session) -> None:
    """Build the sdist and wheel into dist/ and check their metadata."""
    session.run("uv", "build", "--out-dir", "dist", "--clear", external=True)
    dists = sorted(
        str(p) for p in Path("dist").glob("*") if p.suffix in {".whl", ".gz"}
    )  # no shell globbing
    session.run("uvx", "twine", "check", "--strict", *dists, external=True)
```

- `session.run` starts programs without a shell, so `dist/*` would reach twine as a literal string. Expand globs in Python.
- `uv build` writes a `.gitignore` into `dist/`; filter by suffix, or twine tries to check it.
- `twine check --strict` fails on a missing `readme` or content type, which PyPI would show as an empty page.
- Publishing itself belongs in CI with trusted publishing; the python-packaging skill covers it.

## 6. Sessions without a virtual environment

`venv_backend="none"` runs a session in the current environment, for steps that only call external tools (`uv build`, `git`, `docker`). Programs come from PATH, and `session.install` raises "Session b does not have a virtual environment, so use of session.install() is no longer allowed since it would modify the global Python environment". `nox --no-venv` forces this for all selected sessions, which is occasionally useful inside a container that already has the dependencies.

## 7. Windows, macOS and Linux differences

The same noxfile runs everywhere if it avoids shell assumptions.

| Area | Difference | Do this |
|---|---|---|
| Executables | `.nox\tests-3-14\Scripts\python.exe` on Windows, `bin/python` elsewhere | call `session.run("python", ...)`; nox resolves it inside the venv |
| Paths in arguments | `\` versus `/` | build them with `pathlib.Path` and pass `str(path)` |
| Shell features | no pipes, `&&`, globs or `$VAR` in `session.run` | do it in Python; pass variables with `env={...}` |
| Environment variables | case-insensitive on Windows | read and set them through `env=`, not by shell syntax |
| Interpreters | `py` launcher on Windows; `python3.X` names on POSIX | let nox and uv find them; do not hard-code paths |
| Session folder names | `tests-3.14` becomes `.nox/tests-3-14` | do not depend on the folder name; use `session.virtualenv.location` |

Parallel sessions (`-j`) buffer output and print an ASCII banner where the console cannot show Unicode, which matters on older Windows consoles.

## 8. Gotchas

These are the CI and recipe problems seen most.

1. **The coverage job reruns every test session.** `requires=["tests"]` pulled them in. Pass `--no-dependencies` in the job that only combines.
2. **`twine check` is handed a file literally named `dist/*`.** `session.run` has no shell. Expand the glob in Python.
3. **A Windows job fails with "Program cp not found".** The session called a POSIX tool (`cp`, `rm`, `sh`). Use Python's `shutil` and `pathlib` inside the session.
4. **A matrix job runs no sessions.** The session name in the workflow (`tests-3.14`) does not match the noxfile, often because the classifiers lack that version. Run `uvx nox -l` locally to see the real names.
5. **`./noxfile.py` runs a different noxfile.** `nox.main()` loads `noxfile.py` from the current directory. Run it from the project root, under that name.
