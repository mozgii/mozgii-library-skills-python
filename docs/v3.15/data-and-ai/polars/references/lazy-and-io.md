---
title: Polars lazy queries, streaming and file I/O
author: MOZGII, Inc.
lang: en
---

# Polars lazy queries, streaming and file I/O

How Polars 2.x plans and runs lazy queries: reading the plan, what the optimiser pushes into the scan, the streaming engine with out-of-core spilling, writing results with `sink_*` and partitioned output, CSV and Parquet options, many files and Hive partitions, and the list of 2.0 changes to check when upgrading from 1.x. Every example ran on Polars 2.1.0.

## Contents

This reference has 8 sections.

1. Reading the query plan
2. Pushdown: what moves into the scan
3. The streaming engine, memory and spilling
4. Writing with sink and PartitionBy
5. Processing results in batches
6. CSV options
7. Many files, Hive partitions and cloud storage
8. Upgrading from Polars 1.x

## 1. Reading the query plan

`LazyFrame.explain()` prints the optimised logical plan as text, from the output at the top to the scans at the bottom. `explain(optimized=False)` shows the plan as written, so the two can be compared.

```python
import polars as pl

pl.DataFrame({
    "id": list(range(1, 9)),
    "kind": ["a", "b"] * 4,
    "value": [1.0, 2.5, 3.0, 4.5, 5.0, 6.5, 7.0, 8.5],
    "note": ["x"] * 8,
}).write_parquet("events.parquet")

lf = pl.scan_parquet("events.parquet").filter(pl.col("kind") == "a").select("id", "value")
plan = lf.explain()
"SELECTION" in plan, "PROJECT 3/4 COLUMNS" in plan                # (True, True)
```

- `PROJECT k/n COLUMNS` on the scan means only k of n columns are read; `SELECTION` means the filter runs while reading.
- `lf.show_graph()` draws the plan with Graphviz; since 2.0 it shows the streaming physical graph by default (`plan_stage="ir"` for the logical one).
- `lf.collect_schema()` returns the output columns and dtypes without running the query: a cheap check in tests.

## 2. Pushdown: what moves into the scan

The optimiser rewrites the query so that less data is read and moved.

| Optimisation | Effect |
|---|---|
| predicate pushdown | filters move into the scan; Parquet row groups whose min/max statistics exclude the filter are skipped |
| projection pushdown | only the columns used anywhere in the query are read |
| slice pushdown | `head(n)` stops reading early |
| common subexpression elimination | an expression used twice is computed once |
| join reordering and runtime filters | cheaper join order; the build side's keys filter the probe side's scan |

Pushdown stops at operations that depend on all rows. A filter written after a window function (`over`), a `sort` with `head`, or a Python UDF (`map_batches`) cannot move past it; put filters as early as the logic allows.

## 3. The streaming engine, memory and spilling

Since Polars 2.0, `LazyFrame.collect()` uses the streaming engine unless told otherwise. It runs the plan as a pipeline of batches across all cores and, when memory use passes 80% of available RAM, spills intermediate data to disk (out-of-core). Operations that hold state, such as group-bys and sorts, can spill.

```python
big = pl.LazyFrame({"k": [i % 1000 for i in range(200_000)], "v": list(range(200_000))})
out = big.group_by("k").agg(total=pl.col("v").sum()).sort("k").collect()           # streaming
same = big.group_by("k").agg(total=pl.col("v").sum()).sort("k").collect(engine="in-memory")
out.equals(same), out.height                                                          # (True, 1000)
```

- Row order is not kept unless the query needs it; sort at the end or pass `maintain_order`.
- If the streaming engine cannot run a query, Polars falls back to the in-memory engine.
- `pl.Config.set_engine_affinity("in-memory")` or the `POLARS_ENGINE_AFFINITY` environment variable change the default process-wide. Eager `DataFrame` operations always run in memory.
- `engine="gpu"` runs on NVIDIA GPUs with `cudf-polars` installed (Linux, unstable); unsupported queries fall back to the CPU.

## 4. Writing with sink and PartitionBy

`sink_parquet`, `sink_csv`, `sink_ipc` and `sink_ndjson` run a lazy query and write its result in batches, so the output never has to fit in memory. Passing `pl.PartitionBy` writes one file or folder per key value.

```python
from pathlib import Path

lf_all = pl.scan_parquet("events.parquet")
lf_all.filter(pl.col("value") > 2).sink_parquet("filtered.parquet")
lf_all.sink_parquet(pl.PartitionBy("by_kind", key="kind"), mkdir=True)
sorted(p.parent.name for p in Path("by_kind").rglob("*.parquet"))                  # ['kind=a', 'kind=b']
pl.scan_parquet("by_kind/**/*.parquet").select(pl.len()).collect().item()           # 8
```

- `PartitionBy(base, key=..., max_rows_per_file=...)` also splits large partitions into several files.
- `sink_*(..., lazy=True)` returns a LazyFrame instead of running at once, so several sinks can run together with `pl.collect_all([...])` and share one scan.
- Write output files to a temporary name and rename on success when other processes read the folder.

## 5. Processing results in batches

When the result itself is too large to hold but each part can be handled in Python (sending rows to an API, writing to a database), `collect_batches` yields it as a sequence of DataFrames.

```python
seen = 0
for batch in big.filter(pl.col("v") % 2 == 0).collect_batches(chunk_size=25_000):
    seen += batch.height                                                              # handle one batch at a time
seen                                                                                  # 100000
```

## 6. CSV options

CSV carries no types, so a schema is the most important option. In Polars 2.0, `read_csv` is implemented as `scan_csv(...).collect()` and the two take the same parameters.

```python
Path("people.csv").write_text("id;name;born;score\n007;Ann;1990-02-01;3,5\n042;Bob;;4,0\n", encoding="utf-8")
people = pl.read_csv(
    "people.csv",
    separator=";",
    decimal_comma=True,                               # 3,5 -> 3.5
    schema_overrides={"id": pl.String, "born": pl.Date},
    null_values=[""],
)
people.dtypes, people.get_column("id").to_list()                                     # ([String, String, Date, Float64], ['007', '042'])
```

- `schema=` gives every column (no inference at all); `schema_overrides=` fixes only some. A list for `schema_overrides` must cover every column since 2.0; use a dict.
- `infer_schema_length=None` scans the whole file for inference, slow but safe for messy files; `infer_schema=False` reads everything as strings.
- `try_parse_dates=True` detects ISO dates; for other formats read as `String` and parse with `str.to_date(format)`.
- `encoding="utf8-lossy"` replaces invalid bytes instead of failing; for other encodings, decode the file first.

## 7. Many files, Hive partitions and cloud storage

Scans accept globs and lists, and read Hive-style folders (`key=value/`) as columns that filters can prune.

```python
hive = pl.scan_parquet("by_kind/**/*.parquet", hive_partitioning=True, include_file_paths="source")
hive.filter(pl.col("kind") == "b").select(pl.len()).collect().item()               # 4: only kind=b files read
```

- `missing_columns="insert"` and `extra_columns="ignore"` on `scan_parquet` let files with slightly different schemas be read together.
- Cloud paths (`s3://`, `gs://`, `az://`, `hf://`) work in every scan and sink; credentials come from the environment or `storage_options=`. Never put keys in code.
- Prefer a few large files (100 MB to 1 GB) over many small ones; per-file overhead dominates with thousands of tiny files.

## 8. Upgrading from Polars 1.x

Polars 2.0 changed defaults that alter results without errors; these are the ones to test.

| Change in 2.0 | What to do |
|---|---|
| lazy queries run on the streaming engine; row order not kept | sort explicitly, `maintain_order=` on joins and group-bys |
| `pl.concat(how="horizontal")` raises on different heights | pad deliberately, or check heights |
| `explode` turns an empty list into zero rows | add a null element first if a row per list is needed |
| signed integer + `UInt64` gives `Int128`, not `Float64` | cast inputs to one integer type |
| `String` cannot be cast to `Date`/`Datetime`/`Time` | `str.to_date`, `str.to_datetime`, `str.to_time` |
| integer <-> categorical casts removed; bool and int operators disallowed | `.cat.to(pl.Enum([...]))` from codes, `.cat.physical()` to codes; compare explicitly |
| `is_in` casts strictly; naive vs aware datetimes in `is_in` raise | match dtypes before `is_in` |
| `LazyFrame.profile()` removed | time `collect()`; read `explain()` |
| `read_csv` lost `n_threads`, `batch_size`, `sample_size`, `rechunk`; `columns=[...]` keeps the requested order | update calls; `.rechunk()` after reading if needed |
| `cut` and `qcut` deprecated | `bin_intervals`, `bin_quantiles`, `bin_ranks` |
| the DataFrame interchange protocol removed | exchange through Arrow (`to_arrow`, `pl.from_arrow`, PyCapsule) |

Run the test suite with `pl.Config.set_engine_affinity("in-memory")` once to tell engine-related differences (order) from other ones.
