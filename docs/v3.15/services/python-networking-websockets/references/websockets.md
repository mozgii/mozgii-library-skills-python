---
title: WebSocket servers and clients with the websockets library
author: MOZGII, Inc.
lang: en
---

# WebSocket servers and clients with the websockets library

The websockets library (17.4, October 2026) in depth: a server that answers health checks and checks tokens before the handshake, tracks clients and broadcasts, closes with the right codes and shuts down cleanly; the threading client for code without asyncio; tests; and the move from the deprecated legacy API.

## Contents

This reference has 9 sections.

1. Server skeleton with graceful shutdown
2. Health checks and authentication in process_request
3. Tracking clients and broadcasting
4. Close codes and reading why a connection closed
5. Messages: text, binary, size and compression
6. The threading client and reconnect
7. Testing a WebSocket server
8. Migrating from the legacy API
9. Gotchas

## 1. Server skeleton with graceful shutdown

A websockets server is a handler coroutine called once per connection; `serve()` returns a `Server` used as an async context manager. On shutdown, `server.close()` stops accepting and closes open connections with code 1001 (going away), which tells clients to reconnect elsewhere.

```python
import asyncio
import signal
import sys

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed


async def handler(ws: ServerConnection) -> None:
    try:
        async for message in ws:
            await ws.send(message)
    except ConnectionClosed:
        pass                                                   # the client vanished; nothing to clean up


async def main() -> None:
    stop = asyncio.Event()
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, stop.set)      # Windows: Ctrl+C cancels main() instead
    async with serve(handler, "127.0.0.1", 8765) as server:
        asyncio.get_running_loop().call_later(0.2, stop.set)  # demo only: stop at once
        await stop.wait()
        server.close(code=1012, reason="restarting")           # 1012 service restart, 16.0+
        await server.wait_closed()
    print("stopped")

asyncio.run(main())
```

`async for message in ws` ends quietly on a normal close and raises `ConnectionClosedError` on an abnormal one; catching `ConnectionClosed` covers both. The skill `python-services-shutdown` covers signal handling and drain deadlines for the whole process.

## 2. Health checks and authentication in process_request

`process_request(connection, request)` runs before the WebSocket handshake. Return `None` to continue, or a response from `connection.respond(status, body)` to answer plain HTTP: a health check for a load balancer, or a 401 for a bad token.

```python
import asyncio
import hmac
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import InvalidStatus
from websockets.http11 import Request, Response

TOKEN = "s3cret-token"                                         # load from the environment in real code


def process_request(connection: ServerConnection, request: Request) -> Response | None:
    url = urlsplit(request.path)
    if url.path == "/healthz":
        return connection.respond(HTTPStatus.OK, "OK\n")
    if url.path != "/ws":
        return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
    token = parse_qs(url.query).get("token", [""])[0] or request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not hmac.compare_digest(token.encode(), TOKEN.encode()):   # constant-time; bytes, so non-ASCII cannot raise
        return connection.respond(HTTPStatus.UNAUTHORIZED, "Bad token\n")
    return None


async def handler(ws: ServerConnection) -> None:
    await ws.send(f"welcome to {ws.request.path.split('?')[0]}")


async def main() -> None:
    async with serve(handler, "127.0.0.1", 8767, process_request=process_request):
        async with connect("ws://127.0.0.1:8767/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
            print(await ws.recv())                             # welcome to /ws
        for token in ("wrong", "%C3%A9"):                      # attack case: a non-ASCII token
            try:
                await connect(f"ws://127.0.0.1:8767/ws?token={token}")
            except InvalidStatus as exc:
                print(exc.response.status_code)                # 401, both times

asyncio.run(main())
```

- `hmac.compare_digest` raises `TypeError` on a `str` holding non-ASCII characters, so comparing strings turns `?token=%C3%A9` into an HTTP 500 instead of a 401. Encode both sides.
- A token in the query string ends up in proxy and access logs; prefer a header (non-browser clients), a cookie, or a short-lived single-use token.
- `websockets.asyncio.server.basic_auth(realm=..., check_credentials=...)` returns a ready `process_request` for HTTP Basic auth.
- `process_request` may be a coroutine; a database lookup there delays only that handshake.
- `origins=["https://app.example.com"]` on `serve()` refuses browser pages from other sites (cross-site WebSocket hijacking).

## 3. Tracking clients and broadcasting

`server.connections` holds every open connection, and `broadcast(connections, message)` sends one message to many without waiting: a client whose send buffer is full is skipped instead of slowing everyone down.

```python
import asyncio

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, broadcast, serve

rooms: dict[str, set[ServerConnection]] = {}


async def handler(ws: ServerConnection) -> None:
    room = rooms.setdefault(ws.request.path, set())
    room.add(ws)
    try:
        await ws.wait_closed()
    finally:
        room.discard(ws)                                       # always remove, or the set leaks


async def main() -> None:
    async with serve(handler, "127.0.0.1", 8768):
        async with connect("ws://127.0.0.1:8768/prices") as a, connect("ws://127.0.0.1:8768/prices") as b:
            await asyncio.sleep(0.05)                          # let both handlers register
            broadcast(rooms["/prices"], '{"EURUSD": 1.0842}')
            print(await a.recv(), await b.recv())

asyncio.run(main())
```

- `broadcast` is a plain function, not a coroutine. With `raise_exceptions=True` it raises an `ExceptionGroup` of the sends that failed.
- For guaranteed delivery to each client, give each one an `asyncio.Queue(maxsize=...)` and a sender task, and disconnect clients whose queue stays full.
- Broadcasting across several server processes needs a shared bus (Redis pub/sub, Postgres `LISTEN/NOTIFY`); `server.connections` is per process.

## 4. Close codes and reading why a connection closed

A close code tells the other side why the connection ended. `ConnectionClosed` carries `rcvd` (the frame received) and `sent` (the frame sent); `rcvd` is `None` when the TCP connection dropped without a close frame.

| Code | Name | Use |
|---|---|---|
| 1000 | `NORMAL_CLOSURE` | done; do not reconnect unless the app wants to |
| 1001 | `GOING_AWAY` | server shutting down or page left; reconnect |
| 1006 | `ABNORMAL_CLOSURE` | never sent: reported when the TCP connection dropped |
| 1008 | `POLICY_VIOLATION` | auth expired, forbidden action; do not retry blindly |
| 1009 | `MESSAGE_TOO_BIG` | message over `max_size` |
| 1011 | `INTERNAL_ERROR` | the handler raised |
| 1012 | `SERVICE_RESTART` | restart; reconnect after a short delay |
| 1013 | `TRY_AGAIN_LATER` | overloaded; reconnect with backoff |
| 4000-4999 | application codes | your own reasons, documented in your protocol |

```python
from websockets.exceptions import ConnectionClosed
from websockets.frames import CloseCode


def should_reconnect(exc: ConnectionClosed) -> bool:
    if exc.rcvd is None:
        return True                                            # network drop
    return exc.rcvd.code in (CloseCode.GOING_AWAY, CloseCode.SERVICE_RESTART, CloseCode.TRY_AGAIN_LATER)
```

Close with `await ws.close(code, reason)`; the reason is limited to 123 bytes of UTF-8.

## 5. Messages: text, binary, size and compression

A WebSocket message is text (`str`) or binary (`bytes`); `send(str)` sends a text frame and `send(bytes)` a binary one, and `recv()` returns the matching type.

- `recv(decode=False)` returns text frames as `bytes` and skips UTF-8 decoding, which saves time when you pass JSON straight to `orjson.loads`.
- `max_size` caps one incoming message (1 MiB by default); a larger one closes the connection with 1009. A tuple `(message_limit, fragment_limit)` sets both (16.0+).
- Since 17.4 `recv()` also refuses a message of more than 4096 frames, in every implementation, because thousands of tiny frames cost memory while staying under `max_size`. The connection closes with 1009 and the reason "too many fragments", and the `ConnectionClosedError` is chained from `TooManyFragments`. `WEBSOCKETS_MAX_FRAGMENTS` changes the limit; it is read once when websockets is imported, so set it in the environment before the process starts. The changelog lists no CVE id yet.
- `recv_streaming()` yields the fragments of one large message without joining them in memory, so the 4096-frame limit does not apply to it.
- Compression (`permessage-deflate`) is on by default. It saves bandwidth for repetitive JSON and costs CPU and memory on every connection; pass `compression=None` for many connections with small messages.
- Sending an async iterator (`await ws.send(chunks())`) sends one fragmented message.

## 6. The threading client and reconnect

`websockets.sync.client.connect` is a blocking client for scripts and threads; since 17.1 `reconnect()` gives the same automatic reconnection as the asyncio client.

```python
import threading

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect, reconnect
from websockets.sync.server import serve


def echo(ws) -> None:
    try:
        for message in ws:
            ws.send(message)
    except ConnectionClosed:
        pass


with serve(echo, "127.0.0.1", 8769) as server:
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with connect("ws://127.0.0.1:8769", open_timeout=5) as ws:
        ws.send("hi")
        print(ws.recv(timeout=5))                              # recv takes a timeout in the sync API
    for ws in reconnect("ws://127.0.0.1:8769"):                # reconnects with backoff on failure
        ws.send("once")
        print(ws.recv(timeout=5))
        ws.close()                                             # close normally before leaving the loop
        break
    server.shutdown()
```

Leaving a `connect` block or the `reconnect` loop with an exception, `break` included, closes the connection with code 1011 (internal error), which the server logs as a failure; call `ws.close()` first when the exit is normal.

## 7. Testing a WebSocket server

Start the real server on port 0 inside the test, read the port it got, and connect a real client. No mocking is needed, and the test covers the handshake, `process_request` and close codes.

```python
import asyncio

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve


async def echo(ws: ServerConnection) -> None:
    async for message in ws:
        await ws.send(message)


async def test_echo() -> None:
    async with serve(echo, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send("ping")
            async with asyncio.timeout(2):
                assert await ws.recv() == "ping"

asyncio.run(test_echo())
print("passed")
```

Under pytest with `pytest-asyncio` or `anyio`, mark the test async and drop the `asyncio.run` line. Always put a timeout around `recv()` in tests, or a bug hangs the suite.

## 8. Migrating from the legacy API

The legacy implementation (`websockets.legacy`) is deprecated since 14.0 and kept for a limited time under the project's compatibility policy. These are the changes that touch most code.

| Legacy | New `websockets.asyncio` |
|---|---|
| `websockets.legacy.server.serve`, `websockets.server.serve` | `websockets.asyncio.server.serve` (also `websockets.serve` since 14.0) |
| `WebSocketServerProtocol`, `WebSocketClientProtocol` | `ServerConnection`, `ClientConnection` |
| `async def handler(ws, path)` | `async def handler(ws)`; path in `ws.request.path` |
| `ws.request_headers`, `ws.response_headers` | `ws.request.headers`, `ws.response.headers` |
| `ws.open`, `ws.closed` | `ws.state is State.OPEN` (`from websockets.protocol import State`), or handle `ConnectionClosed` |
| `connect(..., extra_headers=...)` | `connect(..., additional_headers=...)` |
| `serve(..., extra_headers=...)` | a `process_response(connection, request, response)` that updates `response.headers` |
| `process_request(path, request_headers)` returning a tuple | `process_request(connection, request)` returning `connection.respond(...)` or `None` |
| `basic_auth_protocol_factory` | `process_request=basic_auth(...)` |
| `async for ws in connect(...)` retrying every error | retries only transient errors; customise with `process_exception=` |

Run the test suite with `-W error::DeprecationWarning` to find remaining legacy imports.

## 9. Gotchas

These are the websockets-specific failures seen most.

1. **`ConnectionClosedError: sent 1011 (internal error) keepalive ping timeout`.** The event loop was blocked longer than `ping_timeout` (CPU work or a blocking call in a handler), so Pongs were not processed. Move blocking work to `asyncio.to_thread`.
2. **The client gets HTTP 403 or 400 behind nginx.** The proxy does not forward `Upgrade` and `Connection` headers. Configure `proxy_http_version 1.1` and both headers.
3. **Connections drop after exactly 60 seconds idle behind a proxy or load balancer.** The proxy's idle timeout is shorter than the ping interval. Lower `ping_interval` below it.
4. **Memory grows with many idle clients.** Per-connection compression buffers. Pass `compression=None`.
5. **`InvalidStatus: server rejected WebSocket connection: HTTP 401` ends the reconnect loop.** 4xx answers are fatal by design. Refresh the credentials, then reconnect.
6. **A system proxy is used unexpectedly.** Since 15.0 clients use proxies from the environment and OS settings. Pass `proxy=None` to connect directly.
7. **`ConnectionClosedError: sent 1009 (message too big) too many fragments`.** A sender split one message across more than 4096 frames. Send larger fragments, read with `recv_streaming()`, or raise `WEBSOCKETS_MAX_FRAGMENTS` when the peer is trusted.
