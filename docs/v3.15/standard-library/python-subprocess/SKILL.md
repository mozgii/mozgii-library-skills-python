---
name: python-subprocess
description: Runs external programs from Python safely and portably on macOS, Linux and Windows - choosing subprocess.run, Popen or asyncio, an argument list with check, timeout and capture, finding executables with shutil.which, never shell=True with untrusted input, streaming output without deadlocks, environment and encoding control, killing whole process trees on timeout, exit codes and signals, and asyncio subprocesses that do not outlive cancellation. Use when calling git, ffmpeg or any command-line tool from Python, capturing a command's output, running commands with timeouts, or stopping child processes reliably.
metadata:
  tags:
  - python
  - subprocess
  - processes
  - shell
  - timeout
  - cross-platform
---

# Python subprocesses

`subprocess` starts other programs and talks to them through pipes. Used carelessly it opens shell-injection holes, hangs on full pipes, leaves orphaned grandchildren, or works on macOS and breaks on Windows. This skill gives the safe patterns. Parallel Python work with threads, processes and interpreters is the `python-concurrency` skill. A long-lived Python worker that another program calls is the `python-stdio-worker` skill.

## Step 0: read the Python version

Timeouts, encodings and `asyncio` behaviour depend on the Python version and the OS, so read both, and confirm the program you mean to call is the one on `PATH`.

```bash
uv run python -c "import sys; print(sys.version_info[:3], sys.platform, sys.flags.utf8_mode)"
uv run python -c "import shutil; print(shutil.which('git'))"       # the program this code will call
```

## Choose run, Popen or asyncio

The choice follows from when you need the output and from what else the program does while the child runs.

| Need | Use | Why the others lose here |
|---|---|---|
| a command that finishes, and its result | `subprocess.run` | `Popen` adds pipe and wait handling for nothing; asyncio needs a running loop |
| output line by line, or input fed while it runs | `subprocess.Popen` | `run` returns only when the child has ended |
| a timeout that stops the child's own children too | `Popen` with its own session or process group | `run(timeout=...)` kills the direct child only |
| a child started from code on an event loop, or many children at once | `asyncio.create_subprocess_exec` | `run` and `Popen.wait` block the loop, and every other task with it |
| a long-lived Python child that takes many jobs | the `python-stdio-worker` skill | one process per job pays the start-up cost each time |

## Run a command

`subprocess.run` with an argument list is the default for any command that finishes.

```python
import shutil
import subprocess


def git_head(repo: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is not installed or not on PATH")
    done = subprocess.run(
        [git, "-C", repo, "rev-parse", "HEAD"],
        capture_output=True,            # stdout and stderr into done.stdout / done.stderr
        text=True, encoding="utf-8",    # str instead of bytes, with an explicit encoding
        check=True,                     # non-zero exit raises CalledProcessError
        timeout=10,                     # raises TimeoutExpired and kills the child
        stdin=subprocess.DEVNULL,       # never wait for keyboard input
    )
    return done.stdout.strip()
```

- Pass a list, never a string: each element reaches the program as one argument, whatever spaces or quotes it contains.
- `check=True` turns failures into exceptions; `CalledProcessError` carries `returncode`, `stdout` and `stderr`.
- Always set a `timeout` for programs that talk to the network or could wait forever.
- `stdin=subprocess.DEVNULL` stops a child from blocking on a prompt or stealing the parent's terminal input.
- To run Python itself, use `[sys.executable, "-m", "module"]`, not `"python"`, so the child uses the same interpreter and environment.

## Never build a shell command from input

`shell=True` passes one string to `/bin/sh` or `cmd.exe`, so any `;`, `|`, `$()` or `&` in user input runs as a command.

```python
# WRONG: filename "x; rm -rf ~" deletes the home directory
subprocess.run(f"convert {filename} out.png", shell=True)

# RIGHT: no shell, each argument separate; "--" ends options so "-rf" is a filename
subprocess.run(["convert", "--", filename, "out.png"], check=True)
```

- If a pipeline is really needed, connect two `Popen` objects, or do the filtering in Python.
- `shlex.join(args)` produces a shell-safe string for logging or for showing the user a command to copy.
- Windows `.bat` and `.cmd` files always run through `cmd.exe`, which has its own quoting rules; avoid passing untrusted arguments to them.
- Ruff's `S602`-`S607` rules flag shell use and partial executable paths.

## Executables and PATH

Resolve the program once and fail with a clear message when it is missing.

- `shutil.which("ffmpeg")` returns the full path, honouring `PATH` and, on Windows, `PATHEXT` (`.exe`, `.cmd`).
- A bundled binary is found by its path relative to the package (`importlib.resources`), not by `PATH`.
- On Windows, `subprocess.run(["git", ...])` works (the OS appends `.exe` and searches `PATH`), but `["npm", ...]` fails because `npm` is a `.cmd` file; `shutil.which` applies `PATHEXT` and finds both, so pass its result.

## Environment and working directory

The child inherits the parent's environment unless you pass `env`; pass a modified copy, and never put secrets in arguments.

```python
import os

env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "PG"))}   # drop variables that change behaviour
env["LC_ALL"] = "C.UTF-8"                       # stable, parseable output from Unix tools
env["MY_APP_TOKEN"] = token                     # secrets in the environment, not argv (argv is visible in ps)
subprocess.run([tool, "sync"], env=env, cwd=project_dir, check=True, timeout=60)
```

- `env=` replaces the whole environment: start from `os.environ` or the child may lose `PATH`, `HOME` and, on Windows, `SYSTEMROOT`.
- `cwd=` sets the child's working directory; never `os.chdir` in the parent for this.
- A Python child started with `-I` ignores `PYTHON*` variables and the user site directory.

## Text, bytes and encodings

Output is bytes unless you ask for text; choose the encoding explicitly so behaviour does not depend on the user's locale.

- `text=True, encoding="utf-8", errors="replace"` decodes as UTF-8 and never raises on bad bytes.
- Python 3.15 uses UTF-8 by default (PEP 686); on older versions the default is the locale encoding, which is often cp1252 on Windows.
- Binary output (images, archives) stays bytes: omit `text`.

## Stream output while it runs

Long-running commands should report progress line by line; read with `Popen` and merge stderr into stdout, or read the two pipes separately.

```python
from collections.abc import Iterator, Sequence


def stream_lines(cmd: Sequence[str]) -> Iterator[str]:
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                          text=True, encoding="utf-8", errors="replace", bufsize=1) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line.rstrip("\n")
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
```

- Never read `proc.stdout` to the end and then `proc.stderr` with both as pipes: when the child fills the stderr pipe buffer (about 64 KiB) it blocks, and both processes wait forever. Merge them (`stderr=STDOUT`), use `communicate()`, or read each pipe on its own thread.
- Many programs buffer output when stdout is a pipe, so lines arrive in bursts. For a Python child pass `-u`; for others look for a `--line-buffered` or progress flag.

## Timeouts that stop the whole tree

`subprocess.run(timeout=...)` kills the direct child only; grandchildren keep running. Start the child in its own process group and stop the group.

```python
import contextlib
import os
import signal
import sys


def run_tree(cmd: Sequence[str], timeout: float) -> int:
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True              # own session and process group
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, **kwargs)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_tree(proc)
        raise


def stop_tree(proc: subprocess.Popen[bytes], grace: float = 3.0) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGTERM)          # ask politely first
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=grace)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)          # always: the group can outlive its leader
    proc.wait()                                          # reap: no zombie left behind
```

The last `killpg` runs even when the leader exited in time: a grandchild that ignores `SIGTERM` stays alive after its parent has gone, and only `SIGKILL` to the group reaches it. `PermissionError` is suppressed because macOS can refuse the call for a group holding only zombies.

On Python 3.15, `Popen.wait(timeout=...)` waits on an event (pidfd, kqueue or `WaitForSingleObject`) instead of polling, so short timeouts no longer burn CPU.

## Exit codes and signals

The return code says how the child ended; read it before trusting the output.

| `returncode` | Meaning |
|---|---|
| `0` | success |
| `1` to `255` | the program's own failure code (`2` is often a usage error) |
| negative, POSIX only | killed by signal `-N`: `-15` SIGTERM, `-9` SIGKILL (often the out-of-memory killer) |
| `130` or `-2` | interrupted by Ctrl+C |
| `3221225477` (`0xC0000005`), Windows | access violation: the child crashed |

## Async subprocesses

In asyncio code, use `asyncio.create_subprocess_exec` so waiting on the child does not block the event loop.

```python
import asyncio
import contextlib
import os
import signal
from collections.abc import Sequence


async def run_async(cmd: Sequence[str], timeout: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL,
        start_new_session=os.name == "posix")
    try:
        async with asyncio.timeout(timeout):
            out, err = await proc.communicate()
    except BaseException:                                # TimeoutError and CancelledError both leave the child running
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()                              # the child only: taskkill /T for a whole tree
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode(), err.decode()
```

- Catch `BaseException`, not `TimeoutError`: cancelling the task raises `CancelledError`, which is not an `Exception`, and the child would keep running.
- Kill the group, not the child. On Python 3.14, `await proc.wait()` after `proc.kill()` blocked until a grandchild holding the pipes had exited, so a 0.5 s timeout took 6 s; on 3.15 it returned at once.

`create_subprocess_shell` exists but carries the same injection risk as `shell=True`. On Windows, asyncio subprocesses need the default Proactor event loop.

## Verify

Prove the three failures that reach production: a hostile argument, a full pipe and a surviving grandchild. `my_app.proc` stands for the module holding `run_tree`.

```python
# tests/test_proc.py
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from my_app.proc import run_tree

ECHO = [sys.executable, "-c", "import sys; print(sys.argv[1])"]


def test_hostile_name_stays_one_argument(tmp_path: Path) -> None:
    name = "x; touch pwned"
    done = subprocess.run([*ECHO, name], cwd=tmp_path, capture_output=True, text=True, check=True, timeout=10)
    assert done.stdout.strip() == name and not (tmp_path / "pwned").exists()


def test_large_output_on_both_pipes_does_not_hang() -> None:
    noisy = "import sys; sys.stderr.write('e' * 1_000_000); sys.stdout.write('o' * 1_000_000)"
    done = subprocess.run([sys.executable, "-c", noisy], capture_output=True, timeout=10, check=True)
    assert len(done.stdout) == len(done.stderr) == 1_000_000


@pytest.mark.skipif(os.name == "nt", reason="Windows: check with taskkill /T by hand")
def test_timeout_stops_the_grandchild(tmp_path: Path) -> None:
    marker = tmp_path / "survived"
    late = f"import pathlib, time; time.sleep(2); pathlib.Path({str(marker)!r}).touch()"
    parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {late!r}]); time.sleep(30)"
    with pytest.raises(subprocess.TimeoutExpired):
        run_tree([sys.executable, "-c", parent], timeout=0.5)
    time.sleep(2.5)
    assert not marker.exists()
```

```bash
uv run pytest -q tests/test_proc.py
uv run ruff check --select S602,S604,S605,S607 src      # expect nothing, or a reasoned ignore
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `shell=True` to get a pipeline | two `Popen` objects, or the filter in Python |
| `os.system` or `os.popen` | `subprocess.run` with a list |
| a call with no `timeout` | a timeout on every call that can wait |
| `proc.wait()` with `stdout=PIPE` | `communicate()`, or read while it runs |
| a secret in argv | the environment, stdin or a `0600` file |
| `"python"` as the program | `sys.executable`, so the child uses this interpreter |

## Checklist

A subprocess call is safe when every item holds.

- [ ] Arguments are a list; no `shell=True` with any outside input; `--` before user-supplied paths.
- [ ] The executable is resolved with `shutil.which` or a known path, with a clear error when missing.
- [ ] `check=True` (or an explicit return-code check) and a `timeout` on every call.
- [ ] `stdin=DEVNULL` unless the child needs input; encoding set explicitly for text.
- [ ] Long-running children run in their own process group and are stopped as a tree.
- [ ] Secrets travel in the environment, never in argv; logs use `shlex.join` without secrets.
- [ ] Tested on every OS the program supports, including Windows quoting and paths.

## Gotchas

These are the subprocess failures seen most.

1. **The program hangs forever.** Both stdout and stderr are pipes and only one is read; the other filled up. Use `communicate()`, merge with `stderr=STDOUT`, or read on threads.
2. **The timeout fired but the work continues.** Grandchildren survived the kill. Use a process group and `killpg`, or `taskkill /T` on Windows.
3. **`FileNotFoundError` for a command that works in the terminal.** It is a shell builtin or alias (`cd`, `ll`), a `.cmd` on Windows, or not on the child's `PATH`. Resolve with `shutil.which` and pass the full path.
4. **Output arrives all at once at the end.** The child buffers when stdout is a pipe. Use `-u` for Python children, or the program's line-buffering flag.
5. **`UnicodeDecodeError` reading output on Windows.** The default encoding was the code page. Pass `encoding="utf-8", errors="replace"`.
6. **Zombie processes pile up.** `Popen` objects were dropped without `wait()`. Always wait, or use `with Popen(...)`.
7. **`ps` shows a password.** It was passed as an argument. Use an environment variable, stdin or a file with `0600` permissions.
8. **The child keeps running after its asyncio task was cancelled.** `except TimeoutError` does not catch `CancelledError`. Kill it in `except BaseException`, as `run_async` does.

## See also

- `python-stdio-worker`: a long-lived Python child instead of one call per job
- `python-services-shutdown`: signals, process groups and clean exits
- `python-concurrency`: threads, processes and interpreters for parallel Python
- `python-security`: command injection in a wider review
- `python-cross-platform`: paths and quoting across operating systems
