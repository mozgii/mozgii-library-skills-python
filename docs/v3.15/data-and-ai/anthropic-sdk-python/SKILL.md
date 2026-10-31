---
name: anthropic-sdk-python
description: Calls Claude from Python with the official Anthropic SDK (package anthropic, version 2) - client setup with ANTHROPIC_API_KEY, listing models, the Messages API with system prompts, stop reasons and usage, streaming with messages.stream, the tool use loop and the beta tool runner, structured outputs with messages.parse, prompt caching with cache_control, token counting, adaptive and extended thinking, retries, timeouts and error classes (APIStatusError, RateLimitError, OverloadedError), the async client, and offline tests with httpx2.MockTransport. Use when writing Python code that calls Claude or the Anthropic API, adding tool use or function calling, streaming a reply, getting JSON that matches a schema, cutting cost with prompt caching, handling rate limits and overloaded errors, or testing code that calls Claude.
metadata:
  tags:
  - python
  - anthropic
  - claude
  - llm
  - messages-api
  - tool-use
  - streaming
  - prompt-caching
---

# Calling Claude with the Anthropic Python SDK

The `anthropic` package is the official Python client for the Claude API: typed request parameters, pydantic response models, streaming helpers, retries and a tool runner. It follows the API closely and changes often, so every parameter here was checked against `anthropic` 2.3.0; check yours against the installed types, not old blog posts. Tools and structured output in depth are in [tools-and-structured-output.md](references/tools-and-structured-output.md); batches, files, caching and tests in [batches-files-and-testing.md](references/batches-files-and-testing.md).

## Step 0: read the installed versions

Read the Python and SDK versions first, and look up a parameter in the installed types before using it.

```bash
uv run python -c "import sys, anthropic; print(sys.version_info[:2], anthropic.__version__)"
uv run python -c "from anthropic.types.message_create_params import MessageCreateParamsBase as P; print(sorted(P.__annotations__))"
```

| SDK version | Change that matters |
|---|---|
| 1.0 | `httpx` replaced by `httpx2`: custom clients, transports and mocks must be `httpx2` objects; `.with_raw_response` and several request parameters changed, see [upgrading-from-0x.md](references/upgrading-from-0x.md) |
| 2.0 to 2.3 (October 2026) | API version `2026-10-21`; Python 3.10 or later |

## Choose the API call

Pick the call from the shape of the work; every option here is covered below.

| The work | Call | Why the others lose here |
|---|---|---|
| one reply under about 21,000 tokens | `client.messages.create` | streaming adds code you do not need |
| a long reply, or text shown as it arrives | `client.messages.stream` | `create` raises `ValueError` for long outputs |
| a typed result | `client.messages.parse` | parsing free text breaks on the first change of wording |
| Claude calling your functions | `client.beta.messages.tool_runner`, or your own loop for full control | one `create` stops at the first `tool_use` |
| many live calls at once | `AsyncAnthropic` with a semaphore | threads hold a blocked worker per request |
| many requests with no hurry | Message Batches | live calls cost more and hit rate limits |

## Client setup and credentials

One `Anthropic` client per process, created once and reused, holds the connection pool. Without arguments it reads `ANTHROPIC_API_KEY` from the environment.

```python
from anthropic import Anthropic

client = Anthropic()                                   # ANTHROPIC_API_KEY from the environment
client = Anthropic(max_retries=4, timeout=120.0)       # retries and a per-request timeout in seconds
```

- Credentials resolve in order: an explicit argument (`api_key=`, `auth_token=`, `profile=`, ...), then `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_PROFILE`, workload identity variables, and the active profile on disk.
- `ANTHROPIC_BASE_URL` points the client at a proxy or gateway. Never put the key in code or logs (the python-security skill covers secrets).
- `client.with_options(timeout=10, max_retries=0)` returns a copy for one call site.
- Close the client in short scripts and tests (`with Anthropic() as client:`).

## Choose a model

Model IDs change with each release, so read them from the API. In October 2026 the current IDs are `claude-opus-5-5` (most capable), `claude-sonnet-5-5`, `claude-haiku-5-5` (fastest, cheapest) and `claude-fable-5-1` (Fable 5.1). Examples here use `claude-opus-5-5`.

```python
for model in client.models.list():                     # pages are fetched as you iterate
    print(model.id, model.display_name, model.lifecycle, model.max_input_tokens)
```

- `client.models.retrieve(model_id)` adds `capabilities`, a plain dict read with brackets: `m.capabilities["structured_outputs"]["supported"]`. `id`, `display_name`, `max_input_tokens` and `max_tokens` are typed attributes.
- `lifecycle` is `active`, `deprecated` or `retired`; `retires_at` gives the date. The SDK warns (`DeprecationWarning`) for models on its deprecated list.
- Keep the model ID in configuration so an upgrade is one change.

## Send a message

`client.messages.create` takes the model, `max_tokens` and the conversation; `system` holds instructions that apply to the whole conversation.

```python
reply = client.messages.create(
    model="claude-opus-5-5",
    max_tokens=1024,
    system="You answer questions about the user's invoices. Answer in one short paragraph.",
    messages=[{"role": "user", "content": "Which invoices are overdue?"}],
)
text = "".join(block.text for block in reply.content if block.type == "text")
print(text, reply.stop_reason, reply.usage.input_tokens, reply.usage.output_tokens)
```

- `reply.content` is a list of blocks (`text`, `tool_use`, `thinking`, ...); never assume `content[0]` is text.
- The API is stateless: send the whole conversation each time, alternating `user` and `assistant` turns. A message's `content` is a string or a list of blocks (text, images, documents, tool results).
- `reply._request_id` holds the `request-id` header; log it with errors.

## Stop reasons

`reply.stop_reason` says why the model stopped, and code should branch on it rather than assume the answer is complete.

| `stop_reason` | Meaning | Do |
|---|---|---|
| `end_turn` | finished | use the reply |
| `max_tokens` | hit your `max_tokens` or the model's maximum | raise it, or continue the conversation |
| `stop_sequence` | matched one of `stop_sequences` | `reply.stop_sequence` says which |
| `tool_use` | wants a tool result | run the tools and send results |
| `pause_turn` | the API paused a long-running turn | send the reply back as-is to let it continue |
| `refusal` | streaming classifiers stopped a possible policy violation | show a message; do not retry the same input |
| `model_context_window_exceeded` | the context window is full | shorten the conversation, or use compaction |

A refusal carries the reason in `stop_details`, which is `None` for other stop reasons.

```python
import logging

log = logging.getLogger("claude")

if reply.stop_reason == "refusal" and reply.stop_details:
    log.warning("refused: %s", reply.stop_details.category)   # cyber, bio, frontier_llm, reasoning_extraction, general_harms or None
    print(reply.stop_details.explanation or "This request can't be completed.")
```

Without a fallback, a refused request simply stops. On Claude Fable 5.1 the API can re-run it on another model: `client.beta.messages.create(..., betas=["server-side-fallback-2026-06-01"], fallbacks=[{"model": "claude-opus-4-8"}])`. If the last response still stops with `refusal`, every model in the chain declined. Keep it off batch jobs, which refuse the parameter, and off Amazon Bedrock, Vertex AI and Microsoft Foundry, which do not offer it.

## Keep long conversations going with compaction

Compaction is a beta server feature that summarises older turns when a conversation nears the context window, so a long-running loop keeps going without your own trimming code.

```python
history = [{"role": "user", "content": "Summarise yesterday's support tickets."}]
reply = client.beta.messages.create(
    betas=["compact-2026-01-12"],
    model="claude-opus-5-5",
    max_tokens=16000,
    messages=history,
    context_management={"edits": [{"type": "compact_20260112"}]},
)
history.append({"role": "assistant", "content": reply.content})   # all of it: compaction blocks must go back
```

Support varies by model: check `m.capabilities["context_management"]["compact_20260112"]["supported"]`. Appending only the text of a reply drops the compaction block and loses the benefit. For an instruction that arrives mid-conversation, such as a mode switch, some models accept `{"role": "system", ...}` inside `messages`, which keeps the cached top-level `system` prompt intact.

## Stream a reply

Streaming shows text as it is generated and is required for long outputs: with the default timeout the SDK raises `ValueError: Streaming is required for operations that may take longer than 10 minutes` when `max_tokens` is above about 21,000 on `create`.

```python
with client.messages.stream(
    model="claude-opus-5-5",
    max_tokens=64_000,
    messages=[{"role": "user", "content": "Write the release notes."}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
    final = stream.get_final_message()                 # the complete Message, with usage
```

- `stream.text_stream` yields text deltas only; iterate `stream` itself for every event (`text`, `thinking`, `input_json`, `message_stop`, ...). `stream.get_final_text()` joins the text blocks.
- `client.messages.create(..., stream=True)` returns the raw server-sent events without accumulation.
- Leaving the `with` block early (a `break`) closes the HTTP response; you stop reading the rest.

## Use tools

Tool use lets Claude ask your code to run a function: you describe tools with a JSON Schema, Claude answers with `tool_use` blocks and `stop_reason == "tool_use"`, and you send `tool_result` blocks back until it stops asking.

```python
import json

import anthropic

client = anthropic.Anthropic()
WEATHER_TOOL: anthropic.types.ToolParam = {
    "name": "get_weather",
    "description": "Current weather for a city. Use for any question about today's weather.",
    "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}
TOOLS = {"get_weather": lambda city: f"18 C in {city}"}   # an allow-list: the model picks by name


def run_tool(name: str, tool_input: dict[str, object]) -> str:
    return json.dumps(TOOLS[name](**tool_input)) if name in TOOLS else f"unknown tool {name}"


messages: list[anthropic.types.MessageParam] = [{"role": "user", "content": "Weather in Oslo?"}]
for _turn in range(10):                                # a hard cap on tool rounds
    reply = client.messages.create(model="claude-opus-5-5", max_tokens=1024, tools=[WEATHER_TOOL], messages=messages)
    messages.append({"role": "assistant", "content": reply.content})
    if reply.stop_reason != "tool_use":
        break
    results: list[anthropic.types.ToolResultBlockParam] = [
        {"type": "tool_result", "tool_use_id": block.id, "content": run_tool(block.name, block.input)}
        for block in reply.content if block.type == "tool_use"
    ]
    messages.append({"role": "user", "content": results})
```

The beta tool runner runs this loop for you; `@beta_tool` builds each schema from the signature and docstring: `client.beta.messages.tool_runner(model=..., max_tokens=..., tools=[get_weather], messages=...)` returns a runner, and `runner.until_done()` gives the final message.

Cap the runner with `max_iterations=`, and return tool failures as `tool_result` with `is_error: True`. Details are in [tools-and-structured-output.md](references/tools-and-structured-output.md).

## Get structured output

`client.messages.parse` takes a pydantic model (or any type `pydantic.TypeAdapter` accepts) as `output_format`, sends its JSON Schema, and returns the reply with `parsed_output` already validated.

The worked example below shows the call with its failure paths.

A cut-off or refused reply raises `pydantic.ValidationError`, so check `stop_reason`. Without the helper, pass `output_config={"format": {"type": "json_schema", "schema": {...}}}` to `create`.

## Cache long prompts

Prompt caching stores a request prefix (tools, system, earlier messages) so later requests with the same prefix read it cheaper and faster. Mark the end of the reusable part with `cache_control`.

```python
PRODUCT_MANUAL = "..."                                  # thousands of words that never change between calls
reply = client.messages.create(
    model="claude-opus-5-5", max_tokens=1024,
    system=[{"type": "text", "text": PRODUCT_MANUAL, "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": "How do I reset the device?"}],
)
print(reply.usage.cache_creation_input_tokens, reply.usage.cache_read_input_tokens)
```

- `"ttl": "1h"` keeps an entry for an hour instead of the default 5 minutes.
- A top-level `cache_control={"type": "ephemeral"}` on `create` marks the last cacheable block for you.
- Anything that changes before the marker (a timestamp, a reordered tool list) misses the cache; put stable content first.

## Count tokens and control thinking

`client.messages.count_tokens` returns the input token count for a request without running it, which is how to check a prompt against a context window or a budget.

```python
count = client.messages.count_tokens(model="claude-opus-5-5", system="Answer briefly.", messages=messages)
print(count.input_tokens)
```

The `thinking` parameter controls Claude's reasoning before it answers, and `output_config={"effort": ...}` how much work it puts in.

| Setting | Effect |
|---|---|
| `thinking={"type": "adaptive"}` | Claude decides when and how much to think |
| `thinking={"type": "enabled", "budget_tokens": 8000}` | manual budget, at least 1,024 and below `max_tokens` |
| `thinking={"type": "disabled"}` | no thinking |
| `"display": "omitted"` in either config | thinking text redacted, signature kept |
| `output_config={"effort": "high"}` | `low`, `medium`, `high`, `xhigh` or `max` |

Thinking arrives as `thinking` blocks before the text and counts against `max_tokens`. Accepted types and the default when `thinking` is omitted depend on the model.

## Errors, retries and timeouts

The SDK retries connection errors, timeouts, 408, 409, 429 and 5xx responses twice by default with exponential backoff (0.5 s growing to at most 8 s), honouring `retry-after` and `retry-after-ms`; then it raises.

| Exception | When |
|---|---|
| `APIConnectionError`, `APITimeoutError` | no response: network failure or timeout |
| `BadRequestError` (400), `RequestTooLargeError` (413) | invalid or too large request: fix the code |
| `AuthenticationError` (401), `PermissionDeniedError` (403), `NotFoundError` (404) | key or model wrong or not allowed |
| `RateLimitError` (429) | rate limit after the retries |
| `OverloadedError` (529) | API overloaded after the retries |
| `InternalServerError` (other 5xx) | server error after the retries |
| `APIStatusError` | base of all status errors: `.status_code`, `.type`, `.request_id` |

Catch the specific class first:

```python
import logging

import anthropic

log = logging.getLogger("claude")

try:
    reply = client.messages.create(model="claude-opus-5-5", max_tokens=512, messages=messages)
except anthropic.RateLimitError as exc:
    log.warning("rate limited, request %s", exc.request_id)
    raise
except anthropic.APIStatusError as exc:
    log.error("Claude API %s %s (request %s)", exc.status_code, exc.type, exc.request_id)
    raise
```

The default timeout is 10 minutes (connect 5 s); set a shorter one for interactive calls. General retry design is the python-error-handling skill.

## Async client

For many concurrent calls, `uv add "anthropic[aiohttp]"` and `AsyncAnthropic(http_client=DefaultAioHttpClient())` (from `anthropic`) replace the default `httpx2` transport with aiohttp. It ships inside the SDK since 1.0, so a separate `httpx-aiohttp` pin can go.

`AsyncAnthropic` has the same methods as coroutines (`await client.messages.create(...)`, `async with client.messages.stream(...)`); use it in asyncio programs, one instance per event loop, and bound concurrency with a semaphore so a burst does not hit the rate limit. A worked example with tests is in [batches-files-and-testing.md](references/batches-files-and-testing.md). For many requests that need no immediate answer, the Message Batches API costs less.

## Test without the network

Tests never call the real API: give the client `DefaultHttpxClient(transport=httpx2.MockTransport(handler))`, where `handler` answers with canned JSON. The full fake is in [batches-files-and-testing.md](references/batches-files-and-testing.md).

`respx` patches `httpx`, not `httpx2`, so SDK 1.x and 2.x requests slip past it to the real network. More fakes are in [batches-files-and-testing.md](references/batches-files-and-testing.md).

## Worked example: extract an invoice, with every failure handled

This function returns a typed result or raises one exception the caller can handle; the test runs offline. Each branch below was run against `anthropic` 2.3.0 with a mock transport.

```python
import logging

import anthropic
from pydantic import BaseModel, ValidationError

log = logging.getLogger("invoices")
MODEL = "claude-opus-5-5"                              # in real code: from settings


class Invoice(BaseModel):
    number: str
    total_cents: int


class ExtractionFailed(Exception):
    pass


def extract_invoice(client: anthropic.Anthropic, text: str) -> Invoice:
    try:
        reply = client.messages.parse(
            model=MODEL, max_tokens=1024, output_format=Invoice,
            system="Extract the invoice. Amounts are whole cents.",
            messages=[{"role": "user", "content": text}],
        )
    except ValidationError as exc:                     # cut off, refused or malformed
        raise ExtractionFailed("the reply did not match the schema") from exc
    except anthropic.APIStatusError as exc:
        log.error("Claude API %s (request %s)", exc.status_code, exc.request_id)
        raise ExtractionFailed(f"the API returned {exc.status_code}") from exc
    except anthropic.APIConnectionError as exc:
        raise ExtractionFailed("the API could not be reached") from exc
    if reply.parsed_output is None:
        raise ExtractionFailed(f"no output, stop reason {reply.stop_reason}")
    return reply.parsed_output
```

The offline test of every branch is in [batches-files-and-testing.md](references/batches-files-and-testing.md).

## Verify

Prove the code without spending tokens first, then make one live call to confirm the key and the model.

```bash
uv run python -c "import anthropic; print(anthropic.__version__)"   # matches uv.lock
env -u ANTHROPIC_API_KEY uv run pytest -q                           # offline tests pass with no key set
grep -rnE "content\[0\]\.text|sk-ant-" src tests                     # expect nothing
uv run python -c "from anthropic import Anthropic; print(Anthropic().models.retrieve('claude-opus-5-5').lifecycle)"   # expect active
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `reply.content[0].text` | join the blocks where `block.type == "text"` |
| `while True` around a tool loop | `for _turn in range(10)`, or `max_iterations=` on the runner |
| `getattr(tools, block.name)` or a shell built from `block.input` | a dict of allowed tools, with each input validated before it runs |
| a new `Anthropic()` for every call | one client per process |
| the same request again after `refusal` | show a message; a repeat gets the same answer |
| `respx` in tests | `httpx2.MockTransport` on the client |
| a model ID repeated through the code | one setting, checked against `client.models.list()` |

## Checklist

Code that calls Claude is ready when every item holds.

- [ ] One client per process; the key comes from the environment or a secret store.
- [ ] The model ID lives in configuration and was checked with `client.models.list()`.
- [ ] Code branches on `stop_reason` and reads blocks by type, not by index.
- [ ] Long outputs stream; interactive calls have a short timeout.
- [ ] Tool loops have a turn limit and return tool failures as `is_error` results.
- [ ] Stable prompt prefixes carry `cache_control`, and `usage` shows cache reads.
- [ ] Errors are logged with `request_id`.
- [ ] Tests run offline with `httpx2.MockTransport`.

## Gotchas

These are the problems seen most often with the SDK.

1. **`ValueError: Streaming is required for operations that may take longer than 10 minutes`.** `max_tokens` is high on a non-streaming call. Use `messages.stream`, or pass an explicit `timeout`.
2. **`AttributeError: 'ToolUseBlock' object has no attribute 'text'`.** Code read `reply.content[0].text`. Filter blocks by `block.type == "text"`.
3. **Tests hit the real API although respx is active.** The SDK uses `httpx2`. Use `httpx2.MockTransport`.
4. **The cache never hits.** Something before the `cache_control` marker changes per request. Move variable content after the marker, check `cache_read_input_tokens`, and ask the API why with `diagnostics` (see the batches-files-and-testing reference).
5. **`TypeError: Messages.create() got an unexpected keyword argument 'temperature'`.** The 2.x request types differ from older examples; `temperature`, `top_p` and `top_k` are not among them. Pass them through `extra_body={"temperature": 0.2}` only when the model accepts them, and check the parameter in `MessageCreateParamsBase`.
6. **`BadRequestError` about tool results.** A `tool_use` got no matching `tool_result`, or the result was not in the user message right after it. Send one result per `tool_use` id in the next user message.
7. **`UserWarning` about `thinking.type=enabled`.** For some models (`claude-opus-4-6`) the SDK recommends `{"type": "adaptive"}`.

## References

The reference files hold the longer recipes for this skill.

- [tools-and-structured-output.md](references/tools-and-structured-output.md): the tool loop in full, `strict`, `tool_choice`, `is_error` results, the tool runner and `ToolError`, MCP tools, structured output, thinking with tools
- [upgrading-from-0x.md](references/upgrading-from-0x.md): every break from the 0.x SDK, how to find it and what to write instead
- [batches-files-and-testing.md](references/batches-files-and-testing.md): Message Batches, the Files API, caching diagnostics, the async client, and offline tests for replies, tool loops, streams and errors

## See also

- `mcp-python-servers`: exposing your tools to any MCP host instead of a hand-written loop
- `python-asyncio`: bounded concurrency around `AsyncAnthropic`
- `python-error-handling`: retry design above the SDK's own retries
- `python-security`: keeping the API key out of code and logs
- `python-http-clients`: general HTTP client habits, with `httpx`
