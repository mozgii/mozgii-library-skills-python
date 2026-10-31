---
title: Combining coverage across CI jobs, Python versions and operating systems
author: MOZGII, Inc.
lang: en
---

# Combining coverage across CI jobs, Python versions and operating systems

One test run on one machine measures one path through version- and platform-specific code. A project that supports three Pythons on three operating systems needs the data from every job merged before the floor is checked. This reference covers the data files, path remapping, a GitHub Actions workflow, diff coverage on pull requests, and running coverage under nox and tox.

## Contents

This reference has 8 sections.

1. Why combine across jobs
2. Data files and their names
3. Remapping paths with [paths] and relative_files
4. A GitHub Actions matrix with a combine job
5. Diff coverage on pull requests
6. Coverage under nox and tox
7. Reporting in CI without failing too early
8. Gotchas

## 1. Why combine across jobs

Code branches on the platform and the Python version, so each job covers a different part of it.

```python
import os
import sys

def config_dir() -> str:
    if sys.platform == "win32":
        return os.environ["APPDATA"]
    return os.path.expanduser("~/.config")

def hello(name: str) -> str:
    if sys.version_info >= (3, 14):
        return f"Hello, {name}!"
    return "Hello, " + name + "!"
```

A Linux job on 3.14 misses the Windows line and the pre-3.14 line; neither job alone reaches 100%. Combined data from a Windows job and a 3.12 job does. The alternative, excluding platform code with environment-driven pragmas, is in the reports-and-exclusions reference; combining is more honest because the code really ran.

## 2. Data files and their names

coverage.py stores data in SQLite files. The base name is `.coverage` (change it with `[run] data_file` or `COVERAGE_FILE`).

| Mode | File name | Written when |
|---|---|---|
| normal | `.coverage` | each `coverage run` replaces it, unless `--append` |
| parallel | `.coverage.<host>.pid<pid>.<random>.<hash>` | each process writes its own; set by `parallel = true`, `patch = ["subprocess"]`, `concurrency = ["multiprocessing"]` or `coverage run -p` |

- `coverage combine` merges every `.coverage.*` file in the current directory (or the directories you name) into `.coverage` and deletes the inputs; `--keep` keeps them, `--append` adds to an existing `.coverage`.
- Since 7.14, `report`, `html`, `xml`, `json` and `lcov` combine parallel files first; since 7.15, `--keep-combined` keeps the inputs.
- Combining skips files whose content hash matches one already combined, so the same artifact downloaded twice is not counted twice.
- `coverage debug data` lists the measured files and line counts in the current data file; use it when a report looks wrong.

## 3. Remapping paths with [paths] and relative_files

Each job records file paths as they were on its runner: `/home/runner/work/shop/shop/src/shop/price.py` on Linux, `D:\a\shop\shop\src\shop\price.py` on Windows, or a `site-packages` path when the package was installed rather than run from source. Combining needs one canonical path per file.

```toml
[tool.coverage.run]
relative_files = true                 # record paths relative to the working directory

[tool.coverage.paths]
source = [
  "src/",                             # the canonical form: the first entry
  "*/site-packages/",                 # installed copies on Linux and macOS
  "*\\site-packages\\",               # installed copies on Windows (\\ in TOML is one backslash)
]
```

- `relative_files = true` makes most remapping unnecessary when every job runs from the repository root with an editable install, which is what `uv sync` does by default.
- `[paths]` entries are matched as prefixes; each later entry is rewritten to the first. Since 7.16 a pattern replaces only the leading part of a path, and `combine` converts `\` and `/` to the local style.
- Testing the built wheel (`uv sync --no-editable`, or tox installing the sdist) records `site-packages` paths. The `*/site-packages/` entry maps them back to `src/`.
- Run `coverage combine` from the repository root, where the canonical paths exist; the report reads source files from disk.

## 4. A GitHub Actions matrix with a combine job

This workflow tests on three operating systems and three Pythons, uploads each job's data files, then combines them in one job that checks the floor and writes a summary.

```yaml
name: tests
on: [push, pull_request]

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python: ["3.12", "3.13", "3.14"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
      - run: uv sync --locked --python ${{ matrix.python }}
      - run: uv run coverage run -m pytest      # parallel = true in the config: one file per process
      - uses: actions/upload-artifact@v7
        with:
          name: coverage-${{ matrix.os }}-${{ matrix.python }}
          path: .coverage.*
          include-hidden-files: true            # the files start with a dot; skipped otherwise
          if-no-files-found: error

  coverage:
    needs: test
    if: always()                                # report even when one test job failed
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
      - uses: actions/download-artifact@v8
        with:
          pattern: coverage-*
          merge-multiple: true                  # all files into the working directory
      - run: uv sync --locked
      - run: uv run coverage combine
      - run: uv run coverage report --format=markdown >> "$GITHUB_STEP_SUMMARY"
        continue-on-error: true                 # the summary is written even below the floor
      - run: uv run coverage xml
        continue-on-error: true
      - uses: actions/upload-artifact@v7
        with:
          name: coverage-xml
          path: coverage.xml
      - run: uv run coverage report             # the gate: exits 2 below fail_under
```

- Set `parallel = true` (or `patch = ["subprocess"]`, which implies it) so every job writes uniquely named files; two jobs writing plain `.coverage` would overwrite each other after `merge-multiple`.
- The combine job needs the source tree checked out, because reports read the files to show missing lines.
- On Windows runners the default shell is PowerShell; the commands above work there unchanged.
- A third-party service (Codecov, Coveralls) takes `coverage.xml` from the combine job; upload once, from there, not from every matrix job.

## 5. Diff coverage on pull requests

A project-wide floor of 90% lets a pull request add a completely untested module as long as the total stays above 90. Diff coverage checks only the lines the pull request changed. diff-cover (11.0 in October 2026) reads `coverage.xml` and the git diff.

```bash
uv run coverage xml
uvx diff-cover coverage.xml --compare-branch=origin/main --fail-under=80 --format markdown:diff-cover.md
```

Its console output names the changed lines that no test ran:

```
Diff: origin/main...HEAD, staged and unstaged changes
src/shop/price.py (25.0%): Missing lines 24-26
Coverage: 25%
```

- In GitHub Actions, check out with `fetch-depth: 0` so `origin/main` exists for the diff.
- `--branch-coverage` counts partially covered branches as uncovered.
- Append `diff-cover.md` to `$GITHUB_STEP_SUMMARY` or post it as a pull-request comment.

## 6. Coverage under nox and tox

Session runners create one environment per Python version, so each session writes its own parallel files and a final session combines them.

```python
import nox


@nox.session(python=["3.12", "3.13", "3.14"])
def tests(session: nox.Session) -> None:
    session.install("-e", ".", "--group=test")
    session.run("coverage", "run", "--parallel-mode", "-m", "pytest", *session.posargs)


@nox.session(default=False, requires=["tests"])
def coverage(session: nox.Session) -> None:
    session.install("coverage>=7.17")
    session.run("coverage", "combine")
    session.run("coverage", "report")
```

- `requires=["tests"]` makes `nox -s coverage` run every tests session first. The full noxfile pattern is in the nox-sessions skill.
- In tox, set `parallel = true` in the coverage config, run `coverage run -m pytest` in each environment, and add a `coverage` environment that depends on the test environments (`depends = ["3.12", "3.13", "3.14"]`) and runs `coverage combine` then `coverage report`.
- A session that installs the project non-editably records `site-packages` paths; keep the `[paths]` mapping from section 3.

## 7. Reporting in CI without failing too early

Every reporting command, not only `report`, applies `fail_under`: `coverage xml`, `json` and `html` write their file and then exit with status 2 when the total is below the floor, printing "Coverage failure: total of 71 is less than fail-under=90".

- Generate all artifacts first with `continue-on-error: true` (or `|| true` in a shell step), then run one `coverage report` as the gate, as in the workflow in section 4.
- Or pass `--fail-under=0` to the artifact commands and keep the real floor only in the config, read by the gating `coverage report`.
- pytest-cov applies the floor after the test run; `--cov-report=xml` still writes the file before failing.

## 8. Gotchas

These are the combining problems seen most.

1. **"No data to combine".** The data files were not where `combine` looked: the upload skipped hidden files, or `download-artifact` put each artifact in its own folder. Use `include-hidden-files: true` and `merge-multiple: true`, or pass the folders to `coverage combine dir1 dir2`.
2. **The report lists the same file twice**, once under `src/` and once under `site-packages/` or a Windows path. A `[paths]` entry is missing. Add the pattern and combine again from the original part files.
3. **The combined report lists tests, scripts or vendored files that local reports do not.** One job measured with a different `source` (a `--cov=.` flag, say), and its extra files were merged in. Keep `source_pkgs` in the shared config, not in per-job flags.
4. **Lines are shown as missing in code that the Windows job ran.** The Windows job used a different config, so its data lacked branches or used other paths. Every job must read the same `pyproject.toml`.
5. **The combine job fails because a test job failed.** `needs:` skips dependents on failure by default. Add `if: always()` to see coverage for the jobs that did run.
6. **diff-cover finds no changed lines, or fails to find the branch,** on a shallow clone. Check out with `fetch-depth: 0` and compare against `origin/main`.
