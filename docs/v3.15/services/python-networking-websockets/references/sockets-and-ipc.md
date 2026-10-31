---
title: Sockets, socket options and local IPC in Python
author: MOZGII, Inc.
lang: en
---

# Sockets, socket options and local IPC in Python

The socket-level detail behind the networking skill: a threaded TCP server that stops cleanly, dual-stack IPv6, the socket options that matter and how they differ on macOS, Linux and Windows, TCP keepalive, half-close, UDP, and local inter-process communication over Unix domain sockets and Windows named pipes.

## Contents

This reference has 9 sections.

1. A threaded TCP server that stops cleanly
2. Dual-stack IPv6 servers
3. Socket options by platform
4. TCP keepalive on macOS, Linux and Windows
5. Half-close: ending a request without closing the socket
6. UDP datagrams
7. Unix domain sockets with asyncio
8. Cross-platform local IPC with multiprocessing.connection
9. Gotchas

## 1. A threaded TCP server that stops cleanly

A threaded TCP server accepts in a loop and gives each connection a thread. A short accept timeout lets the loop notice a stop flag, because a thread blocked in `accept()` does not wake when another thread closes the socket on every OS.

```python
import socket
import threading


def handle(conn: socket.socket) -> None:
    with conn:
        conn.settimeout(30)                                   # drop clients idle for 30 s
        try:
            while data := conn.recv(65536):
                conn.sendall(data)                            # echo
        except TimeoutError:
            pass


def serve(stop: threading.Event, host: str = "127.0.0.1", port: int = 9000) -> None:
    with socket.create_server((host, port), backlog=128) as srv:
        srv.settimeout(0.5)                                   # wake twice a second to check the flag
        while not stop.is_set():
            try:
                conn, _addr = srv.accept()
            except TimeoutError:
                continue
            conn.settimeout(None)                             # accepted sockets inherit the timeout on some OSes
            threading.Thread(target=handle, args=(conn,), daemon=True).start()


stop = threading.Event()
server = threading.Thread(target=serve, args=(stop,), kwargs={"port": 9000})
server.start()
with socket.create_connection(("127.0.0.1", 9000), timeout=2) as c:
    c.sendall(b"ping")
    print(c.recv(4))                                          # b'ping'
stop.set()
server.join()
```

`socketserver.ThreadingTCPServer` packages the same pattern with `serve_forever()` and `shutdown()`; it is fine for tools, while asyncio scales further.

## 2. Dual-stack IPv6 servers

A dual-stack socket listens on IPv6 and accepts IPv4 clients too, which appear as IPv4-mapped addresses (`::ffff:192.0.2.1`). Check support first, since some systems disable it.

```python
import socket

addr = ("", 9001)                                             # all interfaces
if socket.has_dualstack_ipv6():
    srv = socket.create_server(addr, family=socket.AF_INET6, dualstack_ipv6=True)
else:
    srv = socket.create_server(addr)
print(srv.family.name, srv.getsockname()[:2])
srv.close()
```

Clients get both families for free: `socket.create_connection` and `asyncio.open_connection` try every address `getaddrinfo` returns. asyncio's `open_connection(..., happy_eyeballs_delay=0.25)` races IPv6 and IPv4 (RFC 8305) so a broken IPv6 route does not cost a full connect timeout.

## 3. Socket options by platform

Socket options are set with `sock.setsockopt(level, option, value)` before `bind` or `connect`. The same name can mean different things per OS.

| Option | Effect | Platform notes |
|---|---|---|
| `SO_REUSEADDR` | bind a port whose old connections are in `TIME_WAIT` | POSIX: set it on servers (`create_server` does). Windows: it allows another socket to bind the same port at once; do not set it |
| `SO_EXCLUSIVEADDRUSE` | no other socket may bind this port | Windows only; the safe choice for Windows servers |
| `SO_REUSEPORT` | several sockets share one port; the kernel spreads connections | Linux (load balancing across processes), macOS and BSD (different rules); not on Windows. `create_server(reuse_port=True)` |
| `TCP_NODELAY` | send small writes at once (no Nagle delay) | all; use for request-response protocols with small messages |
| `SO_KEEPALIVE` | probe idle connections | all; tune with the options in section 4 |
| `SO_RCVBUF`, `SO_SNDBUF` | kernel buffer sizes | all; Linux doubles the value you set and caps it at a system limit |
| `TCP_NOTSENT_LOWAT` | limit unsent bytes in the kernel | Linux and macOS (3.7+); lowers latency for streaming writers |

```python
import socket
import sys

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
if sys.platform == "win32":
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
else:
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0))
srv.listen()
cli = socket.create_connection(srv.getsockname())
cli.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
print(cli.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 0)   # True
cli.close()
srv.close()
```

## 4. TCP keepalive on macOS, Linux and Windows

TCP keepalive makes the kernel probe a silent connection and fail it when the peer is gone. The defaults wait about two hours before the first probe, so tune them; the option names differ per OS.

```python
import socket


def enable_keepalive(sock: socket.socket, idle: int = 60, interval: int = 10, count: int = 5) -> None:
    """Fail a dead connection after about idle + interval * count seconds."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    idle_opt = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)  # Linux/Windows, macOS
    if idle_opt is not None:
        sock.setsockopt(socket.IPPROTO_TCP, idle_opt, idle)
    if hasattr(socket, "TCP_KEEPINTVL"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval)
    if hasattr(socket, "TCP_KEEPCNT"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, count)


s = socket.socket()
enable_keepalive(s)
print(s.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0)    # True
s.close()
```

- macOS names the idle option `TCP_KEEPALIVE` (Python 3.10+); Linux and Windows 10 1709+ use `TCP_KEEPIDLE`.
- On older Windows, `sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, idle_ms, interval_ms))` sets idle and interval in milliseconds.
- Keepalive finds dead peers in minutes. For seconds, use an application heartbeat.

## 5. Half-close: ending a request without closing the socket

Half-close sends end-of-file to the peer while keeping the socket open for the reply: `sock.shutdown(socket.SHUT_WR)`. Protocols that read "until EOF" need it.

```python
import socket

a, b = socket.socketpair()
a.sendall(b"whole request")
a.shutdown(socket.SHUT_WR)                    # b's recv now returns b"" after the data
request = b""
while chunk := b.recv(4096):
    request += chunk
b.sendall(request.upper())
b.close()
print(a.recv(4096))                           # b'WHOLE REQUEST'
a.close()
```

In asyncio the same call is `writer.write_eof()`; check `writer.can_write_eof()` first, since TLS transports cannot half-close.

## 6. UDP datagrams

UDP sends separate datagrams with no connection, no ordering and no delivery guarantee; each `recvfrom` returns exactly one datagram. Use it for discovery, metrics and media, never for data that must arrive.

```python
import socket

rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx.bind(("127.0.0.1", 0))
rx.settimeout(2)
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
tx.sendto(b"cpu=0.42", rx.getsockname())
data, sender = rx.recvfrom(65535)             # one whole datagram; a smaller buffer truncates it
print(data)
tx.close()
rx.close()
```

- Keep datagrams under about 1,200 bytes on the internet to avoid IP fragmentation; on loopback the limit is near 64 KiB (lower on macOS by default).
- On Windows, a `sendto` to a closed port makes the next `recvfrom` raise `ConnectionResetError`; catch it and keep reading.
- asyncio has `loop.create_datagram_endpoint()` with a protocol class for UDP.

## 7. Unix domain sockets with asyncio

A Unix domain socket server binds to a file path; on macOS and Linux asyncio serves it with `start_unix_server` and connects with `open_unix_connection`. File permissions decide who may connect.

```python
import asyncio
import os
import tempfile
from pathlib import Path


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.write((await reader.readline()).upper())
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def main() -> None:
    path = Path(tempfile.mkdtemp()) / "app.sock"              # short path: about 104 bytes max on macOS
    path.unlink(missing_ok=True)                              # a crashed server leaves the file behind
    server = await asyncio.start_unix_server(handle, path=path)
    os.chmod(path, 0o600)                                     # only this user may connect
    async with server:
        reader, writer = await asyncio.open_unix_connection(path)
        writer.write(b"ping\n")
        print(await reader.readline())                        # b'PING\n'
        writer.close()
        await writer.wait_closed()

asyncio.run(main())
```

- The directory's permissions matter too: put the socket in a `0700` directory such as `$XDG_RUNTIME_DIR` on Linux or a per-user temp directory on macOS.
- Linux also has abstract sockets (a name starting with `\0`): no file, but no permissions either; any local user can connect.

## 8. Cross-platform local IPC with multiprocessing.connection

`multiprocessing.connection` gives one API over Unix domain sockets on macOS and Linux and named pipes on Windows, with message framing, pickled objects and an HMAC challenge (`authkey`) built in. It is blocking; run it in a thread for an asyncio program.

```python
import os
import secrets
import sys
import tempfile
import threading
from multiprocessing.connection import Client, Listener


def ipc_address(name: str) -> str:
    if sys.platform == "win32":
        return rf"\\.\pipe\{name}-{os.getpid()}"                 # a named pipe
    return os.path.join(tempfile.mkdtemp(), f"{name}.sock")       # a Unix domain socket


address = ipc_address("myapp")
authkey = secrets.token_bytes(32)                                 # pass to the child through its environment or stdin
listener = Listener(address, authkey=authkey)                     # family chosen from the address


def server() -> None:
    with listener.accept() as conn:                               # fails if the client's authkey differs
        while (msg := conn.recv()) != "bye":
            conn.send({"echo": msg})


t = threading.Thread(target=server)
t.start()
with Client(address, authkey=authkey) as conn:
    conn.send("hello")
    print(conn.recv())                                            # {'echo': 'hello'}
    conn.send("bye")
t.join()
listener.close()
```

- `conn.recv()` unpickles, and unpickling runs code chosen by the sender. The `authkey` handshake is what keeps other local users out; always set it, and use `send_bytes` / `recv_bytes` with JSON when the peer is not fully trusted.
- A named pipe is reachable by other users on the machine unless its security descriptor says otherwise; the `authkey` handshake covers that too.
- For a host process in another language, a Unix socket (POSIX) or named pipe (Windows) with length-prefixed JSON is the portable contract; a loopback TCP port with a random token is the simplest single code path.

## 9. Gotchas

These are the socket-level failures seen most.

1. **Two servers on Windows both bind port 9000.** `SO_REUSEADDR` was set by hand. Use `SO_EXCLUSIVEADDRUSE` on Windows, or `create_server`.
2. **A dead peer is noticed only after two hours.** Default keepalive timings. Tune them (section 4) or add an application heartbeat.
3. **Small request-response messages each take 40 ms or more.** Nagle's algorithm plus delayed ACKs. Set `TCP_NODELAY`, or batch writes into one `sendall`.
4. **`OSError: AF_UNIX path too long`.** The socket path passed about 104 bytes (macOS) or 108 (Linux). Use a short directory.
5. **The server thread never stops.** It is blocked in `accept()` without a timeout. Use `settimeout` on the listening socket or `selectors` with a wake-up socket.
6. **`ConnectionResetError` reading UDP on Windows.** An earlier datagram hit a closed port. Catch it and continue.
