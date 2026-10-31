---
title: Metrics with prometheus_client and OpenTelemetry
author: MOZGII, Inc.
lang: en
---

# Metrics with prometheus_client and OpenTelemetry

How to expose metrics from Python: when to use `prometheus_client` and when OpenTelemetry metrics, the four Prometheus instrument types, a `/metrics` endpoint, naming and units, histogram buckets, multiprocess servers and process pools, the OTel Prometheus reader, views that change buckets or drop attributes, and keeping cardinality bounded. Examples were run with prometheus-client 0.26.0 and opentelemetry-sdk 1.45.2.

## Contents

This reference has 8 sections.

1. prometheus_client or OpenTelemetry metrics
2. Counters, gauges, histograms and info with prometheus_client
3. Naming, units and the _created series
4. Histogram buckets
5. Multiprocess mode for process pools and pre-fork servers
6. OpenTelemetry metrics on a Prometheus endpoint
7. OpenTelemetry views: buckets and attribute filtering
8. Gotchas

## 1. prometheus_client or OpenTelemetry metrics

Both produce the same numbers; the choice is about where they go and what else the program already uses.

| Situation | Use |
|---|---|
| Prometheus scrapes the service, no tracing planned | `prometheus_client`: small, no SDK, mature multiprocess support |
| the service already sends OTel traces | OTel metrics with OTLP export: one SDK, one resource, exemplars link metrics to traces |
| OTel metrics, but Prometheus must scrape | OTel SDK with `PrometheusMetricReader` (section 6) |
| a library | OTel API only (`opentelemetry-api`); the application decides where metrics go |

## 2. Counters, gauges, histograms and info with prometheus_client

`prometheus_client` keeps metrics in a process-wide registry and serves them as text on `/metrics`. Define each metric once at module level, then record from anywhere; labels select one time series.

```python
import time
import urllib.request

from prometheus_client import Counter, Gauge, Histogram, Info, start_http_server

JOBS = Counter("jobs_processed", "Jobs finished", ["queue", "outcome"])     # exported as jobs_processed_total
IN_FLIGHT = Gauge("jobs_in_flight", "Jobs being processed now")
LATENCY = Histogram("job_duration_seconds", "Time per job", ["queue"],
                    buckets=(0.01, 0.05, 0.1, 0.5, 1, 5, 30))
BUILD = Info("app_build", "Build information")
BUILD.info({"version": "1.4.0"})


def handle(queue: str) -> None:
    with IN_FLIGHT.track_inprogress(), LATENCY.labels(queue).time():
        time.sleep(0.02)
    JOBS.labels(queue=queue, outcome="ok").inc()


server, thread = start_http_server(9464, addr="127.0.0.1")       # returns the server and its thread
handle("email")
body = urllib.request.urlopen("http://127.0.0.1:9464/metrics").read().decode()
print("\n".join(l for l in body.splitlines() if l.startswith(("jobs_", "job_duration_seconds_count", "app_build"))))
server.shutdown()
```

- `start_http_server` serves on a daemon thread; bind to `127.0.0.1` or an internal interface, since metrics reveal internals.
- `Counter.inc()` only goes up; a value that goes down is a `Gauge`. `Gauge.set_function(fn)` reads a value at scrape time.
- Pre-create label combinations you know (`JOBS.labels("email", "ok")`) so the series exist at zero before the first event; alert rules on `rate()` need them.
- `prometheus_client` also registers process metrics (CPU, memory, open files; Linux only) and Python GC metrics by default.
- For an existing WSGI or ASGI app, `make_wsgi_app()` and `make_asgi_app()` return an app to mount at `/metrics`.

## 3. Naming, units and the _created series

Prometheus names are `snake_case` with the unit as a suffix and `_total` on counters; OTel names use dots and a separate `unit`, and the exporters translate them.

| Prometheus | OpenTelemetry instrument |
|---|---|
| `jobs_processed_total` (counter) | `jobs.processed`, unit `{job}` |
| `job_duration_seconds` (histogram) | `job.duration`, unit `s` |
| `queue_depth` (gauge) | `queue.depth`, up-down counter or observable gauge |
| `payload_size_bytes` | `payload.size`, unit `By` |

- Use base units: seconds and bytes, never milliseconds or megabytes.
- Counters export an extra `<name>_created` series with the creation timestamp. Disable it with the environment variable `PROMETHEUS_DISABLE_CREATED_SERIES=True` or `prometheus_client.disable_created_metrics()` when your backend charges per series.
- Help text is required by `prometheus_client`; write what the number means, including the unit.

## 4. Histogram buckets

A histogram counts observations into buckets with upper bounds; quantiles are estimated from the buckets at query time, so the bounds must surround the values you care about.

- The default buckets (5 ms to 10 s) suit web requests. Background jobs, file sizes and queue waits need their own.
- Put more buckets near your target: for a 200 ms objective, `(0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1, 2)`.
- Each bucket is a series per label combination: 10 buckets times 50 label combinations is 500 series for one histogram.
- A `Summary` computes quantiles in the process and cannot be aggregated across instances; prefer histograms.

## 5. Multiprocess mode for process pools and pre-fork servers

Each process has its own registry, so a pool of workers or a pre-fork server (gunicorn) would report only the process that answered the scrape. Multiprocess mode writes every process's values to files in `PROMETHEUS_MULTIPROC_DIR`, and a `MultiProcessCollector` merges them at scrape time.

```python
import os
import tempfile

os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", tempfile.mkdtemp())   # before importing prometheus_client; children inherit it

from concurrent.futures import ProcessPoolExecutor

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest, multiprocess

JOBS = Counter("jobs_processed", "Jobs finished", ["queue"])
WORKERS_BUSY = Gauge("workers_busy", "Workers busy now", multiprocess_mode="livesum")


def work(n: int) -> int:
    WORKERS_BUSY.inc()
    JOBS.labels("email").inc()
    WORKERS_BUSY.dec()
    return n


if __name__ == "__main__":
    with ProcessPoolExecutor(4) as pool:
        list(pool.map(work, range(20)))
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)                         # merges every process's files
    print("\n".join(l for l in generate_latest(registry).decode().splitlines() if l.startswith(("jobs", "workers"))))
    # jobs_processed_total{queue="email"} 20.0
```

- Set the variable with `setdefault` (or outside Python): with the `spawn` start method (macOS, Windows) each child re-imports the module, and an unconditional `mkdtemp()` would give every child its own directory.
- Empty the directory when the service starts; stale files from a previous run are merged too.
- Gauges need a `multiprocess_mode`: `livesum`, `liveall`, `max`, `min`, `sum`, `all` and `mostrecent` variants; `live*` modes drop processes that died, which needs `multiprocess.mark_process_dead(pid)` from the server's worker-exit hook.
- `Info`, `Enum` and custom collectors do not work in multiprocess mode.

## 6. OpenTelemetry metrics on a Prometheus endpoint

`opentelemetry-exporter-prometheus` (0.66b2) provides `PrometheusMetricReader`, which registers OTel metrics with `prometheus_client`'s registry; `start_http_server` then serves both.

```python
import urllib.request

from opentelemetry import metrics
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from prometheus_client import start_http_server

metrics.set_meter_provider(MeterProvider(resource=Resource.create({"service.name": "indexer"}),
                                         metric_readers=[PrometheusMetricReader()]))
server, _ = start_http_server(9465, addr="127.0.0.1")

files = metrics.get_meter("indexer").create_counter("files.indexed", unit="{file}")
files.add(5, {"kind": "pdf"})
body = urllib.request.urlopen("http://127.0.0.1:9465/metrics").read().decode()
print("\n".join(l for l in body.splitlines() if l.startswith(("files_indexed_total", "target_info"))))
server.shutdown()
```

The output contains `files_indexed_total{kind="pdf",otel_scope_name="indexer",...} 5.0` and a `target_info` series carrying the resource attributes (`service_name="indexer"`). Join on `target_info` in queries to filter by service.

## 7. OpenTelemetry views: buckets and attribute filtering

A view changes how the SDK aggregates an instrument without touching the code that records it: different histogram buckets, fewer attributes, a new name, or dropping the instrument. Views are passed to the `MeterProvider`.

```python
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.metrics.view import DropAggregation, ExplicitBucketHistogramAggregation, View

reader = InMemoryMetricReader()
provider = MeterProvider(metric_readers=[reader], views=[
    View(instrument_name="http.client.duration",
         aggregation=ExplicitBucketHistogramAggregation((0.05, 0.1, 0.25, 0.5, 1, 2.5, 5))),
    View(instrument_name="jobs.processed", attribute_keys={"queue"}),     # keep only "queue"
    View(instrument_name="debug.*", aggregation=DropAggregation()),       # wildcard: drop these instruments
])
metrics.set_meter_provider(provider)
meter = metrics.get_meter("demo")
meter.create_histogram("http.client.duration", unit="s").record(0.3)
jobs = meter.create_counter("jobs.processed")
jobs.add(1, {"queue": "email", "job_id": "a1"})
jobs.add(1, {"queue": "email", "job_id": "b2"})

for rm in reader.get_metrics_data().resource_metrics:
    for sm in rm.scope_metrics:
        for m in sm.metrics:
            for dp in m.data.data_points:
                print(m.name, dict(dp.attributes), getattr(dp, "value", None), getattr(dp, "explicit_bounds", None))
# http.client.duration {} None (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5)
# jobs.processed {'queue': 'email'} 2 None
```

`attribute_keys` is the safety net for cardinality: when a library records an attribute with unbounded values, a view keeps only the keys you allow.

## 8. Gotchas

These are the metrics problems seen most.

1. **Prometheus runs out of memory or the bill jumps.** A label with unbounded values (user id, full URL, error message). Remove it, bucket it, or filter it with a view.
2. **Counts drop to zero after a deploy and `rate()` shows spikes.** Counters reset when the process restarts; this is expected. Use `rate()` / `increase()`, never raw counter values, in dashboards.
3. **A pre-fork server reports a different number on every scrape.** Each worker has its own registry. Use multiprocess mode.
4. **`ValueError: Duplicated timeseries in CollectorRegistry`.** The module defining the metric was imported twice (as a script and as a module) or the metric is created inside a function. Define metrics once at module level.
5. **Multiprocess metrics keep growing across restarts.** Old files in `PROMETHEUS_MULTIPROC_DIR`. Clear the directory at startup.
6. **Histogram quantiles are flat or wrong.** Buckets do not surround the observed values (all observations in `+Inf`). Choose buckets from real data.
