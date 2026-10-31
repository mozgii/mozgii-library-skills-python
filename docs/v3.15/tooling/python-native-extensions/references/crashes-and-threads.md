---
title: Debugging crashes and threading in native extensions
author: MOZGII, Inc.
lang: en
---

# Debugging crashes and threading in native extensions

A bug in native code does not raise an exception: it corrupts memory, crashes the process, or produces wrong results only under threads. This reference covers reading a crash, faulthandler, native debuggers on each OS, AddressSanitizer, Python's debug allocator, reference-count bugs, releasing and re-acquiring the GIL in each binding tool, and making an extension safe for free-threaded Python. The faulthandler, lldb and AddressSanitizer runs shown were made on macOS with Python 3.14.

## Contents

This reference has 9 sections.

1. Recognise a native crash
2. faulthandler: the first look
3. Native debuggers: lldb, gdb and Visual Studio
4. AddressSanitizer for memory errors
5. Python's debug allocator and development mode
6. Reference-count bugs
7. Releasing and re-acquiring the GIL
8. Thread safety for free-threaded Python
9. Gotchas

## 1. Recognise a native crash

A native crash ends the process without a Python traceback. The exit status tells you it happened.

| OS | What you see |
|---|---|
| macOS, Linux | "Segmentation fault" or "Bus error"; exit status 139 (shell) or `returncode == -11` (`subprocess`); 134 or -6 for an abort |
| Windows | exit code `3221225477` (`0xC0000005`, access violation), often with no message at all |

A test run that stops in the middle with no pytest summary is the same symptom. In CI, run the suite with faulthandler on (`-X faulthandler`, or pytest's built-in faulthandler plugin, which is on by default) so the log shows where it stopped.

## 2. faulthandler: the first look

faulthandler installs signal handlers that print every thread's Python stack when the process crashes. On Python 3.14+ it also prints the C stack of the crashing thread.

```bash
python -X faulthandler crash.py           # or PYTHONFAULTHANDLER=1 python crash.py
```

This crash comes from calling a C function through ctypes with NULL pointers:

```
Fatal Python error: Segmentation fault

Current thread 0x00000001fda23f80 (most recent call first):
  File "crash.py", line 3 in <module>

Current thread's C stack trace (most recent call first):
  Binary file "/usr/lib/system/libsystem_platform.dylib", at _sigtramp+0x38
  Binary file "/usr/lib/libffi.dylib", at ffi_call_SYSV+0x50
  Binary file ".../_ctypes.cpython-314-darwin.so", at _ctypes_callproc+0x2f4
```

- The Python frames name the call that entered native code; that is usually enough to find the bad arguments.
- The C stack shows symbol names only for exported or debug-built code. A frame from your library may be missing when it was built with optimisation and no frame pointers; rebuild with `-g -fno-omit-frame-pointer`.
- For hangs, `faulthandler.dump_traceback_later(60, exit=True)` prints all stacks after 60 seconds and exits; `faulthandler.register(signal.SIGUSR1)` (POSIX) dumps on demand.

## 3. Native debuggers: lldb, gdb and Visual Studio

A debugger stops at the faulting instruction with the native stack, arguments and source lines. Build the extension with debug information first: `-g -O0` for C and C++ (`cmake.build-type = "Debug"` in scikit-build-core, `CFLAGS="-g -O0"` with setuptools), or `maturin develop` without `--release` for Rust.

On macOS, run Python under lldb:

```bash
lldb --batch -o run -o bt -- "$(python -c 'import sys; print(sys.executable)')" crash.py
```

```
* thread #1, queue = 'com.apple.main-thread', stop reason = EXC_BAD_ACCESS (code=1, address=0x0)
  frame #0: 0x000000010003830c libvec.dylib`dot(a=0x0000000000000000, b=0x0000000000000000, n=4) at vec.c:13:41
```

- Point lldb at the real interpreter binary. Homebrew's `python3.14` is a launcher that `exec`s another binary, so lldb stops at "stop reason = exec" instead of the crash; uv-managed Pythons and `sys.executable` from inside a virtual environment are real binaries.
- On Linux, `gdb --args python crash.py`, then `run` and `bt`. When gdb loads CPython's `python-gdb.py` extension (Debian and Ubuntu ship it with their Python debug packages), `py-bt` prints the Python stack too.
- On Windows, build with `/Zi` (CMake's `RelWithDebInfo` or `Debug`), then run under Visual Studio (Debug, Attach to Process, or open `python.exe` as a project) or WinDbg; the `.pdb` files must sit next to the `.pyd`.
- Attach to a running process when the crash is hard to reproduce from a script: `lldb -p <pid>`, `gdb -p <pid>`.

## 4. AddressSanitizer for memory errors

AddressSanitizer (ASan) instruments your C, C++ or Rust code to report out-of-bounds accesses, use-after-free and double frees at the moment they happen, with the stack of the bad access and of the allocation.

```bash
cc -g -O1 -fsanitize=address -fno-omit-frame-pointer -shared -fPIC -o libvec.dylib vec.c
```

Python itself is not built with ASan, so the runtime must be loaded first, and Python's own small-object allocator must be turned off so buffers come from `malloc`, which ASan watches:

```bash
# macOS
PYTHONMALLOC=malloc DYLD_INSERT_LIBRARIES="$(clang -print-file-name=libclang_rt.asan_osx_dynamic.dylib)" python oob.py
# Linux (gcc)
PYTHONMALLOC=malloc LD_PRELOAD="$(gcc -print-file-name=libasan.so)" python oob.py
```

Reading one element past a 4-element ctypes array produces:

```
ERROR: AddressSanitizer: heap-buffer-overflow on address 0x603000034320 ...
READ of size 8 at 0x603000034320 thread T0
    #0 0x0001037445ac in dot vec.c:13
```

- Without `PYTHONMALLOC=malloc` the same bug went unreported: the array lived in a pymalloc arena, where reading past the end stays inside memory ASan considers valid.
- macOS strips `DYLD_INSERT_LIBRARIES` for system-protected binaries; use a Python from uv, Homebrew's framework binary or python.org, not `/usr/bin/python3`.
- Python's own allocations may be reported as leaks at exit; set `ASAN_OPTIONS=detect_leaks=0` and focus on the access errors.
- On Windows, MSVC supports `/fsanitize=address`; the ASan runtime DLL must be on `PATH` when Python loads the module.

## 5. Python's debug allocator and development mode

`PYTHONMALLOC=debug` wraps Python's allocators with guard bytes and checks: it detects writes past the end of a Python-allocated block (reported when the block is freed), use of freed memory filled with a pattern, and calls to the wrong allocator family (`PyMem_Free` on memory from `malloc`). Development mode, `-X dev`, turns it on together with faulthandler, `ResourceWarning`s and extra debug checks.

```bash
python -X dev -m pytest tests/
PYTHONMALLOC=debug python -m pytest tests/test_native.py
```

These checks cover memory obtained through Python's allocator API (`PyMem_*`, `PyObject_*`), not plain `malloc` in your own code; ASan covers that.

## 6. Reference-count bugs

In C API code (and Cython or C++ code that handles raw `PyObject*`), every new reference must be released once, and a borrowed reference must not outlive its owner.

| Bug | Symptom |
|---|---|
| missing `Py_DECREF` | memory grows; objects never die (a leak) |
| extra `Py_DECREF` | crash later, somewhere unrelated, when the object is freed twice or reused |
| a borrowed reference kept after its container changed | crash or a wrong object, often only under load or threads |

- Use the binding tool's smart handles (`nb::object`, `py::object`, `Bound<'py, T>` in PyO3); they manage counts for you. Raw C API code is where these bugs live.
- `sys.getrefcount(obj)` before and after a call in a loop shows a leak: a count that grows by one per call.
- On free-threaded builds, borrowed references from containers (`PyList_GetItem`, `PyDict_GetItem`) are unsafe because another thread can remove the item; use the strong-reference versions added in 3.13 (`PyList_GetItemRef`, `PyDict_GetItemRef`).

## 7. Releasing and re-acquiring the GIL

Releasing the GIL lets other Python threads run while native code works. The rule is the same in every tool: while the GIL is released, touch no Python object and call no Python API. Copy what you need into native values first.

| Tool | Release | Re-acquire (for a callback into Python) |
|---|---|---|
| ctypes | automatic for `CDLL` calls | automatic for ctypes callbacks |
| Cython | `with nogil:` block, functions declared `nogil` | `with gil:` |
| nanobind | `nb::gil_scoped_release`, or `nb::call_guard<nb::gil_scoped_release>()` on `m.def` | `nb::gil_scoped_acquire` |
| pybind11 | `py::gil_scoped_release`, or `py::call_guard<py::gil_scoped_release>()` | `py::gil_scoped_acquire` |
| PyO3 | `py.detach(\|\| ...)` | `Python::attach(\|py\| ...)` |
| C API | `Py_BEGIN_ALLOW_THREADS` ... `Py_END_ALLOW_THREADS` | `PyGILState_Ensure()` / `PyGILState_Release()` |

- Release the GIL around loops that take more than a few microseconds; for tiny calls the release costs more than it saves.
- A buffer passed in (a NumPy array, `bytes`) stays valid while the GIL is released only because the caller holds a reference; never let native threads keep a pointer after the call returns.
- On free-threaded builds there is no GIL, but the same calls still matter: they detach the thread from the interpreter, so stop-the-world pauses such as garbage collection do not wait for your loop. They also keep the code correct on regular builds.

## 8. Thread safety for free-threaded Python

On 3.14t and 3.15t, Python threads run native code truly in parallel. Code that relied on the GIL for safety (a global cache, a lazily initialised table, a counter in a static variable) now races. Before declaring free-threading support, review and fix:

- **Global and static mutable state**: protect it with a mutex, make it thread-local, or initialise it once at import.
- **Objects mutated from several threads**: lock per object. Tools provide locks that cost nothing on regular builds: `cython.pymutex` and `with cython.critical_section(obj):` in Cython, `nb::ft_mutex` with `nb::ft_lock_guard` and `nb::ft_object_guard` in nanobind, `pyo3::sync::critical_section::with_critical_section` in PyO3, `Py_BEGIN_CRITICAL_SECTION(obj)` in the C API (3.13+).
- **Borrowed references** from containers: switch to the `...Ref` functions (section 6).
- **Third-party C libraries** that are not thread-safe: wrap calls into them in one lock.

Test with real concurrency on a free-threaded interpreter:

```bash
uv run --python 3.14t python -c "import sys, fastvec; print(sys._is_gil_enabled())"   # False: support declared
uv run --python 3.14t --with pytest-run-parallel pytest --parallel-threads=8 tests/
```

- pytest-run-parallel (0.10 in October 2026) runs each test in several threads at once, which exposes races in code that single-threaded tests never hit.
- `PYTHON_GIL=0` forces the GIL off even after loading a module that did not declare support; use it to test a module before you declare it, never in production.
- ThreadSanitizer (`-fsanitize=thread`) finds data races the tests miss; it needs a Python built with it to avoid noise, so it is a CI job for mature projects.

## 9. Gotchas

These are the debugging and threading problems seen most.

1. **lldb stops at "stop reason = exec" and never reaches the crash.** The `python` it launched is a wrapper that runs another binary. Launch `sys.executable` from a uv or venv interpreter.
2. **AddressSanitizer reports nothing for an obvious overflow.** The buffer came from pymalloc. Set `PYTHONMALLOC=malloc`.
3. **The crash moves or disappears under the debugger.** Undefined behaviour depends on memory layout. Use ASan, which catches the first bad access instead of its consequence.
4. **A free-threaded test run passes, then fails one run in a hundred.** A data race. Run with pytest-run-parallel and more iterations (`--iterations`), and review shared state with section 8.
5. **A callback from a native thread into Python crashes.** The thread did not hold the GIL (or was not attached to the interpreter). Acquire it with your tool's re-acquire call before touching Python.
