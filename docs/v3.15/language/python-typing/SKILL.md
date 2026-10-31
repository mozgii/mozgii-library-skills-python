---
name: python-typing
description: Writes type hints with Python's modern type system (3.12 to 3.15) and checks them with mypy, Pyright or ty - generics with PEP 695 syntax and type aliases, Protocol, TypedDict with NotRequired, ReadOnly and closed, Literal and exhaustive match with assert_never, overload, ParamSpec for decorators, Self, override, deprecated, TypeIs versus TypeGuard, Final, NewType, Annotated, LiteralString, and lazy annotations in 3.14. Use when annotating code, designing typed APIs, typing decorators or generic classes, choosing between mypy and Pyright, or when a type checker reports an error you do not understand.
metadata:
  tags:
  - python
  - typing
  - type-hints
  - mypy
  - pyright
  - ty
  - generics
  - protocol
  - typeddict
---

# Python type hints and type checkers

Type hints are annotations a static checker reads to find bugs before the code runs; Python itself mostly ignores them. This skill covers what to write (the type system as of Python 3.15) and which checker runs it. Pyright strict setup is in the `pyright-strict` skill, ty setup in the `ty` skill; mypy configuration is in [mypy.md](references/mypy.md).

## Step 0: read the versions

Typing features depend on both the Python version the code supports and the checker version.

```bash
grep -n "requires-python" pyproject.toml
uv run python -c "import sys; print(sys.version_info[:2])"
uv run mypy --version; uv run pyright --version
```

Features newer than the minimum supported Python come from `typing_extensions` (4.16 in October 2026), which backports every typing addition: `from typing_extensions import TypeIs, ReadOnly, deprecated`.

## Which checker

All three checkers implement the same typing specification; they differ in speed, strictness defaults and editor integration.

| Checker | Strengths | Run |
|---|---|---|
| Pyright (and Pylance, basedpyright) | fast, strong inference, same engine as VS Code's Pylance | `uv run pyright` |
| mypy 2.x | the reference implementation, plugins (pydantic, Django), parallel mode | `uv run mypy src` |
| ty (Astral, beta on `0.0.x`) | very fast, Rust, also a language server, `uv check` runs it; no plugins, and diagnostics may change in any release | `uv run ty check` |

Pick one as the CI gate. Running a second one is fine as advice, but the two disagree on edge cases (empty containers, overload resolution), and chasing both costs time.

## Annotation basics

Annotate every function signature and every variable whose type cannot be inferred; use built-in generics and `|` unions.

```python
from collections.abc import Iterable, Mapping, Sequence


def mean(values: Sequence[float]) -> float:            # accept the widest type you need
    return sum(values) / len(values)


def index(rows: Iterable[tuple[str, int]]) -> dict[str, int]:   # return the concrete type you build
    return dict(rows)


timeout: float | None = None
headers: Mapping[str, str] = {}
```

- Parameters take abstract types from `collections.abc` (`Iterable`, `Sequence`, `Mapping`); returns give concrete ones (`list`, `dict`). `list[int]` is invariant, so a function taking `list[float]` refuses a `list[int]`, while `Sequence[float]` accepts it.
- `typing.List`, `Dict`, `Optional`, `Union` are old spellings; Ruff's `UP` rules rewrite them.
- `object` means "anything, and I will check it"; `Any` means "anything, stop checking". Prefer `object` at boundaries.

## Generics with PEP 695 syntax

Python 3.12 added type parameter syntax, so generic functions, classes and aliases no longer need `TypeVar` declarations.

```python
from collections.abc import Sequence, Sized


def first[T](items: Sequence[T]) -> T | None:
    return items[0] if items else None


class Box[T = int]:                         # 3.13: a default for the type parameter (PEP 696)
    def __init__(self, value: T) -> None:
        self.value = value


def biggest[N: (int, float)](a: N, b: N) -> N:     # constrained: N is int or float, not a mix
    return max(a, b)


def longest[S: Sized](items: list[S]) -> S: ...    # bound: any type that is Sized


type Json = dict[str, Json] | list[Json] | str | int | float | bool | None   # recursive alias
type Pair[T] = tuple[T, T]
```

Variance is inferred for the new syntax. On Python 3.11 and older, use `T = TypeVar("T")` from `typing`.

## Protocols: structural types

A `Protocol` describes what an object can do, and any class with matching methods fits without inheriting from it.

```python
from typing import Protocol, runtime_checkable


class SupportsClose(Protocol):
    def close(self) -> None: ...


def shutdown(resources: list[SupportsClose]) -> None:
    for r in resources:
        r.close()


@runtime_checkable                          # allows isinstance(), which checks method names only
class Named(Protocol):
    name: str
```

Use protocols for parameters instead of concrete base classes; they make tests easy (any fake with the right methods fits) and avoid import cycles.

## TypedDict for JSON-shaped data

`TypedDict` types a plain dict with known keys, which fits JSON payloads and `**kwargs`.

```python
from typing import NotRequired, ReadOnly, Required, TypedDict, Unpack


class User(TypedDict):
    id: ReadOnly[int]                       # 3.13: functions may not change it
    name: str
    email: NotRequired[str]                 # key may be missing


class Options(TypedDict, total=False):
    timeout: float
    retries: Required[int]                  # required even though total=False


def connect(host: str, **opts: Unpack[Options]) -> None: ...   # typed keyword arguments


class Strict(TypedDict, closed=True):       # 3.15 (PEP 728): no keys beyond these
    kind: str
```

A `TypedDict` is checked statically only; data from outside still needs runtime validation (the `python-data-models` skill).

## Literal, enums and exhaustive matching

`Literal` restricts a value to fixed constants, and `assert_never` makes the checker prove every case is handled.

```python
from typing import Literal, assert_never

Status = Literal["queued", "running", "done"]


def label(status: Status) -> str:
    match status:
        case "queued":
            return "waiting"
        case "running":
            return "busy"
        case "done":
            return "finished"
        case _:
            assert_never(status)            # adding "failed" to Status makes this line an error
```

The same pattern works with `enum.Enum` members and with unions of classes. mypy reports a missing case with `--enable-error-code exhaustive-match` even without the `assert_never` line.

## Overloads

`@overload` declares several signatures when the return type depends on the argument types.

```python
from typing import overload


@overload
def parse(raw: str) -> int: ...
@overload
def parse(raw: bytes) -> bytes: ...
def parse(raw: str | bytes) -> int | bytes:
    return int(raw) if isinstance(raw, str) else raw
```

Callers see `parse("1")` as `int` and `parse(b"x")` as `bytes`. Only the last, undecorated implementation runs. Prefer a generic or two functions when overloads multiply.

## Typing decorators with ParamSpec

A decorator typed with a `ParamSpec` keeps the wrapped function's parameters, so callers are still checked.

```python
import time
from collections.abc import Callable
from functools import wraps


def timed[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            print(f"{fn.__name__} took {time.perf_counter() - start:.3f}s")
    return wrapper
```

`Concatenate[Request, P]` types a decorator that adds or removes a leading argument. An untyped decorator turns the decorated function into `Unknown`/`Any` everywhere it is used.

## Narrowing: isinstance, TypeIs and TypeGuard

Narrowing tells the checker a value has a more specific type inside a branch. `isinstance`, `is None`, `in` on literals and `match` narrow automatically; custom checks use `TypeIs` (3.13) or `TypeGuard`.

```python
from typing import TypeGuard, TypeIs


def is_number(value: object) -> TypeIs[int | float]:          # narrows both branches
    return isinstance(value, (int, float))                    # True for bool too: bool is a subclass of int


def all_strings(values: list[object]) -> TypeGuard[list[str]]:  # narrows the True branch only
    return all(isinstance(v, str) for v in values)
```

`TypeIs[X]` must return `True` for every value of `X`, because the checker narrows the `False` branch to "not `X`". Excluding `bool` from the check above would make the negative branch wrong, so a test stricter than its type needs `TypeGuard`. `TypeIs[X]` also requires `X` to be a subtype of the parameter type. `list[str]` is not a subtype of `list[object]` (lists are invariant), so that check needs `TypeGuard`; both checkers reject it as `TypeIs`.

## Classes: Self, override, Final, ClassVar

These annotations make class hierarchies safe to change.

```python
from dataclasses import dataclass
from typing import ClassVar, Final, Self, override

MAX_RETRIES: Final = 3                      # cannot be reassigned


@dataclass
class Shape:
    registry: ClassVar[dict[str, type["Shape"]]] = {}

    def scaled(self, factor: float) -> Self:     # returns the subclass type in subclasses
        return self

    def area(self) -> float:
        return 0.0


@dataclass
class Square(Shape):
    side: float = 1.0

    @override                               # renaming Shape.area makes this an error
    def area(self) -> float:
        return self.side**2
```

## Deprecations, NewType, Annotated, LiteralString

These smaller tools each solve one recurring problem.

```python
from typing import Annotated, LiteralString, NewType
from warnings import deprecated            # 3.13; typing_extensions.deprecated before that

UserId = NewType("UserId", int)            # distinct from int for the checker, plain int at runtime


@deprecated("use load_v2()")               # checkers flag callers; Python warns at runtime
def load() -> None: ...


Port = Annotated[int, "1-65535"]           # metadata for runtime tools (pydantic reads it); checkers see int


def run_sql(query: LiteralString, params: tuple[object, ...]) -> None: ...   # Pyright refuses f-strings here; mypy treats it as str
```

## Annotations at runtime

Annotations are evaluated lazily since Python 3.14 (PEP 649): a forward reference no longer needs quotes or `from __future__ import annotations`.

- Code that reads annotations at runtime (pydantic, dataclasses, CLIs) should use `annotationlib.get_annotations(obj, format=annotationlib.Format.FORWARDREF)` on 3.14+: a name imported under `TYPE_CHECKING` comes back as a `ForwardRef`. `Format.VALUE`, `typing.get_type_hints(obj)` and plain `obj.__annotations__` all raise `NameError` for that name; `Format.STRING` gives the source text.
- Keep `from __future__ import annotations` while supporting 3.13 or older; it is harmless on 3.14.
- Python 3.15 adds `typing.TypeForm` (PEP 747) for functions that take a type expression as a value, and the `sentinel` builtin (PEP 661) whose objects can appear in unions: `def get(key: str, default: int | MISSING = MISSING)`.

## Verify

Prove the checker runs in strict mode by giving it a bug it must find; a clean run alone can mean nothing was checked.

```bash
printf 'def f(x: int) -> str:\n    return x\n' > /tmp/typing_probe.py
uv run pyright /tmp/typing_probe.py; echo "exit $?"             # expect reportReturnType and exit 1
uv run mypy --strict /tmp/typing_probe.py; echo "exit $?"       # expect [return-value] and exit 1
rm /tmp/typing_probe.py
uv run mypy src; echo "exit $?"                                 # or pyright: expect exit 0 on the project
grep -rnE '# type: ignore$' src                                 # expect nothing: every ignore names its code
```

`reveal_type(value)` shows what the checker believes at one line, which settles most arguments about narrowing; remove it afterwards.

## Anti-patterns

| Instead of | Do |
|---|---|
| `Any` at a boundary | `object`, then narrow |
| `TypeIs` for a check stricter than the type | `TypeGuard`, which leaves the `False` branch alone |
| `cast` to silence an error | fix the type, or narrow with a check |
| `List[int]`, `Optional[int]` in new code | `list[int]`, `int \| None` |
| a bare `# type: ignore` | `# type: ignore[code]`, or `# pyright: ignore[rule]` |
| reading `__annotations__` in a framework | `annotationlib.get_annotations(obj, format=Format.FORWARDREF)` |

## Checklist

Typed code is ready when every item holds.

- [ ] Every function has annotated parameters and return type.
- [ ] Parameters use `collections.abc` types; returns use concrete types.
- [ ] No `Any` where `object`, a `Protocol` or a generic works.
- [ ] Decorators keep signatures (`ParamSpec`); overrides carry `@override`.
- [ ] Every `match` over a `Literal`, enum or union ends with `assert_never`.
- [ ] Newer typing names come from `typing_extensions` when the minimum Python lacks them.
- [ ] The CI checker reports 0 errors.

## Gotchas

These are the typing errors that confuse people most.

1. **`list[int]` is not accepted where `list[float]` is expected.** Lists are invariant because the function could append a float. Take `Sequence[float]`.
2. **`TypeIs` rejected with "not consistent with value parameter type".** The type inside `TypeIs` has to be a subtype of what the function takes; use `TypeGuard` for container element checks.
3. **A decorated function loses its signature.** The decorator returns `Callable[..., Any]` or is untyped. Type it with `ParamSpec`.
4. **`NameError` at runtime for a type-only import.** A framework evaluated the annotation. Make the import real, or use `get_type_hints`/`annotationlib` after the names exist.
5. **`dict[str, Any]` everywhere.** The checker cannot help. Introduce `TypedDict` or a dataclass where the dict is created.
6. **A `Protocol` with attributes rejects a dataclass.** A protocol attribute `name: str` is read-write; a frozen dataclass field is read-only. Declare it as a `@property` in the protocol.

## References

The reference files hold the longer recipes and the full tool detail.

- [mypy.md](references/mypy.md): mypy 2.x configuration, strict flags, per-module overrides, plugins, stubs, parallel mode and gradual adoption

## See also

- `pyright-strict`: Pyright strict mode and large backlogs
- `ty`: Astral's checker and its rule levels
- `python-data-models`: runtime validation for what types cannot prove
- `python-pattern-matching-enums`: exhaustive `match`
- `python-decorators`: `ParamSpec` in real decorators
