---
title: "Worker runtime: stdio claim, wire, dispatcher"
author: MOZGII, Inc.
lang: en
---

# Worker runtime: stdio claim, wire, dispatcher

The parts of `scripts/worker_skeleton.py`, in the order a message meets them. Every block below is copied from that file, which passes the contract suite on macOS with CPython 3.12 to 3.15, and the blocks pasted in order, followed by `if __name__ == "__main__": main()`, make a working runtime without that file. The wire itself (messages, codes, line limit) is defined in [protocol.md](protocol.md); this file implements it.

## Contents

This reference has 10 sections.

1. Start-up order
2. Claim the channel
3. The wire: one writer, whole lines
4. Reader thread and event loop
5. Dispatcher
6. Request context: cancel, progress, executors
7. Method table and params
8. Errors
9. Stop paths, signals and children
10. Large payloads

## 1. Start-up order

The start-up order puts the handshake before anything slow.

```
python3 -I -B -u -X utf8 -m my_app_worker --role=media
  ├─ stdlib imports only (the skeleton imports in about 25 ms)
  ├─ claim_stdio()                 fd 0 → devnull, fd 1 → stderr, private copies of both pipes
  ├─ logging → stderr
  ├─ signal handlers               SIGTERM → graceful stop; SIGINT ignored when stdin is a pipe
  ├─ $/ready                       the host's start timeout stops counting here
  ├─ reader thread starts          requests sent earlier waited in the pipe buffer
  └─ asyncio loop serves until stop; then os._exit(code)
```

Heavy imports (torch, a large application package, database drivers) happen inside methods or in a `warmup` method, never on this path (warm-and-concurrency.md §4).

The file's header holds the standard-library imports and the constants the sections below use.

```python
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from multiprocessing import get_context
from pathlib import Path
from typing import BinaryIO, TypedDict, TypeVar, cast

PROTOCOL = 1
VERSION = "0.1.0"
MAX_LINE_BYTES = 4 * 1024 * 1024  # equal to the host's line limit: a longer line kills the worker
STOP_GRACE_S = 3.0                # bounded drain after shutdown, EOF or SIGTERM

log = logging.getLogger("worker")
T = TypeVar("T")
```

## 2. Claim the channel

Claiming the channel means keeping private copies of the stdio pipes and pointing fd 0 and fd 1 elsewhere.

```python
def claim_stdio() -> tuple[BinaryIO, BinaryIO]:
    """Keep private copies of the real stdin/stdout pipes; point fd 0 at devnull and fd 1 at stderr.

    After this, print(), C-level printf, logging to stdout and every child process write to stderr,
    and nothing but the reader thread can consume protocol bytes.
    """
    sys.stdout.flush()
    wire_in = os.dup(0)          # os.dup returns non-inheritable descriptors: children never see the wire
    wire_out = os.dup(1)
    os.dup2(2, 1)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    if sys.platform == "win32":  # os.dup2 updates the CRT table only; child processes inherit the Win32 slots
        import ctypes
        import msvcrt

        win32 = ctypes.WinDLL("kernel32", use_last_error=True)
        win32.SetStdHandle(-10, msvcrt.get_osfhandle(0))  # STD_INPUT_HANDLE
        win32.SetStdHandle(-11, msvcrt.get_osfhandle(1))  # STD_OUTPUT_HANDLE
    sys.stdout = sys.stderr
    return os.fdopen(wire_in, "rb", closefd=False), os.fdopen(wire_out, "wb", closefd=False)
```

- `os.dup` returns non-inheritable descriptors, so `subprocess` children and process-pool workers never hold the protocol pipes. A child that inherits fd 1 writes into stderr; one that reads fd 0 reads EOF instead of stealing requests.
- `sys.stdout = sys.stderr` catches Python-level writes; `os.dup2(2, 1)` catches C-level writes, `os.write(1, ...)` and children. Both are needed. The official MCP Python SDK claims its stdio transport the same way.
- Windows: `os.dup2` changes the C runtime's table, while `subprocess` children inherit the Win32 standard handles, hence `SetStdHandle`. This path follows the MCP SDK's Windows code and has not been run here; run the contract suite on a Windows runner.
- `closefd=False`: the reader thread may still block on the descriptor while the process exits; it must never be closed and reused under it.
- Call it first in `main()`, never at import time: spawn-based process pools import the main module again in every child.

## 3. The wire: one writer, whole lines

The `Wire` class is the only code that writes to the protocol pipe.

```python
class Wire:
    def __init__(self, out: BinaryIO, on_broken: Callable[[], None]) -> None:
        self._out = out
        self._lock = threading.Lock()
        self._on_broken = on_broken
        self.broken = False

    def send(self, message: dict[str, object]) -> None:
        try:
            data = json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:   # numpy scalars, NaN, datetime: never a half-written line
            if "id" not in message:
                log.error("dropped unserializable notification: %s", exc)
                return
            log.error("unserializable result for id %s: %s", message["id"], exc)
            data = json.dumps({"jsonrpc": "2.0", "id": message["id"],
                               "error": {"code": -32603, "message": "result is not JSON serializable"}})
        line = data.encode("utf-8") + b"\n"
        if len(line) > MAX_LINE_BYTES:
            if "id" not in message:
                log.error("dropped notification of %d bytes", len(line))
                return
            line = json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {
                "code": -32000, "message": f"result of {len(line)} bytes exceeds the line limit; return a file path",
                "data": {"code": "RESULT_TOO_LARGE"}}}).encode() + b"\n"
        with self._lock:
            if self.broken:
                return
            try:
                self._out.write(line)
                self._out.flush()      # one message, one flush: progress leaves at once
            except (BrokenPipeError, OSError):
                self.broken = True     # the host is gone
        if self.broken:
            self._on_broken()

    def reply(self, req_id: object, result: object) -> None:
        self.send({"id": req_id, "result": result})

    def error(self, req_id: object, err: RpcError) -> None:
        body: dict[str, object] = {"code": err.code, "message": err.message}
        if err.data is not None:
            body["data"] = err.data
        self.send({"id": req_id, "error": body})

    def notify(self, method: str, params: object) -> None:
        self.send({"method": method, "params": params})
```

- Compact `json.dumps` never emits a raw newline (it escapes control characters), so a message is exactly one line. `ensure_ascii=False` keeps Cyrillic paths short; the bytes are UTF-8.
- `allow_nan=False`: Python writes `NaN` by default, JavaScript's `JSON.parse` rejects it, and the host would drop the reply as noise and time the call out. The same branch catches numpy scalars, `datetime` and `Path` objects: convert them in the method.
- The size check mirrors the host's line limit; a longer line is fatal there (the worker is killed), so it becomes a `RESULT_TOO_LARGE` error here.
- One lock and one flush per message: threads (progress from executors) never interleave bytes, and progress leaves at once instead of sitting in a buffer.
- A write to a closed pipe raises `BrokenPipeError` (Python ignores SIGPIPE): the host is gone, so the worker stops instead of crashing in a loop.

## 4. Reader thread and event loop

The reader thread does blocking reads and hands each line to the asyncio loop.

```python
def reader(wire_in: BinaryIO, rt: Runtime) -> None:
    """Blocking reads on a plain thread: portable across macOS, Linux and Windows pipes, files and ttys."""
    try:
        while True:
            line = wire_in.readline(MAX_LINE_BYTES + 1)
            if not line:
                break                                           # EOF: the parent closed stdin or died
            if len(line) > MAX_LINE_BYTES and not line.endswith(b"\n"):
                while line and not line.endswith(b"\n"):        # discard the rest of the oversized line
                    line = wire_in.readline(MAX_LINE_BYTES + 1)
                rt.loop.call_soon_threadsafe(rt.wire.error, None, RpcError(-32600, "request line too long"))
                continue
            if line.strip():
                rt.loop.call_soon_threadsafe(rt.on_line, line)
    except (OSError, ValueError):
        pass
    try:
        rt.loop.call_soon_threadsafe(rt.stop, "stdin closed")
    except RuntimeError:
        pass                                                    # loop already closed
```

```python
def flag(name: str, default: str) -> str:
    for option in sys.argv[1:]:
        if option == f"--{name}":
            return "1"
        if option.startswith(f"--{name}="):
            return option.split("=", 1)[1]
    return default


async def serve(wire_in: BinaryIO, wire_out: BinaryIO) -> int:
    loop = asyncio.get_running_loop()
    rt: Runtime | None = None

    def broken() -> None:
        if rt is not None:
            loop.call_soon_threadsafe(rt.stop, "stdout closed")

    wire = Wire(wire_out, broken)
    rt = Runtime(wire, loop, float(flag("idle-release-s", "0")), float(flag("idle-exit-s", "0")))
    def on_term(_sig: int, _frame: object) -> None:
        loop.call_soon_threadsafe(rt.stop, "SIGTERM")             # graceful, bounded by STOP_GRACE_S

    signal.signal(signal.SIGTERM, signal.SIG_IGN if flag("ignore-sigterm", "") else on_term)
    if not os.isatty(wire_in.fileno()):
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # terminal Ctrl+C in dev must not kill a supervised worker

    time.sleep(int(flag("warmup-ms", "0")) / 1000)    # stands in for interpreter + light imports
    if flag("noise", ""):
        print("a stray print before the handshake")   # lands on stderr: the channel is already claimed
    wire.notify("$/ready", {"protocol": int(flag("protocol", str(PROTOCOL))), "pid": os.getpid(),
                            "name": "skeleton", "version": VERSION, "capabilities": sorted([*METHODS, "$/ping", "shutdown"])})
    threading.Thread(target=reader, args=(wire_in, rt), name="stdin-reader", daemon=True).start()
    await rt.done.wait()
    return rt.exit_code


def main() -> None:
    wire_in, wire_out = claim_stdio()                 # first: before anything can print
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(asctime)s %(name)s: %(message)s")
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None   # psycopg async needs it on Windows
    code = asyncio.run(serve(wire_in, wire_out), loop_factory=loop_factory)
    logging.shutdown()
    sys.stderr.flush()
    os._exit(code)    # do not wait for executor threads stuck in native code; the host saw every reply
```

- Blocking `readline` on a thread works for pipes, regular files and ttys on macOS, Linux and Windows. `loop.connect_read_pipe` does not: on Windows the Proactor loop accepts only handles opened for overlapped I/O, and the selector loop has no pipe support at all.
- `readline(MAX_LINE_BYTES + 1)` bounds memory per line; an oversized request is drained and answered with `-32600` and `id: null`.
- EOF (`b""`) means the parent closed stdin or died: the reader schedules the same stop as `shutdown`.
- `loop_factory=asyncio.SelectorEventLoop` on Windows, because psycopg's async mode is not compatible with the default Proactor loop; since stdin is read on a thread, nothing needs Proactor. The skeleton uses `asyncio.run(..., loop_factory=...)`; Step 0 checks the interpreter supports it.
- `os._exit` at the end: executor threads stuck in native code would otherwise keep the interpreter alive at exit. Every reply was flushed before `rt.done` fires, and logging is shut down first.

## 5. Dispatcher

The dispatcher is `Runtime.on_line`: it parses each line and turns a request into one task with exactly one reply. `Runtime` holds everything one worker process owns: the wire, the loop, the requests in flight, the children, the executors and the stop path (§9).

```python
@dataclass
class Running:
    token: CancelToken
    task: asyncio.Task[None]


@dataclass
class Runtime:
    wire: Wire
    loop: asyncio.AbstractEventLoop
    idle_release_s: float
    idle_exit_s: float
    inflight: dict[object, Running] = field(default_factory=lambda: {})
    children: set[subprocess.Popen[bytes]] = field(default_factory=lambda: set())
    on_stop: list[Callable[[], Awaitable[None]]] = field(default_factory=lambda: [])  # e.g. pool.close
    closing: bool = False
    exit_code: int = 0
    done: asyncio.Event = field(default_factory=asyncio.Event)
    _idle: list[asyncio.TimerHandle] = field(default_factory=lambda: [])
    _io: ThreadPoolExecutor | None = None
    _gpu: ThreadPoolExecutor | None = None
    _cpu: ProcessPoolExecutor | None = None

    # Executors are created on first use: a worker that never runs CPU work never pays for a process pool.
    @property
    def io(self) -> ThreadPoolExecutor:
        self._io = self._io or ThreadPoolExecutor(max_workers=8, thread_name_prefix="io")
        return self._io

    @property
    def gpu(self) -> ThreadPoolExecutor:
        self._gpu = self._gpu or ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu")  # one model queue
        return self._gpu

    @property
    def gpu_started(self) -> bool:
        return self._gpu is not None

    @property
    def cpu(self) -> ProcessPoolExecutor:
        if self._cpu is None:   # spawn: fork is unsafe once threads exist (and the default on macOS anyway)
            self._cpu = ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) // 2), mp_context=get_context("spawn"))
        return self._cpu

    def reset_cpu(self) -> None:
        """A killed pool process breaks the whole pool (BrokenProcessPool): drop it, the next call makes a new one."""
        if self._cpu is not None:
            self._cpu.shutdown(wait=False, cancel_futures=True)
            self._cpu = None

    def on_line(self, line: bytes) -> None:
        try:
            msg: object = json.loads(line)
        except (UnicodeDecodeError, ValueError, RecursionError):   # RecursionError: nested too deep to parse
            self.wire.error(None, RpcError(-32700, "Parse error"))
            return
        if not isinstance(msg, dict):
            self.wire.error(None, RpcError(-32600, "Invalid Request"))
            return
        req = cast("dict[str, object]", msg)
        name, req_id, raw_params = req.get("method"), req.get("id"), req.get("params")
        if isinstance(req_id, (dict, list)):        # never a valid id, and unhashable: inflight would raise
            self.wire.error(None, RpcError(-32600, "Invalid Request"))
            return
        params = cast("dict[str, object]", raw_params) if isinstance(raw_params, dict) else {}
        if not isinstance(name, str):
            if req_id is not None:
                self.wire.error(req_id, RpcError(-32600, "Invalid Request"))
            return                                  # a response sent to us: the protocol has none
        if name == "$/cancel":
            running = self.inflight.get(params.get("id"))
            if running:
                running.token.cancel()
                running.task.cancel()
            return
        if req_id is None:
            return                                  # other notifications: ignore
        if name == "$/ping":
            self.wire.reply(req_id, {})             # answered on the loop: proves the dispatcher is alive
            return
        if name == "shutdown":
            self.wire.reply(req_id, {})
            self.stop("shutdown request")
            return
        if self.closing:
            self.wire.error(req_id, AppError("SHUTTING_DOWN", "worker is shutting down"))
            return
        handler = METHODS.get(name)
        if handler is None:
            self.wire.error(req_id, RpcError(-32601, f"Method not found: {name}"))
            return
        if req_id in self.inflight:
            self.wire.error(req_id, RpcError(-32600, f"duplicate id {req_id!r}"))
            return
        for timer in self._idle:
            timer.cancel()
        token = CancelToken()
        ctx = Ctx(req_id, token, Progress(self.wire, req_id), self)
        task = self.loop.create_task(self._run(handler, params, ctx, name))
        task.add_done_callback(lambda t: self._done(ctx, t))
        self.inflight[req_id] = Running(token, task)

    async def _run(self, handler: Handler, params: dict[str, object], ctx: Ctx, name: str) -> None:
        try:
            result = await handler(params, ctx)
            ctx.progress.flush()
            self.wire.reply(ctx.id, result)
        except (asyncio.CancelledError, Cancelled):
            self.wire.error(ctx.id, Cancelled())
        except RpcError as err:
            self.wire.error(ctx.id, err)
        except Exception:
            log.error("method %s (id %s) failed\n%s", name, ctx.id, traceback.format_exc())  # traceback: stderr only
            self.wire.error(ctx.id, RpcError(-32603, f"internal error in {name}"))
        ctx.replied = True

    def _done(self, ctx: Ctx, task: asyncio.Task[None]) -> None:
        if not ctx.replied:                     # cancelled before its first step: the body never ran
            self.wire.error(ctx.id, Cancelled())
        self.inflight.pop(ctx.id, None)
        if not self.inflight and not self.closing:
            self._arm_idle()

    def _arm_idle(self) -> None:
        self._idle = []
        if self.idle_release_s > 0:
            self._idle.append(self.loop.call_later(self.idle_release_s, lambda: self.loop.create_task(release_models(self))))
        if self.idle_exit_s > 0:
            self._idle.append(self.loop.call_later(self.idle_exit_s, self.stop, "idle"))

    def stop(self, reason: str) -> None:
        if self.closing:
            return
        self.closing = True
        log.info("%s, exiting", reason)
        for running in self.inflight.values():
            running.token.cancel()
            running.task.cancel()
        self.loop.create_task(self._finish())

    async def _finish(self) -> None:
        deadline = time.monotonic() + STOP_GRACE_S
        while self.inflight and time.monotonic() < deadline:   # let the -32800 replies out
            await asyncio.sleep(0.01)
        for hook in self.on_stop:
            try:
                await asyncio.wait_for(hook(), 1.0)
            except Exception:
                log.exception("stop hook failed")
        for child in self.children:
            child.kill()                                        # a worker never leaves its children behind
        for executor in (self._io, self._gpu, self._cpu):
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
        self.done.set()
```

`_arm_idle` calls `release_models`, which runs on the GPU thread. Keep these helpers when you remove the demo methods, and give `MODELS` your model type.

```python
MODELS: dict[str, object] = {}                # touched only on the gpu thread


def drop_models() -> int:
    count = len(MODELS)
    MODELS.clear()
    # real worker: gc.collect(); torch.mps.empty_cache() or torch.cuda.empty_cache()
    return count


async def release_models(rt: Runtime) -> int:
    if not rt.gpu_started:
        return 0
    released = await rt.loop.run_in_executor(rt.gpu, drop_models)
    if released:
        log.info("idle: released %d model(s)", released)
    return released
```

| Incoming | Action |
|---|---|
| not JSON, or nested too deep to parse | `-32700` with `id: null` |
| not an object, or an `id` that is an array or object | `-32600` with `id: null` |
| object without `method` | `-32600` when it carries an id, otherwise ignored (nothing sends responses to the worker) |
| `$/cancel { id }` | set that request's token and cancel its task |
| other notification | ignored |
| `$/ping` | `{}` at once, on the loop thread: proves the dispatcher is alive |
| `shutdown` | `{}` at once, then the stop path |
| unknown method | `-32601 "Method not found: <name>"` |
| duplicate id in flight | `-32600` |
| a method while stopping | `-32000`, `data.code = "SHUTTING_DOWN"` |
| a method | a task per request; exactly one reply when it ends |

A line of 200,000 `[` passes the size check and raises `RecursionError` inside `json.loads`, and an `id` of `[1]` raises `TypeError` at `req_id in self.inflight`. Both escape into the loop's exception handler with no reply unless `on_line` catches them first, and the host's call then times out.

`_done` is a task callback because a task cancelled before its first step never runs its body: requests followed at once by EOF or `$/cancel` would otherwise get no reply. The contract suite's `test_every_request_is_answered_even_at_eof` queues three requests and EOF before the handshake; an earlier version of the skeleton without `_done` failed it.

## 6. Request context: cancel, progress, executors

The request context (`Ctx`) carries the cancel token, the progress reporter and the executors a method needs.

```python
class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        """Call at every checkpoint (between files, chunks, frames)."""
        if self._event.is_set():
            raise Cancelled()


class Progress:
    """$/progress for one request: at most `rate` messages per second, the final state always delivered."""

    def __init__(self, wire: Wire, req_id: object, rate: float = 10.0) -> None:
        self._wire = wire
        self._id = req_id
        self._rate = rate
        self._tokens = rate
        self._stamp = time.monotonic()
        self._pending: dict[str, object] | None = None
        self._lock = threading.Lock()

    def __call__(self, done: int, total: int, label: str | None = None, checkpoint: object = None) -> None:
        params: dict[str, object] = {"id": self._id, "done": done, "total": total}
        if label is not None:
            params["label"] = label
        if checkpoint is not None:
            params["checkpoint"] = checkpoint
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self._rate, self._tokens + (now - self._stamp) * self._rate)
            self._stamp = now
            if self._tokens < 1 and done < total:
                self._pending = params   # coalesced: the newest state and checkpoint win
                return
            self._tokens = max(0.0, self._tokens - 1)
            self._pending = None
        self._wire.notify("$/progress", params)

    def flush(self) -> None:
        with self._lock:
            params, self._pending = self._pending, None
        if params is not None:
            self._wire.notify("$/progress", params)


@dataclass
class Ctx:
    id: object
    token: CancelToken
    progress: Progress
    rt: Runtime
    replied: bool = False

    def log(self, level: str, message: str, data: object = None) -> None:
        """$/log: an event the user should see. Diagnostics go to the stderr logger instead."""
        params: dict[str, object] = {"level": level, "message": message}
        if data is not None:
            params["data"] = data
        self.rt.wire.notify("$/log", params)

    async def run(self, executor: Executor, fn: Callable[..., T], *args: object) -> T:
        """Run blocking work on an executor; on cancel, wait until the work has really stopped."""
        fut = asyncio.get_running_loop().run_in_executor(executor, fn, *args)
        try:
            return await asyncio.shield(fut)
        except asyncio.CancelledError:
            self.token.cancel()
            try:
                await fut
            except BaseException:
                pass
            raise
```

- `CancelToken` is a `threading.Event`, so code on executor threads and on the loop checks the same flag. Call `ctx.token.check()` at every natural checkpoint (between files, chunks, frames, batches).
- `Ctx.run` shields the executor future: when the task is cancelled it sets the token and waits until the thread has really stopped, so the `-32800` reply never races a thread that still writes files or holds the GPU.
- `Progress` is a token bucket (10 per second, bursts of up to 10): a four-step job reports all four steps, a loop over 100,000 files sends about ten messages per second. A dropped report is kept as pending, so the newest checkpoint is flushed before the reply.

## 7. Method table and params

The method table maps names to async handlers, and small helpers validate params at the boundary.

```python
Handler = Callable[[dict[str, object], Ctx], Awaitable[object]]
METHODS: dict[str, Handler] = {}


def method(name: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        METHODS[name] = fn
        return fn
    return register
def method(name: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        METHODS[name] = fn
        return fn
    return register


def int_param(params: dict[str, object], name: str, default: int, lo: int, hi: int) -> int:
    value = params.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise invalid(f"{name} must be an integer in [{lo}, {hi}]")
    return value


def str_param(params: dict[str, object], name: str, max_len: int = 10_000) -> str:
    value = params.get(name)
    if not isinstance(value, str) or len(value) > max_len:
        raise invalid(f"{name} must be a string of at most {max_len} characters")
    return value


def allowed_path(raw: object, roots: Sequence[Path]) -> Path:
    """Resolve a path from a request; refuse anything outside the allowed roots, symlinks included."""
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise invalid("path must be a non-empty string")
    path = Path(raw).resolve()
    if not any(path.is_relative_to(root.resolve()) for root in roots):
        raise AppError("FORBIDDEN", "path is outside the allowed folders")
    return path
```

- A handler is `async def name(params: dict[str, object], ctx: Ctx) -> <TypedDict>`; register it with `@method("domain.verb")` in a module that `__main__.py` imports.
- Method names are `domain.verb` (`media.probe`) and match what the host calls; list them in `$/ready` capabilities.
- Job methods receive `{ jobId, params, checkpoint }` ([database.md](database.md) §3).

## 8. Errors

Errors are three exception classes that map to JSON-RPC codes.

```python
class RpcError(Exception):
    def __init__(self, code: int, message: str, data: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class AppError(RpcError):
    """Domain error: one JSON-RPC code, a stable machine-readable reason in data.code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(-32000, message, {"code": reason})


class Cancelled(RpcError):
    def __init__(self) -> None:
        super().__init__(-32800, "cancelled")


def invalid(message: str) -> RpcError:
    return RpcError(-32602, message)
```

| Code | Set by the worker when | `data` |
|---|---|---|
| `-32700` | a line is not JSON, or is nested too deep to parse | - |
| `-32600` | not an object, oversized line, an `id` that is an array or object, duplicate id | - |
| `-32601` | the method is not registered | - |
| `-32602` | params fail validation | optional field errors |
| `-32603` | an unexpected exception; message `internal error in <method>` | - (traceback on stderr) |
| `-32800` | the request was cancelled | - |
| `-32000` | a domain reason | `{ code: "NOT_FOUND" \| "CONFLICT" \| "BAD_INPUT" \| "FORBIDDEN" \| "UNAVAILABLE" \| "SHUTTING_DOWN" \| "RESULT_TOO_LARGE" \| ... }` |

- Never send `-32001` (timeout), `-32002` (worker gone) or `-32003` (overloaded): the host sets them, and its retry policy treats them differently from worker errors.
- The stable `data.code` is what a host shows a person ("file not found" instead of "worker error"). Reuse the host's own reason names where the meaning is the same (`BAD_INPUT`, `FORBIDDEN`).
- Messages are short sentences for people; paths are fine, stack frames and SQL are not.

## 9. Stop paths, signals and children

The stop path is the same whichever way the worker is told to stop: `Runtime.stop` and `Runtime._finish` in §5.

| Trigger | Path |
|---|---|
| `shutdown` request | reply `{}`, `stop("shutdown request")` |
| stdin EOF | reader thread → `stop("stdin closed")` |
| SIGTERM | handler → `stop("SIGTERM")` (`--ignore-sigterm` exists only for the host's escalation test) |
| stdout closed (`BrokenPipeError`) | `stop("stdout closed")` |
| idle (`--idle-exit-s`) | `stop("idle")`; normally the host's idle timeout sends `shutdown` instead |
| SIGINT | ignored when stdin is a pipe: terminal Ctrl+C reaches the whole foreground process group (on Windows every process on the console) and must not kill a supervised worker mid-write |

- `stop` cancels every token and task, waits up to `STOP_GRACE_S` for the `-32800` replies, runs `on_stop` hooks (closing the database pool), kills children started through `ctx.rt.children`, and shuts executors down without waiting.
- Process-pool workers end on their own when the parent exits (their call queue closes); in the live run they were gone within a second.
- Start every subprocess (ffmpeg, a tool) with `stdin=subprocess.DEVNULL` and add it to `ctx.rt.children`, so the worker never leaves one behind after EOF. The host's process-group SIGKILL covers the rest on POSIX; on Windows it uses `taskkill /T`.

## 10. Large payloads

Large payloads travel as files or shared memory, never as one huge line.

| Payload | Send |
|---|---|
| input files | absolute paths, checked against the allowed roots the host passes |
| results up to a few MB | inline JSON |
| larger results (transcripts, waveforms, index dumps) | a temp file in the app's cache dir; reply with its path; the caller deletes it |
| frames, audio buffers, tensors | shared memory (`multiprocessing.shared_memory`) or a memory-mapped file whose name travels in the message |
| a stream of rows | `$/progress` with small batches, or a file the worker appends to |
