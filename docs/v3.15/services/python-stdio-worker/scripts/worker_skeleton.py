# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright 2026 MOZGII, Inc. (https://mozgii.com/)
"""Reference stdio JSON-RPC worker: the Python side of the protocol in references/protocol.md.

Any host (a Node or Electron app, a Rust or Go program, another Python process) can supervise it.
Standard library only, no network, no file writes.
Run:  python -I -B -u -X utf8 worker_skeleton.py [--protocol=1] [--warmup-ms=0] [--noise] [--ignore-sigterm]
                                                  [--idle-release-s=0] [--idle-exit-s=0]
Methods: $/ping, echo, sleep {ms, steps}, cpu {n, chunks}, infer {text}, warmup, release, noise,
         crash {code}, hang {ms}, grandchild, big {bytes}, fail {code}, shutdown.
"""

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


# ---------------------------------------------------------------- 1. claim the protocol channel


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


# ---------------------------------------------------------------- 2. errors


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


# ---------------------------------------------------------------- 3. wire: one writer, whole lines


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
            request_id = message["id"]
            log.error("unserializable result for id %s: %s", request_id, exc)
            data = json.dumps({"jsonrpc": "2.0", "id": request_id,
                               "error": {"code": -32603, "message": "result is not JSON serializable"}})
        line = data.encode("utf-8") + b"\n"
        if len(line) > MAX_LINE_BYTES:
            if "id" not in message:
                log.error("dropped notification of %d bytes", len(line))
                return
            request_id = message["id"]
            line = json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {
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


# ---------------------------------------------------------------- 4. per-request context: cancel + progress


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


Handler = Callable[[dict[str, object], Ctx], Awaitable[object]]
METHODS: dict[str, Handler] = {}


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


# ---------------------------------------------------------------- 5. runtime: reader thread + asyncio dispatcher


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


# ---------------------------------------------------------------- 6. methods (demo; replace with your own)


class SleepParams(TypedDict):
    ms: int
    steps: int


class SleepResult(TypedDict):
    slept: int


@method("echo")
async def echo(params: dict[str, object], ctx: Ctx) -> object:
    return params


@method("sleep")
async def sleep(params: dict[str, object], ctx: Ctx) -> SleepResult:
    p = SleepParams(ms=int_param(params, "ms", 1000, 0, 3_600_000), steps=int_param(params, "steps", 10, 1, 100_000))
    for i in range(1, p["steps"] + 1):
        await asyncio.sleep(p["ms"] / p["steps"] / 1000)
        ctx.token.check()
        ctx.progress(i, p["steps"], f"step {i}")
    return {"slept": p["ms"]}


def sum_squares(lo: int, hi: int) -> int:     # top-level: a spawn child imports it by name
    return sum(i * i for i in range(lo, hi))


@method("cpu")
async def cpu(params: dict[str, object], ctx: Ctx) -> dict[str, int]:
    n = int_param(params, "n", 1_000_000, 1, 10**9)
    chunks = int_param(params, "chunks", 8, 1, 10_000)
    step = -(-n // chunks)
    parts = len(range(0, n, step))            # can be fewer than chunks: n=9, chunks=4 gives 3
    total = 0
    for done, lo in enumerate(range(0, n, step), start=1):
        ctx.token.check()                     # a chunk already in a pool process cannot be interrupted
        try:
            total += await ctx.run(ctx.rt.cpu, sum_squares, lo, min(n, lo + step))
        except Exception as exc:
            if type(exc).__name__ == "BrokenProcessPool":
                ctx.rt.reset_cpu()
                raise AppError("POOL_BROKEN", "a CPU process died; retry the call") from exc
            raise
        ctx.progress(done, parts, checkpoint={"next": lo + step, "partial": total})
    return {"sum": total}


class FakeModel:
    """Stands in for a model: loaded once on the gpu thread, cached, released on idle."""

    def __init__(self) -> None:
        time.sleep(0.3)                       # load cost paid once per process, not per call

    def __call__(self, text: str) -> str:
        return text.upper()


MODELS: dict[str, FakeModel] = {}             # touched only on the gpu thread


def model(name: str) -> FakeModel:
    if name not in MODELS:
        MODELS[name] = FakeModel()            # real worker: import torch here, then load weights
    return MODELS[name]


@method("warmup")
async def warmup(params: dict[str, object], ctx: Ctx) -> dict[str, list[str]]:
    await ctx.run(ctx.rt.gpu, model, "default")
    return {"loaded": sorted(MODELS)}


@method("infer")
async def infer(params: dict[str, object], ctx: Ctx) -> dict[str, str]:
    text = str_param(params, "text")
    return {"text": await ctx.run(ctx.rt.gpu, lambda: model("default")(text))}


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


@method("release")
async def release(params: dict[str, object], ctx: Ctx) -> dict[str, int]:
    return {"released": await release_models(ctx.rt)}


@method("noise")
async def noise(params: dict[str, object], ctx: Ctx) -> dict[str, bool]:
    print("print() from a handler")                                   # Python level
    os.write(1, b"raw write to fd 1, as C code would do\n")           # C level
    subprocess.run([sys.executable, "-c", "print('a child process printing')"], check=True)  # inherited fd 1
    logging.getLogger("noisy.library").warning("a library warning")
    return {"ok": True}


@method("crash")
async def crash(params: dict[str, object], ctx: Ctx) -> None:
    log.error("crashing on purpose")
    os._exit(int_param(params, "code", 3, 0, 255))


@method("hang")
async def hang(params: dict[str, object], ctx: Ctx) -> dict[str, bool]:
    time.sleep(int_param(params, "ms", 60_000, 0, 3_600_000) / 1000)  # blocks the loop on purpose: pings fail
    return {"ok": True}


@method("grandchild")
async def grandchild(params: dict[str, object], ctx: Ctx) -> dict[str, int]:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"], stdin=subprocess.DEVNULL)
    ctx.rt.children.add(child)
    return {"pid": child.pid}


@method("big")
async def big(params: dict[str, object], ctx: Ctx) -> dict[str, str]:
    return {"data": "x" * int_param(params, "bytes", 1024, 0, 64 * 1024 * 1024)}


@method("fail")
async def fail(params: dict[str, object], ctx: Ctx) -> None:
    if params.get("code") == "internal":
        raise KeyError("secret/internal/path")             # becomes -32603 without the traceback
    raise AppError("NOT_FOUND", "no such file")


# ---------------------------------------------------------------- 7. entry point


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


if __name__ == "__main__":
    main()
