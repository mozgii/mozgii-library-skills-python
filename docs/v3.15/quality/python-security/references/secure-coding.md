---
title: Python secure coding recipes
author: MOZGII, Inc.
lang: en
---

# Python secure coding recipes

Tested recipes for the security-sensitive operations that come up in ordinary Python code. Each ran on Python 3.14 on macOS; the standard-library ones need no dependencies.

## Contents

This reference has 9 sections.

1. Containing user-supplied paths
2. Extracting zip and tar archives safely
3. Writing private files atomically
4. Signed tokens with HMAC
5. Checking user-supplied URLs against SSRF
6. Parsing YAML and XML safely
7. Storing passwords
8. Encrypting data at rest
9. Static analysis and audits in CI

## 1. Containing user-supplied paths

Path containment resolves the joined path (following `..` and symlinks) and refuses anything outside the root.

```python
from pathlib import Path


def safe_join(root: Path, user_path: str) -> Path:
    if "\x00" in user_path:                             # resolve() would raise ValueError, a 500 instead of a refusal
        raise PermissionError(f"NUL byte in path: {user_path!r}")
    base = root.resolve()
    target = (base / user_path).resolve()
    if not target.is_relative_to(base):
        raise PermissionError(f"path escapes {base}: {user_path!r}")
    return target


safe_join(Path("/srv/uploads"), "a/b.txt")          # /srv/uploads/a/b.txt
safe_join(Path("/srv/uploads"), "../etc/passwd")    # PermissionError
safe_join(Path("/srv/uploads"), "/etc/passwd")      # PermissionError: an absolute part replaces the base
safe_join(Path("/srv/uploads"), "a\x00b")           # PermissionError: NUL byte
```

A symlink created inside the root after the check can still redirect a later open (time-of-check to time-of-use). Where other users can write into the root, open files with `os.open(..., os.O_NOFOLLOW)` or keep the directory private.

## 2. Extracting zip and tar archives safely

Archive extraction needs a containment check per member and limits on size and count.

```python
import tarfile
import zipfile
from pathlib import Path

MAX_TOTAL = 100 * 2**20          # 100 MiB uncompressed
MAX_FILES = 10_000


def safe_untar(archive: Path, dest: Path) -> None:
    with tarfile.open(archive) as tf:
        members = tf.getmembers()
        if len(members) > MAX_FILES or sum(m.size for m in members) > MAX_TOTAL:
            raise ValueError("archive too large")
        tf.extractall(dest, filter="data")          # refuses absolute paths, "..", outside links, devices


def safe_unzip(archive: Path, dest: Path) -> None:
    base = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_TOTAL:
            raise ValueError("archive too large")
        for info in infos:
            if not (base / info.filename).resolve().is_relative_to(base):
                raise PermissionError(f"unsafe member {info.filename!r}")
        zf.extractall(base)
```

- A tar member such as `../evil.txt` raises `tarfile.OutsideDestinationError` under the `data` filter, which is the default since Python 3.14.
- `file_size` in a zip header is declared by the archive, so a hostile file can lie; for strict limits, stream each member with `zf.open(info)` and count bytes as you copy.

## 3. Writing private files atomically

An atomic write goes to a temporary file in the same directory and is renamed over the target, so readers see the old or the new content, never half.

```python
import os
import tempfile
from pathlib import Path


def atomic_write_private(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")   # created with mode 0600
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())                     # on disk before the rename
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)                        # atomic on POSIX and Windows (same volume)
    except BaseException:
        os.unlink(tmp)
        raise
```

## 4. Signed tokens with HMAC

An HMAC signature proves a value was issued by someone holding the key; add an expiry so stolen tokens stop working.

```python
import base64
import hashlib
import hmac
import json
import time


def issue(payload: dict[str, object], key: bytes, ttl_s: int = 3600) -> str:
    body = base64.urlsafe_b64encode(json.dumps({**payload, "exp": int(time.time()) + ttl_s}).encode()).decode()
    sig = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify(token: str, key: bytes) -> dict[str, object]:
    body, _, sig = token.encode("utf-8", "replace").rpartition(b".")   # bytes: a non-ASCII token cannot raise TypeError
    expected = hmac.new(key, body, hashlib.sha256).hexdigest().encode()
    if not hmac.compare_digest(expected, sig):
        raise PermissionError("bad signature")
    payload: dict[str, object] = json.loads(base64.urlsafe_b64decode(body))
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < time.time():
        raise PermissionError("token expired")
    return payload
```

The payload is readable by anyone; sign, do not hide. For interoperable tokens use a maintained JWT library (`pyjwt`) and pin the accepted algorithms.

## 5. Checking user-supplied URLs against SSRF

Server-side request forgery (SSRF) happens when a user-supplied URL makes the server fetch an internal address such as `127.0.0.1`, `169.254.169.254` (cloud metadata) or a private network.

```python
import ipaddress
import socket
from urllib.parse import urlsplit

EMBEDS_IPV4 = tuple(map(ipaddress.ip_network, (
    "::/96",            # IPv4-compatible
    "::ffff:0:0/96",    # IPv4-mapped
    "::ffff:0:0:0/96",  # IPv4-translated: ::ffff:0:7f00:1 is 127.0.0.1
    "64:ff9b::/96",     # NAT64
)))


def is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    site_local = isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local
    return ip.is_global and not ip.is_multicast and not site_local and not any(ip in net for net in EMBEDS_IPV4)


def check_public_url(url: str) -> str:
    if any(ch == "\\" or ch.isspace() or not ch.isprintable() for ch in url):   # before urlsplit, which strips tabs and newlines
        raise ValueError("backslash, whitespace or control character in URL")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("only https URLs with a host are allowed")
    if "@" in parts.netloc:
        raise ValueError("userinfo in URL")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"{parts.hostname} does not resolve") from exc
    for _family, _type, _proto, _name, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if not is_public(ip):
            raise ValueError(f"{parts.hostname} resolves to a non-public address {ip}")
    return url
```

Parsers disagree, so the backslash and userinfo refusals come first. For `https://127.0.0.1\@example.com/`, `urlsplit` and httpx read the host as `example.com`, while urllib3, and so requests, connects to `127.0.0.1`: the check passes one host and the client fetches another. `urlsplit` also strips tabs and newlines before parsing, so a scan after it sees a clean URL.

`is_global` alone is not enough. It is `True` for multicast (`224.0.0.1`, `ff02::1`), for site-local `fec0::1` (`is_site_local` exists only on `IPv6Address`), for the IPv4-translated `::ffff:0:7f00:1`, for `::7f00:1` (IPv4-compatible) and for `64:ff9b::7f00:1` (the NAT64 prefix), and a NAT64 gateway turns the last one into `127.0.0.1` on its side. On an IPv6-only network with DNS64, where every IPv4 site resolves into `64:ff9b::/96`, read the last 32 bits as an `IPv4Address` and test that address instead of refusing the prefix.

DNS can change between the check and the request (DNS rebinding). For strong protection, connect to the checked IP yourself (pass it to the HTTP client with the original `Host` header), disable redirects, or route outgoing requests through an egress proxy that enforces the rule.

## 6. Parsing YAML and XML safely

Safe parsers build plain data and refuse constructs that run code or read files.

```python
import defusedxml.ElementTree as ET
import yaml

config = yaml.safe_load(text)                     # "!!python/object/apply:os.system" raises ConstructorError
root = ET.fromstring(xml_bytes)                   # external and expanding entities raise EntitiesForbidden
```

`defusedxml` (0.7.1) wraps the standard library parsers; with `lxml`, parse with `resolve_entities=False, no_network=True`.

## 7. Storing passwords

Password storage uses Argon2id from `argon2-cffi` (25.1), with parameters encoded in the hash so they can be raised later.

```python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

ph = PasswordHasher()


def check_and_upgrade(stored: str, password: str) -> tuple[bool, str | None]:
    try:
        ph.verify(stored, password)
    except VerifyMismatchError:
        return False, None
    return True, ph.hash(password) if ph.check_needs_rehash(stored) else None
```

Rate-limit login attempts and lock out after repeated failures; a strong hash slows offline attacks, not online guessing.

## 8. Encrypting data at rest

The `cryptography` package (50.0) provides authenticated encryption; Fernet for simple cases, AES-GCM for formats you control.

```python
import os

from cryptography.fernet import Fernet, MultiFernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

box = MultiFernet([Fernet(new_key), Fernet(old_key)])     # encrypts with new_key, decrypts with either
token = box.encrypt(b"secret")
box.rotate(token)                                         # re-encrypt old tokens under the new key

key = AESGCM.generate_key(bit_length=256)
nonce = os.urandom(12)                                    # unique per message for this key
ciphertext = AESGCM(key).encrypt(nonce, b"payload", b"record-42")   # third argument: authenticated, not secret
plain = AESGCM(key).decrypt(nonce, ciphertext, b"record-42")
```

Keep keys out of the database they protect; load them from a secrets manager or the OS keyring.

## 9. Static analysis and audits in CI

Static analysis catches dangerous calls; audits catch known-vulnerable dependencies. Run both on every pull request.

```bash
uv run ruff check --select S,BLE,TRY src            # flake8-bandit rules in Ruff
uvx bandit -r src -ll -ii                           # Bandit 1.9: medium and higher severity and confidence
uvx semgrep scan --config p/python                  # broader rule packs, including taint tracking
uv audit                                            # OSV check of uv.lock (experimental in uv 0.13)
uv export --format requirements.txt --no-dev --no-emit-project | uvx pip-audit -r /dev/stdin --disable-pip
```

Treat findings like failing tests: fix them, or suppress one with a comment that names the rule and the reason (`# noqa: S603  # args are a fixed list`).
