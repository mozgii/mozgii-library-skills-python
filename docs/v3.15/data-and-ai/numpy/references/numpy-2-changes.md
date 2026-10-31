---
title: NumPy 2 changes and how to upgrade
author: MOZGII, Inc.
lang: en
---

# NumPy 2 changes and how to upgrade

What changed between NumPy 1.26 and NumPy 2.5 that breaks or changes the results of existing code, and the order of steps to upgrade a codebase. Every example ran on NumPy 2.5.4 with Python 3.14.

## Contents

This reference has 9 sections.

1. Upgrade steps
2. Removed and renamed names
3. NEP 50 type promotion
4. The copy keyword and __array__
5. The default integer on Windows
6. Scalar repr in output and doctests
7. StringDType and np.strings
8. Array API additions
9. Changes from 2.1 to 2.5

## 1. Upgrade steps

Upgrading to NumPy 2 is mostly mechanical, but a few changes alter numbers silently, so the order matters.

1. Run `ruff check --select NPY201 --fix .`: it rewrites most removed names.
2. Upgrade every dependency with compiled code to a release built against NumPy 2. An extension built for NumPy 1.x fails to import with "A module that was compiled using NumPy 1.x cannot be run in NumPy 2". Wheels built against NumPy 2 also run on 1.26.
3. Run the tests with `-W error::DeprecationWarning` to surface the next round of removals.
4. Review numeric results where `float32`, `uint8` or other narrow types meet Python numbers (section 3).
5. Fix doctests and logged output that print scalars (section 6).

## 2. Removed and renamed names

NumPy 2.0 removed aliases that duplicated Python builtins or other NumPy names; NumPy 2.x removes more as deprecations expire. Accessing a removed alias raises `AttributeError` with the replacement in the message.

| Removed | Use |
|---|---|
| `np.float_`, `np.complex_` | `np.float64`, `np.complex128` |
| `np.NaN`, `np.NAN`, `np.Inf`, `np.Infinity` | `np.nan`, `np.inf` |
| `np.unicode_`, `np.string_` | `np.str_`, `np.bytes_` |
| `np.bool8`, `np.object0` and other `*0`/`8` aliases | `np.bool`, `np.object_` |
| `np.alltrue`, `np.sometrue`, `np.product`, `np.cumproduct` | `np.all`, `np.any`, `np.prod`, `np.cumprod` |
| `np.in1d`, `np.row_stack`, `np.trapz` | `np.isin`, `np.vstack`, `np.trapezoid` |
| `numpy.distutils` (gone in 2.5) | meson-python or scikit-build-core |
| `np.core.*` internals | the public `numpy` namespace; `np._core` is private |

```python
import numpy as np

try:
    np.float_
except AttributeError as exc:
    str(exc)                                          # '`np.float_` was removed in the NumPy 2.0 release. Use `np.float64` instead.'
np.isin([1, 5], [5, 6])                               # array([False,  True])
```

## 3. NEP 50 type promotion

NumPy 2 decides result dtypes from the dtypes involved, not from the values. Python `int`, `float` and `complex` are "weak": they adapt to the array's dtype. NumPy scalars such as `np.float64(2.5)` are "strong" and take part in promotion like arrays.

```python
(np.array([1.0], dtype=np.float32) * 2.5).dtype      # dtype('float32'): Python float is weak
(np.array([1.0], dtype=np.float32) * np.float64(2.5)).dtype   # dtype('float64'): NumPy scalar is strong
np.array([200], dtype=np.uint8) + 100                 # array([44], dtype=uint8): wraps, no upcast
try:
    np.uint8(1) + 300
except OverflowError as exc:
    str(exc)                                          # 'Python integer 300 out of bounds for uint8'
```

- NumPy 1.x looked at values and could upcast `float32` to `float64` when a Python float was involved; results computed in NumPy 1 may differ in the last digits.
- A Python integer that does not fit the array dtype raises `OverflowError` instead of upcasting.
- When precision matters, cast first: `x.astype(np.float64) * factor`.

## 4. The copy keyword and __array__

`copy=` in `np.array` and `np.asarray` has three values in NumPy 2: `True` always copies, `None` copies only when needed, and `False` never copies and raises when it would have to.

```python
lst = [1, 2, 3]
try:
    np.array(lst, copy=False)
except ValueError as exc:
    str(exc).splitlines()[0]                          # 'Unable to avoid copy while creating an array as requested.'
arr = np.asarray(lst)                                 # the NumPy 1 meaning of copy=False
np.asarray(arr) is arr                                # True: no copy of an existing array
```

Library classes that implement `__array__` should accept `dtype=None, copy=None`; NumPy passes `copy` through when the caller asks for a copy, and a signature without it gets a `DeprecationWarning` in that case.

## 5. The default integer on Windows

NumPy 1.x used 32-bit `long` as the default integer on Windows, so `np.array([1, 2]).dtype` was `int32` there and `int64` on macOS and Linux. NumPy 2 uses `int64` on every 64-bit platform.

- Code that relied on `int32` on Windows (sizes in binary files, C interfaces) now gets `int64`; state the dtype explicitly.
- Old Windows-only overflow bugs in sums and products of large integers disappear after the upgrade, which can change results.
- `np.intp` (pointer-sized) is the index dtype; it is 64-bit on 64-bit systems everywhere.

## 6. Scalar repr in output and doctests

NumPy 2 prints scalars with their type (NEP 51): `repr(np.float64(1.5))` is `np.float64(1.5)`, where NumPy 1 printed `1.5`. `str()` and f-strings are unchanged.

```python
repr(np.float64(1.5)), str(np.float64(1.5))          # ('np.float64(1.5)', '1.5')
with np.printoptions(legacy="1.25"):
    repr(np.float64(1.5))                             # '1.5'
```

Doctests and snapshot tests that print scalars need updating; `legacy="1.25"` restores the old output while migrating. Convert to Python numbers (`float(x)`, `x.item()`, `arr.tolist()`) before JSON encoding: `json.dumps` cannot serialise NumPy scalars.

## 7. StringDType and np.strings

`np.dtypes.StringDType()` (2.0) stores variable-length UTF-8 strings, where the fixed-width `"U"` dtype pads every element to the longest one and the `object` dtype holds Python objects. It supports a missing-value sentinel.

```python
names = np.array(["apple", "kiwi", None], dtype=np.dtypes.StringDType(na_object=None))
np.strings.upper(names[:2])                           # array(['APPLE', 'KIWI'], dtype=StringDType(na_object=None))
np.strings.str_len(names[:2])                         # array([5, 4])
np.strings.slice(np.array(["abcdef"]), 1, 4)          # array(['bcd'], dtype='<U6')
```

- `np.strings` (2.0) holds fast vectorised string functions (`find`, `replace`, `startswith`, `strip`, `slice` in 2.3) that work on `"U"`, `"S"` and `StringDType` arrays.
- `np.char` is the older namespace; `np.char.chararray` and `np.char.array` are deprecated in 2.5.
- pandas 3 has its own `str` dtype; convert at the boundary rather than mixing.

## 8. Array API additions

NumPy 2 follows the Python array API standard, which brought new functions with consistent names. Code written against them also runs on other array libraries.

| New in 2.x | Replaces or adds |
|---|---|
| `np.unique_values`, `np.unique_counts`, `np.unique_inverse`, `np.unique_all` | `np.unique(..., return_counts=True)` and friends |
| `np.isdtype(dtype, "integral")` | `np.issubdtype` checks |
| `np.astype(x, dtype)` | `x.astype(dtype)` as a function |
| `np.matrix_transpose`, `np.vecdot`, `np.linalg.vecdot` | transposes of the last two axes, vector dot products |
| `np.cumulative_sum(x, include_initial=True)` | `np.concatenate([[0], np.cumsum(x)])` |
| `np.matvec`, `np.vecmat` (2.2) | matrix-vector products over stacks |

```python
np.unique_counts(np.array([3, 1, 3, 3]))             # UniqueCountsResult(values=array([1, 3]), counts=array([1, 3]))
np.isdtype(np.int16, "integral")                      # True
```

## 9. Changes from 2.1 to 2.5

Each 2.x release adds features and expires deprecations; read the release notes of every version you skip.

| Version | Changes worth knowing |
|---|---|
| 2.1 | Python 3.13; preliminary free-threaded support; array API 2023.12; `np.unstack`; `np.cumulative_sum`, `np.cumulative_prod` |
| 2.2 | `np.matvec`, `np.vecmat`; `bool()` of an empty array raises (use `arr.size > 0`) |
| 2.3 | `np.strings.slice`; builds with OpenMP; preliminary Windows on ARM; `numpy.typing.mypy_plugin` deprecated |
| 2.4 | `astype(..., casting="same_value")`; setting `.strides` deprecated; `np.testing.assert_warns` deprecated (use `pytest.warns`) |
| 2.5 | Python 3.12+ only; `numpy.distutils` removed; `np.sort(..., descending=True)`; setting `.shape` and `.dtype` deprecated; `np.char.chararray` deprecated; better free-threaded scaling |

```python
np.sort(np.array([3, 1, 2]), descending=True)        # array([3, 2, 1])
np.array([1.0, 2.0]).astype(np.int64, casting="same_value")   # array([1, 2])
```
