# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright 2026 MOZGII, Inc. (https://mozgii.com/)
"""Start a program, ask it to stop the way a service manager would, and report how it ended.

Usage:
    python3 -I shutdown_probe.py [--after SECONDS] [--grace SECONDS] [--ready-line TEXT] -- COMMAND [ARGS...]

Examples:
    python3 -I shutdown_probe.py --after 2 --grace 10 -- python3 -m my_service
    python3 -I shutdown_probe.py --ready-line "ready" --grace 5 -- uv run my-worker

What it does:
    1. Starts COMMAND in its own process group (POSIX) or console process group (Windows).
    2. Waits --after seconds, or until a stdout line contains --ready-line.
    3. Sends the stop request: SIGTERM on macOS and Linux (as systemd, launchd and Docker do),
       CTRL_BREAK_EVENT on Windows (the closest console equivalent).
    4. Waits up to --grace seconds, then kills the whole group (SIGKILL, or taskkill /T /F).
    5. Prints the exit code and how long the program took to stop.

Exit status: 0 when the program stopped by itself within the grace period with exit code 0,
1 when it stopped with a non-zero code, 2 when it had to be killed. Standard library only.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time


def start(cmd: list[str]) -> subprocess.Popen[str]:
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding="utf-8", errors="replace", text=True, bufsize=1, **kwargs)  # type: ignore[call-overload]


def request_stop(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        os.killpg(proc.pid, signal.SIGTERM)


def kill_tree(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.formatter_class = argparse.RawDescriptionHelpFormatter
    cli.add_argument("--after", type=float, default=2.0, help="seconds to wait before the stop request (default 2)")
    cli.add_argument("--grace", type=float, default=10.0, help="seconds allowed to stop before a kill (default 10)")
    cli.add_argument("--ready-line", help="send the stop request once a stdout line contains this text")
    cli.add_argument("program", nargs=argparse.REMAINDER, help="the program to test, after --")
    args = cli.parse_args()
    cmd = args.program[1:] if args.program[:1] == ["--"] else args.program
    if not cmd:
        cli.error("give the command to run after --")

    proc = start(cmd)
    ready = threading.Event()
    assert proc.stdout is not None
    stdout = proc.stdout

    def pump() -> None:
        for line in stdout:
            print(f"  | {line.rstrip()}", flush=True)
            if args.ready_line and args.ready_line in line:
                ready.set()

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    if args.ready_line:
        give_up = time.monotonic() + max(args.after, 60.0)
        while not ready.wait(timeout=0.05) and proc.poll() is None:   # a program that died never prints the line
            if time.monotonic() >= give_up:
                print("probe: the ready line never appeared", file=sys.stderr)
                break
    else:
        time.sleep(args.after)
    if proc.poll() is not None:
        reader.join(timeout=1)                                        # its last lines: usually why it died
        print(f"probe: the program exited by itself with code {proc.returncode} before the stop request")
        return 1

    sent = time.monotonic()
    request_stop(proc)
    try:
        code = proc.wait(timeout=args.grace)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        proc.wait()
        reader.join(timeout=1)
        print(f"probe: no exit within {args.grace:.1f}s after the stop request; killed (code {proc.returncode})")
        return 2
    reader.join(timeout=1)
    took = time.monotonic() - sent
    print(f"probe: exited with code {code} {took:.2f}s after the stop request")
    return int(code != 0)


if __name__ == "__main__":
    sys.exit(main())
