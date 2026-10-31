---
title: Polars dates, time zones, windows and interop
author: MOZGII, Inc.
lang: en
---

# Polars dates, time zones, windows and interop

How to work with time in Polars 2.x: parsing dates and datetimes, attaching and converting time zones across DST changes, the `dt` namespace, window functions with `over`, time-based rolling windows and `group_by_dynamic`, as-of joins, and moving data between Polars, pandas, NumPy, Arrow and DuckDB. Every example ran on Polars 2.1.0, pandas 3.1.0 and DuckDB 2.0.0.

## Contents

This reference has 8 sections.

1. Parsing dates and datetimes
2. Time zones and DST
3. The dt namespace
4. Window functions with over
5. Rolling windows by time
6. Resampling with group_by_dynamic
7. As-of joins
8. Exchanging data with pandas, NumPy, Arrow and DuckDB

## 1. Parsing dates and datetimes

Strings become dates with `str.to_date` and datetimes with `str.to_datetime`, with an explicit format. Since Polars 2.0, casting a `String` column to `Date` or `Datetime` raises; parsing is always explicit.

```python
from datetime import UTC, datetime

import polars as pl

raw = pl.DataFrame({
    "day": ["28/10/2026", "29/10/2026"],
    "stamp": ["2026-10-28 09:15", "2026-10-29 18:00"],
    "iso": ["2026-10-28T09:15:00+01:00", "2026-10-29T18:00:00Z"],
})
parsed = raw.select(
    pl.col("day").str.to_date("%d/%m/%Y"),
    pl.col("stamp").str.to_datetime("%Y-%m-%d %H:%M", time_zone="UTC"),   # naive text read as UTC
    pl.col("iso").str.to_datetime(time_zone="UTC"),                         # offsets converted to UTC
)
[str(t) for t in parsed.dtypes]                       # ['Date', "Datetime(time_unit='us', time_zone='UTC')", "Datetime(time_unit='us', time_zone='UTC')"]
parsed.get_column("iso").dt.hour().to_list()          # [8, 18]
```

- `strict=False` turns unparsable values into null instead of raising; count the nulls afterwards so bad input does not vanish silently.
- The default time unit is microseconds; `time_unit="ns"` keeps nanoseconds when the source has them.

## 2. Time zones and DST

A `Datetime` column has at most one time zone for the whole column. `replace_time_zone` attaches a zone to naive local readings (or removes one); `convert_time_zone` converts aware values, keeping the instant.

```python
local = pl.DataFrame({"wall": ["2026-03-29 01:30", "2026-03-29 02:30", "2026-10-25 02:30"]}).select(
    pl.col("wall").str.to_datetime("%Y-%m-%d %H:%M")
)
aware = local.select(
    pl.col("wall").dt.replace_time_zone("Europe/Paris", ambiguous="earliest", non_existent="null")
)
aware.get_column("wall").dt.convert_time_zone("UTC").dt.strftime("%H:%M").to_list()   # ['00:30', None, '00:30']
```

- 02:30 on 29 March 2026 does not exist in Paris (clocks jump from 02:00 to 03:00): `non_existent="null"` makes it null, the default `"raise"` fails.
- 02:30 on 25 October 2026 happens twice: `ambiguous="earliest"` picks the first (summer time), `"latest"` the second, `"raise"` fails, `"null"` gives null.
- Store and compute instants in UTC; convert for display or for grouping by local calendar days. The rules behind gaps and folds are the python-datetime skill.

## 3. The dt namespace

`dt` methods extract parts, truncate to periods and shift by calendar amounts, all vectorised.

```python
ts = pl.DataFrame({"t": pl.datetime_range(pl.datetime(2026, 1, 30, 22), pl.datetime(2026, 1, 31, 2), "1h", eager=True)})
ts.select(
    day=pl.col("t").dt.truncate("1d"),
    next_month=pl.col("t").dt.offset_by("1mo"),       # calendar month: 30 Jan -> 28 Feb (2026 is not a leap year)
    weekday=pl.col("t").dt.weekday(),                 # Monday = 1 ... Sunday = 7
).row(0)                                              # (datetime.datetime(2026, 1, 30, 0, 0), datetime.datetime(2026, 2, 28, 22, 0), 5)
```

- Duration strings: `ns`, `us`, `ms`, `s`, `m`, `h`, `d`, `w`, `mo`, `q`, `y`, and combinations like `"1h30m"`. `mo`, `q` and `y` are calendar periods; `d` is a calendar day in a time-zone-aware column.
- `dt.month_start()`, `dt.month_end()`, `dt.add_business_days(n)`, `dt.is_business_day()` cover common calendar logic.
- Subtracting two datetimes gives a `Duration`; `dt.total_seconds()` turns it into a number.

## 4. Window functions with over

`expr.over(keys)` computes an expression per group and returns a value for every row, like SQL's `OVER (PARTITION BY ...)`. With `order_by=`, order-dependent functions (cumulative sums, ranks, shifts) run in that order within each group.

```python
sales = pl.DataFrame({
    "store": ["a", "a", "b", "a", "b"],
    "day": [1, 2, 1, 3, 2],
    "units": [5, 3, 8, 6, 2],
})
sales.with_columns(
    store_total=pl.col("units").sum().over("store"),
    running=pl.col("units").cum_sum().over("store", order_by="day"),
    share=(pl.col("units") / pl.col("units").sum().over("store")).round(2),
).sort("store", "day").get_column("running").to_list()     # [5, 8, 14, 8, 10]
```

- `rank()`, `shift()`, `diff()`, `cum_*` and `first()`/`last()` all work inside `over`.
- `mapping_strategy="explode"` or `"join"` returns group-level lists instead of one value per row, for top-n per group patterns; `pl.col("x").top_k(3).over("g", mapping_strategy="explode")`.

## 5. Rolling windows by time

`rolling_*_by` functions compute moving statistics over a time window, so gaps and irregular timestamps are handled correctly; `rolling_mean(window_size=n)` counts rows instead. The time column must be sorted within each group.

```python
readings = pl.DataFrame({
    "at": [datetime(2026, 10, 28, h, tzinfo=UTC) for h in (0, 1, 2, 5, 6)],
    "temp": [10.0, 12.0, 14.0, 20.0, 22.0],
}).sort("at")
readings.with_columns(
    avg_3h=pl.col("temp").rolling_mean_by("at", window_size="3h"),
).get_column("avg_3h").to_list()                      # [10.0, 11.0, 12.0, 20.0, 21.0]: the 3h window before each reading
```

For several statistics over the same window, `LazyFrame.rolling(index_column="at", period="3h").agg(...)` computes them in one pass.

## 6. Resampling with group_by_dynamic

`group_by_dynamic` buckets rows into fixed time intervals (every hour, day, week) and aggregates each bucket, which is pandas `resample` in Polars. The index column must be sorted.

```python
events = pl.DataFrame({
    "at": [datetime(2026, 10, 28, 9, m, tzinfo=UTC) for m in (0, 10, 40)] + [datetime(2026, 10, 28, 11, 5, tzinfo=UTC)],
    "clicks": [1, 2, 3, 4],
}).sort("at")
hourly = events.group_by_dynamic("at", every="1h").agg(clicks=pl.col("clicks").sum())
hourly.get_column("clicks").to_list()                 # [6, 4]: empty hours are not created
```

- Empty intervals produce no rows; to get zeros, join with a full range from `pl.datetime_range(..., interval="1h")` and fill nulls.
- For local-day buckets, convert to the local zone first, then group; a "day" in a DST-changing zone can be 23 or 25 hours.
- `group_by=` inside `group_by_dynamic` buckets per key (per store, per sensor).

## 7. As-of joins

`join_asof` matches each left row with the nearest right row by a sorted key, typically time: the last price before a trade, the reading closest to an event. `by=` restricts matches to the same key; `tolerance=` limits how far a match may be.

```python
trades = pl.DataFrame({"at": [datetime(2026, 10, 28, 10, m) for m in (1, 7, 30)], "sym": ["X", "X", "X"]}).sort("at")
quotes = pl.DataFrame({"at": [datetime(2026, 10, 28, 10, m) for m in (0, 5, 6)], "sym": ["X"] * 3,
                       "price": [100.0, 101.0, 102.0]}).sort("at")
trades.join_asof(quotes, on="at", by="sym", strategy="backward", tolerance="10m").get_column("price").to_list()   # [100.0, 102.0, None]
```

`strategy="forward"` takes the next row, `"nearest"` the closest either way. Both inputs must be sorted by the `on` key. Without `by=`, Polars checks and raises on unsorted input; with `by=` it cannot check and emits `UserWarning: Sortedness of columns cannot be checked when 'by' groups provided`, so sort both sides yourself.

## 8. Exchanging data with pandas, NumPy, Arrow and DuckDB

Polars data is Arrow memory, so exchange with Arrow-based tools is usually zero-copy, and conversion to NumPy-backed pandas copies.

```python
import duckdb
import numpy as np

table = sales.to_arrow()                              # pyarrow.Table, zero-copy
pl.from_arrow(table).equals(sales)                    # True
pdf = sales.to_pandas(use_pyarrow_extension_array=True)
pl.from_pandas(pdf).equals(sales)                     # True
pl.from_numpy(np.arange(6).reshape(3, 2), schema=["x", "y"]).shape   # (3, 2)
duckdb.sql("SELECT store, sum(units)::BIGINT AS u FROM sales GROUP BY store ORDER BY store").pl().to_dicts()   # [{'store': 'a', 'u': 14}, {'store': 'b', 'u': 10}]
```

- DuckDB finds Polars and pandas DataFrames by their Python variable name in SQL; `.pl()` and `.df()` return the result as Polars or pandas.
- DuckDB's `sum` of an integer column is a 128-bit `HUGEINT`, which arrives in Polars as `Decimal`; cast in SQL (`::BIGINT`) when an integer is expected.
- `to_numpy()` on a frame with mixed dtypes or nulls copies and may produce `object` or float arrays; select numeric columns without nulls first for a zero-copy view.
- Many libraries accept Polars directly through the Arrow PyCapsule interface (`__arrow_c_stream__`), including recent pandas (`pd.DataFrame.from_arrow`), DuckDB and plotting tools.
