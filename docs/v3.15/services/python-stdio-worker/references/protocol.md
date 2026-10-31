---
title: Stdio JSON-RPC worker protocol
author: MOZGII, Inc.
lang: en
---

# Stdio JSON-RPC worker protocol

The wire between a host process and a Python worker: newline-delimited JSON-RPC 2.0 over the worker's stdin and stdout. The host can be written in any language (Node, Electron, Rust, Go, Python). `scripts/worker_skeleton.py` implements the worker side and `scripts/test_worker_contract.py` checks it.

## Contents

This reference has 6 sections.

1. Framing
2. Messages the host sends
3. Messages the worker sends
4. Error codes
5. Lifecycle and timeouts
6. Versioning

## 1. Framing

Framing is one JSON object per line, UTF-8, terminated by `\n`, in both directions.

- A line never contains a raw newline: compact `json.dumps(..., separators=(",", ":"))` escapes control characters.
- `NaN` and `Infinity` are not JSON. Encode with `allow_nan=False`; JavaScript's `JSON.parse` rejects them.
- Both sides agree on a maximum line size (the skeleton uses 4 MiB). The host kills a worker that sends a longer line; the worker answers an oversized request with `-32600`.
- stdout carries protocol lines only. Logs, warnings and child-process output go to stderr, which the host reads as plain text.

## 2. Messages the host sends

The host sends requests (with an `id`) and two notifications (without one).

| Message | Shape | Worker's answer |
|---|---|---|
| a method call | `{"jsonrpc":"2.0","id":7,"method":"media.probe","params":{...}}` | exactly one reply with the same `id` |
| `$/ping` | `{"id":8,"method":"$/ping","params":null}` | `{"id":8,"result":{}}` at once, even while work runs |
| `$/cancel` | `{"method":"$/cancel","params":{"id":7}}` | request 7 stops at its next checkpoint and replies `-32800` |
| `shutdown` | `{"id":9,"method":"shutdown"}` | `{"id":9,"result":{}}`, then cancel, flush and exit 0 |

Closing the worker's stdin (EOF) means the same as `shutdown`. It is how a worker dies with its host even after the host was killed with `SIGKILL`.

## 3. Messages the worker sends

The worker sends replies and three notifications; it never sends requests to the host.

| Message | Params | Rule |
|---|---|---|
| `$/ready` | `{"protocol":1,"pid":123,"name":"media","version":"0.1.0","capabilities":["$/ping","media.probe","shutdown"]}` | the first line, sent after standard-library imports only |
| `$/progress` | `{"id":7,"done":40,"total":100,"label":"file 40","checkpoint":{"next":40}}` | at most about 10 per second per request; the final state is always sent before the reply |
| `$/log` | `{"level":"info","message":"model loaded"}` | events a person should see; diagnostics go to stderr |

A `checkpoint` is opaque to the host. A job runner stores it and passes it back as `params.checkpoint` when the job resumes; the worker validates it before trusting it.

## 4. Error codes

Error codes follow JSON-RPC 2.0, with a few reserved by this protocol.

| Code | Sent by | Meaning |
|---|---|---|
| `-32700` | worker | the line is not JSON, or is nested too deep to parse (`id: null`) |
| `-32600` | worker | not an object, oversized line, an `id` that is an array or object (`id: null`), duplicate id in flight |
| `-32601` | worker | method not registered |
| `-32602` | worker | params failed validation |
| `-32603` | worker | unexpected exception; message `internal error in <method>`, traceback on stderr only |
| `-32800` | worker | the request was cancelled |
| `-32000` | worker | a domain reason in `data.code`: `NOT_FOUND`, `CONFLICT`, `BAD_INPUT`, `FORBIDDEN`, `UNAVAILABLE`, `SHUTTING_DOWN`, `RESULT_TOO_LARGE` |
| `-32001` | host only | the call timed out |
| `-32002` | host only | the worker exited before replying |
| `-32003` | host only | too many requests in flight |

A worker never sends `-32001` to `-32003`: the host's retry policy treats them differently from worker errors.

## 5. Lifecycle and timeouts

The host owns the lifecycle; the worker only has to answer quickly and stop cleanly.

| Phase | Host | Worker |
|---|---|---|
| start | spawns with pipes, a clean environment and a start timeout (for example 10 s) | claims stdout, sends `$/ready` before heavy imports |
| warm | optional `warmup` call after the handshake | loads models or pools on the right thread |
| busy | limits requests in flight, sends `$/ping` every few seconds | answers pings from the event loop |
| hung | two missed pings: kill and restart with backoff | nothing: a blocked loop is a bug the restart exposes |
| idle | after an idle timeout sends `shutdown` | replies, releases, exits 0 |
| stop | `shutdown`, then closes stdin, then `SIGTERM`, then `SIGKILL` of the process group (`taskkill /T /F` on Windows) | exits on the first of them |

## 6. Versioning

The protocol version is the integer `protocol` field in `$/ready`.

- The host refuses a worker whose major version differs and logs both numbers.
- Adding a method or an optional param is not a protocol change; announce it through `capabilities`.
- Renaming a message, changing framing or reusing an error code is a new protocol version, changed on both sides in one release.
