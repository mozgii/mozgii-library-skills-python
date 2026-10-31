---
title: Upgrading to pandas 3
author: MOZGII, Inc.
lang: en
---

# Upgrading to pandas 3

How to move a codebase from pandas 2.x to pandas 3.0 and 3.1: the upgrade path through 2.3, copy-on-write in detail, the `str` dtype and its missing-value rules, microsecond datetimes and the integer trap, zoneinfo, removed APIs and frequency aliases, and the 3.1 deprecations. Every example ran on pandas 3.1.0 with PyArrow 26.

## Contents

This reference has 8 sections.

1. The upgrade path
2. Copy-on-write in detail
3. The str dtype
4. Datetime resolution and the integer trap
5. Time zones are zoneinfo
6. Removed APIs and renamed frequency aliases
7. Deprecations in 3.1
8. Gotchas

## 1. The upgrade path

pandas 3 removed many APIs that pandas 2 only warned about, so the path goes through the last 2.x release.

1. Upgrade to pandas 2.3 and run the tests with warnings as errors (`-W error::FutureWarning -W error::DeprecationWarning`). Fix everything that warns.
2. On 2.3, turn on the new behaviours early: `pd.options.mode.copy_on_write = True` and `pd.options.future.infer_string = True`. Fix what breaks.
3. Upgrade to pandas 3 (and NumPy 2, which 3.1 requires). Install PyArrow so the `str` dtype uses Arrow storage.
4. Search for code that inspects dtypes (`== object`, `"datetime64[ns]"`) or converts datetimes to integers, which change silently (sections 3 and 4).

## 2. Copy-on-write in detail

Copy-on-write (CoW) makes every derived object behave as a copy, while pandas shares memory until one side is written. The option `mode.copy_on_write` has no effect in pandas 3 and will be removed in 4.0.

```python
import numpy as np
import pandas as pd

df = pd.DataFrame({"a": [1, 2, 3], "b": [4.0, 5.0, 6.0]})
col = df["a"]                                         # shares memory with df for now
col.iloc[0] = 100                                     # this write copies col first
df.loc[0, "a"]                                        # np.int64(1): df is unchanged

arr = df["b"].to_numpy()
arr.flags.writeable                                   # False: a read-only view into df
writable = df["b"].to_numpy(copy=True)                # ask for a copy to modify it
```

| pandas 2 pattern | pandas 3 behaviour | Write instead |
|---|---|---|
| `df["a"][mask] = v` | nothing changes; `ChainedAssignmentError` warning | `df.loc[mask, "a"] = v` |
| `s = df["a"]; s[0] = v` to update `df` | only `s` changes | `df.loc[0, "a"] = v` |
| `.copy()` to silence `SettingWithCopyWarning` | not needed; the warning is gone | remove the copy |
| `df.to_numpy()` then write into it | read-only array, `ValueError` on write | `to_numpy(copy=True)` |
| `df.fillna(0, inplace=True)` on a column of `df` via a chain | does nothing | `df["a"] = df["a"].fillna(0)` |

CoW also makes many methods cheaper: `reset_index`, `rename`, `set_axis` and similar no longer copy data unless it is written later.

## 3. The str dtype

pandas 3 infers `str` for string data, where pandas 2 used `object`. The dtype holds only strings and missing values, uses `NaN` as its missing marker like other default dtypes, and is backed by PyArrow when it is installed.

```python
s = pd.Series(["ann", None, "bo"])
s.dtype                                               # <StringDtype(na_value=nan)>
s.isna().tolist(), s.str.len().tolist()               # ([False, True, False], [3.0, nan, 2.0])
try:
    s.iloc[0] = 5
except TypeError as exc:
    str(exc)[:41]                                     # "Invalid value '5' for dtype 'str'. Value "
s.astype(object).dtype                                # dtype('O'): explicit opt-out
```

- Checks like `df[col].dtype == object` to find text columns fail; use `pd.api.types.is_string_dtype(df[col])`.
- A column that mixes strings and other objects (numbers, `Decimal`) stays `object`.
- `dtype="string"` (the older `StringDtype` with `pd.NA` as missing value) still exists and differs: comparisons with missing values return `pd.NA`, not `False`.

## 4. Datetime resolution and the integer trap

pandas 3 keeps the resolution of the input, defaulting to microseconds for strings and Python datetimes (and, since 3.1, for numbers with a `unit=`). Most code does not notice. Code that converts datetimes to integers does: the numbers are 1000 times smaller than with nanoseconds.

```python
ts = pd.to_datetime(["2026-01-01"])
ts.dtype                                              # dtype('<M8[us]')
int(ts.astype("int64")[0])                            # 1767225600000000: microseconds since the epoch
int(ts.as_unit("s").astype("int64")[0])               # 1767225600: state the unit before converting
pd.to_datetime(["2026-01-01 00:00:00.123456789"]).dtype   # dtype('<M8[ns]'): nanosecond input keeps ns
```

- Convert to epoch numbers with an explicit unit: `as_unit("s")`, `as_unit("ms")`, or `(ts - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")`.
- `dtype == "datetime64[ns]"` checks fail; use `pd.api.types.is_datetime64_any_dtype`.
- Microsecond resolution covers years 1 to 9999, so the old `OutOfBoundsDatetime` errors for dates before 1677 or after 2262 mostly disappear.

## 5. Time zones are zoneinfo

pandas 3 returns `zoneinfo.ZoneInfo` (or `datetime.timezone` for fixed offsets) for time zones given as strings, instead of pytz objects. pytz objects passed in explicitly still work.

```python
pd.Timestamp("2026-07-01 12:00", tz="Europe/Paris").tz   # zoneinfo.ZoneInfo(key='Europe/Paris')
```

Remove code that calls pytz methods on `.tz` (`localize`, `normalize`, `.zone`); use `str(tz)` or `tz.key` for the name. On Windows, install `tzdata` so zone names resolve (the python-datetime skill).

## 6. Removed APIs and renamed frequency aliases

pandas 3 removed APIs deprecated during 2.x. The ones most code hits:

| Removed | Use |
|---|---|
| `DataFrame.applymap` | `DataFrame.map` |
| `DataFrame.first`, `DataFrame.last` (by offset) | `df.loc[df.index < df.index[0] + pd.Timedelta("3D")]` |
| `DataFrame.bool`, `Series.bool`, `swapaxes` | `bool(s.item())`, `transpose` |
| `pd.value_counts(x)` | `pd.Series(x).value_counts()` |
| `groupby(..., axis=1)`, `rolling(axis=1)`, `resample(axis=1)` | transpose first, or operate on columns |
| `GroupBy.fillna`, `Resample.fillna` | `GroupBy.ffill`, `bfill`; `Resample.ffill` |
| `read_csv(date_parser=, infer_datetime_format=, keep_date_col=, delim_whitespace=)` | `date_format=`; default strict inference; `sep=r"\s+"` |
| frequency aliases `M`, `Q`, `Y`, `BM`, `H`, `T`, `S` | `ME`, `QE`, `YE`, `BME`, `h`, `min`, `s` |

```python
pd.date_range("2026-01-31", periods=3, freq="ME").strftime("%Y-%m-%d").tolist()   # ['2026-01-31', '2026-02-28', '2026-03-31']
try:
    pd.date_range("2026-01-01", periods=2, freq="H")
except ValueError as exc:
    "Did you mean h?" in str(exc)                     # True
```

Other behaviour changes worth a test: `groupby` on categoricals defaults to `observed=True`; `idxmin`/`idxmax` on an all-NA group raise; logical operators between a Series and a plain list raise (wrap the list in a Series or array).

## 7. Deprecations in 3.1

pandas 3.1 deprecates more, with `Pandas4Warning` (a subclass of `DeprecationWarning` now, a `FutureWarning` in the last 3.x release, removal in 4.0).

- `inplace=` on methods that never worked in place: `drop`, `rename`, `rename_axis`, `reset_index`, `set_index`, `dropna`, `drop_duplicates`, `sort_values`, `sort_index`, `eval`, `query`. Reassign: `df = df.dropna()`. `fillna` and `replace` keep `inplace`.
- `.values` on time-zone-aware, period and interval data, which lost information; use `.array` or `.to_numpy(dtype=...)` explicitly.
- Inferred frequencies are returned as offset objects.
- Selecting labels in `DataFrame.filter` with `items`, `like` or `regex` will be deprecated; `filter(cond=...)` (3.1) filters rows by a mask, and `select()` picks columns.

Run tests with `-W error::pandas.errors.Pandas4Warning` (or `-W error::DeprecationWarning`) to keep the codebase ready for 4.0.

## 8. Gotchas

These are the problems seen most after upgrading to pandas 3.

1. **Assignments silently stopped working.** They were chained (`df[a][b] = v`) or went through a derived object. Assign with `df.loc[...]` on the DataFrame itself.
2. **Epoch integers are 1000 times smaller.** Datetimes are now microseconds. Call `as_unit(...)` before `astype("int64")`.
3. **`ValueError: assignment destination is read-only`.** A NumPy array from `to_numpy()` is a read-only view. Pass `copy=True`.
4. **`TypeError: Invalid value ... for dtype 'str'`.** A non-string was written into a `str` column. Convert the value, or cast the column to `object` deliberately.
5. **`ValueError: Invalid frequency: H`.** Old frequency aliases. Use `h`, `min`, `s`, `ME`, `QE`, `YE`.
