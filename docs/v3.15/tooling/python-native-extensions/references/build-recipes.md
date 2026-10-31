---
title: Build recipes for Python native extensions
author: MOZGII, Inc.
lang: en
---

# Build recipes for Python native extensions

Complete, buildable project layouts for each binding tool, and the CI that turns them into wheels for every platform: nanobind with scikit-build-core (stable ABI, free-threaded, generated stubs), ctypes with structs, cffi and Cython packaged with setuptools, a maturin project for Rust, and cibuildwheel on GitHub Actions. The nanobind, cffi and Cython projects here were built with `uv build` on macOS and their wheels installed and run on Python 3.12, 3.14 and 3.14t, the free-threaded wheels with the GIL still off.

## Contents

This reference has 8 sections.

1. nanobind with scikit-build-core
2. Free-threaded and stable-ABI wheels from one project
3. ctypes with structs and a bundled library
4. cffi packaged with setuptools
5. Cython packaged with setuptools
6. A maturin project for Rust
7. cibuildwheel on GitHub Actions
8. Gotchas

## 1. nanobind with scikit-build-core

scikit-build-core is a build backend that runs CMake; nanobind ships CMake functions for modules and stubs. Keep C++ sources outside the Python package folder, or they are copied into the wheel.

```text
fastvec/
├── pyproject.toml
├── CMakeLists.txt
└── src/
    ├── cpp/_core.cpp          # the binding code (the nanobind example in the main skill)
    └── fastvec/__init__.py    # from fastvec._core import dot
```

```toml
[build-system]
requires = ["scikit-build-core>=1.1", "nanobind>=3.1"]
build-backend = "scikit_build_core.build"

[project]
name = "fastvec"
version = "0.1.0"
requires-python = ">=3.12"

[tool.scikit-build]
minimum-version = "build-system.requires"
build-dir = "build/{wheel_tag}"
wheel.py-api = "cp312"            # one abi3 wheel for 3.12+ (nanobind needs 3.12+ for the stable ABI)
```

The CMake file builds the module, installs it into the package, and generates a typed stub plus `py.typed`:

```cmake
cmake_minimum_required(VERSION 3.15...3.31)
project(${SKBUILD_PROJECT_NAME} LANGUAGES CXX)

find_package(Python 3.12 REQUIRED COMPONENTS Interpreter Development.Module ${SKBUILD_SABI_COMPONENT})
find_package(nanobind CONFIG REQUIRED)

nanobind_add_module(_core STABLE_ABI FREE_THREADED NB_STATIC src/cpp/_core.cpp)
install(TARGETS _core LIBRARY DESTINATION fastvec)

nanobind_add_stub(_core_stub
  MODULE _core
  OUTPUT _core.pyi
  PYTHON_PATH $<TARGET_FILE_DIR:_core>
  DEPENDS _core
  MARKER_FILE py.typed)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/_core.pyi ${CMAKE_CURRENT_BINARY_DIR}/py.typed DESTINATION fastvec)
```

```bash
MACOSX_DEPLOYMENT_TARGET=11.0 uv build             # dist/fastvec-0.1.0-cp312-abi3-macosx_11_0_arm64.whl and the sdist
```

- scikit-build-core downloads CMake and Ninja from PyPI when the machine has none, so the build needs only a C++ compiler.
- The wheel contains `fastvec/__init__.py`, `_core.abi3.so`, `_core.pyi` and `py.typed`. The generated stub types ndarray arguments as `numpy.typing.NDArray[numpy.float64]`, so type checkers need NumPy installed.
- `build-dir = "build/{wheel_tag}"` keeps a build folder per interpreter, which makes rebuilds incremental.
- `NB_STATIC` links nanobind's runtime into the module; drop it when several extension modules in one package should share the runtime.

## 2. Free-threaded and stable-ABI wheels from one project

The same project builds an abi3 wheel on a regular interpreter and a free-threaded wheel on a `t` interpreter: scikit-build-core does not apply `wheel.py-api` to free-threaded builds.

```bash
MACOSX_DEPLOYMENT_TARGET=11.0 uv build --wheel --python 3.14   # fastvec-0.1.0-cp312-abi3-macosx_11_0_arm64.whl
MACOSX_DEPLOYMENT_TARGET=11.0 uv build --wheel --python 3.14t  # fastvec-0.1.0-cp314-cp314t-macosx_11_0_arm64.whl
```

With `FREE_THREADED` in `nanobind_add_module`, the module loads on 3.14t with the GIL still off (`sys._is_gil_enabled()` returns `False`). Without the flag, the same import prints "RuntimeWarning: The global interpreter lock (GIL) has been enabled to load module 'fastvec._core'" and the GIL stays on for the process. On Python 3.15 an `abi3t` build can replace the per-version `t` wheels once the code meets PEP 803's requirements.

## 3. ctypes with structs and a bundled library

ctypes maps C structs to `ctypes.Structure` subclasses and output parameters to `ctypes.byref`. The C side adds a struct and a function that fills one:

```c
typedef struct { double x, y; } Point;

EXPORT void centroid(const Point *pts, size_t n, Point *out) {
    out->x = out->y = 0.0;
    for (size_t i = 0; i < n; i++) { out->x += pts[i].x; out->y += pts[i].y; }
    if (n) { out->x /= n; out->y /= n; }
}
```

```python
import ctypes                                     # _lib: the CDLL from the main skill's ctypes example


class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


_lib.centroid.argtypes = [ctypes.POINTER(Point), ctypes.c_size_t, ctypes.POINTER(Point)]
_lib.centroid.restype = None


def centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    buf = (Point * len(points))(*points)          # a C array of Point, filled from tuples
    out = Point()
    _lib.centroid(buf, len(points), ctypes.byref(out))
    return out.x, out.y
```

`centroid([(0, 0), (4, 0), (4, 3), (0, 3)])` returns `(2.0, 1.5)`.

- `_fields_` must match the C layout exactly, including order and types; ctypes cannot check it. cffi's API mode can.
- A library shipped inside your package is loaded by its absolute path (`Path(__file__).with_name(...)`), never by bare name, which would search system paths.
- On Windows, Python 3.8+ does not search `PATH` for a DLL's dependencies. Dependencies next to the loaded DLL are found; ones in another folder need `os.add_dll_directory(folder)` before loading.
- The shared library must be built per platform (`.dylib`, `.so`, `.dll`), so a package that bundles one still needs platform wheels.

## 4. cffi packaged with setuptools

cffi's API mode compiles a module at build time. setuptools runs the builder through the `cffi_modules` argument.

```text
vec/
├── pyproject.toml
├── setup.py
├── MANIFEST.in               # recursive-include csrc *.c *.h
├── csrc/vec.c, csrc/vec_api.h
└── src/vec/__init__.py, src/vec/_build.py
```

```python
# src/vec/_build.py
from cffi import FFI

ffibuilder = FFI()
ffibuilder.cdef("""
    typedef struct { double x, y; } Point;
    double dot(const double *a, const double *b, size_t n);
    void centroid(const Point *pts, size_t n, Point *out);
""")
ffibuilder.set_source(
    "vec._vec",
    '#include "vec_api.h"',
    sources=["csrc/vec.c"],
    include_dirs=["csrc"],
)
```

```python
# setup.py
from setuptools import setup

setup(cffi_modules=["src/vec/_build.py:ffibuilder"])
```

```toml
[build-system]
requires = ["setuptools>=80", "cffi>=2.1"]
build-backend = "setuptools.build_meta"

[project]
name = "vec"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["cffi>=2.1"]

[tool.setuptools.packages.find]
where = ["src"]
```

The package then wraps the raw functions:

```python
# src/vec/__init__.py
from array import array

from vec._vec import ffi, lib


def centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    pts = ffi.new("Point[]", points)
    out = ffi.new("Point *")
    lib.centroid(pts, len(pts), out)
    return out.x, out.y
```

- The compiled module needs the `cffi` package at runtime, so it is in `dependencies` as well as `build-system.requires`.
- `MANIFEST.in` puts the C sources into the sdist; without it, building from the sdist fails.
- cffi compiles `_vec.abi3.so` against the limited API, but setuptools still tags the wheel `cp314-cp314`. Add `options={"bdist_wheel": {"py_limited_api": "cp312"}}` to `setup()` to tag it abi3, and leave that option out on a free-threaded interpreter, where setuptools refuses it.

## 5. Cython packaged with setuptools

Cython's own build integration is setuptools; `cythonize` turns `.pyx` files into extensions. This setup builds a stable-ABI wheel on a regular interpreter and a `cp314t` wheel on a free-threaded one, because 3.14t has no stable ABI: there setuptools stops with "`py_limited_api='cp312'` not supported. `Py_LIMITED_API` is incompatible with `Py_GIL_DISABLED` on Python 3.14 and older".

```python
# setup.py
import sysconfig

from Cython.Build import cythonize
from setuptools import Extension, setup

FREE_THREADED = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))

ext = Extension(
    "fastdot._kernels",
    ["src/fastdot/_kernels.pyx"],
    define_macros=[] if FREE_THREADED else [("Py_LIMITED_API", "0x030C0000")],   # 3.12 stable ABI
    py_limited_api=not FREE_THREADED,
)
setup(
    ext_modules=cythonize([ext], compiler_directives={"freethreading_compatible": True}),
    options={} if FREE_THREADED else {"bdist_wheel": {"py_limited_api": "cp312"}},
)
```

```toml
[build-system]
requires = ["setuptools>=80", "cython>=3.3"]
build-backend = "setuptools.build_meta"

[project]
name = "fastdot"
version = "0.1.0"
requires-python = ">=3.12"

[tool.setuptools.packages.find]
where = ["src"]
```

```text
# MANIFEST.in
recursive-include src *.pyx
global-exclude *.c *.so
```

- Without the `MANIFEST.in` line, the sdist ships the generated `_kernels.c` but not the `.pyx`, and building the wheel from the sdist fails with "'src/fastdot/_kernels.pyx' doesn't match any files".
- `uv build` (sdist, then wheel from it) catches that; a plain wheel build from the source tree does not.
- For larger projects, meson-python with Cython is the other common backend, used by NumPy and SciPy.

## 6. A maturin project for Rust

maturin builds PyO3 crates into wheels; `maturin new --bindings pyo3 fastwords` creates this layout.

```toml
# Cargo.toml
[package]
name = "fastwords"
version = "0.1.0"
edition = "2024"

[lib]
name = "fastwords"
crate-type = ["cdylib"]

[dependencies]
pyo3 = { version = "0.30.1", features = ["abi3-py312"] }
```

```toml
# pyproject.toml
[build-system]
requires = ["maturin>=1.16,<2"]
build-backend = "maturin"

[project]
name = "fastwords"
requires-python = ">=3.12"
dynamic = ["version"]

[tool.maturin]
python-source = "python"
module-name = "fastwords._lib"
```

- `python-source = "python"` with `module-name = "fastwords._lib"` puts the compiled module inside a Python package in `python/fastwords/`, next to pure-Python code and a `.pyi` stub; the module's name in `#[pymodule]` must match `_lib`.
- `maturin develop --release` builds into the active environment for testing; `maturin build --release` writes wheels; in CI use `PyO3/maturin-action` or cibuildwheel.
- Rust's minimum macOS target is 10.12; cibuildwheel's default for older Intel builds is lower, so set `MACOSX_DEPLOYMENT_TARGET` explicitly for Rust projects.

## 7. cibuildwheel on GitHub Actions

cibuildwheel builds and tests wheels on each runner; one job per OS and CPU, plus an sdist job. Runner labels follow cibuildwheel's own example: `ubuntu-24.04-arm` and `windows-11-arm` are native ARM runners, `macos-15-intel` is the last Intel macOS runner, `macos-latest` is Apple silicon.

```yaml
name: wheels
on: [push, pull_request]

jobs:
  build_wheels:
    strategy:
      matrix:
        os: [ubuntu-latest, ubuntu-24.04-arm, windows-latest, windows-11-arm, macos-15-intel, macos-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3       # cibuildwheel's build[uv] frontend uses it
      - uses: pypa/cibuildwheel@v4.3.5
      - uses: actions/upload-artifact@v7
        with:
          name: wheels-${{ matrix.os }}
          path: wheelhouse/*.whl

  sdist:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
      - run: uv build --sdist
      - uses: actions/upload-artifact@v7
        with:
          name: sdist
          path: dist/*.tar.gz
```

The configuration lives in `pyproject.toml`, so local and CI runs agree:

```toml
[tool.cibuildwheel]
build-frontend = "build[uv]"
test-requires = ["pytest", "numpy"]
test-command = "pytest {project}/tests -q"
skip = ["*-musllinux_i686", "*-win32"]

[tool.cibuildwheel.macos]
environment = { MACOSX_DEPLOYMENT_TARGET = "11.0" }

[tool.cibuildwheel.linux]
archs = ["auto"]                 # x86_64 on ubuntu-latest, aarch64 on the ARM runner
```

- Tests run from a temporary directory against the installed wheel, so they cannot import the source tree by accident; `{project}` points back at the checkout.
- For an abi3 project, cibuildwheel builds the wheel once per platform and reuses it for the later Python versions, testing it on each.
- Publishing collects all artifacts in a final job that uploads with trusted publishing (the python-packaging skill).
- Before pushing, `uvx cibuildwheel --print-build-identifiers --platform linux` shows the identifiers a configuration selects.

## 8. Gotchas

These are the build problems seen most.

1. **C++ sources appear inside the wheel.** They live in the package folder. Move them to `src/cpp/` (or another folder outside the package).
2. **Building from the sdist fails, but building from the checkout works.** The sdist lacks sources (`.pyx`, `.c`, headers). Add them with `MANIFEST.in` (setuptools) and always test with `uv build`, which builds the wheel from the sdist.
3. **`nanobind_add_stub` fails with "No module named 'fastvec'".** The stub generator imports the module from the build directory, where it is not inside a package. Use `MODULE _core` with `PYTHON_PATH $<TARGET_FILE_DIR:_core>`.
4. **A cffi or Cython wheel is tagged for one Python although the module is abi3.** setuptools tags wheels per version unless told otherwise. Set `py_limited_api` in the `bdist_wheel` options.
5. **`ValueError: py_limited_api='cp312' not supported` on 3.14t.** The stable ABI does not exist on free-threaded builds before 3.15. Leave `Py_LIMITED_API`, `py_limited_api=True` and the `bdist_wheel` option out when `Py_GIL_DISABLED` is set.
6. **The Windows wheel imports on the build machine and fails elsewhere with "DLL load failed".** A dependent DLL was found on the build machine's `PATH`. cibuildwheel's default `delvewheel` repair bundles it; outside cibuildwheel, run delvewheel yourself.
