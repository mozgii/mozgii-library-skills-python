---
name: python-services-shutdown
description: Runs Python programs as long-lived services that stop cleanly on macOS, Linux and Windows - SIGTERM and SIGINT handlers, Windows Ctrl+Break and console events, graceful shutdown in threaded and asyncio code (add_signal_handler is Unix-only), draining under a deadline, atexit limits, systemd Type=notify with sd_notify, restart policies, launchd plists, Windows services with pywin32 or a wrapper, health and readiness, single-instance locks instead of pid files, and not daemonising. Use when a Python worker or server must stop without losing work, runs under systemd, launchd, Docker or as a Windows service, restarts on failure, or when Ctrl+C or docker stop leaves it hanging or killed.
metadata:
  tags:
  - python
  - signals
  - graceful-shutdown
  - systemd
  - launchd
  - windows-service
  - daemon
  - cross-platform
---

# Python services: signals, graceful shutdown and supervisors

A service is a program a supervisor starts and stops: systemd on Linux, launchd on macOS, the Service Control Manager on Windows, or a container runtime. The supervisor asks it to stop with a signal or a control code, waits a fixed time, then kills it. A good Python service handles that request, finishes or hands back its work inside the time, and exits with a meaningful code. Container specifics (PID 1, `--init`) are the `python-containers` skill; a worker driven over stdio by a host app is the `python-stdio-worker` skill.

## Step 0: read the versions and the supervisor

Signal and service behaviour depends on the Python version and on what supervises the process, so read both first.

```bash
uv run python -c "import sys, signal; print(sys.version_info[:3], sys.platform, [n for n in ('SIGTERM','SIGINT','SIGHUP','SIGBREAK') if hasattr(signal, n)])"
systemctl --version | head -1        # Linux: systemd version (Type=notify-reload needs 253+)
launchctl version 2>/dev/null        # macOS
```

On Windows, `uv pip list | findstr pywin32` shows whether pywin32 (312, October 2026) is installed for a native service.

## How a supervisor stops a process

Each supervisor sends a polite request, waits a timeout, then forces the stop. The service must finish its shutdown inside that timeout.

| Supervisor | Polite request | Waits | Then |
|---|---|---|---|
| systemd | `SIGTERM` (`KillSignal=`) to the whole cgroup at once by default (`KillMode=control-group`); `KillMode=mixed` sends it to the main process only | `TimeoutStopSec=` (90 s default) | `SIGKILL` to every process in the unit |
| launchd | `SIGTERM` | `ExitTimeOut` (default system-defined: set it) | `SIGKILL` |
| Docker, Kubernetes | `SIGTERM` to PID 1 | `docker stop -t` (10 s), `terminationGracePeriodSeconds` (30 s) | `SIGKILL` |
| Windows Service Control Manager | `SERVICE_CONTROL_STOP` to the service handler | the wait hint the service reports; less at system shutdown | process terminated |
| a terminal | `SIGINT` (Ctrl+C) on POSIX; `CTRL_C_EVENT` or `CTRL_BREAK_EVENT` on Windows | nothing | the user presses again or closes the window |

Python's defaults are not graceful: `SIGINT` raises `KeyboardInterrupt` wherever the main thread happens to be, and `SIGTERM` has no handler, so the process dies at once with no `finally` blocks, no `atexit` handlers and no flushed buffers (shell exit code 143).

## Graceful shutdown in threaded code

Graceful shutdown means: stop taking new work, let work in progress finish under a deadline, release resources, exit. In threaded code a signal handler sets a `threading.Event` that every loop checks; handlers run only in the main thread, so keep the main thread waiting on something that wakes up.

```python
import logging
import os
import queue
import signal
import sys
import threading
import time

log = logging.getLogger("service")
stop = threading.Event()


def request_stop(signum: int, _frame: object) -> None:
    if stop.is_set():
        log.warning("second %s: exiting now", signal.Signals(signum).name)
        os._exit(130)                                      # Ctrl+C twice, or SIGTERM then SIGINT
    log.info("%s received: draining", signal.Signals(signum).name)
    stop.set()


def install_handlers() -> None:
    signal.signal(signal.SIGTERM, request_stop)            # systemd, launchd, docker stop, kill
    signal.signal(signal.SIGINT, request_stop)             # Ctrl+C
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, request_stop)       # Ctrl+Break, CTRL_BREAK_EVENT


def worker(jobs: queue.Queue[int]) -> None:
    while not stop.is_set() or not jobs.empty():           # finish what is queued, then exit
        try:
            job = jobs.get(timeout=0.5)
        except queue.Empty:
            continue
        time.sleep(0.2)                                    # the real work
        log.info("job %d done", job)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    install_handlers()
    jobs: queue.Queue[int] = queue.Queue(maxsize=100)
    threads = [threading.Thread(target=worker, args=(jobs,), name=f"worker-{i}") for i in range(2)]
    for t in threads:
        t.start()
    log.info("ready")
    n = 0
    while not stop.wait(0.1):                              # main thread stays responsive to signals
        if not jobs.full():
            jobs.put(n)
            n += 1
    deadline = time.monotonic() + 10                       # drain budget, below the supervisor's timeout
    for t in threads:
        t.join(timeout=max(0.0, deadline - time.monotonic()))
    if any(t.is_alive() for t in threads):
        log.error("drain timed out")
        logging.shutdown()                                 # os._exit skips every flush and exit hook
        os._exit(1)                                        # return 1 would wait for the stuck thread until SIGKILL
    log.info("stopped cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- Waits in the main thread use a timeout (`stop.wait(0.1)`, `join(timeout=...)`), so it returns to Python bytecode regularly and the handler can run; a C call that blocks without a timeout can delay the handler indefinitely.
- A second signal forces the exit: people press Ctrl+C twice, and supervisors may escalate.
- Worker threads are non-daemon and joined; daemon threads are killed mid-write at interpreter exit.
- Past the deadline only `os._exit` ends the process. `return` and `sys.exit` both run the interpreter's shutdown, which joins every non-daemon thread, so a stuck worker keeps the process alive after the error line. `os._exit` runs no `finally` and no `atexit` handler and flushes nothing: flush logs first, and hand back unfinished work before it.

## Graceful shutdown in asyncio code

In asyncio code the handler sets an `asyncio.Event`. `loop.add_signal_handler` is Unix-only (it raises `NotImplementedError` on Windows), so on Windows install a plain `signal.signal` handler that hands the event to the loop with `call_soon_threadsafe`.

```python
import asyncio
import signal
import sys


def install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    names = ["SIGTERM", "SIGINT"] + (["SIGBREAK"] if sys.platform == "win32" else [])
    for name in names:
        sig = getattr(signal, name)
        if sys.platform == "win32":
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
        else:
            loop.add_signal_handler(sig, stop.set)


async def main() -> int:
    stop = asyncio.Event()
    install_stop_handlers(stop)
    print("ready", flush=True)
    await stop.wait()                                      # the service runs its tasks meanwhile
    try:
        async with asyncio.timeout(10):                    # drain budget
            await asyncio.sleep(0.1)                       # wait for in-flight work here
    except TimeoutError:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

Cancelling a `TaskGroup` with a bounded drain is in the `python-asyncio` skill.

## Draining work under a deadline

Draining is the ordered stop: refuse new work, finish or return in-flight work, flush, close, exit. Budget it below the supervisor's timeout so your code, not `SIGKILL`, decides what happens to unfinished work.

1. Stop intake: close listening sockets, stop consuming from the queue, mark the service not ready.
2. Finish in-flight work up to a deadline (`time.monotonic()` based); past it, hand work back (nack, re-enqueue, write a checkpoint) instead of dropping it.
3. Flush what buffers: logs, telemetry exporters, files (`flush()` then `os.fsync()` for data that must survive a power loss).
4. Close connections and release locks; exit 0 for a clean stop, non-zero when work was abandoned.

Set the supervisor's timeout above the drain budget plus a margin: `TimeoutStopSec=`, `ExitTimeOut`, `docker stop -t`, `terminationGracePeriodSeconds`.

## atexit and its limits

`atexit` handlers run when the interpreter exits normally: `main` returns, `sys.exit()` is called, or an unhandled exception ends the program. They are a last cleanup hook, not a shutdown mechanism.

- They do not run on `SIGKILL`, `os._exit()`, a fatal crash, or a signal without a Python handler (the default `SIGTERM`). Install a `SIGTERM` handler that calls `sys.exit()` if cleanup must run.
- Non-daemon threads are joined before `atexit` handlers run. A worker thread waiting for a stop flag that an `atexit` handler sets therefore waits forever; set the flag from the signal handler or `main`'s `finally`.
- Handlers run in reverse registration order.

## Windows console events

On Windows, Ctrl+C and Ctrl+Break arrive as console events: Python maps `CTRL_C_EVENT` to `SIGINT` and `CTRL_BREAK_EVENT` to `signal.SIGBREAK`. Closing the console window, logging off and shutting down send `CTRL_CLOSE_EVENT`, `CTRL_LOGOFF_EVENT` and `CTRL_SHUTDOWN_EVENT`, which Python does not map; a ctypes `SetConsoleCtrlHandler` recipe for them is in [windows-services.md](references/windows-services.md).

- For `CTRL_CLOSE_EVENT` Windows terminates the process a few seconds after the event, whatever the handler does; drain fast.
- To stop a child Python process gracefully on Windows, start it with `CREATE_NEW_PROCESS_GROUP` and send `proc.send_signal(signal.CTRL_BREAK_EVENT)`; `proc.terminate()` is `TerminateProcess` and runs no cleanup.
- GUI processes (anything that loads `user32.dll`) do not get logoff and shutdown events through console handlers; they handle `WM_QUERYENDSESSION`. Real services get them as service controls.

## Health and readiness

A health check answers "is the process alive and making progress"; a readiness check answers "may it receive work now". They differ during start-up and shutdown, and supervisors use them to restart or route around a process.

- Liveness: a heartbeat timestamp the main loop updates, checked against a maximum age; never a check that only proves the HTTP thread runs while the worker loop is stuck.
- Readiness: false until warm-up finishes (models loaded, migrations checked), and false again as soon as draining starts.
- systemd: `Type=notify` plus `READY=1` when ready, and `WatchdogSec=` plus `WATCHDOG=1` from the main loop as liveness. Docker: `HEALTHCHECK`. Kubernetes: separate liveness and readiness probes.
- Expose them as a heartbeat file, a local endpoint on `127.0.0.1`, or the supervisor's protocol; the `python-containers` skill has a stdlib health-check script.

## Single-instance locks instead of pid files

A pid file says which process was started, not whether it still runs: after a crash it holds a stale pid that may now belong to another process. An OS file lock is released automatically when the process dies, so it is the reliable single-instance check.

```python
import sys
from pathlib import Path
from typing import IO


def acquire_single_instance(lock_path: Path) -> IO[bytes] | None:
    """Return an open lock file while this process is the only instance, or None if another holds it."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+b")                             # never truncate: another instance may hold it
    try:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)  # lock the first byte; fail at once if taken
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f                                               # keep a reference: closing it releases the lock
```

- Put the lock in a per-user directory (`$XDG_RUNTIME_DIR`, `~/Library/Application Support/<app>`, `%LOCALAPPDATA%\<app>`).
- `flock` locks are not reliable on NFS; keep lock files on local disks.

## Do not daemonise

Daemonising (double fork, detaching, writing a pid file) predates supervisors. Under systemd, launchd, Docker or the Windows SCM it breaks supervision: the supervisor loses the process, cannot restart it and cannot collect its logs.

- Run in the foreground; the supervisor detaches, restarts and stops you.
- Log to stdout or stderr; journald, launchd (`StandardErrorPath`), Docker and service wrappers collect it. Set `PYTHONUNBUFFERED=1` or use `-u` so lines arrive at once.
- Exit non-zero on fatal errors so `Restart=on-failure` and `KeepAlive` restart the service; exit 0 only for a clean, intended stop.

## Running under systemd, launchd and Windows

Each OS has one native supervisor; its configuration is in the references.

- Linux: a systemd unit with `Type=notify`, `Restart=on-failure`, `TimeoutStopSec=`, hardening options, and a stdlib `sd_notify` sender: [systemd-and-launchd.md](references/systemd-and-launchd.md).
- macOS: a launchd agent (per user) or daemon (system) plist with `KeepAlive` and `ExitTimeOut`, loaded with `launchctl bootstrap`: same reference.
- Windows: a native service with pywin32's `ServiceFramework`, or a wrapper (WinSW, NSSM) around a console program: [windows-services.md](references/windows-services.md).

## Verify

Send the real signal and time the exit: a clean run proves nothing about shutdown. `scripts/shutdown_probe.py` does this on every OS.

```bash
python3 -I scripts/shutdown_probe.py --ready-line ready --grace 12 -- python3 -m my_service    # expect a clean exit, 0
```

Without `scripts/` on disk (an install that keeps only searchable text), run the same test by hand. On Linux and macOS:

```bash
python3 -u -m my_service & pid=$!
sleep 2; start=$(date +%s); kill -TERM "$pid"
wait "$pid"; echo "exit $? after $(( $(date +%s) - start )) s"     # expect exit 0, under the drain budget
```

On Windows, run this small probe; it starts the service in its own process group, so the event reaches it alone:

```python
import signal
import subprocess
import time

proc = subprocess.Popen(["python", "-m", "my_service"], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
time.sleep(2)
proc.send_signal(signal.CTRL_BREAK_EVENT)
print("exit", proc.wait(timeout=15))                               # expect 0
```

Plant a job that outlives the budget once: the service must exit non-zero with `drain timed out`, not hang until `SIGKILL`.

## Anti-patterns

| Instead of | Do |
|---|---|
| `time.sleep(60)` in the main loop | `stop.wait(60)`, which wakes on the signal |
| real work inside a signal handler | set a flag or an event; the main flow drains |
| `daemon=True` threads for work that writes | non-daemon threads, joined under a deadline |
| a double fork and a pid file | foreground under a supervisor, an OS file lock for single instance |
| a drain budget equal to the supervisor's timeout | a budget below it, with a margin for flushing |
| `proc.terminate()` to stop a Python child on Windows | `CTRL_BREAK_EVENT` to a child in its own process group |

## Checklist

A Python service stops cleanly when every item holds.

- [ ] `SIGTERM` and `SIGINT` (plus `SIGBREAK` on Windows) set a stop flag; a second signal forces exit.
- [ ] The main thread waits with timeouts; workers check the flag and are joined with a deadline.
- [ ] The drain budget is shorter than the supervisor's stop timeout, and unfinished work is handed back.
- [ ] Logs and telemetry are flushed; exit code 0 means a clean stop, non-zero a problem.
- [ ] Readiness turns false when draining starts; liveness proves the main loop progresses.
- [ ] Single instance is enforced with an OS file lock, not a pid file.
- [ ] The process runs in the foreground under a supervisor and logs to stdout or stderr.
- [ ] Tested with `scripts/shutdown_probe.py`, or the inline probe under Verify, on every OS it ships for.

## Gotchas

These are the shutdown bugs seen most.

1. **`docker stop` or `systemctl stop` takes the full timeout, then kills.** No `SIGTERM` handler, Python as PID 1 in a container, or a main thread blocked in a call without a timeout. Install the handler and wait with timeouts.
2. **Ctrl+C prints a `KeyboardInterrupt` traceback from a random line.** The default `SIGINT` behaviour. Install a handler, or catch `KeyboardInterrupt` once at the top and exit 130.
3. **`NotImplementedError` from `loop.add_signal_handler` on Windows.** It is Unix-only. Use `signal.signal` with `call_soon_threadsafe`.
4. **Cleanup in `atexit` never runs under systemd.** `SIGTERM` killed the process with no handler. Handle `SIGTERM` and call `sys.exit()`.
5. **The service hangs at exit.** A non-daemon thread waits for a flag set in an `atexit` handler, which runs only after that thread ends. Set the flag before returning from `main`.
6. **A second instance starts after a crash, or refuses to start.** A stale pid file. Use `flock` / `msvcrt.locking`.
7. **No log lines appear in journald until the service stops.** stdout is block-buffered when it is not a terminal. Set `PYTHONUNBUFFERED=1`.
8. **`proc.terminate()` on Windows skips all cleanup in the child.** It is `TerminateProcess`. Send `CTRL_BREAK_EVENT` to a child started in its own process group.

## References

The reference files hold the supervisor configuration and the helper script.

- [systemd-and-launchd.md](references/systemd-and-launchd.md): systemd units, `Type=notify` and `sd_notify` in the standard library, watchdog, restarts, hardening; launchd plists, `KeepAlive`, `launchctl bootstrap`
- [windows-services.md](references/windows-services.md): pywin32 `ServiceFramework`, WinSW and NSSM wrappers, recovery options, console events with ctypes
- [scripts/shutdown_probe.py](scripts/shutdown_probe.py): starts a program, sends `SIGTERM` (or `CTRL_BREAK_EVENT` on Windows) and reports whether and how fast it stopped

## See also

- `python-containers`: PID 1, `--init` and health checks in images
- `python-stdio-worker`: a worker a host app starts and stops over stdio
- `python-asyncio`: cancelling a `TaskGroup` with a bounded drain
- `python-subprocess`: stopping child process trees
- `python-logging`: flushing logs on the way out
