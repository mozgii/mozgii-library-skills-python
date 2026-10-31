---
name: python-stdio-worker
description: Writes a long-running Python worker that a host process (Electron, Node, Rust, Go or another Python program) starts once and talks to over stdio JSON-RPC, with no HTTP server - stdout reserved for protocol lines, handshake before heavy imports, typed methods, progress, cancel, ping and shutdown, thread, process and GPU queues, cached models, Postgres access, packaging with uv and a relocatable CPython, contract tests over pipes. Use when building a Python sidecar, background worker, JSON-RPC over stdin and stdout, or keeping a model loaded between calls.
metadata:
  tags:
  - python
  - worker
  - sidecar
  - json-rpc
  - stdio
  - asyncio
  - subprocess
  - uv
  - packaging
  - gpu
---

# Python stdio worker process

A stdio worker is a Python program that a host process starts once, keeps warm and calls over newline-delimited JSON-RPC on stdin and stdout. It needs no port, no HTTP framework and no firewall prompt, and it dies with its host. Copy `scripts/worker_skeleton.py` as the runtime and replace its demo methods; `scripts/test_worker_contract.py` proves the wire.

## When to use this skill

Use a stdio worker when one host owns the Python process and calls it many times.

| Situation | Use |
|---|---|
| A desktop app or CLI needs Python for media, ML or data work, called many times | this skill |
| One call, then exit (a converter, a formatter) | a plain subprocess: the `python-subprocess` skill |
| Many clients over a network | an HTTP service, not this skill |
| A model must stay in memory between calls | this skill (Workflow 4) |
| An agent tool server | an MCP server; MCP's stdio transport claims stdout the same way |

## Routing

Each topic has a workflow here and detail in a reference file.

| Task | Go to |
|---|---|
| The wire: messages, codes, line limit, lifecycle | [protocol.md](references/protocol.md) |
| Entry point, stdout protection, handshake | Workflow 1, [runtime.md](references/runtime.md) §1–§4 |
| Methods, typed params, error codes | Workflow 2, [runtime.md](references/runtime.md) §5–§8 |
| Progress, cancel, threads, processes, GPU queue | Workflow 3, [warm-and-concurrency.md](references/warm-and-concurrency.md) §1–§3 |
| Fast start, lazy imports, cached models, GPU memory, idle | Workflow 4, [warm-and-concurrency.md](references/warm-and-concurrency.md) §4–§6 |
| Postgres from the worker, resumable jobs | Workflow 5, [database.md](references/database.md) |
| Bundle the worker with a relocatable CPython via uv | Workflow 6, [packaging.md](references/packaging.md) |
| Worker kinds with conflicting dependencies | [packaging.md](references/packaging.md) §9 |
| Tests over pipes and unit tests | Workflow 7, [testing.md](references/testing.md) |

## Step 0: read what is installed

The worker runs on the interpreter the host starts, which may not be the developer's Python, so read that one.

```bash
PY=.venv/bin/python3                       # or the bundled runtime: <resources>/bin/<target>/python/bin/python3
"$PY" -c "import sys; print(sys.version)"; uv --version; uv python list --only-installed
"$PY" -c "import asyncio, inspect; print('loop_factory' in inspect.signature(asyncio.run).parameters)"   # True on 3.12+
uv pip list --python "$PY"                 # library versions; read their docs for those versions
```

Agree with the host on the protocol major version and the maximum line size before writing code; the worker must announce the same major and keep every line below that limit.

## What the worker owes the host

The worker's obligations are few, and each one prevents a specific failure.

| Rule | Why |
|---|---|
| stdout carries protocol lines only; everything else goes to stderr | one stray byte corrupts a reply; the host logs stderr as text |
| first line `$/ready { protocol, pid, name, version, capabilities }`, sent before heavy imports | the host kills a worker that misses its start timeout |
| one reply per request id; `$/ping` answered while work runs | two missed pings mean kill and restart |
| `$/progress { id, done, total, label?, checkpoint? }` at most about 10/s, final state always sent | the job runner persists the checkpoint and resumes from it |
| `$/cancel { id }` stops at the next checkpoint and replies `-32800` | timeouts and a Cancel button both send it |
| `shutdown` replies `{}`, cancels, flushes, exits 0; stdin EOF does the same | EOF is how the worker dies with its host, even after `kill -9` |
| never send requests; never use `-32001` to `-32003` | those codes belong to the host |

## Program layout

A worker is one uv project; the host starts it with `-m` so the package must be installed in that interpreter.

```
workers/                         uv project, the host's cwd for the worker
  pyproject.toml  uv.lock
  src/my_app_worker/
    __main__.py                  claim stdio → import method modules → serve
    runtime.py                   scripts/worker_skeleton.py, demo methods removed
    methods/media.py  db.py      @method handlers; heavy imports inside them
  tests/test_contract.py         scripts/test_worker_contract.py
  tests/test_methods.py          handlers called without the transport
```

The host starts it as `python3 -I -B -u -X utf8 -m my_app_worker --role=media`. `-I` removes the cwd from `sys.path` and ignores every `PYTHON*` variable, so the flags carry their meaning instead.

Worker kinds whose dependencies conflict get one environment group each: [packaging.md](references/packaging.md) §9.

If `scripts/` is not on disk (an install that keeps only searchable text), assemble `runtime.py` by pasting the Python blocks of [runtime.md](references/runtime.md) in order, §1 imports to §8 errors, then call its `main()` from `__main__.py`. The smoke test under Verify stands in for the contract suite.

## Workflow 1: claim stdout, then send the handshake

Claim the protocol channel at the file-descriptor level as the first action of `main()`. Re-pointing loggers one library at a time misses C extensions, `os.write(1, ...)` and child processes.

```python
def claim_stdio() -> tuple[BinaryIO, BinaryIO]:
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

The entry module imports only the standard library and the method modules, then serves:

```python
# src/my_app_worker/__main__.py
import importlib

from my_app_worker import runtime

for name in ("my_app_worker.methods.media", "my_app_worker.methods.db"):
    importlib.import_module(name)        # registers @method handlers; their drivers and models stay lazy
runtime.main()                           # claim_stdio() → logging to stderr → $/ready → reader thread → loop
```

- `main()` runs only from `__main__.py` or under `if __name__ == "__main__"`: spawn-based process pools re-import the main module in every child.
- Write each message as one compact `json.dumps(..., allow_nan=False)` line plus `b"\n"`, under one lock, flushed at once (runtime.md §3). Binary mode keeps `\n` on Windows.
- Read stdin on a daemon thread with `readline(limit)` and hand lines to the loop with `call_soon_threadsafe`; this works for pipes, files and ttys on every OS, where `connect_read_pipe` does not (runtime.md §4).
- Use `asyncio.run(serve(), loop_factory=asyncio.SelectorEventLoop)` on Windows: psycopg's async mode refuses the default Proactor loop.

Verify:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"$/ping","params":null}' '{"jsonrpc":"2.0","id":2,"method":"nope"}' \
  | "$PY" -I -B -u -X utf8 -m my_app_worker 2>/dev/null
```

It prints exactly three lines (`$/ready` with `"protocol":1`, `{"jsonrpc":"2.0","id":1,"result":{}}`, an error with `"code":-32601`) and exits 0.

## Workflow 2: method table, typed params, errors

A method is an async function of `(params, ctx)` registered by name; params and results are `TypedDict`s.

```python
class SleepParams(TypedDict):
    ms: int
    steps: int


class SleepResult(TypedDict):
    slept: int


@method("sleep")
async def sleep(params: dict[str, object], ctx: Ctx) -> SleepResult:
    p = SleepParams(ms=int_param(params, "ms", 1000, 0, 3_600_000), steps=int_param(params, "steps", 10, 1, 100_000))
    for i in range(1, p["steps"] + 1):
        await asyncio.sleep(p["ms"] / p["steps"] / 1000)
        ctx.token.check()
        ctx.progress(i, p["steps"], f"step {i}")
    return {"slept": p["ms"]}
```

- Validate at the boundary: explicit checks (`int_param`, `str_param`) raise `-32602`. A codebase that already uses pydantic builds `TypeAdapter(SleepParams)` once per module and calls `validate_python(params, strict=True)` (strict rejects `"5"` and `True` for an `int`), mapping `ValidationError` to `-32602`.
- Keep the TypedDicts in step with the host's schema for each method; a host that validates results rejects a renamed key.

| Raise | Reply | Use for |
|---|---|---|
| `RpcError(-32602, ...)` | invalid params | wrong type, out of range, missing field |
| `AppError("NOT_FOUND", ...)` | `-32000`, `data.code` | domain reasons: `NOT_FOUND`, `CONFLICT`, `BAD_INPUT`, `FORBIDDEN` (path outside allowed roots), `UNAVAILABLE` |
| `Cancelled()` or task cancel | `-32800` | after `$/cancel` |
| any other exception | `-32603 "internal error in <method>"` | the traceback goes to stderr with method and id, never into the reply |

Verify: the contract suite's `test_errors_carry_codes_not_tracebacks` passes; stdout holds no `Traceback` while stderr does.

## Workflow 3: progress, cancel and where work runs

Where work runs decides how it is cancelled; the event loop thread must stay free to answer pings.

| Work | Runs on | Cancel |
|---|---|---|
| async I/O, DB queries | the asyncio loop | task cancel at the next `await` |
| blocking calls (file copy, waiting on a subprocess, sync SDKs) | `ctx.run(ctx.rt.io, fn, ...)` thread pool | `ctx.token.check()` in the loop of `fn` |
| CPU-bound Python | `ctx.run(ctx.rt.cpu, fn, ...)`, spawn process pool, in chunks | between chunks; a running chunk finishes |
| model inference | `ctx.run(ctx.rt.gpu, fn, ...)`, one thread that owns the model cache | token checked between batches |

`Ctx.run` shields the executor future, so a cancel waits until the thread has really stopped before replying:

```python
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

- `ctx.progress(done, total, label, checkpoint)` is a token bucket of 10 messages per second per request: bursts pass, floods coalesce, and the newest state and checkpoint are flushed before the reply.
- The checkpoint is what the host stores and sends back as `params.checkpoint` on resume; verify it (size, hash) instead of trusting it.
- Results over a few MB go to a temp file and the reply carries the path; `Wire.send` turns an oversized line into `RESULT_TOO_LARGE` instead of letting the host kill the worker.

Verify: `uv run pytest -q tests/test_contract.py -k "cancel or ping_is_answered or progress"` passes.

## Workflow 4: warm and fast

A warm worker answers the handshake in milliseconds and pays for heavy imports only once.

1. Send `$/ready` after stdlib imports only. Import torch, large packages and drivers inside the methods that need them, or in a `warmup` method the host calls after the handshake. On Python 3.15, `lazy import torch` at module level defers the import the same way (PEP 810).
2. Load each model once, on the GPU thread, into a dict keyed by name; later calls reuse it.
3. Release on idle: drop references, `gc.collect()`, then `torch.mps.empty_cache()` or `torch.cuda.empty_cache()` on the GPU thread (`--idle-release-s`). A full release, including the CUDA context, is a process exit: let the host stop idle workers.
4. Exit when told: `shutdown`, stdin EOF, SIGTERM, or `--idle-exit-s` for a worker run without a host.

```python
MODELS: dict[str, torch.nn.Module] = {}        # touched only on the gpu thread


def model(name: str) -> torch.nn.Module:
    if name not in MODELS:
        import torch                            # first use pays the import; the handshake never does

        MODELS[name] = torch.nn.Linear(4096, 4096).to(device()).eval()   # real worker: load weights here
    return MODELS[name]


@method("gpu.infer")
async def gpu_infer(params: dict[str, object], ctx: Ctx) -> InferResult:
    return await ctx.run(ctx.rt.gpu, infer_on_gpu, int_param(params, "batch", 8, 1, 4096))
```

Measured on Apple silicon: `$/ready` 47 ms after spawn, first call 1.1 s (torch import, model, MPS start), second call 1 ms. Full module with `device()`, `infer_on_gpu` and `release_gpu`: warm-and-concurrency.md §3.

Verify: `"$PY" -X importtime -I -B -m my_app_worker </dev/null >/dev/null 2> it.log; sort -t'|' -k2 -n it.log | tail -5` lists no heavy package; `READY_BUDGET_MS=500 uv run pytest -q tests/test_contract.py -k ready` passes.

## Workflow 5: Postgres from the worker

The host passes database coordinates in one app-prefixed variable; the worker builds an explicit conninfo and deletes `PG*`, because libpq fills every unset parameter from the environment.

```python
@method("facts.write")
async def facts_write(params: dict[str, object], ctx: Ctx) -> FactsResult:
    job_id = int_param(params, "jobId", 0, 1, 2**62)
    count = int_param(params, "count", 3, 1, 10_000)
    p = await pool(ctx)
    async with p.connection() as conn, conn.transaction():   # one transaction per job: all rows or none
        for i in range(count):
            ctx.token.check()
            await conn.execute("insert into facts (job_id, path) values (%s, %s)", (job_id, f"/raw/{i}.mov"))
            ctx.progress(i + 1, count)                       # progress only: nothing is durable yet
    ctx.progress(count, count, checkpoint={"next": count})   # the transaction has committed
    return {"job_id": job_id, "written": count}
```

- `pool(ctx)` opens one `AsyncConnectionPool` lazily (`open=False`, then `await pool.open(wait=True)`) and registers `pool.close` in `ctx.rt.on_stop` (database.md §2).
- Commit, then report the checkpoint; make every batch idempotent so a resume from an older checkpoint adds nothing (database.md §3).
- Job state belongs to the host's job runner: report through `$/progress`. Write the `jobs` row yourself only for a detached job (database.md §4).

Verify: start the worker with `PGPORT=1 PGDATABASE=wrong` and `MY_APP_DB` set; `facts.write` still succeeds and `SELECT count(*) FROM facts WHERE job_id = <id>` returns the count.

## Workflow 6: package with uv and a relocatable CPython

A shipped worker is a managed CPython with the worker installed into its own prefix, built on each target.

```bash
cd workers && uv lock --check                                   # the lock matches pyproject.toml
PYV=$(cat .python-version)                                      # the minor version the lock was made for
T=$PWD/../resources/bin/darwin-arm64                            # one target per build machine
uv python install "$PYV" --install-dir "$PWD/../build/pbs" --no-bin
rm -rf "$T/python" && mkdir -p "$T" && cp -R ../build/pbs/cpython-"$PYV".*-macos-aarch64-none "$T/python"
UV_PROJECT_ENVIRONMENT="$T/python" uv sync --frozen --no-dev --no-editable --reinstall-package my-app-worker \
  --python "$T/python/bin/python3"                              # reinstall: uv's cache key for local code is pyproject.toml
rm -rf "$T/python/lib/python$PYV/site-packages/pip" "$T/python/lib/python$PYV/site-packages/pip-"* "$T/python/bin/pip"*
"$T/python/bin/python3" -I -m compileall -q -j 0 -f --invalidation-mode unchecked-hash "$T/python/lib"
```

- Install into the runtime's own prefix, not a venv: a venv's `pyvenv.cfg` and `bin/python` hold absolute paths and break when the app moves, even with `uv venv --relocatable`.
- Build each target on its own runner; Windows and Linux layouts and the full script are in packaging.md §3.
- Torch and other accelerator builds come from per-platform indexes in `[tool.uv.sources]`; pin one version every chosen index carries (packaging.md §2).

Verify (move test): `mv resources /tmp/elsewhere && /tmp/elsewhere/bin/<target>/python/bin/python3 -I -c "import sys, my_app_worker; print(sys.prefix)"` prints the new path, and the contract suite passes with `WORKER_CMD` pointing at the moved interpreter.

## Workflow 7: test over pipes, then without them

Tests run at two levels: the wire through a real subprocess, and the methods as plain async functions.

- Contract: `scripts/test_worker_contract.py` spawns any worker command with a clean environment and asserts the wire: `$/ready` first, ping, `-32601`, `-32700`, `-32600` for an array id, progress then one result, cancel, ping during work, 50 requests in flight, a reply for every id even when EOF follows at once, EOF and `shutdown` exit 0, and no stray stdout.
- The same file runs against a host's test double written in another language (`WORKER_CMD="node fake-worker.mjs"`), so the two stay interchangeable; Python-only cases skip.
- Methods: call handlers directly with a real `Ctx` over an in-memory `Wire` (testing.md §2).

Verify: `uv run pytest -q tests/` passes on every target the app ships, including a Windows runner.

## Verify

Each workflow above ends in its own check; this smoke test proves the wire end to end and needs no file from this skill.

```python
# tests/test_wire_smoke.py
import json
import subprocess
import sys

CMD = [sys.executable, "-I", "-B", "-u", "-X", "utf8", "-m", "my_app_worker"]


def test_ready_ping_and_unknown_method() -> None:
    sent = '{"jsonrpc":"2.0","id":1,"method":"$/ping","params":null}\n{"jsonrpc":"2.0","id":2,"method":"nope"}\n'
    done = subprocess.run(CMD, input=sent, capture_output=True, text=True, timeout=10, check=True)
    lines = [json.loads(line) for line in done.stdout.splitlines()]         # any stray stdout fails here
    assert lines[0]["method"] == "$/ready" and lines[0]["params"]["protocol"] == 1
    assert lines[1] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert lines[2]["error"]["code"] == -32601 and len(lines) == 3
```

```bash
uv run pytest -q tests/test_wire_smoke.py tests/test_contract.py    # the contract suite too, where scripts/ exists
uv run pyright                                                      # strict: 0 errors
```

## Anti-patterns

| Instead of | Do |
|---|---|
| a local HTTP port for one host | stdio: no port, no firewall prompt, and the worker dies with its host |
| fixing `print` calls one library at a time | `claim_stdio()` at the descriptor level, first in `main()` |
| `import torch` at the top of the entry module | import inside the method, or a `warmup` call after `$/ready` |
| blocking calls on the event loop thread | `ctx.run` on the io, cpu or gpu executor, so pings still answer |
| a venv inside the bundle | sync into the relocatable CPython's own prefix |
| megabytes of JSON in a reply | write a temp file and return its path |

## Checklist

Check each item before handing the worker to the host.

- [ ] `claim_stdio()` is the first call in `main()`; `print` from anywhere lands on stderr.
- [ ] `$/ready` is sent after stdlib imports only; the start budget test passes.
- [ ] Every request id gets exactly one reply, including at EOF and after cancel.
- [ ] Errors carry codes and short messages; tracebacks go to stderr only.
- [ ] Blocking and CPU work never run on the loop thread; pings answer during work.
- [ ] Every subprocess starts with `stdin=subprocess.DEVNULL` and is tracked for kill at stop.
- [ ] The contract suite passes against the packaged, moved runtime.
- [ ] `uv run pyright` in strict mode reports 0 errors.

## Gotchas

These are the failures seen in real workers, with the cause and the fix.

1. **A library print breaks framing.** Calls time out and the host logs non-protocol stdout. Cause: `print`, a logging handler, C code or a child process wrote to fd 1. Fix: `claim_stdio()` first in `main()`.
2. **Progress arrives all at once at the end.** stdout to a pipe is block-buffered. Fix: a private binary writer flushed per message; `-u` for the stderr logs.
3. **The host times out at start**, only on cold machines. Cause: torch or a large package imported before the handshake. Fix: `$/ready` after stdlib imports, heavy imports inside methods, a start budget test.
4. **Nothing is read on Windows, or psycopg refuses to run.** Proactor pipes need overlapped handles and psycopg async rejects Proactor. Fix: reader thread plus `loop_factory=asyncio.SelectorEventLoop` on Windows.
5. **Ctrl+C in the dev terminal kills the worker mid-job.** The terminal sends SIGINT to the whole foreground process group. Fix: ignore SIGINT when stdin is a pipe; stop only through `shutdown`, EOF or SIGTERM.
6. **The process pool hangs or breaks for good.** `fork` in a threaded process, module-level side effects re-run in spawn children, or one killed child breaks the pool. Fix: `get_context("spawn")`, side effects only in `main()`, top-level task functions, recreate the pool on `BrokenProcessPool`.
7. **The worker talks to the wrong database.** A `PG*` variable filled a parameter the conninfo left out. Fix: name host, port, dbname and user explicitly and delete `PG*` in the worker.
8. **`-I` breaks the start** with "No module named my_app_worker". `-I` drops cwd from `sys.path`. Fix: install the package into the runtime (`--no-editable`) and pass `-B -u -X utf8` as flags.
9. **The bundle breaks after the app moves.** A venv keeps an absolute `home`; editable installs keep absolute `.pth` paths. Fix: sync into the runtime prefix, no editable installs, run the move test.
10. **Start is slow only in the packaged app.** Timestamp `.pyc` files go stale when packaging drops mtimes. Fix: `compileall --invalidation-mode unchecked-hash` as the last build step before signing.
11. **A call times out although the worker answered.** `json.dumps` wrote `NaN`, or a line exceeded the limit. Fix: `allow_nan=False`, convert numpy types, return a file path for large results.
12. **GPU memory stays taken after the job.** The caching allocator keeps freed blocks and the CUDA context lives until exit. Fix: drop references, `gc.collect()`, `empty_cache()` on the GPU thread; idle-stop the process for a full release.
13. **The bundle runs yesterday's code.** uv rebuilds a local project only when `pyproject.toml` changes. Fix: `--reinstall-package <name>` in the build, or `tool.uv.cache-keys` over `src/**/*.py`.
14. **Platforms ship different torch versions.** Per-platform indexes resolved different versions in one lock. Fix: pin a version every index carries; check wheel tags against the app's minimum OS.

## References

The reference files hold the full code and the measurements behind each workflow.

- [protocol.md](references/protocol.md): framing, messages, error codes, lifecycle, versioning
- [runtime.md](references/runtime.md): claim, wire, reader, dispatcher, context, stop paths, signals, children
- [warm-and-concurrency.md](references/warm-and-concurrency.md): queues, process pool, models, memory, lazy imports, start-up measurement
- [database.md](references/database.md): conninfo, pool, transactions, resumable and detached jobs
- [packaging.md](references/packaging.md): uv project, torch indexes, build script, bytecode, size, licences
- [testing.md](references/testing.md): contract suite, unit tests, host interop, CI
- `scripts/worker_skeleton.py`: stdlib-only reference worker (`python3 -I worker_skeleton.py` reads JSON-RPC from stdin)
- `scripts/test_worker_contract.py`: pytest contract suite for any worker command (`WORKER_CMD=...`)

## See also

- `python-subprocess`: a one-shot command rather than a warm worker
- `python-asyncio`: the event loop the dispatcher runs on
- `python-services-shutdown`: signals when no host supervises the process
- `psycopg-postgres`: the async pool behind Workflow 5
- `python-ml-packages`: torch builds per platform
- `uv-projects`: the lock and groups behind Workflow 6
