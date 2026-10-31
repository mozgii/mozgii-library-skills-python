---
title: Testing OpenTelemetry and instrumenting code by hand
author: MOZGII, Inc.
lang: en
---

# Testing OpenTelemetry and instrumenting code by hand

How to prove telemetry works and how to add it where no instrumentation package exists: in-memory exporters in pytest, assertions on spans and metrics, instrumenting your own library with the API only, span links for batch work, baggage, and carrying a trace into a child process. Examples were run with opentelemetry-sdk 1.45.2 on Python 3.14.

## Contents

This reference has 8 sections.

1. pytest fixtures with in-memory exporters
2. Asserting on spans: names, parents, attributes, status
3. Testing metrics with InMemoryMetricReader
4. Instrumenting your own library
5. Span links for batches and fan-in
6. Baggage
7. Carrying a trace into a child process
8. Gotchas

## 1. pytest fixtures with in-memory exporters

An in-memory exporter collects finished spans in a list so a test can inspect them. The global tracer provider can be set only once per process (a second `set_tracer_provider` logs `Overriding of current TracerProvider is not allowed` and is ignored), so set it once per session and clear the exporter before each test.

```python
# conftest.py
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="session", autouse=True)
def _tracer_provider() -> TracerProvider:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))     # synchronous: spans are there when the block ends
    trace.set_tracer_provider(provider)
    return provider


@pytest.fixture
def spans() -> InMemorySpanExporter:
    _EXPORTER.clear()
    return _EXPORTER
```

- Use `SimpleSpanProcessor` in tests; `BatchSpanProcessor` exports later, on its own thread, and assertions race it.
- Code that takes a `tracer_provider` parameter (as instrumentation packages do) can get a fresh provider per test instead of the global one.

## 2. Asserting on spans: names, parents, attributes, status

Tests on telemetry check the contract other people rely on: span names, the parent-child shape, required attributes and error status. They do not check timings.

```python
# test_sync.py
from opentelemetry import trace
from opentelemetry.trace import StatusCode

tracer = trace.get_tracer("invoice_sync")


def sync_batch(ids: list[int]) -> None:                       # the code under test
    with tracer.start_as_current_span("sync_batch", attributes={"batch.size": len(ids)}):
        for invoice_id in ids:
            with tracer.start_as_current_span("sync_invoice", attributes={"invoice.id": invoice_id}):
                if invoice_id < 0:
                    raise ValueError("bad id")


def test_batch_has_one_child_per_invoice(spans) -> None:
    sync_batch([1, 2])
    finished = spans.get_finished_spans()
    batch = next(s for s in finished if s.name == "sync_batch")
    children = [s for s in finished if s.name == "sync_invoice"]
    assert batch.attributes["batch.size"] == 2
    assert [c.attributes["invoice.id"] for c in children] == [1, 2]
    assert all(c.parent.span_id == batch.context.span_id for c in children)


def test_error_marks_span(spans) -> None:
    try:
        sync_batch([-1])
    except ValueError:
        pass
    failed = next(s for s in spans.get_finished_spans() if s.name == "sync_invoice")
    assert failed.status.status_code is StatusCode.ERROR
    assert failed.events[0].name == "exception"
```

Run it with `uv run pytest -q` next to the `conftest.py` above. Spans are listed in the order they finished: children before their parent.

## 3. Testing metrics with InMemoryMetricReader

`InMemoryMetricReader` collects metrics on demand with `get_metrics_data()`. Give the code under test a `MeterProvider` built for the test, since the global meter provider also can be set only once.

```python
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


def count_points(reader: InMemoryMetricReader, name: str) -> dict[tuple, float]:
    out: dict[tuple, float] = {}
    for rm in reader.get_metrics_data().resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    for dp in m.data.data_points:
                        out[tuple(sorted(dp.attributes.items()))] = dp.value
    return out


def test_counter_by_outcome() -> None:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    synced = meter.create_counter("invoices.synced")
    synced.add(2, {"outcome": "ok"})
    synced.add(1, {"outcome": "failed"})
    assert count_points(reader, "invoices.synced") == {(("outcome", "ok"),): 2, (("outcome", "failed"),): 1}


test_counter_by_outcome()
print("passed")
```

To make code testable this way, pass a `Meter` (or the provider) into the component, defaulting to `metrics.get_meter(__name__)`.

## 4. Instrumenting your own library

A library that wants to emit telemetry depends on `opentelemetry-api` only and never configures the SDK; when the application installs no SDK, every call is a no-op costing under 2 µs.

```python
from opentelemetry import trace

from mylib import __version__                                 # your package's version

_tracer = trace.get_tracer("mylib", __version__)             # instrumentation scope: name and version


def convert(path: str) -> bytes:
    with _tracer.start_as_current_span("mylib.convert", attributes={"mylib.input.suffix": path.rsplit(".", 1)[-1]}):
        ...
```

- Prefix span names and custom attributes with the library name; reuse semantic-convention names where one exists.
- Do not log or record arguments that may hold personal data or secrets; the application cannot easily remove them later.
- Make the API an optional dependency (`mylib[otel]`) if the library should stay dependency-free, and fall back to a tiny no-op shim when the import fails.
- Accept a `tracer_provider=` argument for applications that do not use the global provider.

## 5. Span links for batches and fan-in

A span has one parent, but a batch consumer processes messages that came from many traces. A link records "this span is related to that one" without making it the parent, so each producer's trace shows where its message was handled.

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Link

trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer("batcher")

contexts = []
for i in range(3):                                            # three producers, three traces
    with tracer.start_as_current_span(f"enqueue {i}") as span:
        contexts.append(span.get_span_context())

with tracer.start_as_current_span("process_batch",
                                  links=[Link(ctx, {"messaging.batch.index": i}) for i, ctx in enumerate(contexts)]) as batch:
    print(len(batch.links))                                   # 3
```

Links must be given when the span starts (`add_link` exists for later, but samplers only see the start-time links). In a real consumer, extract each message's context with `propagate.extract` and link to `trace.get_current_span(ctx).get_span_context()`.

## 6. Baggage

Baggage is a set of key-value pairs that travels with the context across services (in the `baggage` header) so downstream code can read values set upstream, such as a tenant id. It is not added to spans automatically.

```python
from opentelemetry import baggage, context

token = context.attach(baggage.set_baggage("tenant.id", "acme"))
try:
    print(baggage.get_baggage("tenant.id"))                    # 'acme', here and in services called from here
finally:
    context.detach(token)
print(baggage.get_baggage("tenant.id"))                        # None
```

Baggage is sent to every downstream service, including third-party APIs called with instrumented clients; never put secrets or personal data in it.

## 7. Carrying a trace into a child process

A child process continues the parent's trace when the parent passes the `traceparent` (and `baggage`) values in the child's environment and the child extracts them at start-up. Environment variable names are upper case by convention; the propagators expect lower-case keys, so map them both ways.

```python
# child.py
import os

from opentelemetry import propagate, trace
from opentelemetry.sdk.trace import TracerProvider

trace.set_tracer_provider(TracerProvider())
carrier = {k.lower(): v for k in ("TRACEPARENT", "TRACESTATE", "BAGGAGE") if (v := os.environ.get(k))}
with trace.get_tracer("child").start_as_current_span("child_main", context=propagate.extract(carrier)) as span:
    print(format(span.get_span_context().trace_id, "032x"))
```

```python
# parent.py
import os
import subprocess
import sys

from opentelemetry import propagate, trace
from opentelemetry.sdk.trace import TracerProvider

trace.set_tracer_provider(TracerProvider())
with trace.get_tracer("parent").start_as_current_span("run_child") as span:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    env = os.environ | {k.upper(): v for k, v in carrier.items()}
    out = subprocess.run([sys.executable, "child.py"], env=env, capture_output=True, text=True, check=True).stdout
    print(out.strip() == format(span.get_span_context().trace_id, "032x"))     # True: one trace
```

The same mapping works for any carrier without headers: a JSON field in a queue message, a column in a jobs table, or command-line arguments of a worker.

## 8. Gotchas

These are the testing and manual-instrumentation problems seen most.

1. **Tests see no spans.** A `BatchSpanProcessor` has not exported yet, or the provider was set after the code under test fetched its tracer from a no-op provider. Use `SimpleSpanProcessor`, set the provider in a session fixture, and get tracers lazily or at module import after it.
2. **Spans from the previous test show up.** The exporter was not cleared. Clear it in a function-scoped fixture.
3. **The child process starts a new trace.** The environment keys were passed lower case or not at all, or the child extracted before the SDK was configured. Map the keys and extract after setup.
4. **A library's spans appear only in some applications.** The library configured its own SDK provider. Libraries use the API only.
5. **Links are missing in sampled-out traces.** Sampling happened at the batch span's start without the links. Pass links at start so samplers can use them.
