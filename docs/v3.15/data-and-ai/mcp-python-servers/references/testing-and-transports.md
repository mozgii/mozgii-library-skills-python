---
title: Testing MCP servers and choosing their transport
author: MOZGII, Inc.
lang: en
---

# Testing MCP servers and choosing their transport

How to test an MCP server written with the Python SDK (mcp 2.x) at three levels (in-process, as a real stdio process, over HTTP), how to serve it over streamable HTTP or mount it in an existing ASGI app, which protocol version a client gets, and how to register it with Claude Code and Claude Desktop on macOS, Windows and Linux. Every test here ran against mcp 2.3.0.

## Contents

This reference has 10 sections.

1. In-process tests with Client
2. Testing errors, progress and confirmations
3. Testing the real process over stdio
4. Streamable HTTP options
5. Mounting an MCP server in a Starlette app
6. Protocol versions and the Client mode
7. Claude Code and Claude Desktop configuration
8. Debugging with mcp dev and the host logs
9. Evaluating a server with a model
10. Gotchas

## 1. In-process tests with Client

`mcp.Client` connects to an `MCPServer` (or a low-level `Server`) object in the same process, runs its lifespan, and sends every call through the SDK's request path: argument validation, result conversion and error handling all run as in production. A pytest fixture gives each test a fresh connection.

```python
# conftest.py
from collections.abc import AsyncIterator

import pytest

from mcp import Client
from notes_server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncIterator[Client]:
    async with Client(mcp) as connected:
        yield connected
```

The tests mark themselves for anyio's pytest plugin, which the SDK's dependency `anyio` provides; no pytest-asyncio is needed.

```python
# test_notes.py
import pytest

from mcp import Client

pytestmark = pytest.mark.anyio


async def test_add_returns_the_note(client: Client) -> None:
    result = await client.call_tool("add_note", {"title": "Groceries", "body": "milk"})
    assert result.is_error is False
    assert result.structured_content == {"id": 1, "title": "Groceries", "body": "milk"}


async def test_schemas_and_annotations(client: Client) -> None:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert tools["search_notes"].annotations.read_only_hint is True
    assert tools["add_note"].input_schema["required"] == ["title", "body"]
```

- Each `async with Client(mcp)` enters the lifespan again, so state does not leak between tests.
- Assert on `structured_content` for data and on `content[0].text` for what the model reads.
- Assert on the published schemas and annotations too: a refactor that renames a parameter breaks every client.
- For general pytest technique (fixtures, parametrize, coverage), see the pytest-testing skill.

## 2. Testing errors, progress and confirmations

Error paths are where MCP servers differ most from plain functions, so test each kind of failure the server can produce.

```python
from mcp import MCPError, types
from mcp.client.session import ClientRequestContext


async def test_missing_note_is_a_tool_error(client: Client) -> None:
    result = await client.call_tool("delete_note", {"note_id": 99})
    assert result.is_error is True
    assert "search_notes" in result.content[0].text     # the hint the model needs


async def test_missing_resource_is_a_protocol_error(client: Client) -> None:
    with pytest.raises(MCPError) as info:
        await client.read_resource("notes://404")
    assert info.value.code == -32602                    # ResourceNotFoundError


async def test_progress_is_reported() -> None:
    seen: list[float] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        seen.append(progress)

    async with Client(jobs) as c:
        await c.call_tool("import_rows", {"count": 30}, progress_callback=on_progress)
    assert seen == [0, 10, 20, 30]


async def test_delete_needs_confirmation() -> None:
    async def decline(context: ClientRequestContext, params: types.ElicitRequestParams) -> types.ElicitResult:
        return types.ElicitResult(action="decline")

    async with Client(files, elicitation_callback=decline) as c:
        result = await c.call_tool("delete_file", {"path": "a.txt"})
    assert result.is_error is True
```

- A tool that crashes returns `is_error=True` with only `Error executing tool <name>`; a test asserting on the original exception text fails by design. Test crashes by checking the server log with `caplog`, or call the function directly.
- An unknown tool name returns `is_error=True` with `Unknown tool: <name>`, not an exception.

## 3. Testing the real process over stdio

A subprocess test proves that the entry point starts, imports cleanly and keeps stdout clean. `Client` launches the command when given `StdioServerParameters`.

```python
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


async def test_real_process_over_stdio() -> None:
    server = Path(__file__).with_name("notes_server.py")
    params = StdioServerParameters(command=sys.executable, args=[str(server)], env={"NOTES_LOG": "debug"})
    async with Client(params) as c:
        names = {tool.name for tool in (await c.list_tools()).tools}
    assert "search_notes" in names
```

- Use absolute paths and `sys.executable`; the child does not start in the test's folder unless you pass `cwd=`.
- The child inherits only a short list of variables (`HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER` on macOS and Linux; `APPDATA`, `LOCALAPPDATA`, `PATH`, `PATHEXT`, `SYSTEMROOT`, `TEMP`, `USERPROFILE` and a few more on Windows) plus what you pass in `env`. An API key in your shell does not reach the server unless you pass it.
- Stray stdout output shows up as `Failed to parse JSONRPC message from server` in the test's stderr; fail the test on it in CI.
- On exit the client closes the server's stdin and waits 2 seconds, then on macOS and Linux sends SIGTERM and after 2 more seconds SIGKILL; on Windows it kills the process. A server should exit when its stdin closes.

## 4. Streamable HTTP options

`mcp.run("streamable-http", ...)` serves the MCP endpoint with uvicorn; `mcp.streamable_http_app(...)` returns the Starlette app for your own ASGI server. Both take the same options.

| Option | Default | Meaning |
|---|---|---|
| `host` | `"127.0.0.1"` | interface to bind; `0.0.0.0` exposes the server to the network |
| `port` | `8000` | TCP port |
| `streamable_http_path` | `"/mcp"` | endpoint path |
| `json_response` | `False` | answer with plain JSON instead of an SSE stream |
| `stateless_http` | `False` | no session for 2025-era clients; 2026-07-28 traffic never uses sessions |
| `max_request_body_size` | 4 MiB | larger POST bodies get HTTP 413 |
| `transport_security` | DNS rebinding protection for localhost | `TransportSecuritySettings(allowed_hosts=[...], allowed_origins=[...])` for a real hostname |

A client connects with a URL; a string server argument means streamable HTTP.

```python
from mcp import Client

async with Client("http://127.0.0.1:8000/mcp") as client:
    result = await client.call_tool("add", {"a": 1, "b": 2})
```

- DNS rebinding protection is on automatically for `127.0.0.1`, `localhost` and `::1`. The allowlist entries are `host:port` patterns, so a request whose `Host` header has no port is rejected with `421 Invalid Host header`.
- Behind a proxy or on a public hostname, set `allowed_hosts` and `allowed_origins` explicitly, and add auth (`auth=` with `token_verifier=` or `auth_server_provider=` on the constructor) before exposing anything beyond localhost.
- The lifespan runs once when the server starts, shared by every session.

## 5. Mounting an MCP server in a Starlette app

An existing ASGI application mounts the MCP app as a route; the host app's lifespan must run `mcp.session_manager.run()`, because a mounted sub-app's own lifespan never runs.

```python
import contextlib
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp.server import MCPServer

mcp = MCPServer("notes-http", version="1.0.0")


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


mcp_app = mcp.streamable_http_app(json_response=True)        # build it before touching session_manager


@contextlib.asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    async with mcp.session_manager.run():
        yield


app = Starlette(routes=[Route("/health", health), Mount("/", app=mcp_app)], lifespan=lifespan)
```

Run it with `uvicorn asgi_app:app --host 127.0.0.1 --port 8000`; the MCP endpoint is `/mcp` and the health check `/health`. Without the lifespan every MCP request fails with `RuntimeError: Task group is not initialized`. `mcp.session_manager` exists only after `streamable_http_app()` has been called.

## 6. Protocol versions and the Client mode

mcp 2 serves two protocol eras from the same server with no configuration: 2025-era clients that open a session with `initialize`, and 2026-07-28 clients that send self-contained requests with no handshake and no session.

| | 2025-11-25 and older | 2026-07-28 |
|---|---|---|
| Connection | `initialize` handshake, session id over HTTP | none; `server/discover` is an ordinary request |
| Server asks the client (elicitation, sampling, roots) | a live request during the call | the tool returns `InputRequiredResult`; the client retries with answers |
| `ctx.elicit()` directly | works | raises `NoBackChannelError`; use `Resolve` |
| MCP logging (`ctx.info()`) | works, deprecated | works only when the request opts in, deprecated |
| `ping` | available | removed |

`Client` defaults to `mode="auto"`: it probes `server/discover` and falls back to `initialize`, so against an mcp 2 server it lands on 2026-07-28 (`client.protocol_version` says which). Pass `mode="legacy"` to test the 2025 path, which is what most hosts in the field still speak, or a version string such as `mode="2026-07-28"` to pin one. Test both modes when the server asks the person anything.

## 7. Claude Code and Claude Desktop configuration

Claude Code reads project servers from `.mcp.json` at the project root. `claude mcp add --scope project` writes this entry (shown as Claude Code 2.1 wrote it):

```json
{
  "mcpServers": {
    "notes": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/to/notes-server", "python", "-m", "notes_server"],
      "env": {"NOTES_DB": "/abs/path/to/notes.db"}
    },
    "notes-remote": {"type": "http", "url": "https://notes.example.com/mcp"}
  }
}
```

- `uv run --directory <project>` runs the server in its own locked environment wherever the host starts it.
- `--scope local` (the default) keeps the entry private to you in this project; `--scope user` makes it available in every project; `--scope project` shares it through the repository.
- Never commit secrets in `env`. Leave them out of `.mcp.json` and let each person set them with `claude mcp add -e KEY=value` at local scope.

Claude Desktop reads `claude_desktop_config.json` with the same `mcpServers` shape (`command`, `args`, `env`). The file lives in `~/Library/Application Support/Claude/` on macOS, `%APPDATA%\Claude\` on Windows and `$XDG_CONFIG_HOME/Claude/` (usually `~/.config/Claude/`) on Linux. `uv run mcp install server.py --name notes` writes an entry there, using the full path to `uv`.

| OS | What to watch |
|---|---|
| macOS | a GUI app gets a minimal `PATH`; give `command` as an absolute path (`/opt/homebrew/bin/uv`, `~/.local/bin/uv` expanded) |
| Windows | escape backslashes in JSON (`"C:\\Users\\me\\notes"`) or use forward slashes; the command is `uv.exe` or its full path |
| Linux | a desktop session may not load your shell profile; use absolute paths as on macOS |

## 8. Debugging with mcp dev and the host logs

`uv run mcp dev server.py` starts the server under the MCP Inspector, a web UI for listing and calling tools by hand; it needs Node.js because it runs the Inspector with `npx`. `mcp dev` and `mcp install` need the `cli` extra (`uv add "mcp[cli]" --dev`), and both pin the environment they spawn to your installed SDK version.

- Server logs go to stderr, and hosts keep them: Claude Code shows server status with `/mcp`, and Claude Desktop keeps MCP server logs in its logs folder.
- `MCPServer(..., log_level="DEBUG")` raises the SDK's own logging.
- A server that will not start in a host almost always fails the same way from a terminal with the exact `command` and `args` from the config; run that first.

## 9. Evaluating a server with a model

Unit tests prove the tools work; an evaluation proves a model can find the right ones and combine them. Tool names, descriptions and result sizes are what it measures, so run one after changing any of them.

Write about ten questions, then run them through a real model with only your server available and score the final answers.

- Each question is read-only and independent of the others, so the set can run in any order and change nothing.
- Each needs several tool calls to answer. A question one call can answer tests the search box, not the design.
- Each answer is a single value that can be checked exactly, such as a name, a date or a count.
- Answers must not drift. Use closed periods and archived records, never "the latest".
- Find every answer by hand through the same tools first. A question you cannot answer with the tools is a finding in itself.

Read the transcripts of the failures, not just the score. A model that calls the wrong tool or pages through everything shows which description or default to fix.

## 10. Gotchas

These are the testing and transport problems seen most.

1. **A test that passed on mcp 1.x fails at `ctx.elicit()` with `NoBackChannelError`.** `Client(mcp)` now negotiates 2026-07-28. Move the question into a `Resolve(...)` parameter, or use `Client(mcp, mode="legacy")`.
2. **The server cannot find an API key that is set in your shell.** The stdio client passes only a short list of variables. Pass the key in `env`, in the test and in the host config.
3. **`421 Invalid Host header` behind a proxy or from a test client.** DNS rebinding protection rejected the `Host`. Set `allowed_hosts` in `TransportSecuritySettings`.
4. **`RuntimeError: Task group is not initialized` on a mounted app.** The host app's lifespan does not run `mcp.session_manager.run()`. Add it.
5. **HTTP 413 on a large tool call.** The 4 MiB body limit. Raise `max_request_body_size` only as far as needed.
6. **A client built with an `httpx` object or `httpx.Auth` fails, or TLS to a private server breaks after the upgrade.** mcp 2 uses `httpx2`, which checks certificates against the OS trust store. Pass `httpx2` objects, and set `SSL_CERT_FILE` or `SSL_CERT_DIR` when a private CA was only in certifi's bundle.
