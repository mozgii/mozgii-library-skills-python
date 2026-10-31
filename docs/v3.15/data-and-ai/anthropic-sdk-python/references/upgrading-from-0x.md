---
title: Upgrading the Anthropic Python SDK from 0.x
author: MOZGII, Inc.
lang: en
---

# Upgrading the Anthropic Python SDK from 0.x

The move from the 0.x line to 1.x and later changes the HTTP library underneath, the shape of raw responses and which request parameters the SDK accepts. Most code that only calls `client.messages.create` needs a version bump and nothing else, so the work is finding the call sites that reach past it. This reference lists each break, how to find it and what to write instead.

## Contents

This reference has 6 sections.

1. Find what the upgrade touches
2. Python and dependency pins
3. httpx2 at the SDK boundary
4. Raw responses
5. Removed parameters and arguments
6. Bedrock and verification

## 1. Find what the upgrade touches

Search before editing, because each pattern below maps to one section. The SDK releases fast, so read the installed version first and move to the current major in one step.

```bash
uv run python -c "import anthropic; print(anthropic.__version__)"
grep -rnE 'import httpx|http_client=|with_raw_response|temperature=|top_p=|top_k=|output_format=|compaction_control|HUMAN_PROMPT|AI_PROMPT|anthropic\.Stream|AsyncStream|Transport|ProxiesTypes' src tests
```

## 2. Python and dependency pins

The SDK needs Python 3.10 or newer, so a project still testing 3.9 has to decide first whether to drop it. Pydantic v1 and v2 both stay supported.

- Rewrite the `anthropic` requirement in the file's own style, for example `anthropic>=2,<3`.
- Remove `httpx-aiohttp` if it was pinned only for `DefaultAioHttpClient`: the SDK now bundles that transport, and its `aiohttp` extra adds just the `aiohttp` package.
- If your code imports `httpx2` directly, declare it as a dependency. It arrives through `anthropic`, but a direct import should say so.

## 3. httpx2 at the SDK boundary

The SDK now runs on `httpx2`. Only objects that cross into the SDK have to change; a project may keep using `httpx` for its own calls.

| Where | Before | After |
|---|---|---|
| custom client | `Anthropic(http_client=httpx.Client(...))` | `Anthropic(http_client=httpx2.Client(...))` |
| test transport | `httpx.MockTransport(handler)` | `httpx2.MockTransport(handler)` |
| respx mocks | `respx` patches `httpx` | use `httpx2.MockTransport`; respx never sees the call |
| type names | `anthropic.Transport`, `anthropic.ProxiesTypes` | `httpx2.BaseTransport`, `httpx2.Proxy`; async: `httpx2.AsyncBaseTransport` |
| raw payload on `client.post` and friends | `body=b"..."` | `content=b"..."`; `body=` is always JSON |

## 4. Raw responses

`.with_raw_response` now returns `APIResponse` or `AsyncAPIResponse`, the same classes `.with_streaming_response` uses.

| Before | Sync client | Async client |
|---|---|---|
| `r.parse()` | `r.parse()` | `await r.parse()` |
| `r.text` | `r.text()` | `await r.text()` |
| `r.content` | `r.read()` | `await r.read()` |
| `r.json()` | `r.json()` | `await r.json()` |

## 5. Removed parameters and arguments

These raise `TypeError` or change behaviour at the call site, so the type checker finds most of them once the SDK is upgraded.

| Removed | Write instead |
|---|---|
| `temperature`, `top_p`, `top_k` on `messages.create`, `stream` and `parse` | delete them; only when the pinned model accepts them and the code depends on it, `extra_body={"temperature": 0.2}` |
| `output_format={...}` as a raw dict | `output_config={"format": {"type": "json_schema", "schema": ...}}`, or better `messages.parse(..., output_format=Order)` with a pydantic model |
| `messages.parse(..., stream=True)` | `with client.messages.stream(..., output_format=Order) as s: s.get_final_message().parsed_output` |
| `tool_runner(compaction_control={...})` | `betas=["compact-2026-01-12"]` and `context_management={"edits": [{"type": "compact_20260112", "trigger": {"type": "input_tokens", "value": 100_000}}]}` |
| `anthropic.HUMAN_PROMPT`, `AI_PROMPT` and text completions | the Messages API; this one is a rewrite, not a rename |
| `BetaBase64PDFBlockParam` | `BetaRequestDocumentBlockParam` |
| `isinstance(x, anthropic.Stream)` on `client.messages.stream()` objects | now `False`, with no warning; remove the check |

Custom headers now merge without regard to case. When `default_headers`, `extra_headers` or `ANTHROPIC_CUSTOM_HEADERS` name one header twice, the last entry wins, even over headers the SDK sets itself.

## 6. Bedrock and verification

`AnthropicBedrock()` used to warn and fall back to `us-east-1`; it now raises `ValueError` when no region is configured. Set `aws_region=`, `AWS_REGION` or `AWS_DEFAULT_REGION`. Streaming from Bedrock also skips event types the SDK does not know, so code that read the `amazon-bedrock-invocationMetrics` frame loses it.

Prove the upgrade with the type checker and an offline test, then one real call.

```bash
uv run pyright src                       # finds removed parameters and renamed types
uv run pytest -q                         # with httpx2.MockTransport, never the network
uv pip list | grep -E '^(anthropic|httpx2) '
```
