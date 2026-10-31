---
name: python-security
description: Writes and reviews Python code for security - command and SQL injection, eval and unsafe deserialization with pickle, yaml and XML, path traversal and archive extraction with tarfile filters and zip checks, temporary files and file permissions, secrets handling with the secrets module and constant-time comparison, password hashing with Argon2 or scrypt, encryption with the cryptography package, TLS verification, SSRF checks for user-supplied URLs, input size limits, safe logging, and supply-chain security with lockfiles, hashes, uv audit, pip-audit, Ruff S rules and Bandit. Use when handling untrusted input, files, archives, URLs, credentials or tokens, reviewing code for vulnerabilities, or hardening dependencies.
metadata:
  tags:
  - python
  - security
  - injection
  - deserialization
  - secrets
  - cryptography
  - path-traversal
  - supply-chain
  - bandit
---

# Python security

Most security bugs in Python code come from a short list: input reaching a shell, a SQL string or a deserializer; paths and archives that escape their folder; secrets in code, logs or weak hashes; and compromised or vulnerable dependencies. This skill gives the safe pattern for each. Tested code recipes are in [secure-coding.md](references/secure-coding.md).

## Step 0: read the versions

Security advice depends on the Python version and on the crypto packages installed, so read them, then ask the audit what is known to be broken.

```bash
uv run python -c "import sys, ssl; print(sys.version_info[:3], ssl.OPENSSL_VERSION)"
uv pip list 2>/dev/null | grep -iE "^(cryptography|argon2-cffi|pyjwt|defusedxml|bandit|pip-audit) "
uv audit                              # or the pip-audit line under Supply chain
```

Current releases (October 2026): cryptography 50.0.2, argon2-cffi 25.1.0, bandit 1.9.4, pip-audit 2.10.1. `tarfile` filters arrived in 3.12 and reached the 3.11 line as a security backport in 3.11.4, so every supported Python has them. `"data"` became the default only in 3.14; older versions default to `fully_trusted`, so the extraction advice below differs by version. Below 3.13.16, 3.12.15 on the 3.12 line or 3.11.17 on the 3.11 line, a crafted archive gets past the filters (gh-157190, CVE-2026-82049), so read the patch version too.

## The rules

These rules prevent the large majority of real vulnerabilities; each section below explains one.

1. Treat every value from outside the process (HTTP, files, env, CLI, databases you do not own, LLM output) as untrusted until validated.
2. Never pass untrusted data to a shell, `eval`/`exec`, `pickle`, `yaml.load`, or a SQL string.
3. Resolve every user-supplied path and check it stays inside its root; extract archives with filters and size limits.
4. Generate tokens with `secrets`, compare them with `hmac.compare_digest`, hash passwords with Argon2id or scrypt.
5. Keep secrets out of source, argv, logs and error messages.
6. Never turn off TLS verification.
7. Lock dependencies with hashes, audit them in CI, and publish with trusted publishing.

## Command injection

Running a command through a shell lets input such as `; rm -rf ~` run as code. Pass an argument list without a shell.

```python
subprocess.run(["convert", "--", user_file, "out.png"], check=True, timeout=60)   # safe
subprocess.run(f"convert {user_file} out.png", shell=True)                       # injectable
```

`os.system`, `os.popen` and `subprocess` with `shell=True` all go through a shell. Details and Windows quoting: the `python-subprocess` skill.

## SQL injection

SQL built with f-strings or `%` formatting lets input rewrite the query. Use the driver's parameters; the values never become SQL text.

```python
cur.execute("SELECT id, email FROM users WHERE email = %s", (email,))           # psycopg: %s placeholders
cur.execute("SELECT id FROM users WHERE email = ?", (email,))                    # sqlite3: ? placeholders
cur.execute(f"SELECT id FROM users WHERE email = '{email}'")                    # injectable
```

- Table and column names cannot be parameters: pick them from an allowlist, or compose them with `psycopg.sql.Identifier`.
- Annotate query parameters as `LiteralString` (the `python-typing` skill) so Pyright rejects f-string SQL.

## eval, exec and deserialization

Some functions execute code embedded in the data they read, so they must never see untrusted input.

| Unsafe on untrusted data | Safe replacement |
|---|---|
| `eval`, `exec`, `compile` | `ast.literal_eval` for Python literals; `json.loads` for data; a real parser for expressions |
| `pickle.load(s)`, `shelve`, `marshal`, `dill`, `joblib.load`, `torch.load(weights_only=False)` | JSON, MessagePack, or `torch.load(..., weights_only=True)` and `safetensors` for model weights |
| `yaml.load(..., Loader=yaml.Loader)`, `yaml.unsafe_load` | `yaml.safe_load` |
| `xml.etree`, `xml.dom.minidom`, `lxml` on untrusted XML | `defusedxml.ElementTree.fromstring` (blocks entity expansion and external entities) |
| `jinja2.Template(user_text)` | fixed templates with `autoescape=True`; user text only as variables; `SandboxedEnvironment` if users write templates |

Signing a pickle with HMAC protects integrity only if the key is secret; prefer a data-only format.

## Paths and archives

A user-supplied path such as `../../etc/passwd` or an absolute path escapes its intended folder unless it is resolved and checked.

```python
from pathlib import Path


def safe_join(root: Path, user_path: str) -> Path:
    if "\x00" in user_path:                             # resolve() would raise ValueError, a 500 instead of a refusal
        raise PermissionError(f"NUL byte in path: {user_path!r}")
    base = root.resolve()
    target = (base / user_path).resolve()              # follows symlinks and ".."
    if not target.is_relative_to(base):
        raise PermissionError(f"path escapes {base}: {user_path!r}")
    return target
```

- Archives carry the same risk in member names. Extract tar files with `tarfile.extractall(dest, filter="data")`; since Python 3.14 `"data"` is the default filter, and it refuses absolute paths, `..`, links outside the destination and device files. Pass it explicitly to be safe on 3.11, 3.12 and 3.13.
- The `"data"` and `"tar"` filters need a floor: Python 3.13.16 or later on 3.13, 3.12.15 or later on 3.12, 3.11.17 or later on 3.11 (gh-157190, CVE-2026-82049). Older releases let a hard link to a symbolic link change the permissions and modification time of a file outside the destination and expose its contents inside the extracted tree. Python 3.14 and 3.15 releases are not affected.
- `zipfile` has no filter: check each member with `safe_join` before extracting, and cap the total uncompressed size and member count against zip bombs ([secure-coding.md](references/secure-coding.md) §2).
- Uploaded file names are untrusted too: generate your own name (`secrets.token_hex(16)` plus a checked extension).

## Temporary files and permissions

Predictable temporary names and default permissions let other local users read or replace files.

- Use `tempfile.mkstemp`, `NamedTemporaryFile` or `TemporaryDirectory`; never `tempfile.mktemp` (a race) or a hand-built `/tmp/my_app.txt`.
- Create secret-bearing files with mode `0o600` and directories with `0o700`, and replace files atomically with `os.replace` so readers never see half a file.
- On Windows, POSIX modes have no effect beyond read-only; protect secrets with the user profile directory or the OS keyring.

## Secrets and tokens

Security tokens must be unpredictable and compared in constant time.

```python
import hashlib
import hmac
import secrets

reset_token = secrets.token_urlsafe(32)               # 256 bits, URL-safe text
api_key = "sk_" + secrets.token_hex(24)


def verify_signature(body: bytes, received: str, key: bytes) -> bool:
    expected = hmac.new(key, body, hashlib.sha256).hexdigest().encode()
    return hmac.compare_digest(expected, received.encode("utf-8", "replace"))   # constant time, bytes on both sides
```

- `hmac.compare_digest` takes two `bytes` or two ASCII-only `str`. A non-ASCII `str` raises `TypeError`, so one crafted header turns into an HTTP 500. Encode both sides first; `"replace"` also survives a lone surrogate, which a plain `.encode()` refuses with `UnicodeEncodeError`.

- The `random` module is predictable; never use it for tokens, passwords or salts.
- Load secrets from the environment, the OS keyring (`keyring`), or a secrets manager at start-up; keep `.env` files out of git; wrap them in `pydantic.SecretStr` so they do not print.
- Store only a hash of long-lived API keys (`hashlib.sha256(key).hexdigest()`), like passwords but without a slow hash: the key is already random.

## Passwords

Passwords need a slow, salted hash designed for them; fast hashes such as SHA-256 let an attacker test billions of guesses per second.

```python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

ph = PasswordHasher()                                 # Argon2id with current recommended parameters


def register(password: str) -> str:
    return ph.hash(password)                          # "$argon2id$v=19$m=65536,t=3,p=4$..." includes the salt


def login(stored: str, password: str) -> bool:
    try:
        ph.verify(stored, password)
    except VerifyMismatchError:
        return False
    return True                                       # then: if ph.check_needs_rehash(stored), store a new hash
```

Without dependencies, `hashlib.scrypt(password.encode(), salt=os.urandom(16), n=2**17, r=8, p=1, maxmem=2**28)` is acceptable; store the salt and parameters with the hash. It takes `bytes`, so a `str` raises `TypeError`. `n=2**17` is the OWASP minimum, and it needs 128 MiB, over the 32 MiB `maxmem` default.

## Encryption

Use the `cryptography` package's high-level recipes; never design a scheme from primitives.

```python
from cryptography.fernet import Fernet

key = Fernet.generate_key()                           # keep it in a secrets manager, not in code
box = Fernet(key)
token = box.encrypt(b"card ending 4242")              # authenticated: tampering is detected
plain = box.decrypt(token, ttl=3600)                  # reject tokens older than an hour
```

`AESGCM` from `cryptography.hazmat.primitives.ciphers.aead` is the next step when you need associated data or a specific format; never reuse a nonce with the same key. `hashlib` is for integrity (SHA-256, BLAKE2); mark non-security uses such as cache keys with `hashlib.md5(data, usedforsecurity=False)`.

## TLS and outgoing requests

HTTPS clients verify certificates by default; disabling it lets anyone on the network read and change the traffic.

- Never pass `verify=False` (requests, httpx) or build an `ssl` context with `CERT_NONE`. For a private CA, pass a context built from its bundle: `httpx.Client(verify=ssl.create_default_context(cafile="/path/ca.pem"))` (httpx 0.28 deprecated plain path strings); requests takes the path: `session.verify = "/path/ca.pem"`.
- `truststore.inject_into_ssl()` makes Python use the operating system's certificate store, which fixes corporate-proxy errors without disabling checks.
- Set timeouts on every request; a call without one can hang a worker forever.
- When the URL comes from a user (webhooks, previews, imports), check it before fetching: `https` only, and the host must resolve to public addresses, or an attacker reaches internal services (SSRF). Recipe in [secure-coding.md](references/secure-coding.md) §5; disable redirects or re-check after each one.

## Input limits

Unbounded input lets one request exhaust memory or CPU.

- Cap request bodies, uploads and files before parsing (`len(body) > MAX` → reject), and cap counts (items per list, rows per import).
- Regular expressions with nested quantifiers (`(a+)+`) can take exponential time on crafted input; bound the input length, simplify the pattern, or use the `regex` package's timeout.
- `int("9" * 10**6)` is limited by `sys.set_int_max_str_digits` (4300 digits by default); keep the limit.
- `assert` is removed under `python -O`: never use it for validation or access checks.

## Logging and errors

Logs and error pages are read by more people than the code, and kept longer.

- Never log secrets, tokens, passwords, full card numbers or whole request bodies (the `python-logging` skill has a redaction filter).
- Return generic errors to clients and log the detail server-side with an id the client can quote.
- Tracebacks in responses reveal paths, versions and sometimes data: catch exceptions at the boundary.

## Supply chain

Dependencies run with your program's full rights, so a compromised or vulnerable package is your vulnerability.

```bash
uv lock && git add uv.lock                         # exact versions and hashes for every platform
uv sync --locked                                   # install exactly the lock
uv audit                                           # OSV vulnerability check (experimental in uv 0.13)
uv export --format requirements.txt --no-dev --no-emit-project | uvx pip-audit -r /dev/stdin --disable-pip   # stable alternative
uvx bandit -r src -ll                              # or: uv run ruff check --select S
```

- `exclude-newer = "7 days"` in `[tool.uv]` delays brand-new uploads, which catches most malicious releases before you install them.
- Use `explicit = true` for private indexes so a public package with the same name cannot replace yours.
- Publish with trusted publishing and attestations (the `python-packaging` skill); enable Dependabot or Renovate for updates.
- Check new dependencies: maintenance, download counts, the exact name (typosquats), and whether you need them at all.

## Verify

Security code is proven by the attacks it refuses, so keep these cases in the test suite. The functions are the ones in this skill and in [secure-coding.md](references/secure-coding.md).

```python
# tests/test_attacks.py
import io
import tarfile
from pathlib import Path

import pytest

from my_app.security import check_public_url, safe_join, verify_signature


@pytest.mark.parametrize("path", ["../../etc/passwd", "/etc/passwd", "a/../../etc/passwd", "link/secret", "a\x00b"])
def test_path_escapes_are_refused(tmp_path: Path, path: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(tmp_path)              # a link inside the root that points out of it
    with pytest.raises(PermissionError):
        safe_join(root, path)


@pytest.mark.parametrize("token", ["", "0" * 64, "\u00e9" * 64, "a\ud800"])
def test_bad_signature_returns_false_and_never_raises(token: str) -> None:
    assert verify_signature(b"body", token, b"key") is False


@pytest.mark.parametrize("url", [
    "http://example.com/", "https://127.0.0.1/", "https://169.254.169.254/latest/meta-data/",
    "https://[::1]/", "https://[::ffff:127.0.0.1]/", "https://[::7f00:1]/", "https://[64:ff9b::7f00:1]/",
    "https://224.0.0.1/", "https://[ff02::1]/", "https://[fec0::1]/", "https://[::ffff:0:7f00:1]/",
    "https://127.0.0.1\\@example.com/", "https://example.com\\@127.0.0.1/", "https://user@127.0.0.1/",
    "https://example.com@example.org/", "https://exa\tmple.com/", "https://exa\nmple.com/", "https://exa mple.com/",
    "https://exa\x00mple.com/", "https://no-such-host.invalid/",
])
def test_internal_urls_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        check_public_url(url)


def test_tar_member_outside_the_folder_is_refused(tmp_path: Path) -> None:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as tar:
        tar.addfile(tarfile.TarInfo("../evil.txt"))
    data.seek(0)
    with tarfile.open(fileobj=data) as tar, pytest.raises(tarfile.FilterError):
        tar.extractall(tmp_path, filter="data")
```

```bash
uv run pytest -q tests/test_attacks.py                 # every case passes
uv run ruff check --select S src                       # expect nothing, or a reasoned ignore
uv audit                                               # expect no known vulnerability
git ls-files | grep -E '(^|/)\.env$'                   # expect nothing
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `hmac.compare_digest(expected, received)` on raw `str` from a client | encode both sides to `bytes`, as in `verify_signature` |
| a denylist of bad characters for paths, SQL or shells | resolve and check paths, bind parameters, pass argument lists |
| `ip.is_global` alone as an SSRF check | refuse multicast, site-local and the prefixes that embed IPv4 too, as `check_public_url` does |
| `assert user.is_admin` | `if not user.is_admin: raise PermissionError`; `python -O` deletes asserts |
| `random.choices(...)` for a token | `secrets.token_urlsafe(32)` |
| a home-made cipher or SHA-256 for passwords | `Fernet` for data, Argon2id for passwords |

## Checklist

Code handling untrusted input passes review when every item holds.

- [ ] No shell, `eval`, `pickle`, `yaml.load` or string-built SQL on untrusted data.
- [ ] Every user path is resolved and checked against its root; archives extracted with filters and limits.
- [ ] Tokens from `secrets`; comparisons with `hmac.compare_digest`; passwords with Argon2id or scrypt.
- [ ] No secrets in code, argv, logs or error responses; `.env` ignored by git.
- [ ] TLS verification on; timeouts on every outgoing call; user URLs checked for SSRF.
- [ ] Input sizes and counts capped before parsing.
- [ ] Lockfile with hashes; `uv audit` or `pip-audit` and Ruff `S` rules in CI.

## Gotchas

These are the security mistakes that look harmless.

1. **`os.path.join(root, user_path)` returns `user_path` when it is absolute.** Joining does not contain. Resolve and check with `is_relative_to`.
2. **A path in `/srv/data-evil` passes the check for `/srv/data`.** `startswith` compares text, and `/srv/data-evil` starts with `/srv/data`. Use `Path.is_relative_to` on resolved paths.
3. **A downloaded model runs code on your machine as it loads.** `torch.load` and `joblib.load` both unpickle. Use `weights_only=True` or safetensors.
4. **An attacker recovers a token byte by byte from response times.** `==` returns early on the first different byte. Use `hmac.compare_digest`.
5. **`TypeError: comparing strings with non-ASCII characters is not supported` from a login or webhook.** A client sent a non-ASCII token and `compare_digest` got a `str`. Encode both sides to `bytes` first.
6. **`CERTIFICATE_VERIFY_FAILED` on an HTTPS call, and `verify=False` looks like a quick way out.** Added "temporarily", it ships. Fix the trust store with `truststore` or a CA bundle.
7. **A dependency's vulnerability is reported but you do not call that code.** Upgrade anyway, or record the reason with `uv audit --ignore <id>`; audits that are always red get ignored.

## References

The reference files hold the longer recipes and the full tool detail.

- [secure-coding.md](references/secure-coding.md): tested recipes for path containment, safe zip and tar extraction, atomic private files, signed tokens, password storage, encryption, safe parsers and SSRF checks

## See also

- `python-subprocess`: argument lists, quoting on Windows and timeouts
- `python-serialization`: safe formats in place of pickle
- `python-http-clients`: timeouts, redirects and TLS on outgoing calls
- `python-logging`: the redaction filter for secrets
- `python-packaging`: trusted publishing and attestations
- `ruff`: the `S` rules that catch much of this in review
