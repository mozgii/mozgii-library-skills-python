---
title: Compiling Python with mypyc
author: MOZGII, Inc.
lang: en
---

# Compiling Python with mypyc

mypyc is the compiler that ships with mypy: it turns type-annotated Python modules into C extensions, so a typed codebase gets faster without being rewritten in another language. The cost is behaviour. Compiled code enforces annotations at runtime and gives up some of Python's dynamism, so moving a module across is a migration, not a build flag. This reference covers mypy 2.4.1 (October 2026).

## Contents

This reference has 7 sections.

1. When mypyc is the right compiler
2. Compile a module
3. What changes once a module is compiled
4. Code mypyc refuses to compile
5. Native classes: keep the fast path
6. Make compiled code faster
7. Verify a compiled module

## 1. When mypyc is the right compiler

mypyc wins when the code is already pure, strictly typed Python and the slow part is interpreter overhead rather than a missing algorithm. Profile first: compiling code that waits on the network or the database changes nothing.

| Situation | Choice | Why the others lose |
|---|---|---|
| strictly typed pure-Python module, many small calls | mypyc | no second language, and the source still runs as ordinary Python |
| tight numeric loops over arrays | NumPy, Numba | array code outruns any per-element compiled loop |
| wrapping a C library, or loops with hand-tuned C types | Cython | mypyc has no way to call into C headers directly |
| heavy parsing, safe parallelism | Rust with PyO3 | mypyc code still holds the Python object model |
| code that relies on monkey patching, metaclasses or dynamic attributes | leave it interpreted | every section below is a fight |

## 2. Compile a module

Install mypy with its build extra, then compile from `src/`, the root of the import path, so the extension lands beside the source. `mypyc` needs a C compiler (Xcode command line tools, GCC or Clang, or MSVC on Windows).

```bash
uv add --dev "mypy[mypyc]"                    # the extra brings setuptools
cd src && uv run mypyc my_app/pricing.py
```

Build output goes to `build/` under the current directory, and the compiled `.so` or `.pyd` lands beside the source. Python prefers that file on import, so delete it to go back to interpreted code. In a package build, call `mypycify([...])` from `mypyc.build` in `setup.py` instead.

All modules passed to one `mypyc` command or one `mypycify` call form a compilation unit. Calls inside a unit are direct and fast, and `Final` values are inlined; calls across units cost about the same as calling interpreted code. Compile modules that call each other together.

This module uses `i64`, a native 64-bit integer type from `mypy_extensions`, and a `@final` class, the two changes that give the biggest gains on arithmetic-heavy code.

```python
from typing import Final, final

from mypy_extensions import i64

BUCKET: Final = 1_000


@final
class Totals:
    def __init__(self) -> None:
        self.count: i64 = 0

    def add(self, value: i64) -> None:
        self.count += value


def run(n: i64) -> i64:
    totals = Totals()
    i: i64 = 0
    while i < n:
        totals.add(i % BUCKET)
        i += 1
    return totals.count
```

The module stays valid Python: without compilation `i64` behaves as a plain `int`.

## 3. What changes once a module is compiled

Compiled code behaves differently in ways tests can miss, so search the codebase for each of these before compiling.

| Behaviour | What you see | Do |
|---|---|---|
| annotations are enforced at runtime | `TypeError: int object expected; got str` from code that used to work with a wrong annotation | fix the annotation; it was hiding a bug |
| native classes have fixed attributes | `AttributeError` when setting an attribute the class never assigns | declare it in the class |
| calls inside a unit are bound at compile time | `mock.patch` or `monkeypatch.setattr` on a compiled function has no effect on compiled callers; replacing a native class's method fails | inject the dependency, or leave that module interpreted |
| `Final` values are inlined | a test that reassigns `config.MAX_RETRIES` changes nothing | drop `Final` from values that tests or config reassign |
| `__name__` is never `"__main__"` | `if __name__ == "__main__":` never runs, and `python -m` cannot run the module | move the entry point to a small interpreted script |
| `if TYPE_CHECKING` is treated as always true | `RuntimeError: Reached allegedly unreachable code!`, at import when at module level | remove code that runs only when the condition is false |
| native classes do not support `__getattribute__`, `__delattr__` or `__index__` | those methods do nothing useful | make the class non-native |
| attributes cannot be deleted | `del obj.value` fails | list them in `__deletable__` |
| instances with required `__init__` arguments cannot be pickled or copied | `pickle`, `copy.copy` or `multiprocessing` fails | add `@mypyc_attr(serializable=True)` |
| native classes cannot be subclassed from interpreted code | test doubles fail to subclass them | add `@mypyc_attr(allow_interpreted_subclasses=True)`, which costs speed |
| a class attribute without `ClassVar` is an instance attribute with a default | an interpreted subclass overrides it and compiled base-class code never sees the new value | annotate it `ClassVar` |

## 4. Code mypyc refuses to compile

The module must pass mypy first: mypyc will not compile type errors, and a `# type: ignore` in compiled code can produce wrong code rather than a warning. It also refuses `--no-strict-optional` and `--no-strict-bytes`, including per-module and inline settings.

- Classes inside functions, or inside other classes (`error: Nested class definitions not supported`): move them to module level, with a leading underscore if they are internal.
- The same function or class defined in two branches of an `if`.
- Assigning an `int` to a variable or attribute declared `float`, even as the first value: write `0.0`, or `float(n)`. Python allows it and mypy accepts it, but the two have different compiled representations.
- Async generators, including functions under `@asynccontextmanager`: rewrite as a class with `__aiter__` and `__anext__`, or `__aenter__` and `__aexit__`, and keep the old function as a thin wrapper.
- `six`: replace it with plain Python 3 code.

## 5. Native classes: keep the fast path

A class compiles to a native class unless something forces it out, and attribute access and method calls on native classes are where most of the gain comes from.

- Annotate constants with `Final`, and class-level values with `ClassVar`. Prefer `Final`; do not use both.
- Add `@final` to internal classes that have no subclasses, unless they are abstract, a protocol, an exception or a subclass of a standard library class.
- `@mypyc_attr(native_class=True)` turns a silent fallback into a compile error on classes that matter. Class decorators other than `@dataclass`, `@attr.s(auto_attribs=True)`, `@trait` and `@mypyc_attr` make a class non-native without saying so.
- Use `@mypyc_attr(native_class=False)` deliberately for classes with a non-trivial metaclass (`ABCMeta` is fine) or an unsupported base class.
- Only `@property`, `@staticmethod` and `@classmethod` work on native classes. `functools.cached_property` and custom descriptors need a non-native class.
- Native classes have single inheritance; turn mixins into traits with `@trait` from `mypy_extensions`, listed after the regular base.

## 6. Make compiled code faster

Change one thing at a time and measure on a realistic workload: gains vary widely, and some changes do nothing in a given function.

| Change | Why it helps |
|---|---|
| annotate everything, and replace `Any` | unannotated code and `Any` values use slow generic operations |
| `@final` on hot classes, `Final` on constants | direct calls, inlined values |
| `tuple[int, str]` for several return values | fixed-length tuples are unboxed; in mypy's benchmark, returning two ints this way was about 40 times faster than a `NamedTuple` |
| a sentinel value instead of `int \| None` | unions are always boxed; plain `int`, `float` and `bool` are not |
| native classes instead of dicts as records | attribute access beats a key lookup |
| `is` when comparing enum members | an identity check beats `==` |
| move nested functions and lambdas out of hot functions | each call allocates the closure and its environment |
| keep generators | they are efficient when compiled; building a list is usually slower |
| replace `@contextmanager` on a hot path with a class | the decorator's machinery stays interpreted |
| do not cache a bound method in a local variable | the old CPython trick stops the call being bound at compile time |
| `librt` (a mypy dependency) for hot standard library calls | `librt.strings.StringWriter` and `BytesWriter`, `librt.base64`, `librt.random`, `librt.time.time()`, `librt.threading.Lock` |
| `vec[i64]` from `librt.vecs` instead of `list[int]` | packed storage for `i64`, `i32`, `u8`, `float` and `bool`, with much less memory |

Compiled code allocates quickly, so the garbage collector can take a larger share of CPU time. Tune it (`gc.set_threshold`, `gc.freeze`) only when a profile shows `gc_` functions on top, and only in the application's entry point, because the setting affects the whole process.

## 7. Verify a compiled module

Prove the compiled file is the one running before believing a timing. A typo in the module path, or a stale build, silently benchmarks interpreted code.

```python
from my_app import pricing

print(pricing.__file__)                       # must end in .so or .pyd
```

```bash
uv run mypy src/my_app/pricing.py             # clean, or mypyc refuses it
(cd src && uv run mypyc my_app/pricing.py)
uv run pytest -q                              # run the suite against the compiled module
```

Compile benchmarks in a temporary folder so `build/` stays out of the project, run each one enough times to take several milliseconds, and take the best or median of several runs. A compiler can remove work from a synthetic loop entirely, so a benchmark of real code on real data is the only number worth quoting.
