---
name: numpy
description: Writes fast, correct NumPy 2.x code - creating arrays with explicit dtypes, vectorised operations instead of Python loops, broadcasting rules, views versus copies and the copy keyword, basic, boolean and fancy indexing, random numbers with np.random.default_rng and Generator, NaN handling with nan-functions and masks, memory layout (C and Fortran order, strides, contiguity), structured arrays, linear algebra with @ and np.linalg, interoperability with pandas, Polars, PyTorch and the buffer protocol, and the NumPy 2 changes that break old code (removed aliases such as np.float_, NEP 50 type promotion, int64 default on Windows, StringDType). Use when writing numerical Python, speeding up a loop over numbers, debugging a shape or dtype error, upgrading code to NumPy 2, generating reproducible random numbers, or when an array changes unexpectedly because it was a view.
metadata:
  tags:
  - python
  - numpy
  - arrays
  - vectorization
  - broadcasting
  - linear-algebra
  - performance
  - numpy-2
---

# NumPy 2.x arrays done right

NumPy gives Python typed, fixed-size, n-dimensional arrays and compiled loops over them. Code is fast when work happens in whole-array operations and slow when Python touches each element. Most bugs come from three places: a dtype that is not what you think, a view that shares memory with another array, and broadcasting that silently produces the wrong shape. This skill covers the everyday API. NumPy 2 migration details are in [numpy-2-changes.md](references/numpy-2-changes.md); memory layout and performance are in [memory-and-performance.md](references/memory-and-performance.md).

## Step 0: read the versions

NumPy 2 changed type promotion, removed many aliases and changed the default integer on Windows, so read the version first.

```bash
uv run python -c "import sys, numpy as np; print(sys.version_info[:2], np.__version__)"
uv run python -c "import numpy as np; np.show_config()" | grep -iA3 "blas\|lapack"   # which BLAS does linear algebra
uv run ruff check --select NPY201 .                   # code that breaks on NumPy 2
```

NumPy 2.5 (October 2026) needs Python 3.12+. Recent additions: `np.sort(..., descending=True)` (2.5), `astype(..., casting="same_value")` (2.4), and assigning to `.shape` or `.dtype` is deprecated (2.5): use `reshape` and `view`.

## Choosing asarray, array, copy or Numba

Two choices decide most NumPy code: whether an input is copied, and whether the work stays an array expression. The table gives the pick for each case and what goes wrong with the others.

| You need | Use | Why the others lose here |
|---|---|---|
| to read an input that may be a list or an array | `np.asarray(x)` | `np.array(x)` copies an array you were handed for no gain; `x.copy()` makes a list, not an array, and a tuple has no `.copy` at all |
| a private array from an input, or a set dtype | `np.array(x, dtype=...)` | `asarray` returns the same object when `x` already fits, so your writes reach the caller's data |
| to keep or edit part of an array | `arr[a:b].copy()` | a slice is a view: writes go through to the original, and it holds the whole base array in memory |
| to refuse a silent copy | `np.asarray(x, copy=False)` | since 2.0 it raises `ValueError` when a copy is needed, where the default copies quietly |
| arithmetic, masks, `np.where` or reductions over whole arrays | an array expression | Numba adds a dependency and a compile on the first call; `np.vectorize` still calls Python per element |
| a loop where each step needs the last result (a running filter, a state machine) | a `@numba.njit` function | no array expression fits; a plain Python loop took 233 ms where Numba took 2.8 ms on 2,000,000 values, plus 422 ms to compile once (NumPy 2.5.4, Numba 0.68.1, Python 3.14, one Mac) |

## Arrays and dtypes

Every array has one dtype for all its elements. Give it explicitly when the data comes from outside; inference picks `int64`, `float64` or a string type that may not be what the data means.

```python
import numpy as np

prices = np.array([19.99, 5.0, 3.5], dtype=np.float64)
counts = np.array([3, 0, 12], dtype=np.int32)
flags = np.zeros(3, dtype=bool)
grid = np.arange(12, dtype=np.int64).reshape(3, 4)    # shape (3, 4)
grid.shape, grid.dtype, grid.ndim, grid.size          # ((3, 4), dtype('int64'), 2, 12)
prices.astype(np.float32).dtype                       # dtype('float32'): astype returns a new array
```

- Integers overflow silently in arrays: `np.array([200], dtype=np.uint8) + 100` is `[44]`. Pick a dtype wide enough, or check with `np.iinfo(dtype).max`.
- `astype(np.int64, casting="same_value")` (2.4+) raises `ValueError` instead of truncating `1.5` to `1`.
- `float32` halves memory and is what GPUs and ML libraries want; sums of many `float32` values lose precision, so accumulate with `dtype=np.float64` (`a.sum(dtype=np.float64)`).
- Money is not a float: keep integer cents in `int64` or use `Decimal` outside NumPy.

## Vectorise instead of looping

A Python loop over array elements runs at Python speed, one boxed number at a time; the same work as one array expression runs in compiled code, often 50 to 200 times faster.

```python
rng = np.random.default_rng(7)
x = rng.normal(size=1_000_000)

def clip_loop(values: np.ndarray) -> np.ndarray:      # slow: a Python-level loop
    out = np.empty_like(values)
    for i, v in enumerate(values):
        out[i] = 0.0 if v < 0 else (1.0 if v > 1 else v)
    return out

fast = np.clip(x, 0.0, 1.0)                           # one compiled loop
cond = np.where(x > 0, x, 0.0)                        # if/else per element
bool(np.allclose(fast[:1000], clip_loop(x[:1000])))   # True
```

| Python pattern | Vectorised form |
|---|---|
| `if v > t: a else b` per element | `np.where(cond, a, b)`, `np.select([c1, c2], [a1, a2], default)` |
| running total | `np.cumsum`, `np.cumulative_sum` (2.1, with `include_initial`) |
| count matches | `np.count_nonzero(cond)` or `cond.sum()` |
| group by integer key | `np.bincount(keys, weights=values)` |
| lookup table | fancy indexing `table[codes]` |
| pairwise distances | broadcasting `a[:, None, :] - b[None, :, :]` (watch memory) |
| `sum(a[i] * b[i])` | `a @ b`, `np.vecdot(a, b)` |

`np.vectorize` is a loop with a nicer signature, not vectorisation; it does not speed anything up. When the logic really cannot be expressed with array operations, compile the loop (Numba, Cython; the python-performance skill).

## Broadcasting

Broadcasting lets arrays of different shapes combine without copying: shapes are compared from the right, and each pair of dimensions must be equal or one of them must be 1. A dimension of 1 is stretched to match.

```python
temps = np.array([[20.0, 22.5, 19.0],
                  [21.0, 23.0, 18.5]])                # shape (2 days, 3 sensors)
offsets = np.array([0.5, -0.2, 0.0])                  # shape (3,): one per sensor
temps + offsets                                       # (2, 3) + (3,) -> (2, 3)
day_mean = temps.mean(axis=1, keepdims=True)          # shape (2, 1), keeps the axis for broadcasting
(temps - day_mean).round(2)                           # array([[-0.5,  2. , -1.5], [ 0.17,  2.17, -2.33]])
```

- `keepdims=True` on reductions keeps the reduced axis as size 1 so the result broadcasts back.
- `a[:, None]` (or `a[:, np.newaxis]`) adds an axis: a `(3,)` vector becomes a `(3, 1)` column.
- The classic silent bug: `(n,)` minus `(n, 1)` broadcasts to `(n, n)` instead of failing. Assert shapes at function boundaries (`assert x.shape == (n,)`).

## Views and copies

Slicing, `reshape`, `transpose` and `.T` usually return views: new array objects that share the same memory. Writing through a view changes the original. Boolean and fancy indexing always return copies.

```python
base = np.arange(6)
view = base[1:4]
view[0] = 99
base                                                  # array([ 0, 99,  2,  3,  4,  5]): changed through the view
np.shares_memory(base, view), view.base is base       # (True, True)
picked = base[[0, 2]]                                 # fancy indexing: a copy
picked[0] = -1
base[0]                                               # np.int64(0): unchanged
safe = base[1:4].copy()                               # an independent copy
```

- Return `.copy()` from a function that hands out part of an internal array, or the caller can modify your state.
- `np.asarray(x)` never copies when `x` is already a suitable array; `np.array(x)` copies by default. In NumPy 2, `np.array(x, copy=False)` raises `ValueError` when a copy is needed: use `np.asarray`, or `copy=None` for "copy only if needed".
- `reshape` returns a view when it can and silently copies otherwise; `np.reshape(a, shape, copy=False)` raises instead of copying.

## Indexing: basic, boolean and fancy

Basic indexing uses integers and slices and returns views. Boolean masks select elements where the mask is `True`. Fancy indexing uses integer arrays and returns elements in the order given.

```python
m = np.arange(20).reshape(4, 5)
m[1:3, ::2]                                           # rows 1-2, every second column: a view
m[m % 7 == 0]                                         # array([ 0,  7, 14]): 1-D copy of matches
m[[0, 3], [1, 4]]                                     # array([ 1, 19]): elements (0,1) and (3,4)
m[np.ix_([0, 3], [1, 4])]                             # array([[ 1,  4], [16, 19]]): the 2x2 sub-grid
m[m > 15] = 0                                         # assignment through a mask modifies m in place
```

- Combine masks with `&`, `|` and `~`, with parentheses: `(a > 0) & (a < 5)`. Python's `and` raises "truth value of an array is ambiguous".
- `np.nonzero(mask)` gives the indices of matches; `np.flatnonzero` gives them for the flattened array.
- Assigning through a fancy index with repeated indices writes once per index, not cumulatively: `a[[0, 0]] += 1` adds 1. Use `np.add.at(a, idx, 1)` to accumulate.

## Random numbers with default_rng

`np.random.default_rng(seed)` returns a `Generator`, the current random API. The old `np.random.seed` and `np.random.rand` functions share one hidden global state; avoid them in new code.

```python
rng = np.random.default_rng(12345)                    # seeded: reproducible
rng.integers(0, 10, size=5)                           # array([6, 2, 7, 3, 2])
rng.normal(loc=0.0, scale=1.0, size=(2, 2)).shape     # (2, 2)
rng.choice(["a", "b", "c"], size=4, p=[0.5, 0.3, 0.2]).dtype   # dtype('<U1')
workers = rng.spawn(4)                                # independent streams for 4 workers or processes
```

- Pass the `Generator` into functions (`def simulate(rng: np.random.Generator)`) instead of creating one inside; tests then pass a seeded one.
- `rng.spawn(n)` gives statistically independent child generators for parallel work; never give each worker `default_rng(seed + i)`.
- Streams are stable for a given NumPy version but may change between versions; store seeds and the NumPy version with saved results.
- For secrets and tokens use the `secrets` module, never NumPy.

## NaN and missing values

NaN is a float value meaning "not a number"; it spreads through arithmetic and is never equal to anything, itself included. Integer and bool arrays cannot hold NaN.

```python
v = np.array([1.0, np.nan, 3.0])
v.mean(), np.nanmean(v)                               # (np.float64(nan), np.float64(2.0))
np.isnan(v).sum()                                     # np.int64(1)
v == np.nan                                           # array([False, False, False]): never use ==
np.nan_to_num(v, nan=0.0)                             # array([1., 0., 3.])
```

- The `nan*` functions (`nansum`, `nanmax`, `nanpercentile`) skip NaN; `np.isfinite` excludes both NaN and infinity.
- `in` finds NaN only by object identity: `np.nan in [np.nan]` is `True`, `float("nan") in [np.nan]` is `False`. Test with `np.isnan`.
- Missing integers need a mask (`np.ma.masked_array`) or a library with nullable types (pandas, Polars).

## Linear algebra basics

`@` is matrix multiplication; `np.linalg` has solvers and decompositions running on the installed BLAS and LAPACK.

```python
A = np.array([[3.0, 1.0], [1.0, 2.0]])
b = np.array([9.0, 8.0])
x = np.linalg.solve(A, b)                             # array([2., 3.]): never inv(A) @ b
bool(np.allclose(A @ x, b))                           # True
coef, residuals, rank, sv = np.linalg.lstsq(np.c_[np.ones(4), [1, 2, 3, 4]], [2.0, 4.1, 5.9, 8.0])
coef.round(2)                                         # array([0.05, 1.98]): intercept and slope
```

- `np.linalg.solve` is faster and more accurate than computing an inverse.
- Stacked arrays are handled at once: `np.linalg.solve` on shape `(k, n, n)` solves k systems; `np.vecdot` and `np.matvec` (2.2+) work on the last axes.
- Compare floats with `np.allclose` or `np.isclose`, never `==`.

## Structured arrays and interoperability

A structured dtype stores records with named fields in one array, matching binary file formats and C structs. For tabular analysis, pandas or Polars are better tools.

```python
rec = np.zeros(2, dtype=[("id", np.int32), ("x", np.float64), ("name", "U8")])
rec["id"] = [1, 2]
rec[1]["name"] = "beta"
rec["name"]                                           # array(['', 'beta'], dtype='<U8')
```

NumPy arrays cross library boundaries without copying through the buffer protocol and DLPack: `memoryview(arr)`, `torch.from_dlpack(arr)`, `pl.from_numpy`, `pd.DataFrame(arr)`. Zero-copy conversion needs a supported dtype and usually a contiguous array; details are in [memory-and-performance.md](references/memory-and-performance.md).

## Verify

These checks catch removed names, silent warnings and hidden global state; shapes and dtypes are proven by assertions in your tests.

```bash
uv run ruff check --select NPY201 .                 # All checks passed!
uv run pytest -q -W error::RuntimeWarning           # overflow and invalid-value warnings fail the test
uv run python -W error -c "import numpy as np; np.array([1.0]) / np.array([0.0])"; echo "exit $?"   # expect 1: the setting bites
grep -rnE "np\.random\.(seed|rand|randn|randint)\(|np\.vectorize\(" src    # expect nothing
```

## Anti-patterns

| Instead of | Do |
|---|---|
| a Python loop over array elements | an array expression; Numba or Cython when the logic needs a loop |
| `np.vectorize` for speed | `np.where`, `np.select` or a ufunc |
| `np.linalg.inv(A) @ b` | `np.linalg.solve(A, b)` |
| `np.random.seed(1)` and module-level random calls | `rng = np.random.default_rng(1)`, passed in |
| `default_rng(seed + i)` per worker | `rng.spawn(n)` |
| `a == np.nan` | `np.isnan(a)` |
| `np.array(x, copy=False)` | `np.asarray(x)` |
| floats for money | `int64` cents |

## Checklist

NumPy code is correct and fast when every item holds.

- [ ] Arrays from outside data have explicit dtypes; integer dtypes are wide enough.
- [ ] No Python loops over elements in hot paths; `np.vectorize` is not used for speed.
- [ ] Shapes are asserted at boundaries; reductions use `keepdims=True` when broadcasting back.
- [ ] Functions return copies of internal state; `np.asarray` is used to accept input without copying.
- [ ] Random numbers come from a passed-in `np.random.Generator`; parallel streams use `spawn`.
- [ ] NaN is tested with `np.isnan`; floats are compared with `np.allclose`.
- [ ] `ruff check --select NPY201` is clean; no removed aliases.

## Gotchas

These are the NumPy bugs seen most.

1. **`AttributeError: np.float_ was removed in the NumPy 2.0 release`.** Old aliases. Use `np.float64`, `np.nan`, `np.inf`, `np.str_`; Ruff's `NPY201` fixes most.
2. **An array changed "by itself".** Another name holds a view of it. Copy at the boundary; check with `np.shares_memory`.
3. **A result has shape `(n, n)` instead of `(n,)`.** A column `(n, 1)` broadcast against a vector. Flatten one side or assert shapes.
4. **`ValueError: The truth value of an array with more than one element is ambiguous`.** `and`, `or` or `if` on an array. Use `&`, `|`, `.any()`, `.all()`.
5. **Sums differ between Windows and Linux on old code.** NumPy 1.x defaulted to `int32` on Windows and overflowed; NumPy 2 uses `int64` everywhere. Set integer dtypes explicitly.
6. **`ValueError: Unable to avoid copy while creating an array as requested`.** `np.array(x, copy=False)` from NumPy 1 code. Use `np.asarray(x)`.
7. **`float32` results differ after upgrading to NumPy 2.** NEP 50: `np.float32(3) + 3.0` now stays `float32`. Cast explicitly where precision matters.

## References

The reference files hold the longer recipes.

- [numpy-2-changes.md](references/numpy-2-changes.md): removed and renamed names, NEP 50 promotion, the copy keyword, StringDType and `np.strings`, the array API additions, 2.1 to 2.5 changes, upgrading a codebase
- [memory-and-performance.md](references/memory-and-performance.md): C and Fortran order, strides, sliding windows, in-place operations and `out=`, avoiding temporaries, `einsum`, memory-mapped files, BLAS threads, zero-copy exchange with other libraries

## See also

- `pandas`: labelled tables on top of NumPy
- `polars`: lazy, multi-core queries over columns
- `python-performance`: profiling, and compiling a loop that must stay a loop
- `python-numbers-decimal`: exact decimals and float precision
- `python-concurrency`: threads and processes around array work
