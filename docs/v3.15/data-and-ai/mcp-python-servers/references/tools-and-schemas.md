---
title: MCP tools, schemas and results in the Python SDK
author: MOZGII, Inc.
lang: en
---

# MCP tools, schemas and results in the Python SDK

How the MCP Python SDK (mcp 2.x) turns a function signature into a tool's input schema and its return annotation into an output schema, what each kind of return value produces, how to write resources, prompts and confirmation questions, a complete low-level `Server`, and the names that changed from mcp 1.x. Every schema shown here was printed by mcp 2.3.0.

## Contents

This reference has 10 sections.

1. Input schemas from function signatures
2. Output schemas from return annotations
3. Returning content blocks, images and raw results
4. Tool names, titles and metadata
5. Annotation meanings and defaults
6. Resources, templates and prompts
7. Asking the person with Resolve and Elicit
8. A tool server on the low-level Server
9. Renames from mcp 1.x to mcp 2.x
10. Tools an agent can use well: names, paging and answer size

## 1. Input schemas from function signatures

MCPServer builds a tool's `input_schema` from the function parameters with pydantic, so any type pydantic can validate is a valid parameter type. This tool takes a pydantic model, a `Literal` and an optional string.

```python
from typing import Literal

from pydantic import BaseModel, Field

from mcp.server import MCPServer

mcp = MCPServer("tasks")


class Filter(BaseModel):
    """Which tasks to list."""
    status: Literal["open", "done"] = "open"
    tags: list[str] = Field(default_factory=list, max_length=10)


@mcp.tool()
def list_tasks(filter: Filter, sort: Literal["due", "created"] = "due", owner: str | None = None) -> list[str]:
    """List tasks matching a filter."""
    ...
```

The generated schema, as printed by `client.list_tools()`:

| Parameter | Schema |
|---|---|
| `filter: Filter` | `{"$ref": "#/$defs/Filter"}`, with `Filter` under `$defs`; required |
| `sort: Literal[...] = "due"` | `{"type": "string", "enum": ["due", "created"], "default": "due"}` |
| `owner: str \| None = None` | `{"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}` |

- `required` lists only parameters without defaults (here `["filter"]`).
- A model argument arrives as a validated `Filter` instance; a value outside a `Literal` fails validation and the call returns `is_error=True`.
- A parameter annotated `Context`, or `Annotated[T, Resolve(fn)]`, is not part of the schema.
- `Annotated[int, Field(ge=1, le=100, description="...")]` adds `minimum`, `maximum` and `description`. Descriptions per argument help the model more than a long docstring.
- Prefer flat, small inputs. A deeply nested model is valid, but models fill flat arguments more reliably.

## 2. Output schemas from return annotations

The return annotation decides whether a tool publishes an `output_schema` and how `structured_content` is shaped. The table shows the schema mcp 2.3 generated for each case.

| Return annotation | `output_schema` | `structured_content` |
|---|---|---|
| `-> Note` (pydantic model) | Note's schema | the model as a dict |
| `-> Totals` (`TypedDict`) | `{"title": "Totals", "type": "object", "properties": {...}, "required": [...]}` | the dict |
| `-> dict[str, int]` | `{"type": "object", "additionalProperties": {"type": "integer"}, "title": "per_tagDictOutput"}` | the dict |
| `-> bool` | `{"type": "object", "properties": {"result": {"type": "boolean"}}, "required": ["result"], "title": "delete_noteOutput"}` | `{"result": true}` |
| `-> list[str]` | an object with a `result` array | `{"result": [...]}` |
| `-> Image` or a content block | none | none |
| no annotation | none | none |

- A dataclass or any class with annotated attributes is converted to a pydantic model and treated like one.
- Every structured result also carries a text copy in `content` (the JSON), so clients that ignore `structured_content` still work.
- The return value is validated against the output model. A mismatch (an `int` tool returning a string) is treated as a crash: the client gets `is_error=True` with `Error executing tool <name>`, and the server logs the error.
- `@mcp.tool(structured_output=False)` publishes no output schema and sends only text (a dict becomes pretty-printed JSON). `structured_output=True` raises at decoration time when the annotation cannot carry a schema.
- Clients that read `output_schema` can check results without guessing, so give every tool that returns data a model or `TypedDict`, not `dict[str, Any]`.

## 3. Returning content blocks, images and raw results

A tool returns content blocks when the result is for the model to look at rather than for a program to parse. The `Image` helper takes a file path or bytes and a format.

```python
from mcp.server.mcpserver import Image
from mcp import types


@mcp.tool()
def chart() -> Image:
    """A chart of open tasks as PNG."""
    return Image(data=png_bytes, format="png")          # or Image(path="chart.png")


@mcp.tool()
def summary() -> types.CallToolResult:
    """Build the whole result by hand."""
    return types.CallToolResult(
        content=[types.TextContent(type="text", text="2 open tasks")],
        structured_content={"count": 2},
    )
```

- `Image` arrives as an `image` content block with `mime_type` `image/png`; `Audio` works the same way.
- Returning a `CallToolResult` sends it unchanged. Use it when content and structured content must differ, or to return `is_error=True` with structured details.
- Large binary results cost the model context. Return a resource URI or a file path the host can open when the data is big.

## 4. Tool names, titles and metadata

A tool name must match `^[A-Za-z0-9._-]{1,128}$` (SEP-986): letters, digits, underscore, dash and dot, case-sensitive, no spaces. The SDK warns about names outside that rule.

```python
@mcp.tool(
    name="tasks.archive_done",
    title="Archive finished tasks",
    description="Move every done task to the archive. Use when the person asks to clean up the list.",
    meta={"contract": "tasks.v1"},
)
def archive_done() -> int:
    ...
```

- `title` is for people (shown in a host's UI); `description` is for the model.
- `meta` travels as the tool's `_meta`, the place the protocol reserves for your own data; clients ignore keys they do not know.
- `mcp.add_tool(fn, name=...)` registers a function without the decorator, and `mcp.remove_tool(name)` removes one at run time.
- Keep names stable: clients and saved prompts refer to them. A breaking change to a tool's input is a new tool name.

## 5. Annotation meanings and defaults

`ToolAnnotations` carries four hints. The defaults below are the protocol's, applied when a field is absent.

| Field (wire name) | Meaning | Default |
|---|---|---|
| `read_only_hint` (`readOnlyHint`) | the tool changes nothing | false |
| `destructive_hint` (`destructiveHint`) | a change may delete or overwrite data; meaningful only when not read-only | true |
| `idempotent_hint` (`idempotentHint`) | calling twice with the same arguments has no extra effect | false |
| `open_world_hint` (`openWorldHint`) | the tool reaches outside systems (the web, an API) rather than a closed set of data | true |

| Tool | read_only | destructive | idempotent | open_world |
|---|---|---|---|---|
| search local notes | true | false | true | false |
| add a note | false | false | false | false |
| set a note's title | false | false | true | false |
| delete a note | false | true | true | false |
| send an email | false | false | false | true |
| fetch a web page | true | false | true | true |

Hosts use these to decide when to ask the person. A wrong `read_only_hint=True` on a tool that writes can let a host skip the confirmation, so set them from what the code does.

## 6. Resources, templates and prompts

A resource is read-only data addressed by a URI; a URI with `{placeholders}` registers a resource template whose parameters must match the function's parameters exactly. A prompt is a reusable message template the person picks in the host.

```python
from mcp.server.mcpserver.exceptions import ResourceNotFoundError


@mcp.resource("notes://{note_id}", mime_type="text/markdown")
def note_text(note_id: str) -> str:
    """One note as Markdown."""
    note = store.get(int(note_id))
    if note is None:
        raise ResourceNotFoundError(f"no note {note_id}")
    return f"# {note.title}\n\n{note.body}"


@mcp.prompt(title="Summarise notes")
def summarise(topic: str) -> str:
    """Ask the model to summarise every note about a topic."""
    return f"Call search_notes with {topic!r}, then summarise the notes in five bullet points."
```

- A static resource (no placeholders) takes no parameters and no `Context`; a template may take a `Context`.
- A resource function returns `str` (text), `bytes` (binary) or anything else, which is converted to JSON.
- Templates follow RFC 6570 (`{+path}`, `{?query}`); a query variable's parameter needs a default, and path traversal in extracted values is rejected by default (`resource_security=` changes the policy).
- `ResourceNotFoundError` reaches the client as JSON-RPC error `-32602`; `ResourceError` as `-32603` with your message; any other exception as a generic error naming only the URI.
- A prompt returns a string (one user message) or a list of `UserMessage` and `AssistantMessage` objects from `mcp.server.mcpserver`.

## 7. Asking the person with Resolve and Elicit

A tool parameter annotated `Annotated[T, Resolve(fn)]` is filled by a resolver function instead of the model; a resolver that returns `Elicit(message, Model)` asks the person through the client. The parameter does not appear in the input schema.

```python
from typing import Annotated

from pydantic import BaseModel

from mcp.server.mcpserver import Elicit, Resolve


class Confirm(BaseModel):
    confirm: bool


def ask_confirm(path: str) -> Elicit[Confirm]:
    return Elicit(f"Delete {path}? This cannot be undone.", Confirm)


@mcp.tool(annotations=DELETE)
def delete_file(path: str, answer: Annotated[Confirm, Resolve(ask_confirm)]) -> str:
    """Delete a file after the person confirms."""
    return f"deleted {path}" if answer.confirm else "kept"
```

- The resolver takes tool arguments by name (`path` here), other resolvers, or the `Context`.
- On a 2025-era connection the SDK sends a live elicitation request; on 2026-07-28 it returns an `InputRequiredResult` and the client retries the call with the answer. The tool body is the same.
- Annotated as `Confirm`, a decline or cancel aborts the call. Annotate the parameter as `ElicitationResult[Confirm]` to receive the outcome and branch on it.
- The client must declare the elicitation capability (the SDK `Client` does when `elicitation_callback` is set); otherwise the call fails with JSON-RPC error `-32021`.
- Elicitation schemas should hold only primitive fields such as strings, numbers and booleans, as the protocol specifies.

## 8. A tool server on the low-level Server

The low-level `Server` takes `on_*` handlers in its constructor. It is the right base for a proxy or gateway whose tools are not Python functions; you write the schemas and results yourself.

```python
from typing import Any

from mcp import types
from mcp.server import Server, ServerRequestContext

TOOLS = [
    types.Tool(
        name="echo",
        description="Return the text unchanged.",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        output_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        annotations=types.ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
]


async def list_tools(ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOLS)


async def call_tool(ctx: ServerRequestContext[Any], params: types.CallToolRequestParams) -> types.CallToolResult:
    if params.name != "echo":
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"Unknown tool {params.name}")],
                                    is_error=True)
    text = str((params.arguments or {}).get("text", ""))
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], structured_content={"text": text})


server = Server("echo", on_list_tools=list_tools, on_call_tool=call_tool)
```

- The handlers are the whole server: nothing validates `params.arguments` against `input_schema`, so validate them yourself (a pydantic model per tool works well).
- An exception raised in `call_tool` becomes a JSON-RPC error, not an `is_error` result; catch failures and return `is_error=True` for the model.
- A hand-built `Tool` whose `input_schema` lacks `"type": "object"` fails `tools/list` in mcp 2.
- Run it with `Client(server)` in tests, or over stdio with `mcp.server.stdio.stdio_server()` and `await server.run(read, write, server.create_initialization_options())`.

## 9. Renames from mcp 1.x to mcp 2.x

These are the changes almost every port from mcp 1.x hits, taken from the SDK's migration guide.

| mcp 1.x | mcp 2.x |
|---|---|
| `from mcp.server.fastmcp import FastMCP` | `from mcp.server import MCPServer` |
| `tool.inputSchema`, `result.isError`, `structuredContent` | `tool.input_schema`, `result.is_error`, `structured_content` |
| `McpError` | `MCPError(code, message, data)` |
| `FastMCP("x", port=9000)` | `MCPServer("x")` and `mcp.run("streamable-http", port=9000)` |
| `@server.list_tools()` decorators on the low-level server | `Server(name, on_list_tools=..., on_call_tool=...)` |
| `streamablehttp_client` | `streamable_http_client`, or `Client(url)` |
| resource URIs typed `AnyUrl` | plain `str` |
| `httpx` | `httpx2` (TLS checked against the OS trust store) |
| a plain exception's text reached the model | only `ToolError` text reaches the model |
| sync tools ran on the event loop | sync tools run on a worker thread |
| `MCP_*` environment variables and `.env` read by settings | read the environment yourself and pass values to the constructor |

`mcp.types` still works as an alias of the new `mcp_types` package, so `from mcp.types import ToolAnnotations` needs no change.

## 10. Tools an agent can use well: names, paging and answer size

A tool is read by a model working inside a limited context, often beside other servers, so its name and the size of its answer matter as much as its logic.

- Prefix tool names with the service (`notes_search`, `billing_create_invoice`), because a host may load several servers and `search` will collide. Start with a verb: `get`, `list`, `search`, `create`.
- Keep each tool to one job, and say in its description when to use it and which neighbouring tool to use otherwise.
- Never return an unbounded list. Take a limit with a modest default (20 to 50 items), cap it, and return what the model needs to ask for more.

This list tool bounds its page size in the schema, so a call asking for 500 fails validation with a message the model can read, and `next_offset` tells it how to go on.

```python
from pydantic import BaseModel, Field

from mcp.server import MCPServer

mcp = MCPServer("notes")
NOTES = ["Renew the domain", "Book the review", "Ship the release"]  # your store in real code


class NotePage(BaseModel):
    items: list[str]
    total: int
    next_offset: int | None = Field(description="pass as offset for the next page; null on the last page")


@mcp.tool()
def notes_list(offset: int = 0, limit: int = Field(default=20, ge=1, le=50)) -> NotePage:
    """List notes, newest first. Use notes_search when you know a word from the note."""
    page = NOTES[offset : offset + limit]
    end = offset + len(page)
    return NotePage(items=page, total=len(NOTES), next_offset=end if end < len(NOTES) else None)
```

- Tools that return documents can take a `response_format: Literal["markdown", "json"] = "markdown"` argument: Markdown for reading, with display names and readable dates, and JSON with every field for further processing.
- Large results still need a ceiling. Truncate at a fixed character limit, say that you did, and name the filter or the page that gets the rest.
- With a typed return, the structured result already carries the fields, so a text block can stay short.
