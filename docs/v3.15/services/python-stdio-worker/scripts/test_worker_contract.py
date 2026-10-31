# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright 2026 MOZGII, Inc. (https://mozgii.com/)
"""Contract tests for any stdio JSON-RPC worker that speaks the protocol in references/protocol.md.

The suite runs against any worker command, in any language, so a host's test double and the real worker stay interchangeable:
    uv run pytest -q test_worker_contract.py                                   # the skeleton next to this file
    WORKER_CMD="node path/to/fake-worker.mjs" uv run pytest -q test_worker_contract.py
    WORKER_CMD="resources/bin/darwin-arm64/python/bin/python3 -I -B -u -X utf8 -m my_app_worker" uv run pytest -q ...
Tests that need methods only the Python skeleton has (noise, cpu, infer, fail) skip when the worker lacks them.
"""

from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest

HERE = Path(__file__).resolve().parent
DEFAULT_CMD = [sys.executable, "-I", "-B", "-u", "-X", "utf8", str(HERE / "worker_skeleton.py")]
PASS_ENV = ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "TZ", "SystemRoot", "TEMP", "TMP", "PATH")

Message = dict[str, object]


def worker_cmd() -> list[str]:
    raw = os.environ.get("WORKER_CMD")
    return shlex.split(raw) if raw else list(DEFAULT_CMD)


class Worker:
    """Spawns the worker over pipes the way a host does and collects every stdout line."""

    def __init__(self, extra_args: list[str] | None = None) -> None:
        env = {name: value for name, value in os.environ.items() if name in PASS_ENV}   # no PG*, PYTHON*, NODE_OPTIONS
        started = time.monotonic()
        self.proc = subprocess.Popen(
            [*worker_cmd(), *(extra_args or [])],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, env=env,
        )
        self.lines: queue.Queue[bytes] = queue.Queue()
        self.raw: list[bytes] = []
        self.stderr: list[str] = []
        self._next_id = 1
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        self.ready = self.next(timeout=10)
        self.ready_ms = 1000 * (time.monotonic() - started)

    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.raw.append(line)
            self.lines.put(line)

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr.append(line.decode("utf-8", "replace"))

    def send_raw(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, message: Message) -> None:
        self.send_raw(json.dumps({"jsonrpc": "2.0", **message}).encode() + b"\n")

    def request(self, method: str, params: object = None) -> int:
        req_id = self._next_id
        self._next_id += 1
        self.send({"id": req_id, "method": method, "params": params})
        return req_id

    def next(self, timeout: float = 5.0) -> Message:
        line = self.lines.get(timeout=timeout)
        parsed: object = json.loads(line)       # a non-JSON line fails the test: stdout is protocol only
        assert isinstance(parsed, dict), line
        message = cast(Message, parsed)
        assert message.get("jsonrpc") == "2.0", line
        return message

    def until(self, match: Callable[[Message], bool], timeout: float = 5.0) -> tuple[Message, list[Message]]:
        seen: list[Message] = []
        deadline = time.monotonic() + timeout
        while True:
            message = self.next(timeout=max(0.01, deadline - time.monotonic()))
            if match(message):
                return message, seen
            seen.append(message)

    def reply(self, req_id: int, timeout: float = 5.0) -> tuple[Message, list[Message]]:
        return self.until(lambda m: m.get("id") == req_id and "method" not in m, timeout)

    def call(self, method: str, params: object = None, timeout: float = 5.0) -> Message:
        return self.reply(self.request(method, params), timeout)[0]

    def has(self, method: str) -> bool:
        params = cast(Message, self.ready["params"])
        return method in cast(list[str], params["capabilities"])

    def close(self, timeout: float = 5.0) -> int:
        assert self.proc.stdin is not None
        if not self.proc.stdin.closed:
            self.proc.stdin.close()
        return self.proc.wait(timeout=timeout)


@pytest.fixture
def worker() -> Iterator[Worker]:
    w = Worker()
    yield w
    if w.proc.poll() is None:
        w.close()


def error_code(message: Message) -> int:
    return cast(int, cast(Message, message["error"])["code"])


def test_ready_is_the_first_line(worker: Worker) -> None:
    assert worker.ready["method"] == "$/ready"
    params = cast(Message, worker.ready["params"])
    assert params["protocol"] == 1 and params["pid"] == worker.proc.pid
    assert isinstance(params["version"], str) and isinstance(params["capabilities"], list)


def test_ready_within_the_start_budget(worker: Worker) -> None:
    budget = float(os.environ.get("READY_BUDGET_MS", "1000"))   # keep well under the host's start timeout
    assert worker.ready_ms < budget, f"$/ready after {worker.ready_ms:.0f} ms: move imports out of the start path"


def test_ping_unknown_method_and_parse_error(worker: Worker) -> None:
    assert worker.call("$/ping")["result"] == {}
    assert error_code(worker.call("nope")) == -32601
    worker.send_raw(b"this is not json\n")
    parse_error = worker.next()
    assert parse_error["id"] is None and error_code(parse_error) == -32700


def test_id_that_is_an_array_is_refused(worker: Worker) -> None:
    worker.send_raw(b'{"jsonrpc":"2.0","id":[1],"method":"$/ping"}\n')
    refused = worker.next()
    assert refused["id"] is None and error_code(refused) == -32600
    assert worker.call("$/ping")["result"] == {}


def test_deep_nesting_is_a_parse_error(worker: Worker) -> None:
    worker.send_raw(b"[" * 200_000 + b"\n")
    parse_error = worker.next()
    assert parse_error["id"] is None and error_code(parse_error) == -32700
    assert worker.call("$/ping")["result"] == {}


def test_progress_then_one_result(worker: Worker) -> None:
    reply, before = worker.reply(worker.request("sleep", {"ms": 200, "steps": 4}))
    progress = [cast(Message, m["params"]) for m in before if m.get("method") == "$/progress"]
    assert [p["done"] for p in progress][-1] == 4 and all(p["total"] == 4 for p in progress)
    assert reply["result"] == {"slept": 200}


def test_cancel_replies_minus_32800(worker: Worker) -> None:
    req_id = worker.request("sleep", {"ms": 5000, "steps": 50})
    worker.until(lambda m: m.get("method") == "$/progress")
    started = time.monotonic()
    worker.send({"method": "$/cancel", "params": {"id": req_id}})
    reply, _ = worker.reply(req_id)
    assert error_code(reply) == -32800 and time.monotonic() - started < 1.0


def test_ping_is_answered_while_work_runs(worker: Worker) -> None:
    slow = worker.request("sleep", {"ms": 1000, "steps": 10})
    ping = worker.request("$/ping")
    first, _ = worker.until(lambda m: m.get("id") in (slow, ping) and "method" not in m)
    assert first["id"] == ping


def test_many_requests_in_flight(worker: Worker) -> None:
    ids = {worker.request("echo", {"i": i}) for i in range(50)}
    got: set[object] = set()
    while got != ids:
        got.add(worker.until(lambda m: m.get("id") in ids)[0]["id"])


def test_stdin_eof_exits_zero(worker: Worker) -> None:
    assert worker.close(timeout=5) == 0


def test_every_request_is_answered_even_at_eof() -> None:
    env = {name: value for name, value in os.environ.items() if name in PASS_ENV}
    requests = b"".join(
        json.dumps({"jsonrpc": "2.0", "id": i, "method": "sleep", "params": {"ms": 2000, "steps": 20}}).encode() + b"\n"
        for i in (1, 2, 3))
    proc = subprocess.run([*worker_cmd(), "--warmup-ms=200"], input=requests, capture_output=True, env=env, timeout=15)
    replies = {m["id"] for m in map(json.loads, proc.stdout.splitlines()) if "id" in m}   # requests and EOF queued before ready
    assert replies == {1, 2, 3} and proc.returncode == 0


def test_shutdown_replies_then_exits_zero(worker: Worker) -> None:
    assert worker.call("shutdown", {"reason": "test"})["result"] == {}
    assert worker.proc.wait(timeout=5) == 0


def test_stray_output_never_reaches_stdout(worker: Worker) -> None:
    if not worker.has("noise"):
        pytest.skip("worker has no noise method")
    assert worker.call("noise")["result"] == {"ok": True}
    worker.close()
    for line in worker.raw:
        assert json.loads(line)["jsonrpc"] == "2.0"
    logs = "".join(worker.stderr)
    for text in ("print() from a handler", "raw write to fd 1", "a child process printing", "a library warning"):
        assert text in logs


def test_errors_carry_codes_not_tracebacks(worker: Worker) -> None:
    if not worker.has("fail"):
        pytest.skip("worker has no fail method")
    domain = cast(Message, worker.call("fail")["error"])
    assert domain["code"] == -32000 and domain["data"] == {"code": "NOT_FOUND"}
    internal = cast(Message, worker.call("fail", {"code": "internal"})["error"])
    assert internal["code"] == -32603 and "secret" not in str(internal) and "Traceback" not in str(internal)
    assert error_code(worker.call("sleep", {"ms": "soon"})) == -32602
    worker.close()
    assert "Traceback" in "".join(worker.stderr)


def test_oversized_result_becomes_an_error(worker: Worker) -> None:
    if not worker.has("noise"):
        pytest.skip("only the Python skeleton guards the line limit")
    err = cast(Message, worker.call("big", {"bytes": 5 * 1024 * 1024}, timeout=10)["error"])
    assert err["data"] == {"code": "RESULT_TOO_LARGE"}


def test_cpu_work_in_a_process_pool(worker: Worker) -> None:
    if not worker.has("cpu"):
        pytest.skip("worker has no cpu method")
    n = 2_000_000
    reply, before = worker.reply(worker.request("cpu", {"n": n, "chunks": 4}), timeout=30)
    assert reply["result"] == {"sum": (n - 1) * n * (2 * n - 1) // 6}
    assert any("checkpoint" in cast(Message, m["params"]) for m in before if m.get("method") == "$/progress")


def test_model_loads_once_and_stays_warm(worker: Worker) -> None:
    if not worker.has("infer"):
        pytest.skip("worker has no infer method")
    started = time.monotonic()
    assert worker.call("infer", {"text": "a"})["result"] == {"text": "A"}
    cold = time.monotonic() - started
    started = time.monotonic()
    worker.call("infer", {"text": "b"})
    assert time.monotonic() - started < cold / 3


def test_idle_exit_when_told() -> None:
    w = Worker(["--idle-exit-s=0.3"])
    if not w.has("noise"):
        w.close()
        pytest.skip("only the Python skeleton has worker-side idle exit")
    w.call("echo", {})
    assert w.proc.wait(timeout=5) == 0
