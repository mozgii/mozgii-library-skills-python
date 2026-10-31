---
name: python-object-model
description: Writes Python classes that behave like built-in types through the data model - __repr__, __eq__ and __hash__ together, rich comparisons and total_ordering, __bool__ and __len__, container protocols with collections.abc, __getattr__ versus __getattribute__, properties, descriptors and __set_name__, __slots__, __init_subclass__ registries, classmethod versus staticmethod, metaclasses and when not to use them, and copy, deepcopy and copy.replace hooks. Use when writing dunder methods, making objects work in sets, dicts, sorted() or len(), building a validated attribute, plugin registry, proxy or custom container, or when an object is unhashable, compares wrongly or copies too much or too little.
metadata:
  tags:
  - python
  - data-model
  - dunder-methods
  - descriptors
  - slots
  - metaclasses
  - properties
  - copy
---

# The Python object model

Python calls special methods (dunder methods) for almost every operation: `len(x)` calls `type(x).__len__`, `a == b` calls `__eq__`. A class that implements the right ones works with `sorted`, `set`, `dict` and `copy` without wrappers. Plain data holders are usually a `@dataclass` (the `python-data-models` skill); this skill is for classes where you write the methods yourself.

## Step 0: read the Python version

Some hooks are new, so read the oldest supported Python first.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
grep -n "requires-python" pyproject.toml
```

| Version | Change that matters |
|---|---|
| 3.12 | `functools.cached_property` no longer takes a lock; two threads may both compute the value |
| 3.13 | `copy.replace(obj, **changes)` and the `__replace__` hook |
| 3.14 | annotations are evaluated lazily; class-creation hooks read them with `annotationlib.get_annotations` |
| 3.15 | built-in `frozendict`, a hashable mapping usable as a dict key or a frozen attribute |

## __repr__ and __str__

`__repr__` is the developer's view of an object (logs, debuggers, the REPL) and should look like the call that builds it; `__str__` is the person's view and defaults to `__repr__`.

```python
class Point:
    def __init__(self, x: float, y: float) -> None:
        self.x, self.y = x, y

    def __repr__(self) -> str:
        return f"{type(self).__name__}(x={self.x!r}, y={self.y!r})"   # Point(x=1, y=2)

    def __str__(self) -> str:
        return f"({self.x}, {self.y})"                                # what f"{p}" prints
```

Use `type(self).__name__` so subclasses print correctly, and `!r` so strings keep their quotes. Never put secrets or huge payloads in `__repr__`: it ends up in tracebacks and logs.

## __eq__ and __hash__ together

Equality and hashing are one contract: objects that compare equal must have equal hashes, or sets and dicts lose them. Defining `__eq__` alone sets `__hash__` to `None`, which makes instances unhashable on purpose.

```python
class Sku:
    __slots__ = ("code", "region")

    def __init__(self, code: str, region: str) -> None:
        self.code, self.region = code, region

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.code!r}, {self.region!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sku):
            return NotImplemented             # lets Python try the other side, then falls back to identity
        return (self.code, self.region) == (other.code, other.region)

    def __hash__(self) -> int:
        return hash((self.code, self.region))  # the same fields as __eq__, as a tuple
```

- Return `NotImplemented` for types you do not handle; `Sku("a", "eu") == 1` is then `False`.
- Hash only fields that never change: mutating a hashed field while the object sits in a set makes it unfindable.
- A mutable object that compares by value should stay unhashable. Objects compared by identity need neither method.

## Rich comparisons and total_ordering

The rich comparison methods `__lt__`, `__le__`, `__gt__`, `__ge__` make objects sortable; `functools.total_ordering` derives the other three from `__eq__` and one of them.

```python
from functools import total_ordering


@total_ordering
class Version:
    def __init__(self, major: int, minor: int) -> None:
        self.major, self.minor = major, minor

    def _key(self) -> tuple[int, int]:
        return (self.major, self.minor)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key()

    def __lt__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented             # Version(1, 0) < 3 raises a clear TypeError
        return self._key() < other._key()
```

A tuple key gives lexicographic order for free. To sort once, pass `key=` to `sorted` instead; `@dataclass(order=True)` generates all comparisons.

## __bool__ and __len__ for truthiness

`if obj:` calls `__bool__`, then falls back to `__len__() != 0`, then to true. A class with `__len__` is false when empty, which is usually right and sometimes a bug.

```python
class Batch:
    def __init__(self, items: list[int]) -> None:
        self.items = items

    def __len__(self) -> int:
        return len(self.items)


if not Batch([]):                     # True: an empty batch is falsy
    ...
```

Write `if result is None:` when you mean "no result"; `if not result:` also catches an empty batch. `__len__` must return a non-negative `int`, and `__bool__` a real `bool`.

## Container protocols with collections.abc

Inherit from an abstract base class in `collections.abc` to get a full, consistent container from a few methods; the ABC supplies the rest (`get`, `items`, `__contains__`, `__eq__` for a `Mapping`).

```python
from collections.abc import Iterator, Mapping


class Headers(Mapping[str, str]):
    """Case-insensitive and read-only: headers["Content-Type"] and headers["content-type"] are one key."""

    def __init__(self, raw: dict[str, str]) -> None:
        self._data = {k.lower(): v for k, v in raw.items()}

    def __getitem__(self, key: str) -> str:
        return self._data[key.lower()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)
```

| Protocol | Methods you write | You get |
|---|---|---|
| `Sequence` | `__getitem__`, `__len__` | `__contains__`, `__iter__`, `__reversed__`, `index`, `count` |
| `MutableSequence` | plus `__setitem__`, `__delitem__`, `insert` | `append`, `extend`, `pop`, `remove`, `+=` |
| `Mapping` | `__getitem__`, `__iter__`, `__len__` | `get`, `keys`, `items`, `values`, `__contains__`, `__eq__` |
| `Set` | `__contains__`, `__iter__`, `__len__` | `<=`, `&`, `\|`, `-`, `^`, `isdisjoint` |

Subclassing `dict` directly is a trap for this kind of class: the built-in `update`, `get` and constructor do not call your `__setitem__` or `__getitem__`, so the lowercasing silently stops. Use the ABC, or `collections.UserDict`.

## __getattr__ versus __getattribute__

`__getattr__` runs only when normal lookup fails, so it is the safe hook for proxies and lazy attributes. `__getattribute__` runs on every attribute access, including your own methods' reads, and is rarely what you want.

```python
from typing import Any


class Proxy:
    def __init__(self, target: object) -> None:
        self._target = target

    def __getattr__(self, name: str) -> Any:     # only reached for names Proxy itself lacks
        if name == "_target":                   # copy and pickle ask before __init__ has run
            raise AttributeError(name)
        return getattr(self._target, name)
```

- Special methods are looked up on the type and bypass both hooks: `len(Proxy([1]))` raises `TypeError`. A proxy defines the dunders it needs explicitly.
- Raise `AttributeError` for names you do not handle, or `hasattr`, `copy` and `pickle` break.
- A module-level `__getattr__(name)` can warn on a deprecated name or import a heavy submodule on first use.

## Properties

A property runs code on attribute access while keeping the attribute syntax. Use it for cheap computed values and validation on assignment; a slow operation is a method. A property without a setter is read-only.

```python
class Temperature:
    def __init__(self, celsius: float) -> None:
        self.celsius = celsius                    # goes through the setter, so it is validated too

    @property
    def celsius(self) -> float:
        return self._celsius

    @celsius.setter
    def celsius(self, value: float) -> None:
        if value < -273.15:
            raise ValueError(f"below absolute zero: {value}")
        self._celsius = value
```

Do not wrap every attribute in a property "for later": a public attribute can become a property without changing callers.

## Descriptors and __set_name__

A descriptor is an object stored on a class that defines `__get__`, and optionally `__set__` or `__delete__`; properties, methods and `__slots__` are all descriptors. `__set_name__(owner, name)` runs when the class is created, so the descriptor learns its attribute name. Write one when one rule applies to many attributes (`price = Positive()`, `quantity = Positive()`). Keep the value on the instance, never on the descriptor, which every instance shares. Lookup order and a typed example: [descriptors.md](references/descriptors.md).

## __slots__ for many small objects

`__slots__` replaces the per-instance `__dict__` with fixed storage for the named attributes: instances are smaller, and a typo in an attribute name raises `AttributeError`.

```python
class Pixel:
    __slots__ = ("x", "y", "colour")

    def __init__(self, x: int, y: int, colour: int) -> None:
        self.x, self.y, self.colour = x, y, colour
```

- Measured with `tracemalloc` on 3.14, a three-attribute instance takes about 40 bytes less with slots: it matters for hundreds of thousands of objects, not a dozen.
- Every class in the hierarchy needs `__slots__`, or the subclass gets a `__dict__` back. Add `"__weakref__"` if instances are weakly referenced.
- `functools.cached_property` needs `__dict__` and fails on a slotted class. `@dataclass(slots=True)` writes the slots for you.

## __init_subclass__ for registries and checks

`__init_subclass__` runs on the parent each time a subclass is defined, with keyword arguments from the class statement. It covers plugin registries and subclass validation without a metaclass.

```python
from typing import Any, ClassVar


class Exporter:
    registry: ClassVar[dict[str, type["Exporter"]]] = {}
    format: ClassVar[str]

    def __init_subclass__(cls, /, format: str, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)               # keep cooperative inheritance working
        if format in Exporter.registry:
            raise TypeError(f"duplicate exporter for {format!r}")
        cls.format = format
        Exporter.registry[format] = cls


class CsvExporter(Exporter, format="csv"): ...
class JsonExporter(Exporter, format="json"): ...
```

A registry fills only when the module defining the subclass is imported.

## classmethod versus staticmethod

A `classmethod` receives the class it was called on, so it is the tool for alternative constructors that subclasses inherit correctly. A `staticmethod` receives nothing and is a plain function kept in the class namespace.

```python
from typing import Self


class Color:
    def __init__(self, r: int, g: int, b: int) -> None:
        self.rgb = (r, g, b)

    @classmethod
    def from_hex(cls, text: str) -> Self:                # Paint.from_hex(...) returns a Paint
        text = text.removeprefix("#")
        return cls(*(int(text[i:i + 2], 16) for i in (0, 2, 4)))
```

Prefer a module-level function to a `staticmethod`. Stacking `classmethod` over `property` was removed in 3.13.

## Metaclasses and when not to use them

A metaclass is the class of a class: it controls class creation and class-level behaviour such as `len(cls)`. Enum, ABCMeta and ORMs use them; application code reaches for the simpler tool first.

| You want | Use instead of a metaclass |
|---|---|
| register or validate subclasses | `__init_subclass__` |
| know the attribute name a value was assigned to | a descriptor with `__set_name__` |
| add methods to a class | a class decorator |
| `Model[int]` on the class | `__class_getitem__` or `typing.Generic` |
| a singleton | a module-level instance |

A class cannot have two unrelated metaclasses: inheriting from bases with metaclasses `A` and `B` raises `TypeError: metaclass conflict`. That is the main cost of a metaclass in a library: users cannot mix your base class with an `ABC`, a Qt class or a pydantic model.

Class-creation hooks that read annotations must cope with Python 3.14's lazy annotations. Read them with `annotationlib.get_annotations(cls, format=annotationlib.Format.FORWARDREF)`; plain `cls.__annotations__` raises `NameError` when an annotation names a class defined later.

## copy, deepcopy and copy.replace hooks

`copy.copy` makes a shallow copy, `copy.deepcopy` copies everything it reaches, and `copy.replace` (3.13) builds a changed copy. The hooks `__copy__`, `__deepcopy__` and `__replace__` fix the default, for example to skip a cache or a lock.

```python
import copy
from typing import Any


class Document:
    def __init__(self, title: str, pages: list[str]) -> None:
        self.title, self.pages = title, pages
        self._render_cache: dict[str, str] = {}          # never copied

    def __deepcopy__(self, memo: dict[int, Any]) -> "Document":
        return Document(copy.deepcopy(self.title, memo), copy.deepcopy(self.pages, memo))

    def __replace__(self, **changes: Any) -> "Document":
        return Document(changes.pop("title", self.title), changes.pop("pages", self.pages))


draft = Document("Plan", ["p1"])
renamed = copy.replace(draft, title="Plan v2")
```

Always pass `memo` on to nested `deepcopy` calls: it keeps shared references shared and stops recursion on cycles. Objects holding a lock, file or connection recreate them rather than copy them. `copy.replace` works out of the box for dataclasses, named tuples, `datetime` and `SimpleNamespace`.

## Verify

Prove the contracts with a probe: equal objects hash alike, unknown types compare cleanly, and copies and pickles come back equal.

```bash
uv run ruff check --select PLE,RUF012,B .           # expect no finding
uv run python - <<'PY'
import copy
import pickle

from my_app.models import Sku                       # the class under test

a, b = Sku("x", "eu"), Sku("x", "eu")
assert a == b and hash(a) == hash(b) and len({a, b}) == 1
assert (a == 1) is False                            # __eq__ returned NotImplemented, not an exception
assert copy.copy(a) == a and copy.deepcopy(a) == a
assert pickle.loads(pickle.dumps(a)) == a
print(repr(a))                                      # expect something that looks like a constructor call
PY
uv run mypy --strict src                            # dunder signatures match their protocols
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `__eq__` with no `__hash__` on purpose left unsaid | define both, or keep the class deliberately unhashable |
| raising in `__eq__` for an unknown type | `return NotImplemented` |
| subclassing `dict` to change lookups | `collections.abc.Mapping` or `UserDict` |
| `__getattribute__` for a proxy | `__getattr__` |
| a property around every attribute | a public attribute until it needs code |
| a metaclass for a registry | `__init_subclass__` |
| `@staticmethod` on a class of helpers | a module-level function |

## Pre-merge checklist

A class follows the object model when every item holds.

- [ ] `__repr__` is defined and looks like a constructor call, without secrets.
- [ ] `__eq__` and `__hash__` use the same immutable fields, or the class is deliberately unhashable; `__eq__` returns `NotImplemented` for unknown types, and `__hash__` always returns an integer.
- [ ] Ordering comes from one key tuple, with `total_ordering` or `order=True`.
- [ ] Custom containers inherit from `collections.abc`, not from `dict` or `list`.
- [ ] Proxies use `__getattr__`, raise `AttributeError` for unknown names and define the dunders they need.
- [ ] Repeated validation is one descriptor; `__slots__` only where instance counts are large.
- [ ] Subclass registration uses `__init_subclass__`; copy hooks skip caches and pass `memo`.

## Gotchas

These are the object-model bugs seen most often.

1. **`TypeError: unhashable type` after adding `__eq__`.** Defining `__eq__` set `__hash__` to `None`. Add `__hash__` over the same immutable fields, or keep the object out of sets and dict keys.
2. **An object vanishes from a set or dict.** A field used in `__hash__` changed after insertion. Hash only immutable fields, or make the class frozen.
3. **A dict subclass ignores your `__setitem__`.** `dict.update` and the constructor call the C implementation directly. Inherit from `MutableMapping` or `UserDict`.
4. **`RecursionError` in `__getattr__`.** The hook reads `self._target` before it exists (during unpickling or `copy`). Read through `self.__dict__` and raise `AttributeError` for private names.
5. **`cached_property` raises on a slotted class or dataclass with `slots=True`.** It needs an instance `__dict__`. Compute in `__init__`/`__post_init__` or use a descriptor with weak-keyed storage.
6. **`TypeError: metaclass conflict` when combining two base classes.** Each base brings its own metaclass. Replace your metaclass with `__init_subclass__` or a class decorator.

## References

The reference files hold the deeper material.

- [descriptors.md](references/descriptors.md): data and non-data descriptors, attribute lookup order, bound methods, typed descriptors, storage for slotted classes, a cached property
- [special-methods.md](references/special-methods.md): the table of special methods, reflected and in-place operators, `__format__`, `__index__`, `__missing__`, `__class_getitem__`, the type-level lookup rule

## See also

- `python-data-models`: dataclasses and pydantic for plain records
- `python-decorators`: class decorators and wrappers
- `python-typing`: `Self`, protocols and generics
- `python-functools-caching`: `cached_property` and `total_ordering`
- `python-pattern-matching-enums`: `__match_args__` and enums
