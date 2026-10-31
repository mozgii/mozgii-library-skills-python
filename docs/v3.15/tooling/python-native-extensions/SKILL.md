---
name: python-native-extensions
description: Calls C, C++ and Rust from Python and ships the result - when native code is worth it, and choosing between ctypes, cffi, Cython 3.3, nanobind 3.1, pybind11 3.1 and PyO3 0.30 with maturin 1.16; argtypes and restype, buffers without copies, releasing the GIL, the stable ABI (abi3) and the free-threaded stable ABI (abi3t, Python 3.15), free-threaded builds that keep the GIL off, scikit-build-core for CMake projects, building wheels for macOS, Linux and Windows with cibuildwheel 4, the macOS deployment target, and debugging segfaults in extensions with faulthandler, lldb or gdb. Use when wrapping a C or C++ library, writing a fast extension module, deciding between Cython, nanobind and Rust, building or fixing platform wheels, supporting free-threaded Python, or a Python process crashes inside native code.
metadata:
  tags:
  - python
  - c-extensions
  - ctypes
  - cffi
  - cython
  - nanobind
  - pyo3
  - cibuildwheel
---

# Native extensions for Python

Native code makes sense for a library that exists only in C, C++ or Rust, or a measured hot loop that Python and NumPy cannot make fast enough. It costs a toolchain, platform wheels, and crashes that kill the process instead of raising. Profiling first is the python-performance skill; general packaging is the python-packaging skill.

## Step 0: read the interpreter and the toolchain

Extensions are built for one interpreter ABI; read which one runs.

```bash
uv run python -c "import sys, sysconfig; print(sys.version); print(sysconfig.get_config_var('EXT_SUFFIX')); print('free-threaded:', bool(sysconfig.get_config_var('Py_GIL_DISABLED')))"
cc --version 2>/dev/null | head -1          # clang on macOS (Xcode tools), gcc or clang on Linux
cargo --version 2>/dev/null                  # only for Rust extensions
uv pip list 2>/dev/null | grep -iE "cython|cffi|nanobind|pybind11|maturin|scikit-build|cibuildwheel"
```

On Windows the compiler is MSVC from Visual Studio Build Tools. Current releases (October 2026): Cython 3.3.0, cffi 2.1.1, nanobind 3.1.0, pybind11 3.1.0, PyO3 0.30.1 with maturin 1.16.0, scikit-build-core 1.1.1, cibuildwheel 4.3.5.

## When native code is worth it

Go native only after measuring, and only for the part that is slow.

- First try a better algorithm, NumPy or Polars, and caching; they often give 10x to 100x without a compiler.
- Tight loops (parsing, scanning, small numeric kernels) gain 10x to 100x natively; I/O-bound work gains nothing. Pass whole arrays, not one item per call.

## The options compared

Each tool fits a different job; mixing them is fine.

| Tool | You write | Build step | Best for |
|---|---|---|---|
| ctypes (stdlib) | Python declarations of C signatures | none; loads a shared library at runtime | a few calls into an existing library |
| cffi 2.1 | C declarations in a string | API mode compiles a module; ABI mode needs none | wrapping a C library with structs and callbacks |
| Cython 3.3 | Python with C types (`.pyx`, or pure-Python mode) | compiles to C | numeric loops, gradual speed-ups of Python code |
| nanobind 3.1 | C++17 binding code | CMake via scikit-build-core | new C++ bindings; small, fast binaries; stable ABI |
| pybind11 3.1 | C++ binding code | CMake or setuptools | existing pybind11 code bases |
| PyO3 0.30 + maturin | Rust with attributes | `maturin build` | new performance code with memory safety; Rust libraries |

nanobind is pybind11's successor by the same author: similar API, smaller and faster binaries, stable-ABI support. Choose pybind11 for existing code bases.

## ctypes: call a shared library with no build step

ctypes loads a shared library and calls its functions; you declare each signature in Python. It ships with Python and needs no compiler on the user's machine, only the library.

```c
// vec.c, built with: cc -O2 -shared -fPIC -fvisibility=hidden -o libvec.dylib vec.c
#include <stddef.h>
#if defined(_WIN32)
#  define EXPORT __declspec(dllexport)
#else
#  define EXPORT __attribute__((visibility("default")))
#endif

EXPORT double dot(const double *a, const double *b, size_t n) {
    double s = 0.0;
    for (size_t i = 0; i < n; i++) s += a[i] * b[i];
    return s;
}
```

The Python side declares argument and return types, and passes an `array('d')` without copying it:

```python
import ctypes
import sys
from array import array
from pathlib import Path

_NAMES = {"darwin": "libvec.dylib", "win32": "vec.dll"}
_lib = ctypes.CDLL(str(Path(__file__).with_name(_NAMES.get(sys.platform, "libvec.so"))))

_dbl_p = ctypes.POINTER(ctypes.c_double)
_lib.dot.argtypes = [_dbl_p, _dbl_p, ctypes.c_size_t]
_lib.dot.restype = ctypes.c_double


def dot(a: array, b: array) -> float:
    if a.typecode != "d" or b.typecode != "d" or len(a) != len(b):
        raise ValueError("need two array('d') of equal length")
    pa = (ctypes.c_double * len(a)).from_buffer(a)      # no copy: C reads the array's memory
    pb = (ctypes.c_double * len(b)).from_buffer(b)
    return _lib.dot(pa, pb, len(a))
```

`dot(array("d", [1, 2, 3]), array("d", [4, 5, 6]))` returns `32.0`. Structs (`ctypes.Structure`) and output parameters (`ctypes.byref`) are in [build-recipes.md](references/build-recipes.md).

- Always set `argtypes` and `restype`; the default C `int` truncates 64-bit pointers and sizes, and the process crashes.
- Calls through `CDLL` release the GIL for their duration; `PyDLL` keeps it.
- Library names differ: `libvec.dylib` (macOS), `libvec.so` (Linux), `vec.dll` (Windows). Errors: `use_errno=True` and `ctypes.get_errno()`, or on Windows `WinDLL(name, use_last_error=True)` and `ctypes.get_last_error()`.

## cffi: C declarations, compiled glue

cffi reads C declarations from a string. In API mode it compiles a small extension module that the C compiler checks against the real header, which catches signature mistakes ctypes cannot.

```python
# build_vec.py: run once at build time
from cffi import FFI

ffibuilder = FFI()
ffibuilder.cdef("double dot(const double *a, const double *b, size_t n);")
ffibuilder.set_source("_vec", '#include "vec_api.h"', sources=["vec.c"])

if __name__ == "__main__":
    ffibuilder.compile(verbose=False)
```

The compiled `_vec` module exposes `ffi` and `lib`: `lib.dot(ffi.from_buffer("double[]", a), ffi.from_buffer("double[]", b), len(a))` passes buffers without copying, and `ffi.new("double[]", 1000)` allocates C memory that is freed when the Python object is collected, so keep a reference while C uses it. Structs and packaging: [build-recipes.md](references/build-recipes.md).

## Cython: Python syntax compiled to C

Cython compiles `.pyx` files (or annotated pure-Python files) to C extension modules. Typed memoryviews give direct access to buffers, and `with nogil:` releases the GIL around pure C code.

```cython
# cython: language_level=3, boundscheck=False, wraparound=False

def dot(const double[::1] a, const double[::1] b) -> float:
    """Dot product of two contiguous float64 buffers, computed without the GIL."""
    if a.shape[0] != b.shape[0]:
        raise ValueError("length mismatch")
    cdef Py_ssize_t i, n = a.shape[0]
    cdef double s = 0.0
    with nogil:
        for i in range(n):
            s += a[i] * b[i]
    return s
```

```bash
uv run --with cython --with setuptools cythonize -i fastdot.pyx     # builds fastdot.cpython-314-darwin.so in place
uv run --with cython cython -a fastdot.pyx                          # fastdot.html: yellow lines call into Python
```

Make the hot loop white in the `-a` report, and turn off `boundscheck` and `wraparound` only after tests pass with them on.

## nanobind and pybind11: C++ bindings

nanobind binds C++ functions and classes with a few lines per item and builds with CMake through scikit-build-core. This module accepts NumPy arrays (or any buffer) without copying and releases the GIL inside the loop.

```cpp
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>

namespace nb = nanobind;

double dot(nb::ndarray<const double, nb::ndim<1>, nb::c_contig, nb::device::cpu> a,
           nb::ndarray<const double, nb::ndim<1>, nb::c_contig, nb::device::cpu> b) {
    if (a.shape(0) != b.shape(0)) throw std::invalid_argument("length mismatch");
    const double *pa = a.data(), *pb = b.data();
    size_t n = a.shape(0);
    double s = 0.0;
    {
        nb::gil_scoped_release release;      // no Python objects touched inside
        for (size_t i = 0; i < n; ++i) s += pa[i] * pb[i];
    }
    return s;
}

NB_MODULE(_core, m) {
    m.doc() = "Vector kernels";
    m.def("dot", &dot, nb::arg("a"), nb::arg("b"), "Dot product of two 1-D float64 arrays.");
}
```

- `std::invalid_argument` arrives in Python as `ValueError`; wrong argument types raise `TypeError: dot(): incompatible function arguments`.
- nanobind casts compatible inputs (an int64 array to float64); `nb::arg("a").noconvert()` refuses that.
- pybind11 code looks almost the same (`PYBIND11_MODULE`, `py::gil_scoped_release`). CMake and `pyproject.toml`: [build-recipes.md](references/build-recipes.md).

## PyO3 and maturin: Rust extensions

PyO3 exposes Rust functions and types to Python; maturin builds and packages them. `maturin new --bindings pyo3` creates a project; `maturin develop` builds into the active environment.

```rust
#[pyo3::pymodule]
mod fastwords {
    use pyo3::prelude::*;

    /// Count whitespace-separated words without holding the GIL.
    #[pyfunction]
    fn count_words(py: Python<'_>, text: String) -> usize {
        py.detach(|| text.split_whitespace().count())
    }
}
```

- PyO3 0.26 renamed `allow_threads` to `detach` and `with_gil` to `attach`; older examples use the old names.
- Since 0.28 modules declare free-threaded support by default. `features = ["abi3-py312"]` on the `pyo3` dependency builds one stable-ABI wheel for 3.12+. Rust panics become `PanicException`.

## The stable ABI: abi3 and abi3t

A normal extension is built for one Python version (`_core.cpython-314-darwin.so`, tag `cp314-cp314`). One limited to the stable ABI (`_core.abi3.so`, tag `cp312-abi3`) loads on that version and every later one: one wheel per platform instead of one per Python.

- Opt in per tool: nanobind's `STABLE_ABI` flag (3.12+) with scikit-build-core's `wheel.py-api = "cp312"`, PyO3's `abi3-py312` feature, Cython with `Py_LIMITED_API` defined, left out on a `t` interpreter (build recipe in the references). Benchmark: call-heavy code can lose a few percent.
- abi3 wheels do not install on free-threaded Pythons: uv refuses with "the wheel was built for the stable ABI (`abi3`), which requires a GIL-enabled interpreter".
- Python 3.15 adds `abi3t` (PEP 803), a stable ABI for free-threaded builds. It needs source changes (PEP 697 type data, the PEP 793 `PyModExport_*` hook); scikit-build-core, meson-python, maturin and PyO3 support it. Until you port, build `cp314t` and `cp315t` wheels separately.

## Free-threaded builds

On a free-threaded interpreter (3.14t, 3.15t), importing an extension that has not declared support turns the GIL back on for the whole process, with "RuntimeWarning: The global interpreter lock (GIL) has been enabled to load module 'fastvec._core', which has not declared that it can run safely without the GIL".

| Tool | Declare support |
|---|---|
| nanobind | `FREE_THREADED` in `nanobind_add_module(...)` |
| pybind11 | `PYBIND11_MODULE(name, m, py::mod_gil_not_used())` |
| Cython | `# cython: freethreading_compatible = True` |
| PyO3 | default since 0.28 |
| C API | the `Py_mod_gil` slot set to `Py_MOD_GIL_NOT_USED` |

Declare it only when the code is safe without the GIL: no unprotected global or static mutable state. Check `sys._is_gil_enabled()` after import and run the tests under threads on the `t` interpreter ([crashes-and-threads.md](references/crashes-and-threads.md)).

## Build wheels for every platform with cibuildwheel

Users must not need a compiler, so publish wheels for every OS, CPU and Python you support. cibuildwheel builds them in CI (manylinux and musllinux containers on Linux), repairs each wheel (auditwheel, delocate, delvewheel) and tests it.

```toml
[tool.cibuildwheel]
build-frontend = "build[uv]"
test-requires = ["pytest", "numpy"]
test-command = "pytest {project}/tests -q"

[tool.cibuildwheel.macos]
environment = { MACOSX_DEPLOYMENT_TARGET = "11.0" }
```

- cibuildwheel 4 builds every CPython your `requires-python` allows, including `cp314t` and `cp315t`; `skip = ["cp3??t-*"]` leaves free-threaded ones out until you support them.
- `uvx cibuildwheel --print-build-identifiers --platform linux` lists what it would build. Since 4.0 it runs `abi3audit` on abi3 wheels and `delvewheel` on Windows.
- A local macOS build without `MACOSX_DEPLOYMENT_TARGET` targets the build machine's own version (`macosx_27_0_arm64`), which older Macs refuse.
- `python3 -I scripts/wheel_tags.py --max-macos 11.0 wheelhouse/*.whl` lists each wheel's tags and modules and flags `linux_x86_64` tags, too-new macOS targets and abi3 or free-threaded mismatches. A library installed from a pack holds no script file; the Verify section has the core of that check inline.
- The GitHub Actions matrix: [build-recipes.md](references/build-recipes.md).

## Debug a crash in an extension

A crash in native code ends the process with "Segmentation fault" (exit code 139 or -11 on POSIX, `0xC0000005` on Windows) and no traceback. Start with faulthandler, which prints the Python stack and, on 3.14+, the C stack.

```bash
python -X faulthandler crash.py           # or PYTHONFAULTHANDLER=1
```

It prints "Fatal Python error: Segmentation fault", the Python frames ("File \"crash.py\", line 3"), then "Current thread's C stack trace". Then rebuild with `-g -O0` and run under `lldb -- python crash.py` (macOS), `gdb --args python crash.py` (Linux) or Visual Studio (Windows); AddressSanitizer finds memory errors. Workflow: [crashes-and-threads.md](references/crashes-and-threads.md).

## Verify

Prove a wheel, not a build folder: inspect its tags, then install it into a clean environment and call it. This inline check covers the core of `scripts/wheel_tags.py`, which may not be on disk after a pack install.

```bash
MACOSX_DEPLOYMENT_TARGET=11.0 uv build                    # the wheel is built from the sdist
python3 -I - dist/*.whl <<'PY'
import re, sys, zipfile
from pathlib import Path

for name in sys.argv[1:]:
    python, abi, plat = Path(name).stem.split("-")[-3:]
    modules = [m for m in zipfile.ZipFile(name).namelist() if m.endswith((".so", ".pyd"))]
    print(Path(name).name, python, abi, plat, modules)
    if any(tag.startswith("linux_") for tag in plat.split(".")):
        print("  PROBLEM: PyPI refuses this tag; repair with auditwheel")
    if (mac := re.match(r"macosx_(\d+)_", plat)) and int(mac[1]) > 11:
        print(f"  PROBLEM: needs macOS {mac[1]}; set MACOSX_DEPLOYMENT_TARGET")
    if modules and abi == "none":
        print("  PROBLEM: compiled modules, but the ABI tag is none")
PY
WHEEL=$(ls dist/*.whl | head -1)
uv run --isolated --no-project --with "$WHEEL" python -c "import fastdot; print(fastdot.__file__)"   # the installed wheel, not the source
uv run --isolated --no-project --python 3.14t --with "$WHEEL" python -c "import sys, fastdot; print(sys._is_gil_enabled())"   # on a t wheel, expect False
uvx abi3audit dist/*abi3*.whl                             # only for abi3 wheels
```

## Anti-patterns

| Instead of | Do |
|---|---|
| going native before measuring | a profile, then NumPy or a better algorithm |
| one native call per item | pass the whole array or buffer |
| ctypes without `argtypes` and `restype` | declare every signature |
| `Py_LIMITED_API` on a `t` build | a separate `cp314t` wheel; `abi3t` needs 3.15 |
| declaring free-threaded support untested | threaded tests on a `t` interpreter first |
| a wheel built on your laptop for PyPI | cibuildwheel in CI |
| holding the GIL across a long loop | `with nogil`, `gil_scoped_release`, `py.detach` |

## Pre-merge checklist

A native extension is ready to ship when every item holds.

- [ ] A profile showed the hot spot, and the native version is measured faster on real data.
- [ ] Inputs are validated before native code touches memory.
- [ ] Long native loops release the GIL and touch no Python objects meanwhile.
- [ ] Wheels exist for every supported OS, CPU and Python (or abi3), built and tested by cibuildwheel.
- [ ] macOS wheels target an explicit `MACOSX_DEPLOYMENT_TARGET`; Linux wheels are manylinux or musllinux.
- [ ] Free-threaded support is declared only after thread-safety review and threaded tests on a `t` build.

## Gotchas

These are the native-extension problems seen most.

1. **ctypes returns garbage or crashes on 64-bit pointers.** `restype` was left as the default C `int`. Set `argtypes` and `restype` on every function.
2. **The free-threaded interpreter prints "The global interpreter lock (GIL) has been enabled to load module".** The extension did not declare support. Add the declaration for your tool, after making it thread-safe.
3. **`pip install` builds from source and fails on users' machines.** No wheel matched their platform or Python. Add the missing cibuildwheel identifiers, or ship abi3.
4. **A macOS wheel installs on the build machine only.** Its tag is `macosx_27_0`. Set `MACOSX_DEPLOYMENT_TARGET=11.0`.
5. **PyPI rejects a Linux wheel tagged `linux_x86_64`.** Build it in a manylinux container and repair it with auditwheel, as cibuildwheel does.
6. **Threads do not speed up the native function.** The GIL is held during the loop. Release it (`with nogil`, `gil_scoped_release`, `py.detach`).
7. **The extension crashes only in release builds or only sometimes.** Undefined behaviour: out-of-bounds access, a use after free, a missing reference count. Rebuild with `-g -O0` and AddressSanitizer and run the tests.

## References

The reference files hold the build and debugging detail.

- [build-recipes.md](references/build-recipes.md): complete nanobind project with scikit-build-core, Cython with setuptools and the limited API, packaging cffi modules, a maturin project layout, cibuildwheel configuration and a GitHub Actions wheel matrix, per-platform compilers and settings
- [crashes-and-threads.md](references/crashes-and-threads.md): faulthandler, lldb, gdb and Windows debuggers, AddressSanitizer, `PYTHONMALLOC=debug`, reference-count bugs, releasing the GIL safely in each tool, making extensions thread-safe for free-threaded Python, and testing them

## See also

- `python-performance`: finding the hot spot and mypyc
- `python-packaging`: metadata, trusted publishing and sdists
- `python-debugging`: faulthandler and post-mortem debugging
- `python-concurrency`: free-threaded Python from the Python side
- `python-ml-packages`: choosing builds per accelerator
