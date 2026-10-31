---
name: python-cross-platform
description: Writes Python that runs the same on macOS, Linux and Windows - sys.platform, os.name and platform checks, paths, drive letters and UNC shares with pathlib and PureWindowsPath, case sensitivity, reserved names and the Windows path length limit, line endings and newline=, encodings and UTF-8 mode, locked and open files on Windows, missing signals, process creation, executables and PATHEXT, chmod on Windows, symlinks and junctions, a CI matrix across three OSes, and tests with the platform injected. Use when code works on one OS and fails on another, adding Windows or Linux support, writing paths, processes or file handling for several platforms, or setting up cross-platform tests.
metadata:
  tags:
  - python
  - cross-platform
  - windows
  - macos
  - linux
  - pathlib
  - ci
  - portability
---

# Cross-platform Python on macOS, Linux and Windows

Python hides most OS differences, but not paths, case, line endings, encodings, file locking, signals, processes and permissions; code written on one OS breaks on another in predictable ways. This skill lists those differences and the portable way around each. Files in depth are in the python-files-pathlib skill, running programs in python-subprocess.

## Step 0: read where the code must run

Read which OSes the project supports, what CI runs, and the Python version, before changing anything.

```bash
uv run python -c "import sys, platform; print(sys.version_info[:2], sys.platform, platform.machine(), sys.flags.utf8_mode)"
grep -nE "Operating System|requires-python" pyproject.toml
grep -rnE "runs-on|matrix" .github/workflows/ 2>/dev/null | head
```

| Version | Change that matters |
|---|---|
| 3.12 | `Path.is_junction`; `os.path.isjunction` |
| 3.13 | `os.path.isreserved` (Windows); `os.chmod(..., follow_symlinks=False)` and `os.lchmod` on Windows |
| 3.15 | UTF-8 mode on by default on every OS (PEP 686) |

## Detect the OS

`sys.platform` is the check to use: it is fixed at build time, cheap, and type checkers narrow code on it (`if sys.platform == "win32":` hides Windows-only APIs from the other branches).

```python
import os
import platform
import sys

print(sys.platform)                  # 'darwin', 'linux' or 'win32' (also on 64-bit Windows)
print(os.name)                       # 'posix' or 'nt'
print(platform.system(), platform.release(), platform.machine())   # kernel name, kernel version, CPU
if sys.platform == "darwin":
    print("macOS", platform.mac_ver()[0])
elif sys.platform == "win32":
    print("Windows", platform.win32_ver()[0])
elif sys.platform == "linux":
    print(platform.freedesktop_os_release().get("PRETTY_NAME"))   # reads /etc/os-release
```

- Test for features, not OSes, where you can: `hasattr(os, "fork")`, `hasattr(signal, "SIGHUP")`, `shutil.which("git")`.
- `platform.machine()` gives `arm64` on Apple silicon macOS, `aarch64` on ARM Linux and `ARM64` on Windows on ARM; normalise before comparing.

## Paths, drives and UNC shares

`pathlib.Path` uses the right separator and rules on each OS; `PureWindowsPath` and `PurePosixPath` apply one OS's rules anywhere, which is how to handle Windows paths in code that runs on a Mac (and how to test it).

```python
from pathlib import Path, PurePosixPath, PureWindowsPath

doc = PureWindowsPath(r"C:\Users\Me\Documents\report.txt")
print(doc.drive, doc.anchor, doc.name)                       # C: C:\ report.txt
print(PureWindowsPath(r"\\server\share\team\notes.md").drive)   # \\server\share: a UNC share
print(PureWindowsPath("C:/Users/ME") == PureWindowsPath("c:/users/me"))   # True: case-insensitive
print(PurePosixPath("/Users/ME") == PurePosixPath("/users/me"))           # False
print(Path("data") / "2026" / "a.csv")                       # the native separator for this OS
```

- Never build paths with `+ "/" +` or `"\\"`; never split on `/` to get parts.
- `Path.home()` is `C:\Users\<name>` on Windows; per-user app folders come from platformdirs (python-files-pathlib skill), never from hard-coded `~/.config` or `%APPDATA%` strings.

## Names that are not portable

A name valid on Linux can be invalid or special on Windows; check names that come from people or other systems before creating files.

| Rule | macOS | Linux | Windows |
|---|---|---|---|
| case-sensitive by default | no (APFS) | yes | no (NTFS) |
| forbidden characters | `/` (and `:` in Finder) | `/` and NUL | `< > : " / \ \| ? *` and control characters |
| reserved names | none | none | `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`, with any extension |
| trailing dot or space | allowed | allowed | stripped silently |
| maximum path length | 1024 | 4096 | 260 unless long paths are enabled |

```python
import ntpath
import re

BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def portable_name(name: str) -> str:
    """A file name valid on every OS: forbidden characters replaced, Windows reserved names avoided."""
    clean = BAD.sub("_", name).rstrip(". ") or "_"
    if ntpath.isreserved(clean):                             # 3.13+: CON, nul.txt, COM1...
        clean = "_" + clean
    return clean[:200]


print([portable_name(n) for n in ["report: Q3?.pdf", "con.txt", "notes. ", "a/b"]])
```

Two files whose names differ only in case (`Readme.md`, `README.md`) cannot both exist on macOS and Windows; git checkouts silently lose one. Keep names unique ignoring case.

## Line endings and newline=

Text mode translates line endings: reading turns `\r\n` and `\r` into `\n` on every OS, and writing turns `\n` into `\r\n` on Windows only. Files that other programs parse need a fixed ending.

- Write `newline="\n"` for files with a defined format (JSON Lines, shell scripts, files compared in tests), so Windows does not add `\r`.
- Open CSV with `newline=""` and let the `csv` module write `\r\n`.
- Add a `.gitattributes` with `* text=auto eol=lf` (and `*.bat text eol=crlf`) so git does not convert endings differently on each developer's machine.
- A shell script with `\r\n` fails on Linux with `/usr/bin/env: 'python3\r': No such file or directory`.

## Encodings and UTF-8 mode

Before Python 3.15, `open()` without `encoding=` used the locale encoding: UTF-8 on macOS and most Linux systems, a legacy code page such as cp1252 on Windows. Python 3.15 turns UTF-8 mode on everywhere.

- Always pass `encoding="utf-8"` to `open`, `read_text`, `write_text` and `subprocess.run(text=True)` while 3.11 to 3.14 are supported.
- Run tests with `-X warn_default_encoding` to find every call that relies on the default.
- Output to a Windows console is UTF-8 (since 3.6); output redirected to a file or pipe follows UTF-8 mode, so set `PYTHONUTF8=1` for older Pythons in CI.

## Open files, locks and deleting on Windows

Windows refuses to delete, rename or replace a file that any process has open without delete sharing, and Python opens files without it. Code that deletes or replaces files while they are open works on macOS and Linux and fails on Windows with `PermissionError: [WinError 32]`.

- Close files before deleting, renaming or replacing them; use `with` blocks so they close on errors too.
- Antivirus scanners and search indexers open new files briefly; retry a failed delete or `os.replace` a few times with a short sleep (python-files-pathlib skill).
- `tempfile.NamedTemporaryFile` cannot be reopened by name while open on Windows; use `delete_on_close=False` (3.12) and close it first.
- File locks differ: `fcntl` exists only on POSIX, `msvcrt.locking` only on Windows, and Windows locks are mandatory. Use the `filelock` package for one API.

## Signals on Windows

Windows has only a few signals; `SIGKILL`, `SIGHUP`, `SIGUSR1`, `SIGUSR2`, `SIGCHLD` and `SIGALRM` do not exist, so code that names them raises `AttributeError` there. Ctrl+C raises `KeyboardInterrupt` on every OS.

```python
import signal
import sys
import threading

stop = threading.Event()


def request_stop(signum: int, frame: object) -> None:
    stop.set()


signal.signal(signal.SIGINT, request_stop)                  # Ctrl+C, every OS
signal.signal(signal.SIGTERM, request_stop)                 # sent by docker stop, systemd, kill
if sys.platform == "win32":
    signal.signal(signal.SIGBREAK, request_stop)            # Ctrl+Break, and what CTRL_BREAK_EVENT delivers
if hasattr(signal, "SIGHUP"):
    signal.signal(signal.SIGHUP, request_stop)              # terminal closed; POSIX only
```

- `os.kill(pid, signal.SIGTERM)` on Windows does not deliver a signal: it ends the process at once with `TerminateProcess`. Only `CTRL_C_EVENT` and `CTRL_BREAK_EVENT` reach a console process, and only within its process group.
- To stop a child process gracefully on Windows, start it with `CREATE_NEW_PROCESS_GROUP` and send `CTRL_BREAK_EVENT`, or ask it to exit over stdin or a socket. Details are in [windows-specifics.md](references/windows-specifics.md).

## Processes, executables and PATHEXT

Process creation differs: POSIX can `fork`, Windows cannot; Windows finds programs through `PATHEXT` extensions (`.exe`, `.cmd`, `.bat`); scripts installed by pip or uv are `.exe` launchers on Windows.

- Resolve programs with `shutil.which("node")`; it applies `PATHEXT` on Windows, and finds `npm.cmd` where `subprocess.run(["npm"])` fails.
- Run Python itself with `sys.executable`, never `"python"` or `"python3"` (on Windows `python3` may be the Microsoft Store stub).
- Multiprocessing starts workers with `spawn` on Windows and macOS and `forkserver` on Linux (3.14); guard entry code with `if __name__ == "__main__":` everywhere.
- Shell built-ins (`dir`, `copy`, `echo`) are not programs on Windows; do the work in Python instead.

## Permissions and chmod on Windows

Windows has no POSIX permission bits: `os.chmod` changes only the read-only attribute (`stat.S_IWRITE` on or off), the executable bit does not exist, and access is controlled by ACLs that Python's standard library does not manage.

- Files created with mode `0o600` on Windows are still readable by the same user's other processes and by administrators; protect secrets with the user profile folder or the OS keychain.
- `os.access(path, os.X_OK)` is true for any existing file on Windows; use `shutil.which` to ask "can this run".
- Hidden files: on macOS and Linux a leading `.`; on Windows the hidden attribute (`path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN`).

## Symlinks and junctions

Creating a symlink on Windows needs administrator rights or Developer Mode, otherwise `os.symlink` raises `OSError: [WinError 1314] A required privilege is not held by the client`. Windows also has junctions (folder links that need no privilege), which `Path.is_junction()` (3.12) detects.

- Code that creates symlinks needs a fallback on Windows (copy the file) and tests skip when the privilege is missing.
- `os.symlink(src, dst, target_is_directory=True)` is required for folder links on Windows.

## Open a file with the default app

Opening a file or URL in the app the person chose is one function with one branch per OS; `webbrowser.open` covers URLs on every OS.

```python
import os
import subprocess
import sys
from pathlib import Path


def open_with_default_app(path: Path, platform: str = sys.platform) -> list[str] | None:
    if platform == "win32":
        os.startfile(path)                                   # Windows only; returns at once
        return None
    command = ["open", str(path)] if platform == "darwin" else ["xdg-open", str(path)]
    return command                                           # the caller runs it: subprocess.run(command, check=True)


print(open_with_default_app(Path("report.pdf"), "darwin"), open_with_default_app(Path("report.pdf"), "linux"))
```

## A CI matrix across three OSes

Code is portable when tests pass on all three OSes on every change. A GitHub Actions matrix runs the same job on each.

```yaml
jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python: ["3.14", "3.15"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
      - run: uv sync --locked --python ${{ matrix.python }}
      - run: uv run python -X warn_default_encoding -m pytest -q
```

More on the matrix, shells and fixtures is in [ci-and-testing.md](references/ci-and-testing.md).

## Test OS-specific code with the platform injected

Each OS difference lives in one small function that takes the platform as a parameter with `sys.platform` as the default; tests then cover all three branches on any machine.

```python
import sys
from pathlib import PurePath, PurePosixPath, PureWindowsPath

import pytest


def cache_root(home: PurePath, platform: str = sys.platform, env: dict[str, str] | None = None) -> PurePath:
    env = env or {}
    if platform == "win32":
        return PureWindowsPath(env.get("LOCALAPPDATA", home / "AppData" / "Local"))
    if platform == "darwin":
        return home / "Library" / "Caches"
    return PurePosixPath(env.get("XDG_CACHE_HOME", home / ".cache"))


@pytest.mark.parametrize(("platform", "home", "expected"), [
    ("darwin", PurePosixPath("/Users/me"), "/Users/me/Library/Caches"),
    ("linux", PurePosixPath("/home/me"), "/home/me/.cache"),
    ("win32", PureWindowsPath(r"C:\Users\me"), r"C:\Users\me\AppData\Local"),
])
def test_cache_root(platform: str, home: PurePath, expected: str) -> None:
    assert str(cache_root(home, platform)) == expected


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
```

Never monkeypatch `sys.platform` in a test: the standard library reads it too, and half the process starts behaving like another OS.

## Verify

Most checks run on one machine; the last word belongs to CI on all three OSes.

```bash
uv run python -X warn_default_encoding -W error::EncodingWarning -m pytest -q   # any open() without encoding= fails
uv run ruff check --select PTH,PLW1514 --preview src   # os.path habits and missing encodings
git ls-files | tr '[:upper:]' '[:lower:]' | sort | uniq -d   # expect nothing: names that differ only in case
git ls-files --eol | grep -E "w/crlf" | grep -vE "\.(bat|cmd)$"   # expect nothing: CRLF outside batch files
grep -rnE "os\.fork|signal\.SIG(KILL|HUP|USR1|USR2|ALRM|CHLD)|\bfcntl\b" src   # each hit needs a guard
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `path + "/" + name` | `Path(path) / name` |
| `open(path)` with no encoding | `open(path, encoding="utf-8")` |
| `platform.system() == "Windows"` | `sys.platform == "win32"`, which type checkers narrow on |
| monkeypatching `sys.platform` | the platform as a parameter |
| `subprocess.run(["npm", ...])` | `shutil.which("npm")` first |
| `"python3"` in a command | `sys.executable` |
| `~/.config` or `%APPDATA%` in a string | `platformdirs` |
| `os.kill(pid, signal.SIGTERM)` as a graceful stop on Windows | `CTRL_BREAK_EVENT`, or ask over stdin or a socket |

## Checklist

Code is portable when every item holds.

- [ ] OS checks use `sys.platform`, in a few small functions with the platform injected and tested for all three values.
- [ ] Paths come from `pathlib`; names from outside are checked for forbidden characters, reserved names and case clashes.
- [ ] Every text open has `encoding=` (until 3.15 is the minimum) and `newline=` where the format fixes the ending.
- [ ] Files are closed before delete, rename or replace; deletes and replaces retry on Windows.
- [ ] Only `SIGINT` and `SIGTERM` are assumed; POSIX-only signals are behind `hasattr`.
- [ ] Programs are found with `shutil.which`; Python runs as `sys.executable`; process code has a `__main__` guard.
- [ ] Symlinks, chmod and hidden files have Windows behaviour or a clear refusal.
- [ ] CI runs the test suite on Linux, macOS and Windows.

## Gotchas

These are the portability bugs seen most often.

1. **`PermissionError: [WinError 32] The process cannot access the file because it is being used by another process`.** A file is still open, by you or by antivirus. Close it first; retry.
2. **`AttributeError: module 'signal' has no attribute 'SIGHUP'` on Windows.** POSIX-only signal. Guard with `hasattr`.
3. **`FileNotFoundError` running `npm` or `code` on Windows.** They are `.cmd` files. Resolve with `shutil.which`.
4. **`UnicodeDecodeError` or mojibake only on Windows.** No `encoding=` on Python before 3.15. Pass `encoding="utf-8"`.
5. **Files with `\r\n` in a Linux container, or `python3\r` not found.** A file was written or checked out on Windows in text mode. Write with `newline="\n"`; set `.gitattributes`.
6. **A test that passes on macOS fails on Linux.** Case-insensitive APFS hid a file opened with the wrong case (`Data.csv` for `data.csv`). Fix the case; CI on Linux catches it.
7. **`OSError: [WinError 1314] A required privilege is not held by the client`.** Creating a symlink without Developer Mode. Fall back to a copy.
8. **`FileNotFoundError` for a long path only on Windows.** The 260-character limit. Enable long paths or shorten the folder structure.

## References

The reference files hold deeper material.

- [windows-specifics.md](references/windows-specifics.md): long paths, deleting open files with retries, stopping child processes with CTRL_BREAK_EVENT, console colours, junctions, the registry, and per-machine versus per-user folders
- [ci-and-testing.md](references/ci-and-testing.md): the GitHub Actions matrix in detail, shells per OS, line endings in fixtures, OS-specific test markers, path assertions, and testing Linux in a container from a Mac

## See also

- `python-files-pathlib`: paths, atomic writes and locks
- `python-subprocess`: starting and stopping programs
- `python-text-unicode-regex`: encodings and Unicode names
- `python-services-shutdown`: signals in long-running services
- `python-configuration`: per-user config folders
- `uv-projects`: CI with uv
