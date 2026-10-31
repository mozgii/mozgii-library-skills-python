---
name: python-networking-websockets
description: Writes low-level network code in Python that survives real networks - TCP clients and servers with the socket module, timeouts, SO_REUSEADDR on POSIX and Windows, asyncio streams, framing messages with a length prefix, TLS with ssl.create_default_context and certifi, WebSocket clients and servers with the websockets asyncio API (not the deprecated legacy one), heartbeats, reconnection with backoff, backpressure, and local IPC over Unix domain sockets and Windows named pipes. Use when building a TCP protocol, a socket server, a WebSocket client or server, a chat or live-update feed, a reconnecting client, or talking between local processes over a socket or pipe.
metadata:
  tags:
  - python
  - networking
  - sockets
  - asyncio
  - websockets
  - tls
  - ipc
  - cross-platform
---

# Python networking: sockets, streams and WebSockets

A TCP connection is a byte stream, not a message channel: one `send` can arrive as three `recv` calls, and two sends as one. Peers vanish without closing, networks stall without erroring, and a fast sender can fill a slow receiver's memory. This skill gives the patterns that hold up: framing, timeouts, TLS, heartbeats, reconnection and backpressure. HTTP APIs are the `python-http-clients` skill; asyncio basics (TaskGroup, cancellation) are the `python-asyncio` skill.

## Step 0: read the installed versions

The websockets API changed shape in 14.0 and again in 17.0, so read the installed version before writing code.

```bash
uv run python -c "import sys, ssl; print(sys.version_info[:3], ssl.OPENSSL_VERSION)"
uv pip list 2>/dev/null | grep -iE "^(websockets|certifi|truststore) "
```

Current releases (October 2026): websockets 17.4 (Python 3.11+), certifi 2026.10.23. websockets 14.0 made `websockets.asyncio` the default and deprecated `websockets.legacy`; 17.0 added Trio and made `send(text=...)` keyword-only. 17.4 is a security release, so require `websockets>=17.4`: older versions let one client exhaust memory with a message split into very many small frames.

## Choosing the layer

Pick the highest layer that does the job; raw sockets mean writing framing, timeouts and reconnection yourself.

| Need | Use |
|---|---|
| call a REST or JSON API | httpx (the `python-http-clients` skill) |
| live two-way messages with a browser or a hosted service | WebSocket with `websockets` |
| your own binary protocol between your own programs | asyncio streams with a length prefix |
| two processes on one machine | a Unix domain socket, or `multiprocessing.connection` (named pipes on Windows) |

## TCP client with the socket module

`socket.create_connection` resolves the host, tries every address (IPv6 and IPv4) and returns a connected socket; the `timeout` applies to the connect and to every later call.

```python
import socket


def fetch_banner(host: str, port: int) -> bytes:
    with socket.create_connection((host, port), timeout=5.0) as sock:   # connect timeout and per-call timeout
        sock.sendall(b"HELLO\n")                                          # sendall loops until every byte is sent
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)                                      # b"" means the peer closed
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
```

- `send` may send only part of the buffer; use `sendall`.
- A timeout raises `TimeoutError` (`socket.timeout` is an alias). Without a timeout, `recv` waits forever on a peer that vanished.
- The timeout is per call, not a deadline for the whole exchange.

## TCP server with socket.create_server

`socket.create_server((host, port))` binds and listens in one call; on POSIX it sets `SO_REUSEADDR` so a restarted server can bind while old connections sit in `TIME_WAIT`. Accept in a loop and hand each connection to a thread (tens of clients) or use asyncio streams (thousands).

- Bind to `127.0.0.1` unless other machines must connect; `""` or `0.0.0.0` exposes the port to the network, and the macOS and Windows firewalls may ask the user to allow it.
- On Windows `create_server` does not set `SO_REUSEADDR`: there it would let another process bind the same port.

## Framing messages over a stream

Framing marks where each message ends. A 4-byte big-endian length before each payload handles any bytes; newline-delimited JSON is simpler when messages are text without raw newlines.

```python
import json
import socket
import struct

MAX_FRAME = 16 * 1024 * 1024                                  # refuse frames that would exhaust memory


def recv_exactly(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed mid-frame")
        buf += chunk
    return bytes(buf)


def send_msg(sock: socket.socket, obj: object) -> None:
    payload = json.dumps(obj, separators=(",", ":")).encode()
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def recv_msg(sock: socket.socket) -> object:
    (length,) = struct.unpack("!I", recv_exactly(sock, 4))
    if length > MAX_FRAME:
        raise ValueError(f"frame of {length} bytes is over the limit")
    return json.loads(recv_exactly(sock, length))
```

- Always cap the length: a corrupt or hostile prefix of `0xFFFFFFFF` otherwise allocates 4 GiB.
- For newline framing, cap the line length too (`StreamReader.readline` raises when a line exceeds the stream's `limit`, 64 KiB by default).

## asyncio streams: server and client

asyncio streams (`asyncio.start_server`, `asyncio.open_connection`) give a `StreamReader` and `StreamWriter` per connection and handle thousands of connections in one thread.

```python
import asyncio
import json
import struct

MAX_FRAME = 16 * 1024 * 1024


async def read_msg(reader: asyncio.StreamReader) -> object:
    (length,) = struct.unpack("!I", await reader.readexactly(4))   # IncompleteReadError on EOF
    if length > MAX_FRAME:
        raise ValueError("frame too large")
    return json.loads(await reader.readexactly(length))


async def write_msg(writer: asyncio.StreamWriter, obj: object) -> None:
    payload = json.dumps(obj).encode()
    writer.write(struct.pack("!I", len(payload)) + payload)
    await writer.drain()                                          # wait while the send buffer is full


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            async with asyncio.timeout(60):                       # idle clients are dropped
                msg = await read_msg(reader)
            await write_msg(writer, {"echo": msg})
    except (asyncio.IncompleteReadError, ConnectionError, TimeoutError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def main() -> None:
    async with await asyncio.start_server(handle, "127.0.0.1", 9000):
        reader, writer = await asyncio.open_connection("127.0.0.1", 9000)
        await write_msg(writer, {"n": 1})
        print(await read_msg(reader))                            # {'echo': {'n': 1}}
        writer.close()

asyncio.run(main())
```

- `writer.write` only buffers; `await writer.drain()` is the backpressure point. Skip it and a slow client makes the server buffer without limit.
- Wrap `open_connection` in `asyncio.timeout(...)`: it has no connect timeout of its own.
- Since Python 3.13, `server.close_clients()` ends open connections after `server.close()`.

## TLS with the ssl module

`ssl.create_default_context()` gives a client context that verifies the certificate chain and the host name against the operating system's trust store; pass it as `ssl=` to `open_connection` or wrap a socket with it.

```python
import socket
import ssl

import certifi                                                    # optional: Mozilla's CA bundle

ctx = ssl.create_default_context()                                # system trust store
ctx = ssl.create_default_context(cafile=certifi.where())          # or the certifi bundle, same on every OS
ctx.minimum_version = ssl.TLSVersion.TLSv1_2

with socket.create_connection(("example.com", 443), timeout=10) as raw:
    with ctx.wrap_socket(raw, server_hostname="example.com") as tls:
        print(tls.version(), tls.getpeercert()["subject"])
```

- Never set `check_hostname = False` or `verify_mode = CERT_NONE` outside a test against a local server; for a private CA, `ctx.load_verify_locations("company-ca.pem")`.
- The default trust store comes from OpenSSL's paths: fine on most Linux systems, but on macOS (python.org builds) and Windows it may miss CAs. `certifi` gives one bundle everywhere; `truststore` uses the OS store.
- With asyncio, pass the same context: `await asyncio.open_connection(host, 443, ssl=ctx)`, inside `asyncio.timeout(...)`.
- A server context is `ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)` plus `load_cert_chain(certfile, keyfile)`.

## WebSockets with the websockets library

The `websockets` library implements the WebSocket protocol on asyncio (also threading and Trio). Import from `websockets.asyncio.server` and `websockets.asyncio.client`. The legacy API (`websockets.legacy`, `websockets.server.serve`, `websockets.client.connect`, `WebSocketServerProtocol`) is deprecated; the bare `websockets.serve` and `websockets.connect` point to the new implementation since 14.0, so tutorials written before it break on the handler signature and on `extra_headers`.

```python
import asyncio
import json

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve


async def echo(ws: ServerConnection) -> None:
    async for message in ws:                                      # ends on a normal close
        await ws.send(json.dumps({"echo": json.loads(message)}))


async def main() -> None:
    async with serve(echo, "127.0.0.1", 8765, max_size=1_000_000, ping_interval=20, ping_timeout=20):
        async with connect("ws://127.0.0.1:8765") as ws:
            await ws.send(json.dumps({"hello": "world"}))
            print(await ws.recv())                                # {"echo": {"hello": "world"}}

asyncio.run(main())
```

- `max_size` (1 MiB default) caps an incoming message; raise it on purpose, never to `None` on a public server.
- `recv()` refuses a message split across more than 4096 frames (17.4 and newer, in every implementation): the connection closes with code 1009 and the reason "too many fragments". The `WEBSOCKETS_MAX_FRAGMENTS` environment variable changes the limit, read once at import. `recv_streaming()` has no limit, because it does not hold the frames.
- A handler that raises closes the connection with code 1011 (internal error); catch expected errors inside the handler and send an error message instead.
- Browsers cannot set WebSocket headers: authenticate with a cookie or a token in the first message, and set `origins=`.

## Heartbeats and reconnection with backoff

A heartbeat detects a dead peer that never closed the TCP connection; `websockets` sends a Ping every `ping_interval` seconds and closes the connection when the Pong does not arrive within `ping_timeout`. Reconnection is built into the client: iterate over `connect(...)`.

```python
import asyncio

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


async def follow(uri: str, handle) -> None:
    async for ws in connect(uri, ping_interval=15, ping_timeout=10, open_timeout=10):
        try:
            await ws.send('{"subscribe": "prices"}')             # resubscribe after every reconnect
            async for message in ws:
                await handle(message)
        except ConnectionClosed:
            continue                                              # backoff, then the next connection
```

- The iterator retries network errors and HTTP 500, 502, 503 and 504 with exponential backoff and a random first delay; other errors (a 401 or 403) are raised. `reconnect_delays=` (17.0) and `process_exception=` change both.
- Backoff applies only to failed connection attempts. A connection that opens and then closes resets it, so a server that accepts and at once closes makes the loop reconnect without pause; sleep yourself when a connection lived less than a few seconds.
- State the server forgot (subscriptions, the last seen id) must be sent again on each new connection; keep it in the client.
- Raw TCP protocols need their own heartbeat: a `ping` message every N seconds and a read timeout of about 2N on the other side.

## Backpressure

Backpressure means a fast producer waits for a slow consumer instead of buffering without limit. Every layer has a limit; keep each one bounded.

- asyncio streams: `await writer.drain()` after writes; the reader side is limited by `limit=` on `start_server` / `open_connection`.
- websockets: `send` waits when the outgoing buffer passes `write_limit` (32 KiB); incoming messages queue up to `max_queue` (16) and then reading from the socket stops.
- Between your own tasks: `asyncio.Queue(maxsize=N)`.
- Broadcast to many clients with `websockets.asyncio.server.broadcast(connections, message)`: it skips clients whose buffers are full instead of waiting for the slowest one.

## Local IPC: Unix domain sockets and Windows named pipes

A Unix domain socket is a socket bound to a file path: faster than loopback TCP, invisible to the network, and protected by file permissions. On macOS and Linux, asyncio serves one with `asyncio.start_unix_server(handle, path=...)` and connects with `asyncio.open_unix_connection(path)`; the handler and the framing are the same as for TCP.

- `os.chmod(path, 0o600)` after binding; remove a stale socket file (left by a crash) before binding.
- Paths are limited to about 104 bytes on macOS and 108 on Linux; keep them short.
- Python does not expose `AF_UNIX` on Windows, and asyncio's Unix functions are Unix-only. On Windows use a named pipe (`\\.\pipe\name`) through `multiprocessing.connection`, or loopback TCP on `127.0.0.1` with a random token the client sends first.
- Recipes for both are in [sockets-and-ipc.md](references/sockets-and-ipc.md).

## Verify

Prove the code with attack cases against a real local server: a hostile length prefix and a message cut into too many frames must both be refused.

```bash
uv pip list 2>/dev/null | grep -iE "^websockets "       # expect 17.4 or newer
uv run ruff check --select S104,S501,S507 .             # expect no bind to all interfaces and no disabled TLS check
grep -rn "websockets.legacy" src                        # expect nothing
uv run python - <<'PY'
import asyncio
import struct

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosedError

MAX_FRAME = 16 * 1024 * 1024
assert struct.unpack("!I", b"\xff\xff\xff\xff")[0] > MAX_FRAME   # the prefix recv_msg must refuse


async def reader(ws: ServerConnection) -> None:
    try:
        await ws.recv()
    except ConnectionClosedError as exc:
        print(exc.sent.code, exc.sent.reason)           # expect 1009 too many fragments


async def tiny_frames():
    for _ in range(5000):
        yield "x"


async def main() -> None:
    async with serve(reader, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(tiny_frames())
            await asyncio.sleep(0.2)


asyncio.run(main())
PY
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `recv(1024)` as one message | framing with a length prefix or a delimiter |
| a length prefix read with no cap | refuse it above a fixed maximum |
| `check_hostname = False` | load the private CA |
| a socket with no timeout | a timeout, or `asyncio.timeout` |
| `writer.write` in a loop without `drain` | `await writer.drain()` |
| comparing tokens with `==` | `hmac.compare_digest` on bytes |
| your own reconnect loop around websockets | `async for ws in connect(...)` |

## Pre-merge checklist

Network code is ready when every item holds.

- [ ] Every connect, read and write has a timeout or runs under `asyncio.timeout`.
- [ ] Messages are framed (length prefix or delimiter) and every frame size is capped.
- [ ] Writers await `drain()`; queues between tasks have a `maxsize`.
- [ ] TLS verification is on; private CAs are loaded, never `CERT_NONE`.
- [ ] WebSocket code imports from `websockets.asyncio` (or `websockets.sync`), not `websockets.legacy`; `max_size` is set on purpose; `websockets>=17.4` is pinned.
- [ ] Long-lived connections have a heartbeat and the client reconnects with backoff and restores its state.
- [ ] Servers bind to `127.0.0.1` unless remote access is wanted; local IPC sockets are `0600`.
- [ ] Tested on Windows: no `AF_UNIX`, a different `SO_REUSEADDR`.

## Gotchas

These are the networking bugs seen most.

1. **A client hangs forever after Wi-Fi drops.** No timeout and no heartbeat. Add read timeouts, WebSocket pings or TCP keepalive.
2. **`OSError: [Errno 48] Address already in use` after a restart.** A socket created by hand without `SO_REUSEADDR`, or a stale Unix socket file. Use `create_server`, or delete the socket path first.
3. **`DeprecationWarning: websockets.legacy is deprecated`, or `TypeError` about the handler's arguments.** Code written for the legacy API (`WebSocketServerProtocol`, `handler(ws, path)`, `extra_headers`). Switch to `websockets.asyncio.server.serve` and `ServerConnection`; the handler takes one argument (read the path from `ws.request.path`), and client headers go in `additional_headers`.
4. **The client reconnects hundreds of times a second.** The server accepts and closes at once, and a successful connection resets the backoff. Sleep before reconnecting when the connection was short.
5. **`SSL: CERTIFICATE_VERIFY_FAILED` on macOS or Windows only.** Python's OpenSSL does not see the OS trust store. Pass a `certifi` context or use `truststore`.
6. **`AttributeError: module 'socket' has no attribute 'AF_UNIX'` on Windows.** Use a named pipe or loopback TCP.
7. **`ConnectionClosedError: sent 1009 (message too big) too many fragments`.** The peer split one message across more than 4096 frames, which 17.4 refuses. Send larger fragments, read with `recv_streaming()`, or raise `WEBSOCKETS_MAX_FRAGMENTS` for a trusted peer.

## References

The reference files hold the longer recipes and option tables.

- [websockets.md](references/websockets.md): health checks and auth in `process_request`, broadcast, close codes, the threading client, testing, migrating from the legacy API
- [sockets-and-ipc.md](references/sockets-and-ipc.md): a threaded server, socket options, keepalive, UDP, IPv6, and local IPC on every OS

## See also

- `python-http-clients`: REST and JSON APIs over HTTP
- `python-asyncio`: `TaskGroup`, timeouts and cancellation
- `python-security`: tokens, TLS and hostile input
- `python-services-shutdown`: closing servers without dropping work
- `python-cross-platform`: Windows sockets and pipes
