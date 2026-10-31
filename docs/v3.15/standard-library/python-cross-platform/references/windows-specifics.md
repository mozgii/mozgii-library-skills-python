---
title: Windows specifics for Python developers
author: MOZGII, Inc.
lang: en
---

# Windows specifics for Python developers

What a developer who works on macOS or Linux needs to know to make Python code behave on Windows: long paths, deleting files that are still open, stopping child processes gracefully, apps without a console, terminal colours, junctions, the registry, and which folder a file belongs in. The Windows-only branches below are written against the documented APIs; the parts that run on any OS were run on macOS.

## Contents

This reference has 8 sections.

1. Long paths and the 260-character limit
2. Deleting and replacing files that are open
3. Stopping a child process gracefully
4. Apps without a console: pythonw.exe
5. Colours in the terminal
6. Symlinks and junctions
7. Reading the registry with winreg
8. Which folder a file belongs in

## 1. Long paths and the 260-character limit

Windows APIs limit paths to 260 characters (MAX_PATH) unless long paths are enabled for the machine (`HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled = 1`) and the program declares support; python.exe declares it, and the python.org installer offers to set the registry value. Deep `node_modules` trees, long user names and generated file names hit the limit first.

```python
import sys


def long_paths_enabled(platform: str = sys.platform) -> bool | None:
    """True or False on Windows; None elsewhere, where the limit does not apply."""
    if platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
            return value == 1
    except OSError:
        return False


print(long_paths_enabled())
```

- Without the setting, operations on longer paths fail with `FileNotFoundError` or `[WinError 206] The filename or extension is too long`.
- The `\\?\C:\very\long\path` prefix bypasses the limit for most file APIs, but turns off `/` translation and `..` handling; use it only for absolute, normalised paths.
- Other programs you call (older tools, some installers) may not support long paths even when Python does.

## 2. Deleting and replacing files that are open

Windows refuses to delete, rename or replace a file that is open in any process unless that process opened it with delete sharing, which Python's `open` does not. Antivirus scanners and the search indexer open new files for a moment, so even correct code sees occasional `PermissionError` on Windows.

```python
import os
import sys
import time
from pathlib import Path


def retry_on_windows(action, *args, attempts: int = 10, platform: str = sys.platform):
    for i in range(attempts):
        try:
            return action(*args)
        except PermissionError:
            if platform != "win32" or i == attempts - 1:
                raise
            time.sleep(0.05 * (i + 1))                     # 50 ms, 100 ms, ... about 2.7 s in total


p = Path("old.txt")
p.write_text("x", encoding="utf-8")
Path("new.txt").write_text("y", encoding="utf-8")
retry_on_windows(os.replace, "new.txt", p)
retry_on_windows(p.unlink)
print(p.exists())
```

- Retries cover short holds by scanners; a file held open by an editor or another process of yours stays locked until it is closed.
- Memory-mapped files (`mmap`) and files opened by C libraries count as open too.
- A process cannot delete its own running `.exe`; updaters rename the old executable first and delete it on the next start.

## 3. Stopping a child process gracefully

Windows has no `SIGTERM` delivery between processes: `Popen.terminate()` and `os.kill(pid, SIGTERM)` end the process at once, with no cleanup. A console child can be asked to stop with `CTRL_BREAK_EVENT` if it was started in its own process group, and the child sees it as `signal.SIGBREAK`.

```python
import signal
import subprocess
import sys

CHILD = r"""
import signal, sys, time
def stop(signum, frame):
    print("child: cleaning up", flush=True)
    sys.exit(0)
signal.signal(signal.SIGBREAK if sys.platform == "win32" else signal.SIGTERM, stop)
print("child: ready", flush=True)
time.sleep(30)
"""

flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
proc = subprocess.Popen([sys.executable, "-c", CHILD], stdout=subprocess.PIPE, text=True, creationflags=flags)
print(proc.stdout.readline().strip())
if sys.platform == "win32":
    proc.send_signal(signal.CTRL_BREAK_EVENT)                # delivered as SIGBREAK in the child
else:
    proc.send_signal(signal.SIGTERM)
print(proc.stdout.readline().strip(), "| exit code", proc.wait(timeout=10))
```

- `CTRL_C_EVENT` cannot target a process group, so it cannot stop one child; use `CTRL_BREAK_EVENT`.
- Neither event reaches a child without a console (a GUI app or `pythonw.exe`); give such children a stop command over stdin, a pipe or a socket.
- Killing a whole tree on Windows: `taskkill /PID <pid> /T /F`, or a Job Object (python-subprocess skill).

## 4. Apps without a console: pythonw.exe

A Windows app started with `pythonw.exe`, or frozen as a windowed executable, has no console: `sys.stdout` and `sys.stderr` are `None`. `print` then does nothing and logging to the stream handler is silently lost, while code that calls `sys.stderr.write` or reads `sys.stdout.encoding` raises `AttributeError`.

```python
import logging
import sys
from pathlib import Path


def setup_logging(log_dir: Path) -> None:
    handlers: list[logging.Handler] = [logging.FileHandler(log_dir / "app.log", encoding="utf-8")]
    if sys.stderr is not None:                             # a console exists
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)


setup_logging(Path("."))
logging.getLogger("app").info("started")
print(Path("app.log").read_text(encoding="utf-8").strip())
```

Always log to a file in GUI apps on every OS; on Windows it is the only place the output goes.

## 5. Colours in the terminal

ANSI colour codes work in Windows Terminal and in the console of current Windows versions once virtual terminal processing is on, which modern terminals enable by default; the legacy console host may still print the raw escape codes. Libraries handle the difference.

- `rich` and `click.style` detect the terminal and enable colours on Windows themselves.
- `colorama.just_fix_windows_console()` enables processing on old consoles and does nothing elsewhere.
- Respect `NO_COLOR` and disable colours when output is not a terminal (`sys.stdout.isatty()` is false), the same on every OS.

## 6. Symlinks and junctions

Windows has symlinks (to files or folders, created only with administrator rights or Developer Mode) and junctions (to local folders, no privilege needed). Python reads both; creating a symlink without the privilege raises `OSError: [WinError 1314]`.

```python
import os
import shutil
import sys
from pathlib import Path


def link_or_copy(target: Path, link: Path) -> str:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
        return "symlink"
    except OSError as exc:
        if sys.platform != "win32" or getattr(exc, "winerror", None) != 1314:
            raise
        if target.is_dir():
            shutil.copytree(target, link)
        else:
            shutil.copy2(target, link)
        return "copy"


Path("shared").mkdir(exist_ok=True)
print(link_or_copy(Path("shared").resolve(), Path("shared-link")), Path("shared-link").is_symlink())
```

- `Path.is_junction()` (3.12) detects junctions; `Path.is_symlink()` is false for them.
- `shutil.rmtree` does not follow junctions or symlinks into their targets; deleting the link removes only the link.
- Git for Windows checks out symlinks as plain text files unless `core.symlinks` is on and the privilege is held; do not rely on symlinks in a repository that Windows developers clone.

## 7. Reading the registry with winreg

`winreg` (Windows only) reads and writes the registry, where Windows keeps system settings and where some programs record their install folders. Prefer files in the app's own folders for your own settings; read the registry for system facts.

```python
import sys


def windows_theme_is_dark(platform: str = sys.platform) -> bool | None:
    if platform != "win32":
        return None
    import winreg
    path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
    except OSError:
        return None


print(windows_theme_is_dark())
```

Import `winreg` inside the Windows branch, or behind `if sys.platform == "win32":` at module level, so the module still imports on macOS and Linux and type checkers skip it there.

## 8. Which folder a file belongs in

Windows separates per-user and per-machine data and roaming and local data; writing to the wrong place fails or surprises people.

| Folder | Variable | Use |
|---|---|---|
| program files | `%ProgramFiles%` | installed code; read-only for normal users |
| per-user roaming | `%APPDATA%` | small settings that follow a domain user between machines |
| per-user local | `%LOCALAPPDATA%` | caches, logs, large data, settings on one machine |
| per-machine data | `%PROGRAMDATA%` | data shared by all users; writable by administrators by default |
| temporary | `%TEMP%` | scratch files; `tempfile` uses it |

- Never write next to the executable in `Program Files`; it fails for normal users.
- `platformdirs` maps these for you (`user_config_dir` is local unless `roaming=True`); see the python-files-pathlib skill.
- Environment variable names are case-insensitive on Windows; `os.environ` upper-cases them there.
