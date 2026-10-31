---
title: Cross-platform CI and tests for Python
author: MOZGII, Inc.
lang: en
---

# Cross-platform CI and tests for Python

How to prove Python code on macOS, Linux and Windows: a GitHub Actions matrix with uv, the shell each runner uses, line endings in test fixtures, markers for OS-specific tests, path assertions that hold on every OS, cleanup of temporary files on Windows, and running the Linux tests from a Mac in a container.

## Contents

This reference has 7 sections.

1. The test matrix
2. Shells on each runner
3. Line endings in fixtures
4. Markers for OS-specific tests
5. Path assertions that hold everywhere
6. Temporary files and cleanup on Windows
7. Running the Linux tests from a Mac

## 1. The test matrix

A matrix job runs the same steps on each OS and Python version; `fail-fast: false` lets every cell finish, so one Windows failure does not hide a macOS one.

```yaml
name: tests
on: [push, pull_request]

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python: ["3.14", "3.15"]
        include:
          - os: ubuntu-latest
            python: "3.15t"                       # the free-threaded build, on one OS
    runs-on: ${{ matrix.os }}
    defaults:
      run:
        shell: bash                               # the same shell syntax on every runner
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
        with:
          enable-cache: true
      - run: uv sync --locked --python ${{ matrix.python }}
      - run: uv run python -X warn_default_encoding -m pytest -q
```

- Test the oldest and newest supported Python on every OS; add the versions in between on Linux only if minutes are scarce.
- `macos-latest` runs on Apple silicon; add an Intel macOS runner only if you ship Intel builds or native code.
- Keep the matrix in one job definition; separate per-OS jobs drift apart.

## 2. Shells on each runner

Each runner has a different default shell: `bash` on Linux and macOS, PowerShell (`pwsh`) on Windows. A step written in bash syntax fails on Windows unless the shell is set.

| Runner | Default shell | Note |
|---|---|---|
| `ubuntu-latest` | bash | GNU tools |
| `macos-latest` | bash | BSD tools: `sed -i ''`, `date` options differ |
| `windows-latest` | pwsh | Git Bash available as `shell: bash` |

- Set `defaults.run.shell: bash` for portable scripts, as in the matrix above.
- Better still, write helper scripts in Python (`uv run python scripts/check.py`), which run the same everywhere.
- Paths in Git Bash on Windows look like `/d/a/repo`; pass them to Python as relative paths or let Python build them.

## 3. Line endings in fixtures

Git can rewrite line endings at checkout, so a fixture compared byte for byte passes on Linux and fails on Windows. A `.gitattributes` file fixes the endings in the repository for everyone.

```gitattributes
* text=auto eol=lf
*.bat text eol=crlf
*.png binary
tests/fixtures/** -text
```

- `-text` keeps fixtures byte-exact, whatever the developer's git settings.
- In tests, read text fixtures with `newline=""` when the line endings are part of what is tested, and compare `splitlines()` results otherwise.
- Write expected output with `newline="\n"`, or compare after normalising: `actual.replace("\r\n", "\n")`.

## 4. Markers for OS-specific tests

A test that cannot run on an OS is skipped with a reason; a test for one OS's branch uses the injected platform and runs everywhere. Custom markers keep the skip rules in one place.

```python
# conftest.py
import sys

import pytest

ONLY = {"posix_only": sys.platform != "win32", "windows_only": sys.platform == "win32", "macos_only": sys.platform == "darwin"}


def pytest_configure(config: pytest.Config) -> None:
    for name in ONLY:
        config.addinivalue_line("markers", f"{name}: run this test only where it applies")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        for name, applies in ONLY.items():
            if item.get_closest_marker(name) and not applies:
                item.add_marker(pytest.mark.skip(reason=f"{name}: not this OS ({sys.platform})"))
```

```python
# test_signals.py
import os
import signal

import pytest


@pytest.mark.posix_only
def test_sighup_handler_is_installed() -> None:
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN


@pytest.mark.windows_only
def test_sigbreak_exists() -> None:
    assert hasattr(signal, "SIGBREAK")


def test_runs_everywhere() -> None:
    assert os.sep in ("/", "\\")
```

Run `pytest -rs` to list skipped tests with reasons; a test skipped on every OS is a test that never runs.

## 5. Path assertions that hold everywhere

Comparing paths as strings fails across OSes (separators, drive letter case, short names). Compare `Path` objects, resolve both sides, or compare POSIX forms of relative paths.

```python
import tempfile
from pathlib import Path


def test_output_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = root / "reports" / "q3.csv"
        out.parent.mkdir()
        out.touch()
        assert out.relative_to(root).as_posix() == "reports/q3.csv"   # stable text on every OS
        assert out.resolve() == (root / "reports" / "q3.csv").resolve()
        assert out.samefile(root / "reports" / ".." / "reports" / "q3.csv")


test_output_path()
print("ok")
```

- On GitHub's Windows runners the temp folder can appear in 8.3 short form (`C:\Users\RUNNER~1\...`); `resolve()` both sides before comparing absolute paths.
- On macOS, `/tmp` and `/var` are symlinks into `/private`; again, resolve before comparing.
- Use `pytest`'s `tmp_path` fixture rather than fixed folders, so parallel runs and different OSes do not collide.

## 6. Temporary files and cleanup on Windows

A test that leaves a file open makes its temporary folder impossible to delete on Windows: cleanup fails with `PermissionError: [WinError 32]`, sometimes in a later test. Close everything the test opens, including files opened by the code under test.

- Use `with` for every file, `subprocess.Popen` and `sqlite3` connection in tests; `sqlite3` connections must be closed explicitly (`conn.close()`), since the context manager only commits.
- Run the suite with `python -X dev -W error::ResourceWarning -m pytest` to turn unclosed files into failures on every OS.
- Loggers with `FileHandler` keep their file open; remove and close handlers in a fixture's teardown.

## 7. Running the Linux tests from a Mac

A container runs the Linux leg locally, which catches case-sensitivity and path issues before CI does. Mount the project read-only and let uv create the environment inside the container.

```bash
docker run --rm -v "$PWD":/src:ro -w /work python:3.15-slim sh -c '
  cp -r /src/. /work && pip install -q uv && uv sync --locked && uv run pytest -q'
```

- The copy step keeps the container's `.venv` and caches out of your checkout.
- Windows cannot be run this way on a Mac; keep Windows-only logic in small functions that take the platform as a parameter, so most of it is tested on any OS, and let CI cover the rest.
