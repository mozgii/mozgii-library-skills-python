---
title: Batches, files, caching and offline tests with the Anthropic Python SDK
author: MOZGII, Inc.
lang: en
---

# Batches, files, caching and offline tests with the Anthropic Python SDK

How to send many requests at once with Message Batches, upload documents once with the Files API, find out why the prompt cache missed, run requests concurrently with the async client, and test all of it without network access. Examples were run against `anthropic` 2.3.0 with `httpx2.MockTransport`; nothing here needs an API key to test.

## Contents

This reference has 8 sections.

1. Message Batches
2. The Files API
3. Prompt caching in practice
4. Token budgets
5. Concurrent requests with AsyncAnthropic
6. A fake API for tests
7. Testing tool loops, streams and errors
8. Gotchas

## 1. Message Batches

The Message Batches API takes many Messages requests in one call and processes them in the background; a batch can take up to 24 hours, and results come back as JSON lines keyed by your `custom_id`. Use it for evaluations, bulk summaries and back-fills where nobody waits for the answer.

```python
import time

from anthropic import Anthropic

client = Anthropic()
batch = client.messages.batches.create(requests=[
    {
        "custom_id": f"doc-{doc.id}",
        "params": {"model": "claude-haiku-5-5", "max_tokens": 256,
                   "messages": [{"role": "user", "content": f"Summarise:\n{doc.text}"}]},
    }
    for doc in documents
])

while (batch := client.messages.batches.retrieve(batch.id)).processing_status != "ended":
    time.sleep(60)

for item in client.messages.batches.results(batch.id):        # streamed JSONL, one item per request
    if item.result.type == "succeeded":                       # narrows the union, so .message is typed
        save(item.custom_id, item.result.message)
    else:                                                     # "errored", "expired" or "canceled"
        retry_later(item.custom_id, item.result.type)
```

- `params` takes the same fields as `messages.create` without streaming.
- `processing_status` is `in_progress`, `canceling` or `ended`; `request_counts` has `processing`, `succeeded`, `errored`, `canceled` and `expired`.
- Results are not in request order. Match them by `custom_id`, which must be unique in the batch.
- `client.messages.batches.cancel(batch_id)` stops a batch; `list()` and `delete()` manage old ones.
- Store the batch id before polling, so a restarted process can pick the batch up again.

## 2. The Files API

The Files API (`client.files`) uploads a document or image once and lets later requests refer to it by `file_id`, instead of sending the bytes each time. In SDK 2.3 it is a regular API with no beta header.

```python
from pathlib import Path

meta = client.files.upload(file=Path("report.pdf"), expires_in_seconds=86_400)   # one day
reply = client.messages.create(
    model="claude-opus-5-5", max_tokens=1024,
    messages=[{"role": "user", "content": [
        {"type": "document", "source": {"type": "file", "file_id": meta.id}},
        {"type": "text", "text": "List the three main risks in this report."},
    ]}],
)
```

- `file` accepts a `Path`, bytes, an open binary file, or a `(filename, bytes, mime_type)` tuple. Only the last path component of the filename is kept.
- `expires_in_seconds` must be between 3,600 (one hour) and 7,776,000 (ninety days); after that the bytes are gone.
- `client.files.list()`, `retrieve_metadata(file_id)`, `download(file_id)` and `delete(file_id)` manage uploads. `FileMetadata` has `id`, `filename`, `mime_type`, `size_bytes`, `created_at`, `expires_at` and `downloadable`.
- Delete files you no longer need; a file id in a log is enough to reach the content with the same key.

## 3. Prompt caching in practice

A `cache_control` marker caches everything up to and including the marked block. Tool definitions, system text blocks, message content blocks and tool results all accept `cache_control`, and a top-level `cache_control` on `create` marks the last cacheable block for you.

```python
reply = client.messages.create(
    model="claude-opus-5-5", max_tokens=1024,
    tools=[{**SEARCH_TOOL, "cache_control": {"type": "ephemeral"}}],          # tools are stable
    system=[{"type": "text", "text": STYLE_GUIDE, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
    messages=messages,
    diagnostics={"previous_message_id": previous_reply_id},                   # None on the first turn
)
usage = reply.usage
print(usage.cache_creation_input_tokens, usage.cache_read_input_tokens, usage.input_tokens)
if reply.diagnostics and reply.diagnostics.cache_miss_reason:
    log.info("cache miss: %s", reply.diagnostics.cache_miss_reason.type)
```

- `cache_read_input_tokens` above zero means the prefix came from the cache; `cache_creation_input_tokens` counts what was written. `usage.cache_creation` splits writes into `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`.
- `diagnostics={"previous_message_id": ...}` asks the API to compare this request with the previous one. `cache_miss_reason.type` is `tools_changed`, `system_changed`, `messages_changed`, `model_changed`, `previous_message_not_found` or `unavailable`; it can be `None` while the comparison is still running.
- `max_tokens=0` fills the cache without generating a reply, which warms it before the first real request.
- A timestamp, a random id or a reordered tool list before the marker changes the prefix every time. Keep such values after the last marker.

## 4. Token budgets

`messages.count_tokens` returns the input size of a request (system, tools and messages) without running it, and `models.retrieve` or `models.list` give each model's limits.

```python
count = client.messages.count_tokens(model="claude-opus-5-5", system=SYSTEM, tools=TOOLS, messages=messages)
model = client.models.retrieve("claude-opus-5-5")
if count.input_tokens + 4_000 > (model.max_input_tokens or 0):
    messages = drop_oldest_turns(messages)
```

- `max_tokens` on a request limits the output only. Thinking tokens count toward it.
- Counting is an API call of its own; in a loop, count once and add an estimate for each new message rather than counting every turn.

## 5. Concurrent requests with AsyncAnthropic

`AsyncAnthropic` runs many requests at once on one event loop. A semaphore keeps the number in flight below what the rate limit allows.

```python
import asyncio

from anthropic import AsyncAnthropic


async def summarise_all(texts: list[str]) -> list[str]:
    limit = asyncio.Semaphore(8)
    async with AsyncAnthropic() as client:
        async def one(text: str) -> str:
            async with limit:
                reply = await client.messages.create(model="claude-haiku-5-5", max_tokens=300,
                                                     messages=[{"role": "user", "content": f"Summarise:\n{text}"}])
                return "".join(b.text for b in reply.content if b.type == "text")
        return await asyncio.gather(*(one(t) for t in texts))
```

- Create the async client inside the running loop and close it there (`async with`); do not share one across event loops or threads.
- `asyncio.gather` raises the first failure and leaves the others running; use a `TaskGroup` when one failure should cancel the rest (see the python-asyncio skill).
- The SDK's own retries still apply to each request; a `RateLimitError` after them means the concurrency is too high.

## 6. A fake API for tests

The SDK sends every request through an `httpx2` client, so a test passes `DefaultHttpxClient(transport=httpx2.MockTransport(handler))` and answers from a queue. This fake records each request body so tests can check what was sent.

```python
import json
from collections import deque
from typing import Any

import httpx2
from anthropic import Anthropic, DefaultHttpxClient


def message(content: list[dict[str, Any]], stop_reason: str = "end_turn") -> dict[str, Any]:
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 12, "output_tokens": 7}}


class FakeAPI:
    def __init__(self, *replies: tuple[int, dict[str, Any]]) -> None:
        self.replies = deque(replies)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content) if request.content else {})
        status, body = self.replies.popleft()
        return httpx2.Response(status, json=body)

    def client(self, **options: Any) -> Anthropic:
        transport = httpx2.MockTransport(self)
        return Anthropic(api_key="test-key", http_client=DefaultHttpxClient(transport=transport), **options)
```

- For `AsyncAnthropic`, pass `DefaultAsyncHttpxClient(transport=httpx2.MockTransport(async_handler))` with an `async def` handler.
- `respx` and other `httpx` mocks do not see these requests; the SDK has used `httpx2` since 1.0.
- Make the code under test take the client as a parameter, so tests pass `api.client()` and production passes the real one.

### Test the invoice example from the skill

These tests cover `extract_invoice` from the skill's worked example: a good reply, a reply cut short and a server error, with `max_retries=0` so a failure returns at once.

```python
import httpx2
import pytest
from anthropic import Anthropic, DefaultHttpxClient

from invoices import ExtractionFailed, extract_invoice


def client_answering(text: str, stop_reason: str = "end_turn", status: int = 200) -> Anthropic:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if status != 200:
            return httpx2.Response(status, json={"type": "error", "error": {"type": "api_error", "message": "boom"}})
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 9, "output_tokens": 9},
        })
    http = DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    return Anthropic(api_key="test-key", max_retries=0, http_client=http)


def test_returns_the_invoice() -> None:
    client = client_answering('{"number": "A-17", "total_cents": 4200}')
    assert extract_invoice(client, "A-17, 42.00 EUR").total_cents == 4200


def test_a_reply_cut_short_is_a_clean_failure() -> None:
    with pytest.raises(ExtractionFailed, match="schema"):
        extract_invoice(client_answering('{"number": "A-', "max_tokens"), "A-17")


def test_a_server_error_is_a_clean_failure() -> None:
    with pytest.raises(ExtractionFailed, match="500"):
        extract_invoice(client_answering("", status=500), "A-17")
```

## 7. Testing tool loops, streams and errors

With the fake, a tool loop test queues a `tool_use` reply and a final reply, then checks the `tool_result` the code sent back.

```python
def test_manual_tool_loop() -> None:
    api = FakeAPI(
        (200, message([{"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "Oslo"}}],
                      stop_reason="tool_use")),
        (200, message([{"type": "text", "text": "It is 18 C in Oslo."}])),
    )
    assert run_with_tools(api.client(), "Weather in Oslo?") == "It is 18 C in Oslo."
    sent = api.requests[1]["messages"][-1]["content"][0]
    assert sent == {"type": "tool_result", "tool_use_id": "toolu_1", "content": "18 C and cloudy in Oslo"}
```

A streaming test answers with server-sent events in the order the API sends them.

```python
def sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


STREAM = sse(
    {"type": "message_start", "message": {**message([]), "stop_reason": None}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello, "}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "world."}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 4}},
    {"type": "message_stop"},
)


def stream_handler(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, content=STREAM, headers={"content-type": "text/event-stream"})
```

Error tests return an error body with the status; set `max_retries=0` so the test does not sleep through the backoff, or keep the retries and assert the request count.

```python
import anthropic
import pytest


def test_rate_limit_after_retries() -> None:
    error = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
    api = FakeAPI((429, error), (429, error), (429, error))
    with pytest.raises(anthropic.RateLimitError) as info:
        api.client(max_retries=2).messages.create(model="claude-opus-5-5", max_tokens=10,
                                                  messages=[{"role": "user", "content": "hi"}])
    assert info.value.type == "rate_limit_error"
    assert len(api.requests) == 3                  # the first try and two retries
```

| Status in the fake | Exception raised |
|---|---|
| 400 | `BadRequestError` |
| 429 | `RateLimitError` |
| 529 | `OverloadedError` |
| 503 and other 5xx | `InternalServerError` |
| a handler that raises `httpx2.ConnectError` | `APIConnectionError` |

## 8. Gotchas

These are the batch, file and testing problems seen most.

1. **Batch results do not line up with the input list.** Results come back in any order. Key everything by `custom_id`.
2. **A test takes several seconds.** The client retried an error response with backoff. Use `max_retries=0` in tests that expect errors.
3. **`json.decoder.JSONDecodeError` in the fake on a GET request.** GET requests have no body. Guard with `if request.content`.
4. **A test hits the real API.** The client was built without the fake transport, or with a `respx` mock. Pass `http_client=DefaultHttpxClient(transport=httpx2.MockTransport(...))`.
5. **`cache_read_input_tokens` stays zero.** Something before the marker changes per request. Send `diagnostics` with the previous message id and read `cache_miss_reason`.
