# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright 2026 MOZGII, Inc. (https://mozgii.com/)
"""Container health check with no dependencies: probe an HTTP URL, a TCP port or a heartbeat file.

Usage:
    python -I healthcheck.py --http http://127.0.0.1:8000/healthz [--timeout 3]
    python -I healthcheck.py --tcp 127.0.0.1:5432
    python -I healthcheck.py --file /tmp/heartbeat --max-age 60

In a Dockerfile (works in slim and distroless images, which have no curl):
    HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \\
        CMD ["python", "-I", "/app/healthcheck.py", "--file", "/tmp/heartbeat", "--max-age", "60"]

Checks:
    --http URL     healthy when the response status is below 400 (redirects are not followed)
    --tcp H:P      healthy when a TCP connection opens
    --file PATH    healthy when PATH exists and was modified less than --max-age seconds ago;
                   the service touches it from its main loop (pathlib.Path(PATH).touch())

Exit status: 0 healthy, 1 unhealthy (the codes Docker's HEALTHCHECK expects). One short line is
printed either way; Docker keeps it in `docker inspect --format '{{json .State.Health}}'`.
Standard library only; runs with python -I.
"""

from __future__ import annotations

import argparse
import http.client
import os
import socket
import sys
import time
import urllib.parse


def check_http(url: str, timeout: float) -> tuple[bool, str]:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False, f"not an http(s) URL: {url}"
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parts.hostname, parts.port, timeout=timeout)
    try:
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        conn.request("GET", path, headers={"User-Agent": "healthcheck"})
        status = conn.getresponse().status
    except OSError as exc:
        return False, f"http error: {exc}"
    finally:
        conn.close()
    return status < 400, f"http {status}"


def check_tcp(target: str, timeout: float) -> tuple[bool, str]:
    host, sep, port = target.rpartition(":")
    if not sep or not port.isdigit():
        return False, f"expected HOST:PORT, got {target}"
    try:
        with socket.create_connection((host.strip("[]"), int(port)), timeout=timeout):
            return True, f"tcp {target} open"
    except OSError as exc:
        return False, f"tcp {target}: {exc}"


def check_file(path: str, max_age: float) -> tuple[bool, str]:
    try:
        age = time.time() - os.stat(path).st_mtime
    except OSError as exc:
        return False, f"heartbeat missing: {exc.strerror}"
    return age <= max_age, f"heartbeat {age:.0f}s old (max {max_age:.0f}s)"


def main() -> int:
    cli = argparse.ArgumentParser(prog="healthcheck.py", formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    group = cli.add_mutually_exclusive_group(required=True)
    group.add_argument("--http", metavar="URL", help="probe this URL with GET")
    group.add_argument("--tcp", metavar="HOST:PORT", help="open a TCP connection")
    group.add_argument("--file", metavar="PATH", help="check a heartbeat file's age")
    cli.add_argument("--timeout", type=float, default=3.0, help="seconds for --http and --tcp (default 3)")
    cli.add_argument("--max-age", type=float, default=60.0, help="seconds for --file (default 60)")
    args = cli.parse_args()

    if args.http:
        ok, message = check_http(args.http, args.timeout)
    elif args.tcp:
        ok, message = check_tcp(args.tcp, args.timeout)
    else:
        ok, message = check_file(args.file, args.max_age)
    print(("healthy: " if ok else "unhealthy: ") + message)
    return int(not ok)


if __name__ == "__main__":
    sys.exit(main())
