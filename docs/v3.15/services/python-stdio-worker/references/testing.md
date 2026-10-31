---
title: Testing a stdio worker
author: MOZGII, Inc.
lang: en
---

# Testing a stdio worker

Three layers: a contract suite that drives any worker command over pipes, unit tests of methods without the transport, and the host's own smoke test with the Python worker as its child. General pytest practice lives in the `pytest-testing` skill; this file covers what is specific to a stdio worker.

The blocks import from `worker_skeleton`; in your package that module is `my_app_worker.runtime` (Program layout in SKILL.md).

## Contents

This reference has 4 sections.

1. Contract suite over pipes
2. Unit tests without the transport
3. Interop with the host
4. Start-up budget and CI matrix

## 1. Contract suite over pipes

The contract suite, `scripts/test_worker_contract.py`, spawns the worker the way a host does (pipes, a clean environment without `PG*`, `PYTHON*` or `NODE_OPTIONS`) and asserts the wire in [protocol.md](protocol.md):

| Test | Asserts |
|---|---|
| `test_ready_is_the_first_line` | first stdout line is `$/ready` with `protocol: 1`, the child's pid, a version and capabilities |
| `test_ready_within_the_start_budget` | spawn → `$/ready` under `READY_BUDGET_MS` (default 1000) |
| `test_ping_unknown_method_and_parse_error` | `{}`, `-32601`, `-32700` with `id: null` |
| `test_id_that_is_an_array_is_refused` | `id: [1]` gets `-32600` with `id: null`, and the worker still answers a ping |
| `test_deep_nesting_is_a_parse_error` | 200,000 `[` on one line get `-32700` with `id: null`, and the worker still answers a ping |
| `test_progress_then_one_result` | progress for the request id, final `done == total`, then exactly one result |
| `test_cancel_replies_minus_32800` | `$/cancel` stops a running request within a second |
| `test_ping_is_answered_while_work_runs` | a ping overtakes a running request |
| `test_many_requests_in_flight` | 50 concurrent requests, every id answered |
| `test_stdin_eof_exits_zero`, `test_shutdown_replies_then_exits_zero` | both stop paths exit 0 |
| `test_every_request_is_answered_even_at_eof` | requests queued with EOF before the handshake each get one reply (a result or `-32800`) |
| `test_stray_output_never_reaches_stdout` | `print`, `os.write(1, ...)`, a child process and a library warning all land on stderr |
| `test_errors_carry_codes_not_tracebacks` | `-32000` + `data.code`, `-32603` without internals, `-32602` for bad params, traceback on stderr |
| `test_oversized_result_becomes_an_error`, `test_cpu_work_in_a_process_pool`, `test_model_loads_once_and_stays_warm`, `test_idle_exit_when_told` | the Python runtime's own guarantees |

```bash
cp <skill>/scripts/test_worker_contract.py workers/tests/test_contract.py
cd workers && uv run pytest -q tests/test_contract.py                                   # the skeleton next to the file
WORKER_CMD="$(pwd)/.venv/bin/python -I -B -u -X utf8 -m my_app_worker" uv run pytest -q tests/test_contract.py
WORKER_CMD="node path/to/fake-worker.mjs" uv run pytest -q tests/test_contract.py   # a host's test double; Python-only cases skip
```

When the file is not on disk (a pack install), read this script from the library that served this page and save it first.

Measured: 16 passed against the skeleton and against the built, moved runtime. Keep the suite shared with the host's team: a change to the protocol lands in `protocol.md` first, then in both the host and the worker, and both must pass.

When you replace the demo methods, keep `echo` and `sleep` (or point the tests at your own equivalents) and the `noise`/`fail` cases, so the hygiene and error tests keep running against the real worker.

## 2. Unit tests without the transport

Unit tests call method handlers directly, without pipes. Handlers are plain async functions of `(params, ctx)`: give them a real `Ctx` whose `Wire` writes to memory.

```python
"""Unit tests of worker methods without the transport: a real Ctx, a wire that writes to memory."""

from __future__ import annotations

import asyncio
import io
import json
from typing import cast

import pytest

from pathlib import Path

from worker_skeleton import AppError, CancelToken, Cancelled, Ctx, Progress, Runtime, Wire, allowed_path, fail, sleep


def make_ctx(req_id: int = 1) -> tuple[Ctx, io.BytesIO]:
    out = io.BytesIO()
    rt = Runtime(Wire(out, lambda: None), asyncio.get_running_loop(), idle_release_s=0, idle_exit_s=0)
    return Ctx(req_id, CancelToken(), Progress(rt.wire, req_id), rt), out


def progress_done(out: io.BytesIO) -> list[object]:
    messages = [cast(dict[str, object], json.loads(line)) for line in out.getvalue().splitlines()]
    return [cast(dict[str, object], m["params"])["done"] for m in messages if m.get("method") == "$/progress"]


def test_sleep_reports_progress() -> None:
    async def go() -> None:
        ctx, out = make_ctx()
        assert await sleep({"ms": 40, "steps": 4}, ctx) == {"slept": 40}
        assert progress_done(out) == [1, 2, 3, 4]
    asyncio.run(go())


def test_sleep_stops_at_the_next_checkpoint() -> None:
    async def go() -> None:
        ctx, _ = make_ctx()
        asyncio.get_running_loop().call_later(0.05, ctx.token.cancel)
        with pytest.raises(Cancelled):
            await sleep({"ms": 5000, "steps": 100}, ctx)
    asyncio.run(go())


def test_domain_error_has_a_stable_reason() -> None:
    async def go() -> None:
        ctx, _ = make_ctx()
        with pytest.raises(AppError) as info:
            await fail({}, ctx)
        assert info.value.code == -32000 and info.value.data == {"code": "NOT_FOUND"}
    asyncio.run(go())


def test_paths_outside_the_roots_are_refused(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("x")
    (root / "link").symlink_to(tmp_path / "secret.txt")
    assert allowed_path(str(root / "a.mov"), [root]) == (root / "a.mov").resolve()
    for raw in (str(root / ".." / "secret.txt"), str(root / "link"), "/etc/passwd"):
        with pytest.raises(AppError):
            allowed_path(raw, [root])
```

- `asyncio.run` inside a plain test avoids a plugin dependency; with pytest-asyncio in auto mode (the `pytest-testing` skill) write `async def test_...` instead.
- Methods that touch the database get a real Postgres (a scratch cluster under a temp directory, or a container): build `MY_APP_DB` for it, run the method, check rows, roll back or drop the database. Never point tests at the user's cluster.
- Call the method with the job runner's shape (`{"jobId": ..., "params": {...}, "checkpoint": ...}`) and test a resume from an older checkpoint: the rows must not duplicate.

## 3. Interop with the host

Interop is proven by the host's own smoke test, run with the real Python worker as its child: handshake, many warm calls, progress, cancel, `kill -9` and restart with a new pid, idle stop, and exit on stdin EOF after the host is gone. Afterwards check that no worker process is left: `pgrep -fl my_app_worker || echo "no strays"`.

## 4. Start-up budget and CI matrix

The start-up budget and the CI matrix keep the worker fast and portable.

- Budget: `READY_BUDGET_MS` well under the host's start timeout; the skeleton reaches `$/ready` in about 30 ms, a worker that imports torch first would not.
- Import audit in CI: run the `-X importtime` command from warm-and-concurrency.md §5 against the built runtime and fail when a heavy package appears on the start path.
- Matrix: run the contract suite and the unit tests on every target the app ships, against the built runtime (`WORKER_CMD` pointing into `resources/bin/<target>/python`). Windows is the platform most likely to differ (std handles, event loop, signals) and has not been run for this skill: give it its own CI job.
- Static types: `uv run pyright` in strict mode over `src/` and `tests/` (the `pyright-strict` skill); the skeleton and the contract suite pass with zero errors for the darwin and Windows platforms.
