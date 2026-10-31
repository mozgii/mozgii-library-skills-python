---
name: pandas
description: Writes correct, fast pandas 3.x code - what changed in pandas 3 (copy-on-write always on, the str dtype backed by PyArrow, microsecond datetimes, zoneinfo time zones, pd.col expressions, DataFrame.select and filter(cond=) in 3.1, the deprecated inplace keyword), reading CSV and Parquet with explicit dtypes and dtype_backend, selecting with loc and iloc, why chained assignment no longer works and what became of SettingWithCopyWarning, groupby with named aggregation and transform, merge and join pitfalls with validate and indicator, time series with time zones and resample, replacing apply loops with vectorised code, cutting memory with categories and Arrow types, method chaining with assign and pipe, and tests with pandas.testing. Use when loading or cleaning tabular data, upgrading code to pandas 3, fixing ChainedAssignmentError or wrong merge row counts, speeding up slow apply calls, reducing DataFrame memory, handling time zones in a DataFrame, or comparing DataFrames in tests.
metadata:
  tags:
  - python
  - pandas
  - dataframe
  - data-analysis
  - csv
  - parquet
  - time-series
  - pyarrow
---

# pandas 3.x for data work

pandas is the standard DataFrame library for Python: labelled columns, an index, and a large API for cleaning, joining, grouping and time series. pandas 3.0 (January 2026) changed defaults that old code relies on, and 3.1 (October 2026) added more. This skill covers current behaviour and the everyday tasks. The migration from 2.x in detail is in [pandas-3-migration.md](references/pandas-3-migration.md); input, output, memory and speed are in [io-and-performance.md](references/io-and-performance.md). For lazy, multi-core queries on large data, see the polars skill.

## Step 0: read the versions

pandas 2 and pandas 3 behave differently for the same code, so read the version and whether PyArrow is installed.

```bash
uv run python -c "import sys, pandas as pd; print(sys.version_info[:2], pd.__version__)"
uv run python -c "import pyarrow; print(pyarrow.__version__)"   # str columns use it when present
uv run python -c "import pandas as pd; pd.show_versions()" | head -30
```

pandas 3.1 supports Python 3.11+ and needs NumPy 2. Without PyArrow, the `str` dtype falls back to NumPy object storage, which is slower; install `pandas[pyarrow]` or `pyarrow` alongside.

## What changed in pandas 3

These defaults decide how existing code behaves after an upgrade.

| Change | Effect |
|---|---|
| copy-on-write always on | every subset or method result behaves as a copy; chained assignment does nothing and warns `ChainedAssignmentError` |
| `str` dtype by default | string columns are `str` (PyArrow-backed when installed), not `object`; missing values are `NaN` |
| datetime resolution inference | parsed strings and Python datetimes become `datetime64[us]`, not `[ns]`; numeric input too since 3.1 |
| `zoneinfo` time zones | `tz="Europe/Paris"` gives `zoneinfo.ZoneInfo`, not pytz |
| `groupby(observed=True)` default | categorical groupers list only categories that occur |
| `pd.col("a")` (3.0), `DataFrame.select`, `filter(cond=...)` (3.1) | expressions instead of lambdas in `assign`, `loc`, `select`, `filter` |
| `inplace=` deprecated (3.1) | on `drop`, `rename`, `reset_index`, `set_index`, `dropna`, `sort_values` and more; reassign instead |

## Choosing loc, assign or apply, and pandas, Polars or DuckDB

Pick the operation by what it changes, and the library by the size and shape of the data. The table says what goes wrong with the others in each case.

| You need | Use | Why the others lose here |
|---|---|---|
| to change some rows of a column | `df.loc[mask, "col"] = value` | `df[mask]["col"] = value` changes nothing under copy-on-write; `assign` replaces the whole column |
| a new whole column inside a chain | `.assign(total=lambda d: d["units"] * d["price"])` | `loc` mutates the frame and breaks the chain; `apply(axis=1)` calls Python per row |
| per-row logic with no vectorised form, such as a library call per row | `apply`, as the last choice | on 200,000 rows `apply(axis=1)` took 510 ms where `assign` took 0.3 ms, so try the table in "Replacing apply loops" first |
| data that fits in memory, with labelled indexes, time series or a library that expects a DataFrame | pandas | Polars has no index, and DuckDB answers in SQL, so both mean converting at the edges |
| a pipeline over files near or above memory size, in Python expressions | Polars, `scan_parquet` and `collect` | pandas reads everything first; lazy Polars pushes filters and column choices into the scan |
| SQL, or joins and aggregates over Parquet files larger than memory | DuckDB, `duckdb.sql("select k, sum(v) from df group by k")` | a local frame is read by its variable name, and DuckDB can spill to disk, which neither pandas nor eager Polars does |

Speed alone rarely decides it for small data: a group-by over 5,000,000 rows took 20.5 ms in pandas, 16.4 ms in Polars and 9.6 ms in DuckDB (pandas 3.1.0, Polars 2.1.0, DuckDB 2.0.0, Python 3.14, one Mac).

## Reading CSV and Parquet with dtypes

Read with the dtypes the data means, not the ones pandas guesses: an id column with a leading zero, a code that looks numeric, a date. Declaring them also makes reads faster and stops a bad row from turning a column into strings.

```python
import io

import numpy as np
import pandas as pd

csv = io.StringIO("order_id,customer,amount,placed_at,status\n"
                  "0042,ann,10.50,2026-03-28 22:15,paid\n"
                  "0043,bob,,2026-03-29 08:00,open\n"
                  "0044,ann,99.00,2026-03-29 09:30,paid\n")
orders = pd.read_csv(
    csv,
    dtype={"order_id": "str", "customer": "str", "amount": "float64", "status": "category"},
    parse_dates=["placed_at"],
)
orders.dtypes.astype(str).to_dict()   # {'order_id': 'str', 'customer': 'str', 'amount': 'float64', 'placed_at': 'datetime64[us]', 'status': 'category'}
orders["order_id"].iloc[0]            # '0042': leading zero kept
```

- `usecols=[...]` reads only the needed columns; `nrows=` reads a sample to work out dtypes.
- `dtype_backend="pyarrow"` makes every column an Arrow type (`int64[pyarrow]`, `string[pyarrow]`) with real missing values for integers; `"numpy_nullable"` uses `Int64`, `Float64`, `boolean`.
- Parquet keeps dtypes, so prefer it to CSV for anything pandas writes and reads again: `df.to_parquet(path)`, `pd.read_parquet(path, columns=[...], filters=[("status", "==", "paid")])`.
- Large local CSV files (5 MB or more, uncompressed) are read in parallel in 3.1; `pd.option_context("mode.max_threads", 1)` turns it off inside apps that parallelise themselves.

## Selecting with loc and iloc

`loc` selects by label (index values and column names), `iloc` by integer position. Use them for every selection that also assigns, in one step: `df.loc[rows, cols] = value`.

```python
paid = orders.loc[orders["status"] == "paid", ["order_id", "amount"]]   # rows by mask, columns by name
orders.iloc[0, 2]                     # np.float64(10.5): first row, third column
orders.loc[orders["amount"].isna(), "amount"] = 0.0     # one-step assignment: modifies orders
orders["amount"].tolist()             # [10.5, 0.0, 99.0]
```

- `df["col"]` selects a column; `df[mask]` filters rows. Use `loc` whenever both a row condition and columns are involved.
- With a default `RangeIndex`, `loc[0]` and `iloc[0]` coincide only until rows are filtered or sorted. Use `iloc` for "the first row", `loc` for "the row labelled 0".
- `df.at[label, col]` and `df.iat[i, j]` read or write a single value fast.

## Copy-on-write and chained assignment

In pandas 3, any object you derive (a column, a filtered frame, a method result) behaves as an independent copy. pandas shares memory underneath and copies only when one side is written. So modifying a derived object never changes the original, and the only way to change a DataFrame is to assign into it directly.

```python
df = pd.DataFrame({"a": [1, 2, 3], "b": [4.0, 5.0, 6.0]})
subset = df[df["a"] > 1]
subset["b"] = 0.0                     # changes subset only; no warning in pandas 3
df["b"].tolist()                      # [4.0, 5.0, 6.0]
df.loc[df["a"] > 1, "b"] = 0.0        # the way to change df
df["b"].tolist()                      # [4.0, 0.0, 0.0]
```

`df["b"][df["a"] > 1] = 0` (chained assignment) does nothing in pandas 3 and emits `ChainedAssignmentError`. pandas 1.x and 2.x sometimes modified the original through such chains, and warned with `SettingWithCopyWarning`; that warning is gone, and so is the need for defensive `.copy()` calls written to silence it.

## Group by and aggregate

`groupby` splits rows by keys, applies a function per group and combines the result. Named aggregation gives readable output columns; `transform` returns a result aligned with the original rows.

```python
sales = pd.DataFrame({
    "region": ["north", "south", "north", "south", "north"],
    "product": ["a", "a", "b", "b", "a"],
    "units": [3, 5, 2, 7, 4],
    "price": [10.0, 10.0, 25.0, 25.0, 10.0],
})
summary = (
    sales.assign(revenue=pd.col("units") * pd.col("price"))
    .groupby("region", as_index=False)
    .agg(units=("units", "sum"), revenue=("revenue", "sum"), orders=("units", "size"))
)
summary.to_dict("records")            # [{'region': 'north', 'units': 9, 'revenue': 120.0, 'orders': 3}, {'region': 'south', 'units': 12, 'revenue': 225.0, 'orders': 2}]
sales.groupby("region")["units"].transform("sum").tolist()   # [9, 12, 9, 12, 9]: per-row group total
```

- Pass built-in names (`"sum"`, `"mean"`, `"size"`, `"nunique"`) rather than Python functions: they run in compiled code.
- `dropna=False` keeps rows whose key is missing as their own group; by default they are dropped.
- `groupby(...).apply(func)` calls Python per group; reserve it for logic that no built-in aggregation or `transform` can express.

## Merge and join pitfalls

`pd.merge` joins on key columns like SQL. The common failure is silent: duplicate keys multiply rows, or keys of different dtypes match nothing.

```python
customers = pd.DataFrame({"customer": ["ann", "bob"], "city": ["Oslo", "Lima"]})
joined = orders.merge(customers, on="customer", how="left", validate="many_to_one", indicator=True)
len(joined) == len(orders)            # True: validate guarantees no row multiplication
joined["_merge"].value_counts().to_dict()   # {'both': 3, 'left_only': 0, 'right_only': 0}
```

- `validate="one_to_one"`, `"many_to_one"` or `"one_to_many"` raises `MergeError` when keys are duplicated where they must not be.
- `indicator=True` adds `_merge`, showing rows that found no partner; check it after every left join of outside data.
- Normalise key dtypes and spelling before merging: `"0042"` (str) never matches `42` (int), and `"Ann "` never matches `"ann"`.
- `how="inner"` silently drops unmatched rows. Count rows before and after.
- Overlapping column names get `_x` and `_y`; pass `suffixes=("", "_customer")` to name them.

## Time series and time zones

Timestamps that name instants are stored as UTC-aware columns; conversion to local time happens for display or for local-calendar grouping. Time zone rules are the python-datetime skill.

```python
events = pd.DataFrame({
    "at": pd.to_datetime(["2026-03-28T21:30:00Z", "2026-03-29T00:30:00Z", "2026-03-29T08:00:00Z"], utc=True),
    "clicks": [5, 3, 9],
})
local = events.assign(at=pd.col("at").dt.tz_convert("Europe/Paris"))
local["at"].dt.strftime("%Y-%m-%d %H:%M %Z").tolist()   # ['2026-03-28 22:30 CET', '2026-03-29 01:30 CET', '2026-03-29 10:00 CEST']
local.set_index("at").resample("D")["clicks"].sum().tolist()   # [5, 12]: days in Paris time
```

- `pd.to_datetime(..., utc=True)` gives an aware UTC column and accepts mixed offsets; without it, mixed offsets raise `ValueError: Mixed timezones detected`.
- `tz_localize("Europe/Paris")` attaches a zone to naive local readings (`ambiguous=` and `nonexistent=` for DST folds and gaps); `tz_convert` converts aware values.
- pandas 3 uses `datetime64[us]`; code comparing `.dtype == "datetime64[ns]"` breaks. Use `pd.api.types.is_datetime64_any_dtype`.

## Replacing apply loops

`df.apply(func, axis=1)` and `Series.apply` call a Python function per row, which is often 100 times slower than the vectorised equivalent.

```python
df = pd.DataFrame({"units": [3, 12, 40], "price": [10.0, 8.0, 7.5]})
df["tier"] = np.select([df["units"] >= 30, df["units"] >= 10], ["bulk", "medium"], default="small")
df["total"] = df["units"] * df["price"]
df["label"] = df["tier"].str.upper() + ":" + df["units"].astype("str")
df["label"].tolist()                  # ['SMALL:3', 'MEDIUM:12', 'BULK:40']
```

| Row-wise Python | Vectorised |
|---|---|
| if/elif per row | `np.select`, `np.where`, `Series.where`, `Series.mask` |
| dict lookup per value | `Series.map(mapping)` (unmatched become NaN) |
| string formatting per row | `.str` accessor, string concatenation of columns |
| date parts per row | `.dt.year`, `.dt.floor("h")`, `.dt.tz_convert` |
| binning with if/else | `pd.cut`, `pd.qcut` |

## Method chaining

A chain of methods, each returning a new DataFrame, reads top to bottom and leaves no half-modified intermediate variables. `assign` with `pd.col`, `pipe` for custom steps and `query` for simple filters keep it readable.

```python
def add_margin(frame: pd.DataFrame, rate: float) -> pd.DataFrame:
    return frame.assign(margin=pd.col("total") * rate)


result = (
    df.filter(pd.col("units") > 5)                     # 3.1: filter by a boolean expression
    .pipe(add_margin, rate=0.2)
    .sort_values("total", ascending=False)
    .select("tier", "total", "margin")                 # 3.1: choose and order columns
)
result.to_dict("records")             # [{'tier': 'bulk', 'total': 300.0, 'margin': 60.0}, {'tier': 'medium', 'total': 96.0, 'margin': 19.200000000000003}]
```

On pandas 3.0, use `df[df["units"] > 5]` or `df.loc[pd.col("units") > 5]` and `df[["tier", "total", "margin"]]` instead of `filter` and `select`.

## Testing with pandas.testing

`pandas.testing` compares DataFrames, Series and indexes and reports the first difference in values, dtypes or labels. Plain `==` compares element-wise and is the wrong tool in a test.

```python
from pandas.testing import assert_frame_equal

expected = pd.DataFrame({"tier": ["bulk", "medium"], "total": [300.0, 96.0]})
actual = result[["tier", "total"]].reset_index(drop=True)
assert_frame_equal(actual, expected)                  # passes: same values, dtypes and index
assert_frame_equal(actual.astype({"total": "float32"}), expected, check_dtype=False)   # values only
```

- `check_exact=False` with `rtol=` for float results; `check_like=True` ignores column and row order.
- Build expected frames with explicit dtypes; a test written on pandas 2 that expected `object` for strings fails on pandas 3 until it says `str`.

## Verify

pandas 3 reports most old habits as warnings, so turn those warnings into failures for the test run.

```bash
uv run python -c "import pandas as pd, pyarrow; print(pd.__version__, pyarrow.__version__)"
uv run pytest -q -W error::pandas.errors.ChainedAssignmentError -W error::pandas.errors.Pandas4Warning
grep -rnE "inplace=True|apply\(.*axis=1" src         # expect nothing in hot paths
grep -rn "\.merge(" src | grep -v "validate="        # expect nothing: every merge states its keys
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `df["a"][mask] = v` | `df.loc[mask, "a"] = v` |
| `inplace=True` | `df = df.reset_index()` |
| `df.apply(f, axis=1)` | a vectorised expression from the table above |
| a defensive `.copy()` on every subset | nothing: copy-on-write makes the subset safe |
| `pd.read_csv(path)` with guessed dtypes | `dtype=` and `parse_dates=` |
| a merge with no `validate=` | `validate="many_to_one"`, plus `indicator=True` on outside data |
| `assert df1.equals(df2)` in a test | `assert_frame_equal`, which names the first difference |
| CSV for data you read back | Parquet |

## Checklist

pandas code is correct when every item holds.

- [ ] Reads declare dtypes and dates; data that pandas writes and reads again is Parquet.
- [ ] Every assignment is one step (`df.loc[mask, col] = ...`); no chained assignment, no `inplace=`.
- [ ] Merges use `validate=` and are checked with `indicator=True` or row counts.
- [ ] Instants are UTC-aware columns; local time is a conversion for display or grouping.
- [ ] No `apply(axis=1)` in hot paths; built-in aggregations by name.
- [ ] Code does not depend on `object` dtype for strings or on nanosecond resolution.
- [ ] Tests use `assert_frame_equal` with explicit expected dtypes.

## Gotchas

These are the pandas problems seen most.

1. **`ChainedAssignmentError` and the DataFrame is unchanged.** `df["a"][mask] = v` assigns to a temporary copy. Use `df.loc[mask, "a"] = v`.
2. **A merge returns more rows than the left table.** Duplicate keys on the right. Pass `validate="many_to_one"` and deduplicate.
3. **A merge matches nothing.** Keys differ in dtype or formatting. Cast and normalise both sides first.
4. **Code that checked `dtype == object` for strings stopped working.** pandas 3 uses `str`. Test with `pd.api.types.is_string_dtype`.
5. **Datetimes are off by hours.** Naive timestamps were treated as UTC or local inconsistently. Parse with `utc=True` and convert at the edge.
6. **`Pandas4Warning: The inplace keyword ... is deprecated`.** pandas 3.1 deprecated it. Write `df = df.reset_index()`.
7. **An integer column became float after a merge or reindex.** NaN appeared in a NumPy integer column. Use `Int64` or `dtype_backend="pyarrow"`.

## References

The reference files hold the longer recipes.

- [pandas-3-migration.md](references/pandas-3-migration.md): upgrading from 2.x through 2.3, copy-on-write in detail, the `str` dtype and its NaN semantics, datetime resolution, removed APIs, the 3.1 deprecations
- [io-and-performance.md](references/io-and-performance.md): CSV options, Parquet and Arrow, reading in chunks, memory with categories and Arrow types, `memory_usage(deep=True)`, speed habits, when to switch to Polars or DuckDB

## See also

- `numpy`: the arrays and dtypes under pandas
- `polars`: lazy queries for data too large for pandas
- `python-datetime`: time zone rules behind `tz_convert`
- `python-performance`: profiling a slow pipeline
- `pytest-testing`: where `assert_frame_equal` runs
