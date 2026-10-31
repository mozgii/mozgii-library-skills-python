---
name: python-logging
description: Sets up logging in Python libraries and applications with the standard logging module and structlog - getLogger(__name__) in libraries and no configuration there, one dictConfig at the application entry point, levels, handlers to stderr, JSON output for machines and readable text for people, lazy %-formatting, log.exception, extra fields, request ids through contextvars, QueueHandler for slow handlers, capturing warnings, keeping secrets out of logs, and testing logs with caplog. Use when adding logging, choosing log levels, producing JSON logs, adding context such as a request id, silencing a noisy library, or when log lines are missing or duplicated.
metadata:
  tags:
  - python
  - logging
  - structlog
  - json-logs
  - observability
  - contextvars
  - dictconfig
---

# Python logging

Python's `logging` module separates producing a log record (loggers, in library and application code) from deciding where it goes (handlers and formatters, configured once by the application). Most logging bugs come from mixing the two. This skill sets up both halves; structlog, a structured-logging layer on top, is in [structlog.md](references/structlog.md).

## Step 0: read the version and what already configures logging

Two configurations in one process cause most missing and doubled lines, so find every one before adding yours.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
grep -n "requires-python" pyproject.toml
grep -rnE "basicConfig|dictConfig|addHandler|setLevel" src
uv pip list 2>/dev/null | grep -iE "^(structlog|python-json-logger|logfire) "
```

| Version | Change that matters |
|---|---|
| 3.12 | `logging.getHandlerByName`; dictConfig builds the listener for a `QueueHandler` |
| 3.14 | `QueueListener` works as a context manager; handlers passing `strm=` are deprecated |

## Choose how to log

Pick by who reads the output and how much context it carries.

| Situation | Use | Why the others lose here |
|---|---|---|
| a short script or CLI | `logging.basicConfig` | dictConfig is more than one stream needs |
| a service with one stream | `dictConfig` and the JSON formatter below | `print` has no levels, names or structure |
| many context fields, processors, pretty output in development | structlog | the standard library has no processor chain |
| traces and metrics beside the logs | OpenTelemetry (`python-observability-otel`) | log lines alone cannot join a trace |

## The two rules

Logging is correct when libraries only emit and the application alone configures.

| Code | Does | Never does |
|---|---|---|
| library or module | `log = logging.getLogger(__name__)`, then `log.info(...)` | `basicConfig`, add handlers, set levels, `print` |
| application entry point | configures handlers, formatters and levels once, before other work | configure again in imported modules |

A library that wants silence when the application configures nothing needs no action: Python's last-resort handler prints only `WARNING` and above to stderr.

## Emitting log records

A module gets a logger named after itself, so the hierarchy (`my_app.orders.api`) lets the application tune whole subtrees.

```python
import logging

from my_app.payments import GatewayError, gateway

log = logging.getLogger(__name__)


def charge(order_id: int, cents: int) -> None:
    log.info("charging order %s for %d cents", order_id, cents)     # formatted only if INFO is enabled
    try:
        gateway.charge(order_id, cents)
    except GatewayError:
        log.exception("charge failed for order %s", order_id)        # ERROR level plus the traceback
        raise
    log.debug("charge done", extra={"order_id": order_id})           # extra: fields for structured output
```

- Pass arguments separately (`"%s", value`), not as an f-string: the message is built only when the record is emitted, and log aggregators group identical templates. Ruff's `G` and `LOG` rules check this.
- `log.exception(...)` inside an `except` block logs the traceback; elsewhere use `log.error(..., exc_info=True)`.
- `extra={...}` adds attributes to the record; the key must not clash with a built-in attribute (`message`, `args`, `name`), or logging raises `KeyError`.
- `stacklevel=2` makes a logging helper report its caller's file and line.

## Levels

Pick the level by who must act on the message.

| Level | Use for | Example |
|---|---|---|
| `DEBUG` | detail for a developer chasing a bug | request and response bodies, chosen branches |
| `INFO` | normal milestones an operator might want to see | service started, job finished in 3.2 s |
| `WARNING` | something unexpected that the program handled | retrying after a timeout, deprecated config key |
| `ERROR` | an operation failed; someone may need to act | payment failed, file could not be written |
| `CRITICAL` | the program cannot continue | database unreachable at start-up |

Production usually runs at `INFO`; set noisy third-party loggers to `WARNING` instead of lowering your own.

## Configure once with dictConfig

The application configures logging with one `logging.config.dictConfig` call at start-up; it replaces `basicConfig` once there is more than one handler or format.

```python
import logging
import logging.config

from my_app.logs import JsonFormatter, RequestIdFilter          # both defined below


def setup_logging(level: str = "INFO", json_output: bool = True) -> None:
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,          # keep loggers created at import time
        "filters": {"request_id": {"()": RequestIdFilter}},
        "formatters": {
            "json": {"()": JsonFormatter},
            "text": {"format": "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"},
        },
        "handlers": {
            "stderr": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "formatter": "json" if json_output else "text",
                "filters": ["request_id"],
            },
        },
        "loggers": {"httpx": {"level": "WARNING"}, "urllib3": {"level": "WARNING"}},
        "root": {"level": level, "handlers": ["stderr"]},
    })
    logging.captureWarnings(True)                   # warnings.warn(...) becomes log records
```

- Handlers attach to the root logger; module loggers propagate to it. Adding handlers to module loggers as well prints every line twice.
- `disable_existing_loggers` defaults to `True`, which silences every logger created before the call (most libraries create theirs at import). Always set it to `False`.
- Log to stderr (or stdout in containers that expect it) and let the platform collect it; write files only when there is no collector, through `RotatingFileHandler` or `TimedRotatingFileHandler`.
- Choose JSON or text from the environment: JSON when output goes to a machine, text when a person reads a terminal (`sys.stderr.isatty()`).

## JSON output

A JSON formatter turns each record into one line that log systems can index; the standard library has no built-in one, so a short formatter does it.

```python
import json
import logging
from datetime import UTC, datetime

_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update({k: v for k, v in vars(record).items() if k not in _STANDARD})   # extra= fields
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)
```

Output: `{"ts": "2026-10-28T12:33:01.839+00:00", "level": "info", "logger": "my_app.orders", "msg": "order created", "order_id": 17, "request_id": "req-42"}`. Packages such as `python-json-logger` or structlog do the same with more options.

## Context: request ids with contextvars

A context variable carries a request or job id through every function and task without passing it as an argument; a filter copies it into each record.

```python
import contextvars
import logging
import uuid

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


def handle(headers: dict[str, str]) -> None:
    token = request_id.set(headers.get("x-request-id") or uuid.uuid4().hex)
    try:
        logging.getLogger(__name__).info("handling")   # every log line below carries request_id
    finally:
        request_id.reset(token)
```

Context variables are copied into each asyncio task and `to_thread` call, so concurrent requests keep their own ids. Attach the filter to the handler, not a logger, so records from every library get the field.

## Slow handlers: QueueHandler

A handler that blocks (network, slow disk) slows every log call; a `QueueHandler` hands records to a background thread instead.

```python
import logging.config

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "remote": {"class": "logging.handlers.SocketHandler", "host": "127.0.0.1", "port": 9020},
        "queued": {
            "class": "logging.handlers.QueueHandler",
            "handlers": ["remote"],          # 3.12+: dictConfig creates the QueueListener for these
            "respect_handler_level": True,
        },
    },
    "root": {"level": "INFO", "handlers": ["queued"]},
})
```

dictConfig creates the listener but does not start it; start it right after configuring, and stop it at exit so queued records are flushed:

```python
import atexit
import logging

listener = logging.getHandlerByName("queued").listener      # Python 3.12+
listener.start()
atexit.register(listener.stop)
```

Without `start()` every record waits in the queue and nothing is written. `os._exit` skips `atexit`, so queued records are lost there.

## Secrets and personal data

Logs are copied, shipped and kept for months, so a secret in a log is a leaked secret.

- Never log passwords, tokens, API keys, session cookies, full card numbers or `Authorization` headers; log an id or the last four characters.
- Never log whole request or response bodies at `INFO`; at `DEBUG`, redact known fields first.
- A redaction filter is a safety net, not a plan:

```python
import logging
import re

SECRET = re.compile(
    r"""(?isx)
    (authorization|api[_-]?key|token|password|passwd|secret)(['"]?\s*[:=]\s*)
    (?:
        b?(?P<quote>['"])(?:\\.|(?!(?P=quote)).)*(?:(?P=quote)|\Z)    # quoted: spaces and escaped quotes included
      | (?:\w+\s+)?[^\s'",]+                                          # bare, after an optional scheme: Bearer abc
    )
    """
)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = SECRET.sub(r"\1\2***", record.getMessage())
        record.args = None
        return True
```

- A quoted value is matched to its closing quote, so `password="two words"` loses both words; an unclosed quote is redacted to the end of the message.
- The filter sees the message only: `extra` fields and tracebacks pass through it. A bare value of three or more words, or one holding a comma, still leaks past the first part, which is why logging the secret in the first place is the bug.

## Testing log output

pytest's `caplog` fixture captures records, so tests can assert that an important event is logged.

```python
import logging

import pytest

from my_app.client import fetch_with_retry


def test_retry_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="my_app.client"):
        fetch_with_retry()
    assert any(r.levelno == logging.WARNING and "retrying" in r.getMessage() for r in caplog.records)
```

Assert on the record (level, logger, message, extra fields), not on the formatted text, so changing the format does not break tests.

## Verify

Prove the setup end to end: run it, parse a line as JSON, and feed the redaction filter a real secret as an attack case.

```bash
uv run ruff check --select G,LOG,T20 .                 # expect no f-string message and no print
uv run python - <<'PY'
import contextlib
import io
import json
import logging

from my_app.logs import RedactFilter, request_id, setup_logging

stream = io.StringIO()
with contextlib.redirect_stderr(stream):
    setup_logging(level="INFO", json_output=True)      # ext://sys.stderr is read here
    token = request_id.set("req-42")
    logging.getLogger("my_app.orders").info("order created", extra={"order_id": 17})
    request_id.reset(token)
record = json.loads(stream.getvalue().splitlines()[0])
assert record["request_id"] == "req-42" and record["order_id"] == 17

ATTACKS = [
    "Authorization: Bearer s3cret", "Authorization: Basic s3cret==", "Authorization: Token s3cret",
    'password="s3cret s3cret"', "password='s3cret s3cret'", '{"password": "s3cret s3cret", "id": 1}',
    "{'api_key': 's3cret s3cret'}", 'password="a\\"b s3cret"', 'password="unclosed s3cret',
    'password="line\ns3cret"', "token=b's3cret s3cret'", "client_secret=s3cret", "access_token=s3cret&x=1",
]
for attack in ATTACKS:                                 # each secret must never reach a handler
    leak = logging.makeLogRecord({"msg": attack, "args": None})
    RedactFilter().filter(leak)
    assert "s3cret" not in leak.getMessage(), attack
plain = logging.makeLogRecord({"msg": "token expired at noon", "args": None})
RedactFilter().filter(plain)
assert plain.getMessage() == "token expired at noon"   # ordinary text stays readable
print("ok")
PY
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `log.info(f"order {order_id}")` | `log.info("order %s", order_id)` |
| `logging.info(...)` on the root logger | a module logger from `getLogger(__name__)` |
| `basicConfig` in a library | no configuration in libraries |
| `except Exception as e: log.error(e)` | `log.exception(...)`, which keeps the traceback |
| `print` in code that stays | a logger at `DEBUG` |
| handlers on many loggers | handlers on the root only |
| whole request bodies at `INFO` | ids, with bodies redacted at `DEBUG` |

## Pre-merge checklist

Logging is set up when every item holds.

- [ ] Every module uses `logging.getLogger(__name__)`; no library code configures logging.
- [ ] One `dictConfig` at the entry point with `disable_existing_loggers: False`.
- [ ] Messages use `%s` arguments, not f-strings; exceptions use `log.exception`.
- [ ] Output is JSON for machines and text for terminals; noisy libraries sit at `WARNING`.
- [ ] A request or job id reaches every line through a context variable.
- [ ] No secrets or personal data in messages or extra fields.

## Gotchas

These are the logging problems people hit most.

1. **Nothing is logged.** No handler configured and the level is below `WARNING`, or `disable_existing_loggers` silenced the module's logger. Configure at start-up with `disable_existing_loggers: False`.
2. **Every line appears twice.** Handlers on both a module logger and the root logger, or `basicConfig` called after `dictConfig`. Attach handlers to the root only.
3. **`basicConfig` does nothing.** The root already has a handler (an import configured logging). Pass `force=True`, or find and remove the other configuration.
4. **`KeyError: "Attempt to overwrite 'name' in LogRecord"`.** An `extra` key clashes with a record attribute. Rename it (`job_name`).
5. **Logs vanish, or vanish at exit.** A `QueueHandler`'s listener was never started, or the process ended with `os._exit` before the listener stopped. Start the listener after `dictConfig` and stop it with `atexit`.
6. **A library floods the output.** Its logger inherits the root level. Set `"loggers": {"noisy_lib": {"level": "WARNING"}}`.
7. **Python 3.14 and later warn about `strm`.** Custom handlers passing `strm=` are deprecated (removal in 3.16); use `stream=`.

## References

The reference files hold the longer recipes and the full tool detail.

- [structlog.md](references/structlog.md): structlog configuration for development and production, context binding, and routing standard-library records through it

## See also

- `python-observability-otel`: traces, metrics and log correlation
- `python-error-handling`: where exceptions are caught and reported
- `python-debugging`: when a log line is the fastest check
- `python-http-clients`: request logging without secrets
- `python-security`: what must never be stored
