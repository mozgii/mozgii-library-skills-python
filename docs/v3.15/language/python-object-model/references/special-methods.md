# Python special methods reference

Special methods are the hooks the interpreter calls for operators, built-in functions and statements. This reference lists the ones worth knowing, the rules they share, and working patterns for the less obvious ones: arithmetic with reflected operators, in-place operators, `__format__`, `__index__`, `__missing__` and `__class_getitem__`.

## The type-level lookup rule

The interpreter looks special methods up on the type, never on the instance, and skips `__getattr__` and `__getattribute__` when it does. `len(obj)` is `type(obj).__len__(obj)`.

```python
class Q: ...


q = Q()
q.__len__ = lambda: 3
len(q)              # TypeError: object of type 'Q' has no len()
```

Consequences: a proxy must define every dunder it forwards; patching a dunder in a test means patching the class (`monkeypatch.setattr(Q, "__len__", ...)`); and a dunder set to `None` on the class (`__hash__ = None`, `__iter__ = None`) explicitly disables that operation.

## Table of special methods

The table groups the methods by what triggers them.

| Triggered by | Method | Notes |
|---|---|---|
| `repr(x)`, `str(x)`, `f"{x:spec}"` | `__repr__`, `__str__`, `__format__` | `__format__` gets the text after `:` |
| `bytes(x)` | `__bytes__` | |
| `x == y`, `x < y` and the rest | `__eq__`, `__ne__`, `__lt__`, `__le__`, `__gt__`, `__ge__` | `__ne__` defaults to `not __eq__` |
| `hash(x)`, set and dict keys | `__hash__` | set to `None` by defining `__eq__` |
| `bool(x)`, `if x:` | `__bool__`, then `__len__` | |
| `len(x)` | `__len__` | non-negative `int` |
| `x[k]`, `x[k] = v`, `del x[k]` | `__getitem__`, `__setitem__`, `__delitem__` | slices arrive as `slice` objects |
| `d[k]` on a dict subclass with a missing key | `__missing__` | only for `dict` subclasses |
| `iter(x)`, `for` | `__iter__`, then `__getitem__` from 0 until `IndexError` | |
| `next(it)` | `__next__` | raises `StopIteration` at the end |
| `reversed(x)` | `__reversed__`, then `__len__` + `__getitem__` | |
| `k in x` | `__contains__`, then iteration | |
| `operator.length_hint(x)` | `__length_hint__` | a size estimate for preallocation |
| `x(...)` | `__call__` | |
| `with x:` | `__enter__`, `__exit__` | `async with`: `__aenter__`, `__aexit__` |
| `async for` | `__aiter__`, `__anext__` | |
| `await x` | `__await__` | |
| `x + y` and other binary operators | `__add__`, `__sub__`, `__mul__`, `__matmul__`, `__truediv__`, `__floordiv__`, `__mod__`, `__pow__`, `__lshift__`, `__and__`, `__or__`, `__xor__` | each has a reflected `__r*__` and in-place `__i*__` twin |
| `-x`, `+x`, `abs(x)`, `~x` | `__neg__`, `__pos__`, `__abs__`, `__invert__` | |
| `int(x)`, `float(x)`, `complex(x)` | `__int__`, `__float__`, `__complex__` | |
| `x` used as an index, `bin(x)`, `hex(x)` | `__index__` | must return an exact `int` |
| `round(x)`, `math.floor`, `math.ceil`, `math.trunc` | `__round__`, `__floor__`, `__ceil__`, `__trunc__` | |
| `copy.copy`, `copy.deepcopy`, `copy.replace` | `__copy__`, `__deepcopy__`, `__replace__` | |
| `pickle` | `__reduce__`, `__getstate__`, `__setstate__`, `__getnewargs_ex__` | |
| `Cls[T]` | `__class_getitem__` | generic aliases |
| `isinstance(x, Cls)`, `issubclass` | `__instancecheck__`, `__subclasscheck__` on the metaclass | ABCs use these |
| `match` class patterns with positions | `__match_args__` | a tuple of attribute names |
| `os.fspath(x)`, `open(x)` | `__fspath__` | path-like objects |
| `sys.getsizeof(x)` | `__sizeof__` | |

## Arithmetic operators and NotImplemented

Binary operators try the left operand's method first; if it returns `NotImplemented`, Python tries the right operand's reflected method (`__radd__` for `+`). Returning `NotImplemented` is how a type says "not with that", and it lets the other type answer.

```python
class Money:
    def __init__(self, cents: int, currency: str) -> None:
        self.cents, self.currency = cents, currency

    def __add__(self, other: object) -> "Money":
        if isinstance(other, Money):
            if other.currency != self.currency:
                raise ValueError("currency mismatch")    # a real error, not NotImplemented
            return Money(self.cents + other.cents, self.currency)
        return NotImplemented

    def __radd__(self, other: object) -> "Money":
        if other == 0:
            return self                                   # makes sum([...]) work: sum starts from 0
        return NotImplemented

    def __mul__(self, n: object) -> "Money":
        if isinstance(n, int):
            return Money(self.cents * n, self.currency)
        return NotImplemented

    __rmul__ = __mul__                                    # 3 * money works as money * 3


total = sum([Money(100, "EUR"), Money(250, "EUR")])       # Money(350, 'EUR')
Money(1, "EUR") + 1                                       # TypeError: unsupported operand type(s)
```

- `return NotImplemented`, never `raise NotImplementedError`: the error stops Python from trying the other operand.
- If the right operand's type is a subclass of the left one's and overrides the reflected method, Python calls the reflected method first, so a subclass can take control of mixed operations.
- `sum()` starts from `0`, so `__radd__` must accept `0`; or pass a start value: `sum(items, Money(0, "EUR"))`.

## In-place operators

`x += y` calls `__iadd__` if it exists and rebinds `x` to its return value; otherwise it falls back to `x = x + y`. Mutable types (lists) change in place and return `self`; immutable types (tuples, ints, strings) have no `__iadd__` and build a new object.

That rebinding causes a famous trap: `+=` on a list inside a tuple both mutates the list and then fails to assign back.

```python
t = ([1],)
t[0] += [2]          # TypeError: 'tuple' object does not support item assignment
t                    # ([1, 2],): the list changed anyway
```

Use `t[0].extend([2])` instead. For your own mutable classes, implement `__iadd__` to mutate and `return self`; for immutable ones, leave it out.

## __format__ for format specs

`__format__(spec)` receives whatever follows the colon in an f-string or `format()` call, so a class can define its own mini-language and delegate the rest to a built-in type.

```python
class Temp:
    def __init__(self, celsius: float) -> None:
        self.c = celsius

    def __format__(self, spec: str) -> str:
        if spec.endswith("F"):
            return format(self.c * 9 / 5 + 32, spec[:-1] or ".1f") + " F"
        return format(self.c, spec or ".1f") + " C"


f"{Temp(100):F}"        # '212.0 F'
f"{Temp(21.456):.2f}"   # '21.46 C'
```

The default `object.__format__` raises `TypeError` for any non-empty spec, which is the error people see when they write `f"{obj:>10}"` on a class without `__format__`. Use `f"{str(obj):>10}"` or `f"{obj!s:>10}"` to pad the string form.

## __index__ for integer-like objects

`__index__` marks an object as a lossless integer. Sequences accept it as an index and slice bound, and `bin`, `hex`, `oct`, `range` and `operator.index` call it. It must return an exact `int`.

```python
class Bit:
    def __init__(self, n: int) -> None:
        self.n = n

    def __index__(self) -> int:
        return self.n


[10, 20, 30][Bit(1)]    # 20
bin(Bit(5))             # '0b101'
```

Define `__index__` only for types that really are integers (NumPy integer scalars do). A float-like type defines `__float__` and `__int__` instead, which truncate on purpose.

## __missing__ for dict subclasses

`dict.__getitem__` calls `__missing__(key)` on a subclass when the key is absent. `collections.defaultdict` is built on it. It runs only for `d[key]`, never for `d.get(key)` or `key in d`.

```python
class Defaults(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return f"<{key}>"                  # not stored; store it with self[key] = ... if needed


Defaults(a="1")["b"]                       # '<b>'
"{name} is {age}".format_map(Defaults(name="Ann"))   # 'Ann is <age>'
```

## __class_getitem__ for subscriptable classes

`__class_getitem__` makes `Cls[item]` work on the class itself. Generic classes get it from `typing.Generic` or the PEP 695 syntax (`class Box[T]: ...`); write it by hand only for a runtime registry or a non-typing meaning.

```python
import types


class Box:
    def __class_getitem__(cls, item):
        return types.GenericAlias(cls, item)


Box[int]     # __main__.Box[int]
```

## __iter__ fallback through __getitem__

The old sequence protocol still works: a class with `__getitem__` that accepts 0, 1, 2 and raises `IndexError` at the end is iterable, supports `in`, and with `__len__` supports `reversed`. New code should define `__iter__`, which is clearer and works with `collections.abc.Iterable` checks (`isinstance(x, Iterable)` is `False` for the `__getitem__`-only form).

## Context managers

`with x as y:` calls `type(x).__enter__(x)` and binds its result to `y`; on exit it calls `__exit__(exc_type, exc, tb)`, and a true return value swallows the exception. Return `None` (or `False`) unless swallowing is the point. For one-off managers, `contextlib.contextmanager` on a generator is shorter; cleanup patterns with `ExitStack` are in the `python-error-handling` skill.
