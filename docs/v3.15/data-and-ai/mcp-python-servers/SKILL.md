---
name: mcp-python-servers
description: Builds Model Context Protocol (MCP) servers in Python with the official MCP Python SDK (package mcp, version 2) - MCPServer (formerly FastMCP) versus the low-level Server, tools with typed inputs, structured output with output schemas from return types and pydantic models, tool annotations (readOnlyHint, destructiveHint, idempotentHint, openWorldHint), ToolError results versus protocol errors, resources and prompts, Context for progress, lifespan for shared resources, asking the user with Resolve and Elicit, stdio and streamable HTTP, clean stdout under stdio, registering in Claude Code or Claude Desktop, in-process tests with Client, and the upgrade from mcp 1.x. Use when writing an MCP server or tool in Python, exposing functions or data to Claude or another AI agent, porting FastMCP code to mcp 2, adding output schemas or annotations, or when a server prints garbage on stdio, hides its error messages or will not start in Claude.
metadata:
  tags:
  - python
  - mcp
  - model-context-protocol
  - mcpserver
  - fastmcp
  - ai-agents
  - claude
  - tools
---

# MCP servers in Python

The Model Context Protocol (MCP) lets an AI application (Claude Code, Claude Desktop, any MCP host) call your code as tools, read your data as resources and fetch your prompts. The official Python SDK, the `mcp` package, turns type-hinted functions into all three. Version 2 renamed the main class and changed how errors reach the model, so older tutorials no longer run. This skill covers servers; [tools-and-schemas.md](references/tools-and-schemas.md) has the schema and annotation detail and [testing-and-transports.md](references/testing-and-transports.md) the tests, HTTP serving and client configuration.

## Step 0: read the installed versions

The SDK changes fast, so read the Python version and the SDK version before writing code.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
uv run python -c "import importlib.metadata as m; print('mcp', m.version('mcp'))"
```

| mcp version | What it means for code |
|---|---|
| 1.x | `FastMCP` from `mcp.server.fastmcp`, camelCase fields (`inputSchema`); pin `mcp<2` to keep it running |
| 2.x (2.3 in October 2026) | `MCPServer` from `mcp.server`, snake_case fields (`input_schema`, `is_error`), `Client` for tests, protocol 2026-07-28 served beside 2025-11-25 |

The SDK supports Python 3.10 and later. Add it with `uv add mcp`, or `uv add "mcp[cli]"` for the `mcp dev` and `mcp install` commands.

## Choose MCPServer or the low-level Server

`MCPServer` is the default: it reads your function signatures, builds the JSON Schemas, validates arguments, converts return values and catches errors. The low-level `mcp.server.Server` takes one handler per protocol method (`on_list_tools`, `on_call_tool`, ...) and suits proxies, gateways and tools generated at run time. It does not validate arguments against your `input_schema`, and an exception in `on_call_tool` becomes a protocol error, not an `is_error` result, so validate and catch in the handler.

| Need | Use | Why the other loses here |
|---|---|---|
| tools, resources and prompts written as typed functions | `MCPServer` | low-level `Server` makes you write schemas, validation and error mapping |
| a proxy, a gateway, or tools generated at run time | low-level `Server` | `MCPServer` builds its tools from signatures known at import |

## A minimal MCPServer

A server is an `MCPServer` instance with decorated functions; `mcp.run()` serves it over stdio.

```python
from mcp.server import MCPServer

mcp = MCPServer("notes", version="1.0.0", instructions="Personal notes. Search before adding.")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


if __name__ == "__main__":
    mcp.run()                 # stdio; mcp.run("streamable-http", port=8000) for HTTP
```

- Call the decorator: `@mcp.tool()`; a bare `@mcp.tool` raises `TypeError`.
- The function name becomes the tool name and the docstring its description; `name=`, `title=` and `description=` override them.
- `instructions` tells the model how the tools fit together; `version` defaults to an empty string, so set it.

## Tools with typed inputs

The parameters of a tool function become its `input_schema`; `Annotated[..., Field(...)]` adds limits and per-argument descriptions that the model reads.

```python
from typing import Annotated

from pydantic import Field


@mcp.tool(title="Search notes")
def search_notes(
    query: Annotated[str, Field(max_length=200, description="words to find in title or body")],
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> list[str]:
    """Find notes whose title or body contains the text. Use before adding a note."""
    ...
```

- Parameters without defaults are `required`. Invalid arguments never reach the function: the client gets `is_error=True` with pydantic's message, so the model can retry.
- A `def` tool runs on a worker thread (`anyio.to_thread.run_sync`), an `async def` tool on the event loop; thread-bound objects such as a default `sqlite3` connection break in `def` tools.

## Structured output from return types

The return annotation decides the tool's `output_schema` and its `structured_content`; the SDK also sends a text copy in `content` for clients that read only text.

```python
from pydantic import BaseModel


class Note(BaseModel):
    id: int
    title: str
    body: str


@mcp.tool()
def get_note(note_id: int) -> Note:       # output_schema is Note's JSON Schema
    """Read one note by id."""
    return Note(id=note_id, title="Groceries", body="milk, eggs")
```

| Return annotation | Structured content |
|---|---|
| a pydantic model, `TypedDict` or dataclass | the object itself, schema from the class |
| `dict[str, T]` | the dict itself |
| `int`, `str`, `bool`, `list[T]`, a union, `None` | wrapped as `{"result": value}` |
| a content block, `Image`, `Audio`, or no annotation | none: content only |

`structured_output=False` on the decorator sends text only; `True` fails at import when the return type cannot carry a schema.

## Tool annotations

Tool annotations are hints a client uses to decide whether to ask the person before a call. Set all four on every tool: the protocol's defaults assume the worst (`destructive_hint` and `open_world_hint` default to true).

```python
from mcp.types import ToolAnnotations

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)


@mcp.tool(annotations=DELETE)
def delete_note(note_id: int) -> bool:
    """Delete one note by id. Use only after the person confirmed which note."""
    ...
```

The snake_case fields travel as `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint`. They are hints, never a replacement for your own checks.

## Errors: ToolError, MCPError and crashes

An MCP tool fails in one of three ways, and each reaches the model differently.

| Raised in the tool | Client sees | Use for |
|---|---|---|
| `ToolError("No note with id 99. Call search_notes first.")` | `is_error=True`, text `Error executing tool <name>: <your message>` | failures the model can act on |
| any other exception | `is_error=True`, only `Error executing tool <name>`; traceback in the server log | bugs: their text may hold secrets, so it stays private |
| `MCPError(code, message)` | a JSON-RPC protocol error; the model never sees it | a malformed request the host must handle |

Inside a tool body, an expected failure is one `raise`:

```python
from mcp.server.mcpserver.exceptions import ToolError

raise ToolError("No note with id 99. Call search_notes to find the right id.")
```

Write `ToolError` messages for the model: what went wrong and what to call next. Error design in general is the python-error-handling skill.

## Context for progress and request data

A tool with a parameter annotated `Context` receives the request context; the name does not matter and it stays out of the input schema.

```python
from mcp.server.mcpserver import Context


@mcp.tool()
async def import_rows(count: int, ctx: Context) -> int:
    """Import rows and report progress after each batch."""
    for done in range(0, count + 1, 100):
        await ctx.report_progress(done, count, message=f"{done} of {count} rows")
    return count
```

- `ctx.report_progress(progress, total, message)` is a no-op unless the caller asked for progress.
- `ctx.request_context.lifespan_context` is the object your lifespan yielded.
- `ctx.info()`, `ctx.warning()` and the other log helpers emit `MCPDeprecationWarning`: protocol 2026-07-28 deprecates MCP logging (SEP-2577). Use the `logging` module to stderr.

## Lifespan for shared resources

The lifespan is an async context manager that opens shared resources (a database pool, an HTTP client, a model) once at start and closes them at exit; tools reach what it yields through the context.

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx


@asynccontextmanager
async def lifespan(_server: MCPServer[httpx.AsyncClient]) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as http:
        yield http


mcp = MCPServer("weather", lifespan=lifespan)


@mcp.tool()
async def forecast(city: str, ctx: Context[httpx.AsyncClient]) -> str:
    """Today's forecast for a city."""
    http = ctx.request_context.lifespan_context
    return (await http.get("https://example.com/forecast", params={"city": city})).text
```

Under streamable HTTP the lifespan runs once at start and is shared by every session; per-connection resources belong in the tool body.

## Resources and prompts

A resource is read-only data addressed by a URI, registered with `@mcp.resource("notes://{note_id}")`; placeholders make it a template whose parameters must match the function's parameters exactly. A prompt is a reusable message template, registered with `@mcp.prompt()`. Use a resource for data the host may attach to a conversation and a tool for anything the model decides to fetch or change. Examples, `ResourceNotFoundError` and URI template rules are in [tools-and-schemas.md](references/tools-and-schemas.md).

## Ask the person before a change

A tool that needs the person's answer (a confirmation, a missing value) declares a parameter `Annotated[T, Resolve(fn)]` whose resolver returns `Elicit(message, Model)`. The SDK asks through the client and works for both 2025-era and 2026-07-28 clients; a direct `await ctx.elicit(...)` fails on 2026-07-28 connections with `NoBackChannelError`. The full example is in [tools-and-schemas.md](references/tools-and-schemas.md).

## Transports: stdio and streamable HTTP

Stdio is the transport for a local server that the host starts as a subprocess; streamable HTTP is the transport for a server that runs on its own and serves many clients.

| | stdio | streamable HTTP |
|---|---|---|
| Start | `mcp.run()` | `mcp.run("streamable-http", port=8000)`, served at `/mcp` |
| Who starts it | the host, one process per client | you, one process for many clients |
| Auth | the OS user | OAuth (`auth=`, `token_verifier=` on the constructor) |

Transport options (`host`, `port`, `json_response`, `stateless_http`, `transport_security`) go to `run()` or `streamable_http_app()`, never to the constructor. Details and mounting in an existing ASGI app are in [testing-and-transports.md](references/testing-and-transports.md).

## Keep stdout clean under stdio

Under stdio, stdout carries the JSON-RPC messages, so any other byte on it corrupts the stream. While serving, the SDK points file descriptor 1 at stderr, so `os.write(1, ...)` and child processes cannot reach the wire. Two leaks remain (tested with mcp 2.3): a `print` before `mcp.run()`, at import time, goes straight to the client, which logs `Failed to parse JSONRPC message`; and a `print()` in a tool sits in Python's `sys.stdout` buffer until exit, when it is flushed onto the restored wire.

Configure logging to stderr at the top of the entry module and never print:

```python
import logging
import sys

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
```

For any stdio protocol the same rule is explained in the python-stdio-worker skill.

## Register the server in Claude Code or Claude Desktop

Claude Code starts a stdio server from a command. `claude mcp add` writes the entry; `--scope project` writes it to `.mcp.json` in the project so the team shares it.

```bash
claude mcp add notes --scope project -- uv run --directory /abs/path/to/notes-server python -m notes_server
claude mcp add --transport http notes-remote https://notes.example.com/mcp
```

Claude Desktop reads `claude_desktop_config.json` (macOS `~/Library/Application Support/Claude/`, Windows `%APPDATA%\Claude\`, Linux `~/.config/Claude/`); `uv run mcp install server.py` adds an entry. Use absolute paths: the host does not start the server in your project folder, and a macOS GUI app does not inherit your shell's `PATH`. Entry formats are in [testing-and-transports.md](references/testing-and-transports.md).

## Test a server in-process

`mcp.Client` accepts an `MCPServer` instance and connects in-process, so a test calls tools through the real protocol path without a subprocess.

```python
import pytest

from mcp import Client
from notes_server import mcp


@pytest.mark.anyio
async def test_search_returns_structured_notes() -> None:
    async with Client(mcp) as client:
        await client.call_tool("add_note", {"title": "Groceries", "body": "milk"})
        result = await client.call_tool("search_notes", {"query": "milk"})
    assert result.is_error is False
    assert result.structured_content["total"] == 1
```

Each `async with Client(mcp)` enters the server's lifespan, so every test starts fresh. More test patterns are in [testing-and-transports.md](references/testing-and-transports.md).

## Worked example: a small notes server with its test

This server is complete: a tool that writes, a tool that reads with a typed result, an error the model can act on, and logging on stderr. Save it as `notes_server/__main__.py` in a package, or as one module.

```python
import logging
import sys
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
mcp = MCPServer("notes", version="1.0.0", instructions="Personal notes. Search before adding.")
NOTES: dict[str, str] = {}
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


class SearchResult(BaseModel):
    total: int
    titles: list[str]


@mcp.tool(annotations=WRITE)
def add_note(title: Annotated[str, Field(min_length=1, max_length=200)], body: str) -> str:
    """Save a note under a title. Use after search_notes found nothing similar."""
    NOTES[title] = body
    return title


@mcp.tool(annotations=READ)
def search_notes(query: Annotated[str, Field(min_length=1, max_length=200)]) -> SearchResult:
    """Find notes whose title or body contains the text."""
    titles = [t for t, body in NOTES.items() if query in t or query in body]
    return SearchResult(total=len(titles), titles=titles)


@mcp.tool(annotations=READ)
def read_note(title: str) -> str:
    """Read one note by its exact title."""
    if title not in NOTES:
        raise ToolError(f"No note titled {title!r}. Call search_notes to find the title.")
    return NOTES[title]


if __name__ == "__main__":
    mcp.run()
```

`NOTES` is a module-level dict to keep the example short; a real store opens in the lifespan, which also gives every test a fresh one.

## Verify

Prove the tools through the protocol, then prove that a real stdio client gets clean JSON from the first byte.

```bash
uv run pytest -q
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  | uv run python -m notes_server 2>/dev/null | head -1 | python3 -m json.tool | grep serverInfo   # one JSON line, no stray output
grep -rnE "^\s*print\(" src                        # expect nothing
grep -rnE "FastMCP|mcp\.server\.fastmcp" src tests  # expect nothing on mcp 2.x
```

A tool that touches files, a database or a shell also needs attack cases in its tests: a path with `../`, a value at the schema's length limit plus one, and an id that belongs to someone else must each come back as `is_error=True`.

## Anti-patterns

| Instead of | Do |
|---|---|
| `print()` for debugging in a stdio server | `logging` to stderr |
| `raise Exception("bad id")` for a failure the model can fix | `ToolError` with what to call next |
| tools without annotations | all four set, honestly |
| one `run(action, payload)` tool for everything | one tool per operation, each with its own schema |
| `-> dict` or no return annotation | a pydantic model, so the client gets `output_schema` |
| a raw path or SQL string as a tool argument | an id or a constrained value, checked in the tool |
| a global client opened at import | the lifespan |

## Checklist

An MCP server is ready to ship when every item holds.

- [ ] `mcp` 2.x pinned and locked; imports use `MCPServer`, not `FastMCP`.
- [ ] Every tool has a description saying when to use it, typed parameters with limits, and a return annotation.
- [ ] All four annotations set; anything that deletes or overwrites has `destructive_hint=True`.
- [ ] Expected failures raise `ToolError` with a message the model can act on, and no secrets.
- [ ] Shared resources open in the lifespan; `def` tools touch only thread-safe objects.
- [ ] Nothing writes to stdout; logging goes to stderr.
- [ ] Tests call every tool through `Client(mcp)`, error paths included.
- [ ] The host entry uses absolute paths and was tried in a real host.

## Gotchas

These are the MCP server failures seen most often.

1. **`No module named 'mcp.server.fastmcp'`.** mcp 1.x code on mcp 2. Import `MCPServer` from `mcp.server`, or pin `mcp<2`.
2. **`'Tool' object has no attribute 'inputSchema'`.** mcp 2 fields are snake_case: `input_schema`, `is_error`.
3. **The model sees only `Error executing tool search`.** The tool raised a plain exception, which mcp 2 hides. Raise `ToolError` for failures the model should read.
4. **The client logs `Failed to parse JSONRPC message from server`.** Something printed to stdout. Log to stderr instead.
5. **`SQLite objects created in a thread can only be used in that same thread`.** A `def` tool runs on a worker thread. Make it `async def`, or use `check_same_thread=False` and serialise access.
6. **`MCPServer.__init__() got an unexpected keyword argument 'port'`.** Transport options moved to `mcp.run(...)` and `streamable_http_app(...)`.
7. **The server works in a terminal but not in Claude Desktop.** The host used another working directory and `PATH`. Use absolute paths and read the host's MCP log.

## References

The reference files hold the longer material for this skill.

- [tools-and-schemas.md](references/tools-and-schemas.md): input and output schemas, content results, resources and prompts, Resolve and Elicit, a low-level `Server`, the mcp 1.x to 2.x renames
- [testing-and-transports.md](references/testing-and-transports.md): `Client` tests, subprocess and HTTP tests, streamable HTTP options, mounting in Starlette, protocol versions, `.mcp.json` and Claude Desktop entries, `mcp dev`

## See also

- `anthropic-sdk-python`: calling Claude yourself, and the tool loop on the client side
- `python-stdio-worker`: the stdout discipline of any stdio protocol
- `python-data-models`: the pydantic models behind tool inputs and results
- `pytest-testing`: the test setup around `Client(mcp)`
- `python-security`: checking what a tool is handed
