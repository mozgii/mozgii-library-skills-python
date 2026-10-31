---
title: Worker concurrency, warm models and idle release
author: MOZGII, Inc.
lang: en
---

# Worker concurrency, warm models and idle release

Where each kind of work runs inside the worker, how it is cancelled, and how the worker stays fast: the handshake before heavy imports, models loaded once, memory released when idle. Code is copied from `scripts/worker_skeleton.py` and from a GPU module that ran on Apple silicon with torch.

The blocks import from `worker_skeleton`; in your package that module is `my_app_worker.runtime` (Program layout in SKILL.md).

## Contents

This reference has 7 sections.

1. Where work runs
2. Executors and the process pool
3. GPU queue, model cache, memory release
4. Lazy imports and the start path
5. Measuring start-up
6. Idle: release or exit
7. Backpressure

## 1. Where work runs

Where work runs inside the worker depends on whether it awaits, blocks, burns CPU or uses the GPU.

| Work | Where | Why | Cancel |
|---|---|---|---|
| protocol, `$/ping`, dispatch, async I/O, database | the asyncio loop (main thread) | one thread owns message order; awaits cost nothing | task cancel at the next `await` |
| blocking calls: file copies, `subprocess.run`, sync SDKs, sync drivers | `ctx.rt.io` thread pool | keeps the loop free to answer pings | token checked inside the function |
| CPU-bound Python (parsing, hashing in Python, numeric loops) | `ctx.rt.cpu` process pool, spawn | the GIL lets only one thread run Python bytecode at a time | between chunks; a running chunk finishes |
| model inference (torch, MLX, ONNX) | `ctx.rt.gpu`, one thread | one device, one model cache, no two jobs fighting for memory | between batches |
| ffmpeg and other tools | a child process started from an `io` thread, tracked in `ctx.rt.children` | crash isolation, native speed | kill the child, then raise `Cancelled` |

Libraries that release the GIL (numpy, hashlib on large buffers, most I/O, torch kernels) run fine on threads; measure before moving them to processes, which pay pickling and a spawn per pool process.

## 2. Executors and the process pool

The executors are created lazily: an I/O thread pool, one GPU thread and a spawn process pool.

```python
@property
def io(self) -> ThreadPoolExecutor:
    self._io = self._io or ThreadPoolExecutor(max_workers=8, thread_name_prefix="io")
    return self._io


@property
def gpu(self) -> ThreadPoolExecutor:
    self._gpu = self._gpu or ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu")  # one model queue
    return self._gpu


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
```

```python
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
```

- `get_context("spawn")`: the worker has threads (reader, executors), and `fork` in a threaded process is unsafe (a DeprecationWarning when Python detects it; macOS system libraries can crash the child). Spawn is the default on macOS and Windows; since Python 3.14 the Linux default is `forkserver`, not `fork`. Be explicit anyway.
- Spawn children import the main module again: keep side effects in `main()` under `if __name__ == "__main__"`, and pass top-level functions (a lambda or a closure cannot be pickled). A pool built from `python -c` code hung in the live run for this reason.
- Children inherit the interpreter flags (`-I`, `-B` were set in the child in the live run) and fd 1, which points at stderr after `claim_stdio()`.
- Send work in chunks and check the token between them; `Future.cancel()` cannot stop a running call, and `shutdown(cancel_futures=True)` cancels only queued ones.
- A pool process killed from outside (out of memory, `kill -9`) breaks the whole pool: every later submit raises `BrokenProcessPool`. Drop the pool and let the next call create a new one, as `reset_cpu()` does; report `POOL_BROKEN` so the host's job runner can retry.
- Heavy imports a child needs (numpy, a parser) go into `ProcessPoolExecutor(initializer=...)` or into the task function, not into the parent's start path. `max_tasks_per_child` recycles children that leak memory.
- Pools are created on first use, so a worker that never runs CPU work never starts one.

## 3. GPU queue, model cache, memory release

The GPU queue is one thread that owns the device and the model cache.

```python
"""GPU methods: one model cache owned by the gpu thread, lazy torch import, release on idle."""

from __future__ import annotations

import gc
from typing import TYPE_CHECKING, TypedDict

from worker_skeleton import Ctx, int_param, method

if TYPE_CHECKING:
    import torch

MODELS: dict[str, torch.nn.Module] = {}        # touched only on the gpu thread


def device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


def model(name: str) -> torch.nn.Module:
    if name not in MODELS:
        import torch                            # first use pays the import; the handshake never does

        MODELS[name] = torch.nn.Linear(4096, 4096).to(device()).eval()   # real worker: load weights here
    return MODELS[name]


class InferResult(TypedDict):
    device: str
    norm: float


def infer_on_gpu(batch: int) -> InferResult:
    import torch

    with torch.inference_mode():
        x = torch.ones(batch, 4096, device=device())
        y = model("default")(x)
        return {"device": device(), "norm": float(y.norm().item())}


@method("gpu.infer")
async def gpu_infer(params: dict[str, object], ctx: Ctx) -> InferResult:
    return await ctx.run(ctx.rt.gpu, infer_on_gpu, int_param(params, "batch", 8, 1, 4096))


class MemoryReport(TypedDict):
    released: int
    driver_bytes: int


def release_gpu() -> MemoryReport:
    import torch

    released = len(MODELS)
    MODELS.clear()                              # drop the references first, or nothing can be freed
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
        return {"released": released, "driver_bytes": torch.mps.driver_allocated_memory()}
    if torch.cuda.is_available():
        torch.cuda.empty_cache()                # the CUDA context itself stays until the process exits
        return {"released": released, "driver_bytes": torch.cuda.memory_reserved()}
    return {"released": released, "driver_bytes": 0}


@method("gpu.release")
async def gpu_release(params: dict[str, object], ctx: Ctx) -> MemoryReport:
    return await ctx.run(ctx.rt.gpu, release_gpu)
```

- One thread owns the device and the `MODELS` dict, so loads and inference never race and no lock is needed around the cache.
- `import torch` lives inside functions; `TYPE_CHECKING` imports keep the annotations typed without importing torch at start (pyright strict passes against the installed torch).
- Measured on Apple silicon: `$/ready` 47 ms after spawn; first `gpu.infer` 1.1 s (torch import, model, MPS start); second 1 ms; `gpu.release` brought Metal driver memory back to about 3 MB.
- The caching allocator keeps freed blocks: in a separate run a 1 GiB tensor left `driver_allocated_memory()` at 1032 MB after `del` and `gc.collect()`, and at 0 after `torch.mps.empty_cache()`. `empty_cache()` frees only blocks no tensor uses, so drop every reference first.
- CUDA: `torch.cuda.empty_cache()` returns cached blocks, but the CUDA context (hundreds of MB) stays until the process exits. Not measured here; check `nvidia-smi` on the first CUDA target.
- Several models that do not fit together: keep an LRU of loaded names and release the oldest before loading a new one, on the same thread.

## 4. Lazy imports and the start path

Lazy imports keep the handshake fast: the start path imports the standard library only.

| Rule | Example |
|---|---|
| the entry module and the runtime import the standard library only | `__main__.py` imports `runtime` and the method modules |
| method modules import heavy packages inside functions | `def model(): import torch` |
| annotations come from `TYPE_CHECKING` imports | `if TYPE_CHECKING: import torch` with `from __future__ import annotations` (not needed on 3.14+, where annotations are deferred) |
| on Python 3.15+, a module-level `lazy import` defers the load until first use | `lazy import numpy as np` (PEP 810) |
| a large application package is imported where it is first needed | `async def pool(): from my_app.db import engine` |
| optional warm-up runs after `$/ready` | the host calls `warmup` right after the handshake |

A `warmup` method that loads the default model on the GPU thread:

```python
@method("warmup")
async def warmup(params: dict[str, object], ctx: Ctx) -> dict[str, list[str]]:
    await ctx.run(ctx.rt.gpu, model, "default")
    return {"loaded": sorted(MODELS)}
```

The first request that needs a module still waits for its import, but the handshake, pings and other methods never do. The import lock makes a second thread that imports the same module wait for the first instead of importing twice.

## 5. Measuring start-up

Measure start-up with `-X importtime` and a start budget test.

```bash
"$PY" -X importtime -I -B -m my_app_worker </dev/null >/dev/null 2> importtime.log   # EOF: the worker exits at once
sort -t'|' -k2 -n importtime.log | tail -15                               # biggest cumulative imports (microseconds)
grep -E '\| +(torch|numpy|pandas|sqlalchemy|pydantic)$' importtime.log    # must print nothing for the start path
```

- Python 3.14 adds `-X importtime=2`, which also reports cached imports; plain `-X importtime` is enough here.
- Columns: self time, cumulative time (with nested imports), module name; the output can be garbled in multi-threaded programs, so measure the start path, not a running worker.
- Measure cold as well as warm: the first launch after a reboot reads every file from disk. On one Apple silicon machine `import torch` took about 2 s cold and 376 ms warm.
- The contract suite's `test_ready_within_the_start_budget` measures spawn to `$/ready`; set `READY_BUDGET_MS` well under the host's start timeout. The skeleton needs about 30 ms.

## 6. Idle: release or exit

Idle handling either frees GPU memory and stays warm, or exits.

| Goal | Who | How |
|---|---|---|
| free GPU cache, keep the process warm | worker | `--idle-release-s=N`: after N seconds without requests, `release_models` runs on the GPU thread |
| free everything (CUDA context, RAM) | host | an idle timeout per worker kind: `shutdown`, then EOF; the next call starts it again |
| a worker started without a host (CLI, tests) | worker | `--idle-exit-s=N` |

```python
def _arm_idle(self) -> None:
    self._idle = []
    if self.idle_release_s > 0:
        self._idle.append(self.loop.call_later(self.idle_release_s, lambda: self.loop.create_task(release_models(self))))
    if self.idle_exit_s > 0:
        self._idle.append(self.loop.call_later(self.idle_exit_s, self.stop, "idle"))


async def release_models(rt: Runtime) -> int:
    if not rt.gpu_started:
        return 0
    released = await rt.loop.run_in_executor(rt.gpu, drop_models)
    if released:
        log.info("idle: released %d model(s)", released)
    return released
```

Pick per kind: a transcription worker that holds a 3 GB model is cheap to keep warm on a 64 GB Mac and expensive on an 8 GB laptop; read the machine's memory at start (`os.sysconf` or `psutil` if the codebase has it) and choose the release delay from it.

## 7. Backpressure

Backpressure keeps a busy worker from buffering without bound.

- The host limits requests in flight per worker and waits for its pipe to drain before writing more.
- Inside the worker, the GPU executor queues requests in order; the process pool queues chunks. Both are bounded by what the host sends.
- Progress is coalesced by the token bucket; never write one message per file or frame.
- A request that would allocate without bound (a whole video decoded into RAM) is a bug: stream it in chunks with checkpoints.
- If the host stops reading stdout, `Wire.send` blocks under its lock: work pauses instead of buffering without bound. A blocked loop then misses pings, and the host restarts the worker.
