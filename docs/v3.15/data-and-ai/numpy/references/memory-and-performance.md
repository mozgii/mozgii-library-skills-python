---
title: NumPy memory layout and performance
author: MOZGII, Inc.
lang: en
---

# NumPy memory layout and performance

How NumPy arrays sit in memory and how that decides speed: C and Fortran order, strides and contiguity, sliding windows without copies, in-place operations and `out=`, temporaries, `einsum`, memory-mapped files, BLAS threads, and passing arrays to pandas, Polars and other libraries without copying. Every example ran on NumPy 2.5.4.

## Contents

This reference has 9 sections.

1. Order, strides and contiguity
2. Making an array contiguous
3. Sliding windows without copies
4. In-place operations and out=
5. Avoiding large temporaries
6. einsum for compound products
7. Memory-mapped files
8. BLAS threads and parallel code
9. Zero-copy exchange with other libraries

## 1. Order, strides and contiguity

An array is a block of memory plus a shape and strides: the number of bytes to step for each axis. C order (row-major, the default) keeps each row contiguous; Fortran order keeps each column contiguous. Transposes and step slices change strides, not data.

```python
import numpy as np

a = np.arange(12, dtype=np.float64).reshape(3, 4)
a.strides, a.flags.c_contiguous                        # ((32, 8), True)
a.T.strides, a.T.flags.c_contiguous, a.T.flags.f_contiguous   # ((8, 32), False, True)
a[:, ::2].flags.c_contiguous                           # False: every second column
```

Reductions and element-wise loops are fastest along the contiguous axis. Summing a C-order array along `axis=1` (within rows) reads memory sequentially; along `axis=0` it jumps a row length each step, which matters for large arrays.

## 2. Making an array contiguous

Some code needs contiguous memory: C extensions, `ctypes`, many GPU transfers, `tobytes()` in a defined order, and zero-copy exchange. `np.ascontiguousarray` returns the array itself when it already is, and a C-order copy otherwise.

```python
col = a[:, 1]                                          # a strided view of one column
col.flags.c_contiguous                                 # False
c = np.ascontiguousarray(col)
c.flags.c_contiguous, np.shares_memory(c, a)           # (True, False): copied once
np.ascontiguousarray(a) is a                           # True: no copy when already contiguous
```

Write functions that call `np.ascontiguousarray` at the boundary where the requirement exists, not everywhere.

## 3. Sliding windows without copies

`np.lib.stride_tricks.sliding_window_view` builds a view of every window of a series by manipulating strides; no data is copied until you compute on it.

```python
from numpy.lib.stride_tricks import sliding_window_view

x = np.array([1.0, 2.0, 4.0, 8.0, 16.0])
w = sliding_window_view(x, 3)                          # shape (3, 3), read-only view
w.mean(axis=1)                                         # array([2.33333333, 4.66666667, 9.33333333])
np.convolve(x, np.ones(3) / 3, mode="valid")           # the same moving average, faster for long series
```

The view is read-only on purpose: overlapping windows share elements, so writing would change several windows at once. For large windows on long series, `np.convolve`, `np.cumsum` differences or a pandas/Polars rolling function is faster than reducing a window view.

## 4. In-place operations and out=

`a += b` and ufuncs with `out=` write into existing memory instead of allocating a new array. In tight numeric loops this halves memory traffic.

```python
buf = np.ones(1_000_000)
other = np.full(1_000_000, 2.0)
np.multiply(buf, other, out=buf)                       # buf *= other, no new array
np.add(buf, 1.0, out=buf)
float(buf[0])                                          # 3.0
```

- In-place operations follow casting rules: `int_array += 0.5` raises `UFuncTypeError` because the float result cannot be stored in integers without `casting="unsafe"`.
- Never write into an array that callers still hold, unless the function's contract says so; in-place work belongs inside a function on its own buffers.
- `np.maximum(a, b, out=c)`: pass `out` by keyword; a positional `out` is deprecated since 2.4.

## 5. Avoiding large temporaries

Every operator in an expression creates a full temporary array. `y = a * b + c * d - e` allocates four arrays the size of the input; for arrays near the memory limit, that is the difference between running and swapping.

- Split the expression and reuse buffers with `out=`, as in section 4.
- Reduce early: `(a * b).sum()` allocates `a * b`; `np.vecdot(a, b)` or `a @ b` for 1-D arrays does not.
- Use `float32` where the precision is enough, and `np.int32` or smaller for counts and codes.
- Process data in chunks (rows of a memory-mapped file, batches from a reader) instead of loading everything.
- `numexpr` evaluates a whole expression in cache-sized blocks without temporaries; check its release notes for the version you install.

## 6. einsum for compound products

`np.einsum` writes tensor products, traces, transposes and reductions in one call using index letters; it often avoids temporaries and makes the intent explicit.

```python
A = np.arange(6.0).reshape(2, 3)
B = np.arange(12.0).reshape(3, 4)
bool(np.allclose(np.einsum("ij,jk->ik", A, B), A @ B))   # True: matrix product
np.einsum("ij,ij->i", A, A)                            # array([ 5., 50.]): row-wise squared norms
batch = np.arange(24.0).reshape(2, 3, 4)
np.einsum("bij->bji", batch).shape                     # (2, 4, 3): batched transpose, a view
```

For chains of three or more operands, pass `optimize=True` so NumPy picks the cheapest contraction order. For plain matrix products, `@` calls BLAS directly and is at least as fast.

## 7. Memory-mapped files

`np.memmap` and `np.load(..., mmap_mode="r")` map a file into memory: the OS loads pages on access, so an array larger than RAM can be processed in slices.

```python
big = np.arange(1_000_000, dtype=np.float32)
np.save("big.npy", big)
mm = np.load("big.npy", mmap_mode="r")                 # nothing read yet
total = sum(float(mm[i:i + 100_000].sum(dtype=np.float64)) for i in range(0, mm.shape[0], 100_000))
total                                                  # 499999500000.0
del mm                                                 # release the mapping
```

- `.npy` files store dtype and shape in a header, which makes them safer than raw `np.memmap` files that need both passed in.
- On Windows a mapped file cannot be deleted or overwritten while a mapping is open; drop every reference (`del mm`) before replacing it.
- `np.load` refuses pickled object arrays unless `allow_pickle=True`; leave it off for files from elsewhere (the python-security skill).

## 8. BLAS threads and parallel code

Matrix products and `np.linalg` run in the BLAS library, which starts its own threads: OpenBLAS in Linux and Windows wheels, Apple Accelerate in macOS arm64 wheels (`np.show_config()` names it). Many NumPy functions release the GIL, so Python threads can run array code in parallel.

- A process pool where each worker also starts one BLAS thread per core oversubscribes the CPU. Limit BLAS threads per worker with `threadpoolctl.threadpool_limits(1)` or by setting `OPENBLAS_NUM_THREADS=1` (`OMP_NUM_THREADS` for OpenMP builds) before NumPy is imported.
- Element-wise ufuncs run on one thread; for multi-core element-wise work, split the array across a `ThreadPoolExecutor` (the GIL is released inside the loops) or use Numba with `parallel=True`.
- Free-threaded Python 3.14t is supported by NumPy 2.x wheels, and 2.5 improved ufunc scaling there; measure before relying on it.

## 9. Zero-copy exchange with other libraries

Arrays move between libraries without copying when the receiver can use the same memory and dtype. Check with `np.shares_memory` instead of assuming.

```python
import pandas as pd
import polars as pl

arr = np.arange(6, dtype=np.float64)
s = pl.Series("x", arr)
back = s.to_numpy()
np.shares_memory(arr, back), back.flags.writeable      # (True, False): zero-copy, read-only
df = pd.DataFrame({"x": arr})
np.shares_memory(arr, df["x"].to_numpy())              # False: pandas 3 copied on construction
np.shares_memory(arr, np.from_dlpack(arr))             # True: DLPack exchange
```

| Target | Zero-copy path | Copies when |
|---|---|---|
| Polars | `pl.Series(arr)`, `series.to_numpy()` | nulls present, non-numeric dtypes, chunked series |
| pandas 3 | `pd.Series(arr, copy=False)`, `.to_numpy()` (read-only under copy-on-write) | building from a dict of arrays, mixed dtypes in `DataFrame.to_numpy()` |
| PyTorch, JAX, CuPy | `torch.from_dlpack(arr)`, `np.from_dlpack(tensor)` on CPU | the device differs (GPU memory) |
| C, Rust, `ctypes` | buffer protocol, `memoryview(arr)`, `arr.ctypes.data` | the array is not contiguous |

Read-only results are deliberate: writing into memory owned by another library would corrupt it. Call `.copy()` when you need a writable array.
