---
title: Structured logging with structlog
author: MOZGII, Inc.
lang: en
---

# Structured logging with structlog

structlog (26.1 in October 2026) builds each log entry as a dictionary that passes through a chain of processors and ends in a renderer: JSON for machines, coloured key-value lines for people. Context is bound once and carried into every later line.

## Contents

This reference has 6 sections.

1. Install and configure
2. Logging with key-value pairs
3. Binding context with contextvars
4. Routing standard-library logs through structlog
5. Testing structlog output
6. Gotchas

## 1. Install and configure

structlog is configured once at start-up with a list of processors; the last one renders the entry.

```python
import logging
import sys

import structlog


def setup_logging(json_output: bool, level: int = logging.INFO) -> None:
    shared = [
        structlog.contextvars.merge_contextvars,             # bound context (request ids)
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if json_output:
        processors = [*shared, structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
    else:
        processors = [*shared, structlog.dev.ConsoleRenderer()]   # colours and pretty tracebacks
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),   # drops lower levels cheaply
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


setup_logging(json_output=not sys.stderr.isatty())
```

`uv add structlog`; add `rich` for nicer development tracebacks.

## 2. Logging with key-value pairs

structlog loggers take an event name and keyword arguments instead of a formatted message.

```python
import structlog

log = structlog.get_logger(__name__)

log.info("order created", order_id=17, total_cents=4200)
log.warning("retrying", attempt=2, delay_s=0.4)
try:
    charge(17)
except GatewayError:
    log.exception("charge failed", order_id=17)
```

JSON output: `{"order_id": 17, "total_cents": 4200, "event": "order created", "level": "info", "timestamp": "2026-10-28T12:33:01.996220Z"}`.

- Use a short, constant event name (`"order created"`) and put variable data in fields, so the aggregator can count and filter events.
- `log.bind(user_id=5)` returns a new logger with that field on every line.
- Async code can call `await log.ainfo(...)` to keep rendering off the event loop when processors are slow.

## 3. Binding context with contextvars

`structlog.contextvars` stores fields per task or request; `merge_contextvars` (first in the chain) adds them to every entry.

```python
import structlog
from structlog.contextvars import bind_contextvars, bound_contextvars, clear_contextvars


async def handle(request: Request) -> Response:
    clear_contextvars()                                   # never inherit a previous request's fields
    bind_contextvars(request_id=request.id, path=request.path)
    return await process(request)


with bound_contextvars(job_id="job-9"):                   # scoped: removed on exit
    run_job()
```

## 4. Routing standard-library logs through structlog

Third-party libraries log through `logging`; `ProcessorFormatter` renders their records with the same processors, so all output looks alike.

```python
import logging
import sys

import structlog

shared = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]

structlog.configure(
    processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(structlog.stdlib.ProcessorFormatter(
    foreign_pre_chain=shared,                              # applied to records from plain logging
    processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, structlog.processors.JSONRenderer()],
))
root = logging.getLogger()
root.handlers[:] = [handler]
root.setLevel(logging.INFO)
```

In this mode the standard `logging` levels and handler configuration apply to structlog loggers too.

## 5. Testing structlog output

`structlog.testing.capture_logs` records entries as dictionaries, which makes assertions exact.

```python
from structlog.testing import capture_logs


def test_order_logged() -> None:
    with capture_logs() as entries:
        create_order(17)
    assert {"event": "order created", "order_id": 17, "log_level": "info"} in entries
```

## 6. Gotchas

These are the structlog surprises seen most.

1. **Fields from the previous request appear in a new one.** Context was bound but never cleared. Call `clear_contextvars()` at the start of each request, or use `bound_contextvars`.
2. **`cache_logger_on_first_use=True` ignores a later `configure`.** Loggers cached before reconfiguration keep the old chain. Configure before the first log call, and disable caching in tests.
3. **Library logs look different from yours.** Plain `logging` records bypass structlog's processors. Use `ProcessorFormatter` with `foreign_pre_chain`.
4. **Tracebacks are one long string in JSON.** Add `structlog.processors.dict_tracebacks` before `JSONRenderer` for structured exception data.
