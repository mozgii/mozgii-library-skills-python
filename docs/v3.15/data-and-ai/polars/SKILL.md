---
name: polars
description: Writes fast, correct Polars 2.x code - lazy LazyFrame queries versus eager DataFrames, expressions with pl.col and selectors, select, with_columns, filter and group_by with agg, joins with validate and maintain_order, scanning Parquet and CSV lazily with predicate and projection pushdown, the streaming engine that 2.0 made the default for lazy queries and its row-order consequences, window functions with over, rolling and group_by_dynamic, dates, datetimes and time zones, null versus NaN, converting to and from pandas, NumPy and Arrow, performance habits, and choosing between Polars, pandas and DuckDB. Use when processing tabular data faster than pandas, reading large Parquet or CSV files, writing a lazy query pipeline, upgrading from Polars 1.x to 2.x, fixing results whose row order changed, or deciding which DataFrame tool fits a job.
metadata:
  tags:
  - python
  - polars
  - dataframe
  - lazy
  - parquet
  - streaming
  - data-analysis
  - arrow
---

# Polars 2.x DataFrames and lazy queries

Polars is a DataFrame library written in Rust on Apache Arrow memory. Work is written as expressions that the engine plans, optimises and runs on all cores; there is no index and no row-wise Python. Polars 2.0 (October 2026) made the streaming engine the default for lazy queries, which processes data in batches and does not keep row order unless asked. This skill covers the everyday API. Lazy plans, streaming and I/O in depth are in [lazy-and-io.md](references/lazy-and-io.md); time series, windows and interop are in [time-and-interop.md](references/time-and-interop.md). For pandas code, see the pandas skill.

## Step 0: read the versions

Polars changes quickly and 2.0 changed defaults, so read the version first.

```bash
uv run python -c "import sys, polars as pl; print(sys.version_info[:2], pl.__version__)"
uv run python -c "import polars as pl; pl.show_versions()" | head -20
grep -rn "\.profile()\|engine=\"in-memory\"\|POLARS_ENGINE_AFFINITY" src/ | head   # code tied to 1.x behaviour
```

Polars 2.1 (October 2026) supports Python 3.11+. The `polars` package is a thin wrapper over a runtime: `polars-runtime-32` by default, `polars-runtime-64` for tables over about 4.3 billion rows, `polars-runtime-compat` for old CPUs without AVX2 (`uv add "polars[rt64]"` or `"polars[rtcompat]"`).

## Lazy and eager

A `DataFrame` runs each method at once (eager). A `LazyFrame` records the query and runs it at `collect()`, after the optimiser has pushed filters and column selections down to the scan, removed unused work and chosen join strategies. Start from `pl.scan_*` for files, or call `.lazy()` on a DataFrame.

```python
import polars as pl

orders = pl.DataFrame({
    "order_id": [1, 2, 3, 4, 5],
    "customer": ["ann", "bob", "ann", "cy", "bob"],
    "amount": [10.5, 99.0, 25.0, None, 40.0],
    "status": ["paid", "paid", "open", "paid", "paid"],
})
query = (
    orders.lazy()
    .filter(pl.col("status") == "paid")
    .group_by("customer")
    .agg(total=pl.col("amount").sum(), n=pl.len())
    .sort("customer")
)
query.collect().to_dicts()            # [{'customer': 'ann', 'total': 10.5, 'n': 1}, {'customer': 'bob', 'total': 139.0, 'n': 2}, {'customer': 'cy', 'total': 0.0, 'n': 1}]
```

- Eager is fine for small data and notebooks. Pipelines over files should be lazy from the first `scan_*` to one `collect()` (or `sink_*`) at the end.
- `query.explain()` prints the optimised plan; look for the filter inside the scan (`SELECTION`) and the selected columns (`PROJECT`).
- `sum()` of only nulls is `0`, and `mean()` is `null`: `cy` above shows it.

## Expressions with select, with_columns and filter

An expression (`pl.col("amount") * 2`) describes a computation on columns; it runs inside a context. `select` returns only the given expressions, `with_columns` adds or replaces columns and keeps the rest, and `filter` keeps rows where a boolean expression is true.

```python
import polars.selectors as cs

enriched = orders.with_columns(
    amount_eur=pl.col("amount") * 0.92,
    big=pl.col("amount") > 50,
    customer=pl.col("customer").str.to_uppercase(),
)
enriched.select("order_id", cs.numeric() - cs.by_name("order_id")).columns   # ['order_id', 'amount', 'amount_eur']
orders.filter(pl.col("amount").is_between(20, 100), pl.col("status") == "paid").height   # 2
```

- Expressions in one `with_columns` run in parallel and cannot see each other's results; chain a second `with_columns` to use a new column.
- `pl.when(cond).then(a).otherwise(b)` is the conditional; chain `.when(...)` for more branches.
- Selectors (`cs.numeric()`, `cs.string()`, `cs.starts_with("amt_")`, `cs.by_dtype(pl.Datetime)`) pick columns by type or name and combine with `|`, `&`, `-`.
- `pl.col("a").map_elements(f)` runs Python per value, slowly; almost everything has a native expression (`str.*`, `dt.*`, `list.*`, `replace_strict`).

## Group by and aggregation

`group_by(...).agg(...)` takes any number of expressions per group, including filters and sorts inside the group. The output row order is not defined; sort it, or pass `maintain_order=True`.

```python
summary = (
    orders.group_by("status")
    .agg(
        revenue=pl.col("amount").sum(),
        biggest=pl.col("amount").max(),
        customers=pl.col("customer").unique().sort(),
        paid_over_20=pl.col("amount").filter(pl.col("amount") > 20).count(),
    )
    .sort("status")
)
summary.to_dicts()[1]                 # {'status': 'paid', 'revenue': 149.5, 'biggest': 99.0, 'customers': ['ann', 'bob', 'cy'], 'paid_over_20': 2}
```

`pl.len()` counts rows; `pl.col(x).count()` counts non-null values. `.agg(pl.col("amount"))` without a reduction gives a list column of each group's values.

## Joins

`join` matches rows on key columns. Declare the expected cardinality with `validate=` so duplicated keys raise instead of multiplying rows, and use `maintain_order="left"` when the result must keep the left table's order.

```python
customers = pl.DataFrame({"customer": ["ann", "bob", "dee"], "city": ["Oslo", "Lima", "Pune"]})
joined = orders.join(customers, on="customer", how="left", validate="m:1", maintain_order="left")
joined.select("order_id", "city").to_dicts()[3]      # {'order_id': 4, 'city': None}: cy has no customer row
orders.join(customers, on="customer", how="anti").get_column("customer").to_list()   # ['cy']
```

| `how` | Keeps |
|---|---|
| `inner` | matching rows only |
| `left`, `right`, `full` | all rows of one or both sides, nulls where unmatched (`coalesce=True` merges key columns) |
| `semi` | left rows that have a match, without right columns |
| `anti` | left rows without a match: the "what is missing" query |
| `cross` | every pair |

Null keys do not match each other unless `nulls_equal=True`. Key dtypes must agree; cast first (`pl.col("id").cast(pl.Int64)`). `join_asof` matches each row with the nearest earlier key, for prices or sensor readings by time.

## Scanning Parquet and CSV lazily

`pl.scan_parquet` and `pl.scan_csv` return LazyFrames that read only what the query needs: only the selected columns, and for Parquet only the row groups whose statistics can match the filter.

```python
orders.write_parquet("orders.parquet")
paid = (
    pl.scan_parquet("orders.parquet")
    .filter(pl.col("status") == "paid")
    .select("order_id", "amount")
    .sort("order_id")
    .collect()
)
paid.height                           # 4
"SELECTION" in pl.scan_parquet("orders.parquet").filter(pl.col("status") == "paid").explain()   # True: filter pushed into the scan
```

- Globs and lists read many files as one table: `pl.scan_parquet("data/year=*/*.parquet")`, with Hive partitions turned into columns.
- `pl.read_csv` in 2.0 is `scan_csv(...).collect()`; it lost `n_threads`, `batch_size`, `sample_size` and `rechunk`, and `schema_overrides` given as a list must cover every column.
- Give CSV readers a `schema` or `schema_overrides` dict for ids and codes; inference reads the first rows only.
- `sink_parquet("out.parquet")` writes a lazy result without materialising it in memory.

## The streaming engine and row order

In Polars 2, `collect()` on a lazy query runs on the streaming engine by default: it processes data in batches, spills to disk past 80% of RAM, and handles data larger than memory. It does not keep row order for operations that do not require it (joins, `group_by`, `unpivot`). Eager `DataFrame` methods still use the in-memory engine.

- Add `.sort(...)` at the end, or `maintain_order="left"` on joins and `maintain_order=True` on `group_by`, wherever later code depends on order.
- `pl.Config.set_engine_affinity("in-memory")`, `collect(engine="in-memory")` or `POLARS_ENGINE_AFFINITY=in-memory` restore the 1.x engine.
- `LazyFrame.profile()` was removed in 2.0; time `collect()` as a whole and read `explain()`.

## Null and NaN

Polars keeps missing data (`null`) and the floating-point value NaN apart. `null` exists in every dtype and is skipped by aggregations; NaN is a float value that spreads through arithmetic.

```python
vals = pl.DataFrame({"x": [1.0, None, float("nan")]})
vals.select(nulls=pl.col("x").null_count(), nans=pl.col("x").is_nan().sum(), mean=pl.col("x").mean()).row(0)   # (1, 1, nan)
vals.select(pl.col("x").fill_nan(None).mean()).item()   # 1.0: NaN turned into null, then skipped
```

- `pl.from_pandas` turns NaN into null by default (`nan_to_null=True`), because pandas uses NaN for missing values.
- `fill_null(strategy="forward")`, `fill_null(0)` and `drop_nulls(subset=...)` handle missing values; `fill_nan` and `is_nan` handle NaN.

## Converting to pandas, NumPy and Arrow

Polars stores columns in Arrow format, so conversions to Arrow and from NumPy numeric arrays are usually zero-copy; to pandas they copy unless Arrow-backed pandas dtypes are requested.

```python
import numpy as np

pdf = orders.to_pandas()                               # NumPy-backed pandas, copies
pdf_arrow = orders.to_pandas(use_pyarrow_extension_array=True)   # Arrow-backed, cheaper
back = pl.from_pandas(pdf)
arr = orders.get_column("order_id").to_numpy()         # zero-copy for numeric data without nulls
str(pdf_arrow["customer"].dtype), back.equals(orders), arr.dtype   # ('large_string[pyarrow]', True, dtype('int64'))
```

## Choosing Polars, pandas or DuckDB

The three tools overlap; pick by data size, team and the shape of the work.

| Situation | Pick |
|---|---|
| pipelines over large Parquet or CSV files, multi-core, data near or above RAM | Polars lazy with the streaming engine |
| analysts who think in SQL, joins across many files, ad-hoc queries | DuckDB (`duckdb.sql(...)`, results to Polars with `.pl()` or pandas with `.df()`) |
| a library ecosystem that expects pandas (statsmodels, many plotting tools), small data, existing pandas code | pandas 3 |
| a desktop app's small tables | lists of dataclasses, or SQLite |

Polars and DuckDB exchange Arrow data without copies, so a pipeline can use both: DuckDB SQL for one step and Polars expressions for another.

## Verify

Run the tests on both engines, because an order bug shows up only on the streaming one. Then read the plan to prove the filter reached the scan.

```bash
uv run pytest -q                                      # the default: streaming for lazy queries
POLARS_ENGINE_AFFINITY=in-memory uv run pytest -q     # a test that fails on only one engine depends on row order
uv run python -c "import polars as pl; pl.DataFrame({'a': [1]}).write_parquet('/tmp/probe.parquet'); print('SELECTION' in pl.scan_parquet('/tmp/probe.parquet').filter(pl.col('a') > 0).explain())"   # True
grep -rnE "map_elements|\.profile\(\)" src            # expect nothing
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `pl.read_parquet` then filters | `pl.scan_parquet` and one `collect()` at the end |
| `map_elements` for a string or date step | a native `str.*`, `dt.*` or `list.*` expression |
| trusting the order of a join or `group_by` | `.sort(...)` or `maintain_order` |
| `collect()` in the middle of a pipeline | stay lazy; `sink_*` for large results |
| `fill_null` to clear NaN | `fill_nan(None)`, since NaN is a value and not a null |
| a loop over `df.iter_rows()` | an expression over columns |
| `.cast(pl.Date)` on strings | `str.to_date(format)` |

## Checklist

Polars code is correct and fast when every item holds.

- [ ] File pipelines start with `scan_*` and end with one `collect()` or `sink_*`.
- [ ] Order-dependent results sort explicitly or set `maintain_order`; nothing relies on incidental order.
- [ ] No `map_elements` or Python loops over rows where a native expression exists.
- [ ] Joins state `validate=`; key dtypes match; anti joins check for missing partners.
- [ ] CSV reads have a schema for ids, codes and dates.
- [ ] Null and NaN are handled separately; pandas input is converted with `nan_to_null`.
- [ ] Datetimes that name instants carry a time zone (UTC), converted for display.

## Gotchas

These are the Polars problems seen most.

1. **Results came back in a different order after upgrading to 2.0.** The streaming engine does not keep order for joins and group-bys. Sort, or pass `maintain_order`.
2. **`ColumnNotFoundError` for a column created in the same `with_columns`.** Expressions in one context run in parallel. Split into two `with_columns` calls.
3. **`InvalidOperationError: casting from string to date is not supported`.** Polars 2 removed string-to-temporal casts. Use `str.to_date(format)` or `str.to_datetime(format)`.
4. **`SchemaError` on `read_csv(schema_overrides=[...])`.** In 2.0 a list must give a dtype for every column. Pass a dict by name.
5. **A query is slow despite Polars.** `map_elements`, a `collect()` in the middle of a pipeline, or eager reads of whole files. Stay lazy and use native expressions.
6. **Integer and `UInt64` arithmetic produced `Int128`.** Polars 2 changed that supertype from `Float64`. Cast inputs to one integer type first.
7. **`explode` returns fewer rows than in 1.x.** Empty lists now explode to zero rows. Handle empty lists explicitly if one null row per list was expected.

## References

The reference files hold the longer recipes.

- [lazy-and-io.md](references/lazy-and-io.md): reading the query plan, pushdown, streaming and spilling, `sink_*` and partitioned output, CSV and Parquet options, cloud storage, multiple files, the 2.0 upgrade list
- [time-and-interop.md](references/time-and-interop.md): parsing dates, time zones and DST, `dt` methods, window functions with `over`, rolling and `group_by_dynamic`, `join_asof`, pandas, NumPy, Arrow and DuckDB exchange

## See also

- `pandas`: the DataFrame most libraries expect
- `numpy`: arrays exchanged with Polars
- `python-datetime`: time zone rules
- `python-performance`: when a query is still too slow
