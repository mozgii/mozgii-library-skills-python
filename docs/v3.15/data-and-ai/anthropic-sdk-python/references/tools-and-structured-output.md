---
title: Tool use and structured output with the Anthropic Python SDK
author: MOZGII, Inc.
lang: en
---

# Tool use and structured output with the Anthropic Python SDK

How to give Claude tools from Python with the `anthropic` SDK (2.x): tool definitions, `tool_choice`, a complete manual tool loop with errors and parallel calls, the beta tool runner with `@beta_tool` and `ToolError`, MCP server tools in the runner, and replies that match a JSON Schema. Every example was run offline against `anthropic` 2.3.0 with a mocked transport.

## Contents

This reference has 8 sections.

1. Tool definitions
2. Controlling tool use with tool_choice
3. A complete manual tool loop
4. Thinking blocks in a tool loop
5. The beta tool runner
6. MCP server tools in the runner
7. Structured output with messages.parse
8. Gotchas

## 1. Tool definitions

A client tool is a dict with `name`, `description` and `input_schema` (JSON Schema, draft 2020-12), passed in the `tools` list of `messages.create`. The description is what Claude reads to decide when to call it.

```python
WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Current weather for a city. Use when the user asks about weather or what to wear outside.",
    "input_schema": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name, for example Oslo"},
            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
        },
        "required": ["city"],
    },
}
```

Optional fields of a tool definition (`anthropic.types.ToolParam`) in 2.3:

| Field | Effect |
|---|---|
| `strict: True` | the API guarantees that tool names and inputs validate against the schema |
| `input_examples` | example inputs that show Claude the expected shape |
| `cache_control` | a prompt-cache breakpoint after this tool definition |
| `defer_loading` | the tool is not loaded into the initial prompt |
| `eager_input_streaming` | stream the tool input as it is generated |

- Generate `input_schema` from a pydantic model (`Model.model_json_schema()`) instead of writing it by hand when the input is more than two fields.
- Keep the tool list stable between requests: a changed list misses the prompt cache.

## 2. Controlling tool use with tool_choice

`tool_choice` decides whether Claude may, must or must not use tools.

| `tool_choice` | Meaning |
|---|---|
| `{"type": "auto"}` (default with tools) | Claude decides |
| `{"type": "any"}` | Claude must call one of the tools |
| `{"type": "tool", "name": "get_weather"}` | Claude must call this tool |
| `{"type": "none"}` | no tool calls, even though tools are defined |

`auto`, `any` and `tool` accept `"disable_parallel_tool_use": True` to get at most one tool call per turn, which keeps a loop simpler when tools depend on each other.

## 3. A complete manual tool loop

The manual tool loop gives full control: logging per call, approval before a destructive call, custom error text. It appends Claude's reply unchanged, runs every `tool_use` block of that reply, and sends all results in one user message.

```python
import anthropic

MODEL = "claude-opus-5-5"


def run_with_tools(client: anthropic.Anthropic, question: str, max_turns: int = 8) -> str:
    messages: list[anthropic.types.MessageParam] = [{"role": "user", "content": question}]
    for _ in range(max_turns):
        reply = client.messages.create(model=MODEL, max_tokens=1024, tools=[WEATHER_TOOL], messages=messages)
        messages.append({"role": "assistant", "content": reply.content})
        if reply.stop_reason != "tool_use":
            return "".join(b.text for b in reply.content if b.type == "text")
        results: list[anthropic.types.ToolResultBlockParam] = []
        for block in reply.content:
            if block.type == "tool_use":
                try:
                    output = get_weather(**block.input)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
                except Exception as exc:
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(exc),
                                    "is_error": True})
        messages.append({"role": "user", "content": results})
    raise RuntimeError("tool loop did not finish")
```

- One reply can hold several `tool_use` blocks (parallel calls). Answer every one, matched by `tool_use_id`, in the next user message.
- `is_error: True` tells Claude the call failed; a clear message ("No city named Osloo. Did you mean Oslo?") lets it fix its next call.
- `content` of a `tool_result` is a string or a list of blocks (text, images).
- Check `block.input` before acting on it. It follows your schema, but it is still model output; validate it with the same pydantic model that produced the schema.
- Ask the person before running anything destructive, and never pass `block.input` to a shell.

## 4. Thinking blocks in a tool loop

With thinking on, a reply that calls tools starts with `thinking` blocks carrying a `signature`. Appending `reply.content` unchanged, as the loop above does, sends them back exactly as received, which is what the next turn needs. Do not rebuild the assistant message from the text alone or edit thinking blocks.

```python
reply = client.messages.create(
    model=MODEL, max_tokens=16_000, thinking={"type": "adaptive"},
    tools=[WEATHER_TOOL], messages=messages,
)
for block in reply.content:
    if block.type == "thinking":
        log.debug("thinking: %d chars", len(block.thinking))
messages.append({"role": "assistant", "content": reply.content})     # thinking blocks included
```

With `"display": "omitted"` in the thinking config the thinking text is redacted but the signature is still returned for the next turn.

## 5. The beta tool runner

The tool runner (`client.beta.messages.tool_runner`) runs the loop of section 3 for you: it calls the API, runs the tools Claude asks for, sends the results, and stops when Claude stops asking or after `max_iterations`. `@beta_tool` turns a typed function into a tool: the docstring's first line becomes the description and its `Args:` section the per-argument descriptions.

```python
from anthropic import Anthropic, beta_tool
from anthropic.lib.tools import ToolError


@beta_tool
def lookup_order(order_id: str) -> str:
    """Look up an order's delivery status.

    Args:
        order_id: The order number, like "A-17".
    """
    order = orders.get(order_id)
    if order is None:
        raise ToolError(f"No order {order_id}. Ask the customer to check the number.")
    return order.status


client = Anthropic()
runner = client.beta.messages.tool_runner(
    model="claude-opus-5-5", max_tokens=1024, tools=[lookup_order], max_iterations=5,
    messages=[{"role": "user", "content": "Where is order A-17?"}],
)
for message in runner:                                # one Message per API call
    print(message.stop_reason, [block.type for block in message.content])
final = runner.until_done()                           # the last message
```

| In the tool | Sent to Claude |
|---|---|
| returns a value | `tool_result` with the value |
| raises `ToolError("...")` | `tool_result` with your text, `is_error: True` |
| raises any other exception | `tool_result` with `repr(exc)` (for example `RuntimeError('db down')`), `is_error: True`; the SDK logs the traceback |
| gets arguments that do not fit the signature | an error result; the SDK logs `Invalid arguments for function ...` |

- Because a crash's `repr` is sent to Claude, keep secrets out of exception messages, or catch and raise `ToolError` with safe text.
- `@beta_async_tool` and `client.beta.messages.tool_runner` on `AsyncAnthropic` run async tools; `await runner.until_done()`.
- `stream=True` on the runner yields a message stream per turn (iterate it like `messages.stream`) instead of a finished message.
- `runner.append_messages(...)`, `add_tools(...)` and `remove_tools(...)` change the run between turns.
- The runner is a beta API: it lives under `client.beta` and its types start with `Beta`. Pin the SDK version and read the changelog before upgrading.

## 6. MCP server tools in the runner

`anthropic.lib.tools.mcp` converts the tools of an MCP server into runner tools, so Claude can call any MCP server through the tool runner. It needs the `mcp` package (`uv add "anthropic[mcp]"`). The example connects to an MCP server with the MCP SDK's `Client` and passes its session.

```python
import asyncio

from anthropic import AsyncAnthropic
from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import Client, StdioServerParameters


async def ask(question: str) -> str:
    server = StdioServerParameters(command="uv", args=["run", "--directory", "/abs/path/notes-server", "notes-server"])
    async with AsyncAnthropic() as claude, Client(server) as mcp_client:
        listed = await mcp_client.list_tools()
        tools = [async_mcp_tool(tool, mcp_client.session) for tool in listed.tools]
        runner = claude.beta.messages.tool_runner(
            model="claude-opus-5-5", max_tokens=1024, tools=tools,
            messages=[{"role": "user", "content": question}],
        )
        final = await runner.until_done()
    return "".join(block.text for block in final.content if block.type == "text")
```

- `mcp_tool` is the sync version for the sync runner; `async_mcp_tool` fits the async one.
- The MCP tool's description and `input_schema` become the Claude tool definition unchanged, so the server's descriptions are what Claude reads.
- MCP tool annotations (`destructiveHint` and the rest) are not enforced by the runner. Filter the list or wrap destructive tools with your own confirmation before passing them.

## 7. Structured output with messages.parse

`client.messages.parse` sends the JSON Schema of `output_format` as `output_config.format` and validates the reply into that type. Any type `pydantic.TypeAdapter` accepts works: a model, a `TypedDict`, a `list[Model]`.

```python
from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: str
    cents: int = Field(ge=0)


class Invoice(BaseModel):
    number: str
    total_cents: int
    items: list[LineItem]


import pydantic

try:
    reply = client.messages.parse(
        model="claude-opus-5-5", max_tokens=2048, output_format=Invoice,
        system="Extract invoice data. Amounts in cents.",
        messages=[{"role": "user", "content": invoice_text}],
    )
except pydantic.ValidationError as exc:              # truncated or refused reply: not valid JSON for Invoice
    raise ValueError("Claude did not return a valid invoice") from exc
if reply.parsed_output is None:
    raise ValueError(f"no structured reply (stop_reason={reply.stop_reason})")
invoice: Invoice = reply.parsed_output
```

- When the reply text is not valid JSON for the type (cut off by `max_tokens`, or a `refusal` in plain words), `parse` raises `pydantic.ValidationError` (tested with 2.3); `parsed_output` is `None` when the reply has no text block at all.
- The SDK adjusts the generated schema to what the API accepts before sending it, so read the request body in a test if a constraint matters.
- `output_format` must be a type. A ready-made schema dict goes in `output_config={"format": {"type": "json_schema", "schema": schema}}` on `create`, and you parse the text yourself.
- `output_config` also carries `effort`; `parse` merges the two keys.
- For a structured result that comes from a tool call rather than the final reply, give the tool `strict: True` instead.

## 8. Gotchas

These are the tool-use and structured-output problems seen most.

1. **The loop never ends.** No turn limit, and a tool keeps failing in a way Claude retries. Cap the turns (`max_iterations` on the runner) and make error texts say what to do differently.
2. **`BadRequestError` after a tool call.** A `tool_use` id got no `tool_result`, or the assistant message was rebuilt without its `tool_use` or thinking blocks. Append `reply.content` unchanged and answer every id.
3. **`ValueError: Invalid arguments for function ...` in the log.** Claude's input did not fit the decorated function's signature. Tighten the docstring's `Args:` descriptions and use types the schema can express.
4. **`TypeError` saying `output_format` must be a type.** A schema dict was passed to `parse`. Pass the pydantic class, or put the dict in `output_config`.
5. **`pydantic.ValidationError` from `messages.parse`.** The reply was cut off or refused. Raise `max_tokens`, check `stop_reason`, and catch the error.
6. **Secrets show up in a conversation transcript.** A tool raised an exception whose text held a path, a key or a query, and the runner sent its `repr` to Claude. Raise `ToolError` with safe text.
