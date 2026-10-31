---
name: python-http-clients
description: Calls HTTP APIs from Python with httpx (default) or requests - one reused client with base_url, timeouts on every request, connection limits, raise_for_status and the exception hierarchy, retries for transient failures only, auth and API keys from the environment, pagination, streaming large downloads to disk, uploads, the async client with bounded concurrency, TLS with a private CA or the OS trust store, proxies, logging without secrets, and tests with httpx.MockTransport or respx instead of real network calls. Use when writing a client for a REST or JSON API, downloading files, calling a third-party service, choosing between httpx, requests and aiohttp, or when requests hang, leak connections, fail on certificates or are slow.
metadata:
  tags:
  - python
  - http
  - httpx
  - requests
  - api-client
  - rest
  - timeouts
  - respx
---

# HTTP clients in Python

An HTTP client is code that calls another service; it fails in ways local code does not (slow servers, dropped connections, rate limits, certificate errors). A correct client reuses one connection pool, sets a timeout on every request, checks the status, retries only what is transient, and is tested without the network. httpx is the default here; requests and aiohttp appear where they differ.

## Step 0: read the installed versions

HTTP libraries change argument names between minor versions, so read what the project runs.

```bash
uv pip list 2>/dev/null | grep -iE "^(httpx|requests|aiohttp|respx|urllib3|truststore) "
```

Current releases (October 2026): httpx 0.28.1, requests 2.34, aiohttp 3.14, respx 0.23, truststore 0.10.

| Library | Pick when |
|---|---|
| httpx | the default: sync and async with one API, HTTP/2 (`httpx[http2]`), built-in mock transport |
| requests | existing code, or a script that needs nothing else; sync only |
| aiohttp | an asyncio service with very many concurrent requests, or a WebSocket client |

## One client, reused

A client object holds a connection pool; creating one per request repeats DNS, TCP and TLS work every time and can exhaust sockets. Create one client per service at start-up and close it at exit.

```python
import httpx

client = httpx.Client(
    base_url="https://api.example.com/v2",
    timeout=httpx.Timeout(10.0, connect=3.0),              # 3 s to connect, 10 s for each read/write/pool wait
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    headers={"User-Agent": "my-app/1.4 (+https://example.com/contact)", "Accept": "application/json"},
)

try:
    r = client.get("/orders", params={"status": "open"})
    r.raise_for_status()
    orders = r.json()
finally:
    client.close()                                         # or: with httpx.Client(...) as client:
```

- `httpx.get(...)` at module level creates and closes a client each call: fine in a one-off script, wasteful in a loop or a service.
- httpx's default timeout is 5 s; requests has no default at all and can wait forever. Always pass one to requests: `session.get(url, timeout=(3.05, 30))`.
- httpx does not follow redirects unless `follow_redirects=True`; requests follows them by default.
- `params=` encodes the query string; never build it with f-strings.

## A small typed API client

Wrapping the raw client in a class with one method per endpoint keeps URLs, status handling and parsing in one place, and gives callers typed results.

```python
from dataclasses import dataclass

import httpx


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(status, message)              # args rebuild the exception when unpickled
        self.status = status
        self.message = message

    def __str__(self) -> str:
        return f"HTTP {self.status}: {self.message}"


@dataclass(frozen=True, slots=True)
class Order:
    id: int
    status: str
    total_cents: int


class ShopClient:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http                                  # injected: tests pass a client with a mock transport

    def get_order(self, order_id: int) -> Order | None:
        r = self._http.get(f"/orders/{order_id}")
        if r.status_code == 404:
            return None
        if r.is_error:
            raise ApiError(r.status_code, r.text[:200])
        data = r.json()
        return Order(id=int(data["id"]), status=str(data["status"]), total_cents=int(data["total_cents"]))
```

Validate response bodies at this boundary (a dataclass built field by field as above, or a pydantic model with `Order.model_validate_json(r.content)`), so a changed API fails here with a clear error rather than deep in the caller.

## Status codes and exceptions

httpx raises for transport problems on its own, but an HTTP error status (4xx, 5xx) is a normal response until you call `raise_for_status()` or check `r.is_error`.

| Exception | Raised when | Typical handling |
|---|---|---|
| `httpx.ConnectTimeout`, `ReadTimeout`, `PoolTimeout` (all `TimeoutException`) | a phase exceeded its timeout | retry with backoff |
| `httpx.ConnectError`, `RemoteProtocolError` (`TransportError`) | DNS failure, refused or reset connection | retry a few times |
| `httpx.HTTPStatusError` | `raise_for_status()` on 4xx or 5xx | 429 and 5xx: retry; other 4xx: fix the request |
| `httpx.HTTPError` | base of all of the above | catch at the reporting boundary |

In requests the same roles belong to `requests.Timeout`, `requests.ConnectionError`, `requests.HTTPError` and their base `requests.RequestException`.

## Retries for transient failures

Retry connection failures, timeouts, 429 and 502/503/504, with exponential backoff and a cap; never retry other 4xx, and retry a `POST` only with an idempotency key the server understands.

```python
import httpx
import stamina                                             # or tenacity; or a short loop


def is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 502, 503, 504}
    return isinstance(exc, httpx.TransportError)


@stamina.retry(on=is_transient, attempts=4, timeout=30.0)
def fetch_json(client: httpx.Client, path: str) -> object:
    r = client.get(path)
    r.raise_for_status()
    return r.json()
```

- `httpx.HTTPTransport(retries=2)` retries only failed connection attempts, never responses; it is safe for every method and cheap to add: `httpx.Client(transport=httpx.HTTPTransport(retries=2))`.
- With requests, urllib3's `Retry` on an adapter covers status codes too: `session.mount("https://", HTTPAdapter(max_retries=Retry(total=4, backoff_factor=0.5, status_forcelist=[429, 502, 503, 504])))`; by default it retries only idempotent methods.
- Respect `Retry-After` on 429 and 503; it says how long to wait.

## Authentication and API keys

Credentials come from the environment or a secrets manager at start-up and travel in headers, never in URLs, logs or source code.

```python
import os

import httpx

token = os.environ["SHOP_API_TOKEN"]                       # fail at start-up when missing
client = httpx.Client(base_url="https://api.example.com", headers={"Authorization": f"Bearer {token}"})

basic = httpx.Client(auth=("user", os.environ["SHOP_PASSWORD"]))   # HTTP Basic
```

- A query-string key (`?api_key=...`) lands in server logs, proxies and browser history; use a header when the API allows it.
- For tokens that expire, subclass `httpx.Auth` and refresh inside `auth_flow`, so every request gets a current token without callers knowing.
- Redact `Authorization` and cookie headers in any request logging.

## Pagination

A paginated endpoint is read with a generator that follows the API's next link or cursor until it ends, so callers iterate over items and never see pages.

```python
from collections.abc import Iterator

import httpx


def iter_orders(client: httpx.Client, status: str) -> Iterator[dict[str, object]]:
    params: dict[str, str] = {"status": status, "limit": "100"}
    while True:
        r = client.get("/orders", params=params)
        r.raise_for_status()
        body = r.json()
        yield from body["items"]
        cursor = body.get("next_cursor")
        if not cursor:
            return
        params["cursor"] = cursor
```

For APIs that use `Link` headers, `r.links.get("next", {}).get("url")` gives the next URL.

## Streaming large downloads

A large file is streamed to disk in chunks, so memory stays flat, and written to a temporary name that is renamed only after the download completes and checks out.

```python
import hashlib
import os
from pathlib import Path

import httpx


def download(client: httpx.Client, url: str, dest: Path, sha256: str | None = None) -> Path:
    tmp = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    with client.stream("GET", url, follow_redirects=True) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=1 << 20):     # 1 MiB
                f.write(chunk)
                digest.update(chunk)
    if sha256 is not None and digest.hexdigest() != sha256:
        tmp.unlink()
        raise ValueError(f"checksum mismatch for {url}")
    os.replace(tmp, dest)
    return dest
```

- Without `stream`, `client.get(url).content` loads the whole body into memory.
- A read timeout applies between chunks, not to the whole download; wrap the call in an overall deadline for very slow servers.
- Uploads: `client.post(url, files={"file": ("report.pdf", path.open("rb"), "application/pdf")})` streams a file object; `content=` takes bytes or an iterator.

## Async client and concurrency

`httpx.AsyncClient` has the same API with `await`; many requests run concurrently on one event loop, and a semaphore keeps the number in flight bounded.

```python
import asyncio

import httpx


async def fetch_all(ids: list[int], limit: int = 10) -> list[dict[str, object]]:
    sem = asyncio.Semaphore(limit)

    async with httpx.AsyncClient(base_url="https://api.example.com", timeout=10.0) as client:

        async def one(order_id: int) -> dict[str, object]:
            async with sem:
                r = await client.get(f"/orders/{order_id}")
                r.raise_for_status()
                return r.json()

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(one(i)) for i in ids]
    return [t.result() for t in tasks]
```

- Never call a sync client (or requests) inside `async def`: each call blocks the whole loop.
- `limits=httpx.Limits(max_connections=...)` caps sockets; the semaphore caps tasks waiting for them, which keeps memory and server load bounded.
- Create the `AsyncClient` inside the running loop (in `main()` or a lifespan), not at import time.

## TLS, private CAs and proxies

Certificate verification stays on; when a server uses a private CA or a corporate proxy re-signs traffic, give the client the right trust store instead of turning checks off.

```python
import ssl

import httpx
import truststore

private_ca = ssl.create_default_context(cafile="/etc/ssl/certs/internal-ca.pem")
internal = httpx.Client(verify=private_ca)                 # httpx 0.28: pass an SSLContext, not a path string

os_store = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # the operating system's trust store (Keychain, Windows, Linux)
public = httpx.Client(verify=os_store)

proxied = httpx.Client(proxy="http://proxy.internal:3128")  # httpx 0.28 removed the old proxies= argument
```

- Never pass `verify=False`; it accepts any certificate, including an attacker's.
- `SSL: CERTIFICATE_VERIFY_FAILED` on a company laptop usually means a proxy re-signs TLS with a company CA that Python's bundle lacks; `truststore` fixes it by using the OS store, where IT installed that CA.
- httpx and requests read `HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY` from the environment by default (`trust_env=True`).

## Logging requests

Event hooks log each request and response once, with method, URL, status and duration, and without headers or bodies that may hold secrets.

The two hooks are in [logging-requests.md](references/logging-requests.md).

The query string is dropped from the logged URL because it often carries tokens or personal data. httpx itself logs every request at `INFO` on the `httpx` logger with the full URL, query included (`HTTP Request: GET https://...?token=... "HTTP/1.1 200 OK"`); set that logger to `WARNING` when the application logs at `INFO`: `logging.getLogger("httpx").setLevel(logging.WARNING)`.

## Testing without the network

Tests replace the transport, so the real client code runs against canned responses with no network and no patching. `httpx.MockTransport` is built in; respx adds route matching.

```python
import httpx

from my_app.shop import ShopClient


def test_get_order_returns_none_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/orders/7"
        return httpx.Response(404, json={"detail": "not found"})

    http = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(handler))
    assert ShopClient(http).get_order(7) is None
```

```python
import httpx
import respx                                               # uv add --dev respx

from my_app.shop import Order, ShopClient


@respx.mock(base_url="https://api.test", assert_all_called=True)
def test_get_order_parses_body(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("/orders/7").respond(200, json={"id": 7, "status": "open", "total_cents": 350})
    http = httpx.Client(base_url="https://api.test")
    assert ShopClient(http).get_order(7) == Order(id=7, status="open", total_cents=350)
```

- `pytest-httpx` (an `httpx_mock` fixture) is another option; `responses` and `requests-mock` do the same for requests.
- Simulate failures too: `httpx.Response(503)`, or `raise httpx.ConnectTimeout("timeout", request=request)` inside the handler.
- One end-to-end test against a sandbox of the real API, run separately from unit tests, catches contract changes that mocks hide.

## Verify

Prove the client with Ruff's request rules, a test run with sockets blocked, and an attack case: a redirect to another host must not carry the token.

```bash
uv run ruff check --select S113,S501 .                 # expect no missing timeout and no verify=False
uv run --with pytest-socket pytest --disable-socket    # a unit test that reaches the network fails
grep -rnE "api[_-]?key=|token=" src                    # expect nothing: keys travel in headers
uv run python - <<'PY'
import httpx

seen: list[httpx.Request] = []


def handler(request: httpx.Request) -> httpx.Response:
    seen.append(request)
    if request.url.host == "api.test":
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})
    return httpx.Response(200)


client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                      headers={"Authorization": "Bearer t"}, timeout=5.0)
client.get("https://api.test/report")
assert "authorization" in seen[0].headers
assert "authorization" not in seen[1].headers          # attack case: dropped on the other host
print("ok")
PY
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `requests.get(url)` with no timeout | one client with a timeout on every call |
| a new client per request | one client per service |
| `verify=False` | `truststore`, or an `SSLContext` with the private CA |
| a query built with an f-string | `params=` |
| an API key in the URL | a header, read from the environment |
| retrying every error | retry transient failures, with a cap |
| patching `httpx.get` in tests | `MockTransport` or respx |

## Pre-merge checklist

An HTTP client is ready when every item holds.

- [ ] One client per service, created at start-up, closed at exit; `base_url` set.
- [ ] Every request has a timeout; connection limits are set for concurrent use.
- [ ] Every response's status is checked; bodies are validated into typed objects at the client.
- [ ] Retries cover transient errors only, with backoff and a cap; writes are retried only with idempotency keys.
- [ ] Credentials come from the environment and travel in headers; logs carry no secrets or query strings.
- [ ] TLS verification is on; private CAs come through an `SSLContext` or truststore.
- [ ] Tests use a mock transport or respx; no unit test touches the network.

## Gotchas

These are the HTTP client problems people ask about.

1. **"My script hangs forever on one request."** requests without `timeout=`. Pass one on every call, or move to httpx, which defaults to 5 s.
2. **"`ResourceWarning: unclosed` or 'Too many open files' in a loop."** A new client or session per request, never closed. Create one and reuse it inside `with`.
3. **"A 404 did not raise."** httpx and requests treat error statuses as normal responses. Call `raise_for_status()` or check `r.is_error`.
4. **"`SSL: CERTIFICATE_VERIFY_FAILED` only on the office network."** A proxy re-signs TLS with a company CA. Use `truststore`, never `verify=False`.
5. **"`TypeError: Client.__init__() got an unexpected keyword argument 'proxies'`" (or `app=`).** Removed in httpx 0.28. Use `proxy=`, and `transport=httpx.ASGITransport(app=app)` for in-process apps.
6. **"DeprecationWarning: `verify=<str>` is deprecated".** httpx 0.28 wants an `ssl.SSLContext`: `verify=ssl.create_default_context(cafile=path)`.
7. **"httpx follows no redirects and returns 301."** Redirects are off by default. Pass `follow_redirects=True` on the client or the call.
8. **"API tokens show up in our logs."** httpx logs each request URL at `INFO`, query string included, and some code logs headers. Raise the `httpx` logger to `WARNING`, send keys in headers, and log through a hook that drops the query.
9. **"Async code is no faster than sync."** A sync client or `requests` call inside `async def` blocks the loop, or there is no concurrency (each `await` in sequence). Use `AsyncClient` with a `TaskGroup` and a semaphore.

## References

The reference file holds the logging recipe.

- [logging-requests.md](references/logging-requests.md): httpx event hooks that log method, URL without its query, status and duration

## See also

- `python-error-handling`: retry policies and exception design
- `python-asyncio`: concurrency limits and timeouts around many requests
- `python-security`: redirects, URL allow-lists and server-side request forgery
- `python-html-xml-parsing`: what to do with the page once fetched
- `python-logging`: redaction and logger levels
