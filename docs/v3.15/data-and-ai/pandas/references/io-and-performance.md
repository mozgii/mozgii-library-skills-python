---
title: pandas input, output, memory and speed
author: MOZGII, Inc.
lang: en
---

# pandas input, output, memory and speed

How to read and write data with pandas 3 without losing types or memory: CSV options for messy files, reading in chunks, Parquet and Arrow, measuring and cutting memory with categories, smaller numbers and Arrow types, the speed habits that matter, and when to hand the job to Polars or DuckDB. Every example ran on pandas 3.1.0 with PyArrow 26.

## Contents

This reference has 8 sections.

1. CSV options for messy files
2. Reading large CSV files in chunks
3. Parquet and Arrow
4. Measuring memory
5. Cutting memory
6. Speed habits
7. When pandas is the wrong tool
8. Gotchas

## 1. CSV options for messy files

Real CSV files use other separators, decimal commas, odd missing-value markers and broken lines. `read_csv` has an option for each; state them instead of cleaning afterwards.

```python
import io

import numpy as np
import pandas as pd

raw = io.StringIO("id;amount;note\n1;1.234,50;ok\n2;-;n/a\n3;99,00;bad;extra\n")
df = pd.read_csv(
    raw,
    sep=";",
    decimal=",", thousands=".",                       # European number format
    na_values=["-", "n/a"],                           # extra markers for missing values
    on_bad_lines="skip",                              # or "warn", or a callable that repairs the row
    dtype={"id": "int64"},
)
df["amount"].tolist(), len(df)                        # ([1234.5, nan], 2)
```

- `encoding="utf-8-sig"` strips the byte-order mark that Excel writes; `encoding="cp1252"` reads old Windows exports. Read bytes in a hex viewer when unsure, never guess repeatedly.
- `keep_default_na=False` stops pandas from turning strings like `"NA"` or `"null"` into missing values when they are real data (a country code, a name).
- `date_format="%d/%m/%Y"` with `parse_dates=[...]` parses a known format strictly; pandas 3 removed `date_parser` and `infer_datetime_format`.
- `engine="pyarrow"` uses PyArrow's multi-threaded parser but supports fewer options: `thousands=` and `chunksize=` raise `ValueError: The 'thousands' option is not supported with the 'pyarrow' engine`. Use the default `"c"` engine for those; in 3.1 it also reads large files in parallel.

## 2. Reading large CSV files in chunks

`chunksize=` returns an iterator of DataFrames, so a file larger than memory can be aggregated piece by piece. Combine partial results, not rows.

```python
big = io.StringIO("store,units\n" + "\n".join(f"s{i % 3},{i}" for i in range(10_000)))
totals: pd.Series | None = None
with pd.read_csv(big, chunksize=2_500, dtype={"store": "category", "units": "int64"}) as reader:
    for chunk in reader:
        part = chunk.groupby("store", observed=True)["units"].sum()
        totals = part if totals is None else totals.add(part, fill_value=0)
totals.astype("int64").to_dict()                      # {'s0': 16668333, 's1': 16661667, 's2': 16665000}
```

When the job is a filter or an aggregation over a large file, Polars `scan_csv` or DuckDB does it in one query, in parallel, with less code; see section 7.

## 3. Parquet and Arrow

Parquet stores columns with their types, compresses well and lets readers load only some columns and row groups. It is the default format for data that pandas writes and reads again.

```python
sales = pd.DataFrame({
    "day": pd.date_range("2026-10-01", periods=6, freq="D", tz="UTC"),
    "store": pd.Categorical(["a", "b", "a", "b", "a", "b"]),
    "units": np.arange(6, dtype="int32"),
})
sales.to_parquet("sales.parquet", index=False)
back = pd.read_parquet("sales.parquet", columns=["store", "units"], filters=[("units", ">=", 3)])
back.dtypes.astype(str).to_dict(), len(back)          # ({'store': 'category', 'units': 'int32'}, 3)
```

- `filters=` pushes the condition into the reader, which skips row groups whose statistics exclude it; `columns=` skips the other columns entirely.
- `partition_cols=["store"]` writes a folder per value (`store=a/...`), which readers can prune by path.
- `dtype_backend="pyarrow"` on any reader returns Arrow-backed columns with real missing values for every type; convert with `df.convert_dtypes(dtype_backend="pyarrow")`.
- `pd.DataFrame.from_arrow(table)` (3.0) and `__arrow_c_stream__` exchange data with any Arrow-based library through the PyCapsule interface.

## 4. Measuring memory

`memory_usage(deep=True)` reports the real bytes per column, including Python objects in `object` columns; without `deep=True`, object columns count only their pointers.

```python
n = 100_000
frame = pd.DataFrame({
    "city": np.random.default_rng(0).choice(["Oslo", "Lima", "Pune"], n),
    "qty": np.ones(n, dtype="int64"),
})
as_object = frame.astype({"city": object})
mb = lambda d: round(float(d.memory_usage(deep=True).sum()) / 1e6, 1)
mb(as_object), mb(frame), mb(frame.astype({"city": "category"}))   # (6.1, 2.0, 0.9)
```

`df.info(memory_usage="deep")` prints the same per frame. Measure before and after a change; the numbers above show the pandas 3 `str` dtype already saving most of what `object` wasted, and `category` saving more for repeated values.

## 5. Cutting memory

Most DataFrames shrink by half or more with three changes: categories for repeated strings, the smallest numeric types that hold the values, and only the needed columns.

```python
slim = frame.assign(
    city=frame["city"].astype("category"),
    qty=pd.to_numeric(frame["qty"], downcast="integer"),   # int64 -> int8 when the values fit
)
slim.dtypes.astype(str).to_dict(), mb(slim)            # ({'city': 'category', 'qty': 'int8'}, 0.2)
```

- `category` pays off when a column has few distinct values compared with its length (cities, statuses, product codes), not for free text or ids.
- Downcasting floats to `float32` halves memory; sums of many values lose precision, so aggregate in `float64`.
- `usecols=` and `columns=` at read time beat dropping columns after loading.
- Integer columns with missing values become `float64` under NumPy dtypes; `Int32` or `int32[pyarrow]` keep them integers.

## 6. Speed habits

pandas is fast when work stays in vectorised operations on whole columns. These habits matter more than any setting.

| Slow | Fast |
|---|---|
| `for i, row in df.iterrows()` | column expressions; `itertuples()` if a loop is unavoidable (often 10 times faster than `iterrows`) |
| `df.apply(f, axis=1)` | `np.select`, `np.where`, `.str`, `.dt`, `Series.map` |
| growing a DataFrame with `pd.concat` in a loop | collect rows or frames in a list, `pd.concat` once |
| `groupby(...).apply(python_func)` | named aggregations, `transform("sum")` |
| repeated filters on a key | `set_index(key).sort_index()` then `.loc[key]` |
| string columns as `object` | the default `str` dtype with PyArrow installed |

```python
rows = [{"k": i % 3, "v": i} for i in range(1_000)]
built = pd.DataFrame.from_records(rows)               # one construction, not 1,000 concats
built.groupby("k")["v"].sum().tolist()                # [166833, 166167, 166500]
```

`DataFrame.eval` and `query` use numexpr when it is installed; pandas 3.1 refuses numexpr 2.14.1, which could return wrong results, and asks for 2.14.2 or newer.

## 7. When pandas is the wrong tool

pandas holds the whole DataFrame in memory and runs most operations on one core. Other tools fit some jobs better.

| Job | Better choice |
|---|---|
| files larger than memory, multi-core queries, lazy pipelines | Polars (`scan_parquet`, `scan_csv`, streaming engine) |
| SQL over Parquet or CSV files, joins of large tables | DuckDB (`duckdb.sql("SELECT ... FROM 'data/*.parquet'")`), which returns pandas with `.df()` |
| numeric arrays without labels | NumPy |
| a few thousand rows in an app | plain lists and dataclasses, or SQLite |

Mixing is cheap through Arrow: `pl.from_pandas(df)`, `df_pl.to_pandas()`, and DuckDB queries pandas DataFrames in scope by name.

## 8. Gotchas

These are the I/O and memory problems seen most.

1. **`DtypeWarning: Columns (3) have mixed types`.** The column holds numbers and text in different parts of the file. Declare its dtype, or fix the source.
2. **`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9`.** The file is not UTF-8 (often cp1252 from Windows). Pass the right `encoding`.
3. **Leading zeros vanished from ids.** pandas inferred integers. Read the column with `dtype="str"`.
4. **Memory use is ten times the CSV size.** `object` columns, wide numeric types, unused columns. Measure with `memory_usage(deep=True)` and apply section 5.
5. **A Parquet file read back has different dtypes.** It was written by another tool with other types (for example `large_string`). Pass `dtype_backend=` or cast after reading.
