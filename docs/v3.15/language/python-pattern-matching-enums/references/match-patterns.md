# Match statement patterns in detail

This reference gives the exact rules of each pattern kind in Python's `match` statement, the cases that surprise people, and how matching performs. Every example was run on Python 3.14 and 3.15.

## Literal patterns

A literal pattern matches when the subject equals the literal. Numbers, strings, bytes, negative numbers and complex literals are allowed; `None`, `True` and `False` are compared with `is`.

```python
match value:
    case 0 | -1:              # negative literals are allowed
        ...
    case "go":
        ...
    case None:                # identity: only None itself
        ...
    case True:                # identity: 1 does not match True, but True is tested before the next case
        ...
    case 1:                   # equality: 1 and 1.0 both match
        ...
```

- Equality means `0.1 + 0.2` does not match `case 0.3:`. Floats need a guard with `math.isclose`.
- `case 1:` matches `True` too (`True == 1`), so test `True` first when the two must differ.
- Expressions are not allowed in patterns: `case 60 * 60:` is a syntax error. Use a dotted constant (`case Limits.HOUR:`) or a guard.

## Capture and wildcard patterns

A capture pattern is a bare name: it always matches and binds the subject (or the matched part) to that name. The wildcard `_` always matches and binds nothing; it may appear many times in one pattern.

- A capture pattern at the top level of a case matches everything, so any case after it is unreachable and Python refuses to compile it.
- The same name cannot be bound twice in one pattern: `case [x, x]:` is a syntax error. Check equality with a guard: `case [x, y] if x == y:`.
- Bindings remain after the `match` statement, including from a case whose guard failed. Treat them as local to the case.

## Value patterns: constants through dotted names

A value pattern is a dotted name such as `Color.RED`, `http.HTTPStatus.NOT_FOUND` or `settings.DEFAULT_MODE`; it matches when the subject equals that value. The dot is what makes Python read the name as a value rather than a capture.

```python
from http import HTTPStatus

match response.status_code:
    case HTTPStatus.OK:
        ...
    case HTTPStatus.NOT_FOUND | HTTPStatus.GONE:
        ...
    case code if 500 <= code < 600:
        ...
```

`HTTPStatus` is an `IntEnum`, so it matches the plain integer status code from any HTTP library.

## Sequence patterns

A sequence pattern `[a, b]` or `(a, b)` (the brackets are the same) matches any `collections.abc.Sequence` of the right length, except `str`, `bytes` and `bytearray`. Lists, tuples, `range`, `collections.deque`, `array.array` and `memoryview` all match.

| Subject | Matches `case [a, b]:` |
|---|---|
| `(1, 2)`, `[1, 2]`, `range(2)`, `deque([1, 2])` | yes |
| `array.array("i", [1, 2])`, `memoryview(b"ab")` | yes |
| `"ab"`, `b"ab"` | no: strings and bytes are excluded on purpose |
| `iter([1, 2])`, a generator | no: iterators are not sequences |
| `{1, 2}`, `{"a": 1, "b": 2}` | no |

One starred name collects any number of items, as a list: `case [first, *middle, last]:`. Only one star is allowed per sequence, so "contains X anywhere" is a guard: `case [*items] if "admin" in items:`.

```python
match (n % 3, n % 5):          # a tuple subject matches several values at once
    case (0, 0):
        return "FizzBuzz"
    case (0, _):
        return "Fizz"
    case (_, 0):
        return "Buzz"
    case _:
        return str(n)
```

Matching a generator needs `list(gen)` first, which consumes it; decide whether that is acceptable for the data size.

## Mapping patterns

A mapping pattern `{"type": "click", "x": x}` matches any `collections.abc.Mapping` that has every listed key with a matching value. Extra keys are ignored. `**rest` binds a dict of the remaining keys; `**_` is not allowed because it would mean nothing.

```python
match message:
    case {"op": "set", "key": str(key), "value": value}:
        store[key] = value
    case {"op": "delete", "key": str(key), **rest} if not rest:     # no extra keys allowed here
        store.pop(key, None)
    case {"op": str(op)}:
        raise ValueError(f"unknown op {op!r}")
```

- Keys must be literals or dotted constants; a captured name cannot be a key.
- Matching reads keys with `get`, so a `defaultdict` does not grow new keys during a match.
- `dict`, `OrderedDict`, `defaultdict`, `ChainMap` and `MappingProxyType` all match. A `TypedDict` is a `dict` at runtime and matches like one.
- To require that no other keys exist, bind `**rest` and add `if not rest`.

## Class patterns

A class pattern `Cls(...)` first checks `isinstance(subject, Cls)`, then matches each keyword sub-pattern against the attribute of that name and each positional sub-pattern against the attribute named in `Cls.__match_args__` at that position.

```python
class Vec:
    __match_args__ = ("x", "y")

    def __init__(self, x: float, y: float) -> None:
        self.x, self.y = x, y


match v:
    case Vec(0, 0):
        ...
    case Vec(x, y=0):            # positional and keyword can be mixed
        ...
```

- A missing attribute in a keyword sub-pattern makes the case fail; it does not raise.
- Too many positional sub-patterns raise `TypeError: Vec() accepts 2 positional sub-patterns (3 given)` when that case is tried.
- `@dataclass` generates `__match_args__` from fields in order, excluding keyword-only fields; `@dataclass(match_args=False)` turns it off. `NamedTuple` generates it from its fields.
- `__match_args__` is part of the public API: reordering it breaks callers' positional patterns. For classes outside your control, prefer keyword sub-patterns.

Built-in types `bool`, `bytearray`, `bytes`, `dict`, `float`, `frozenset`, `int`, `list`, `set`, `str` and `tuple` take one positional sub-pattern that matches the subject itself: `case str(name):` checks the type and binds the value.

Class patterns use `isinstance`, so abstract base classes and protocols marked `@runtime_checkable` work: `case Sized():` matches anything with `__len__`.

## OR patterns

An OR pattern `p1 | p2 | p3` tries the alternatives left to right and matches with the first that succeeds. Every alternative must bind the same set of names; `case [a] | [a, b]:` is refused with `SyntaxError: alternative patterns bind different names`.

```python
match token:
    case "+" | "-" as op:
        ...
    case Point(x=0, y=v) | Point(x=v, y=0):
        ...
```

## AS patterns

`pattern as name` matches the inner pattern and binds the whole matched part to `name`. It is how you keep a reference to a sub-structure while also checking its shape.

```python
match payload:
    case {"user": {"id": int() as user_id, "roles": [*roles]}} if "admin" in roles:
        grant_admin(user_id)
    case [Point() as start, *_, Point() as end]:
        draw_line(start, end)
```

## Guards

A guard `if condition` after a pattern is evaluated only when the pattern matched, with its names already bound. If the guard is false, matching continues with the next case.

- Guards run in order and may run for several cases on one subject; keep them free of side effects.
- A guard is the place for comparisons patterns cannot express: ranges, `isclose`, membership, equality between two captured values.

## How matching performs

Python tries the cases one after another. A `match` with ten string literals compiles to the same sequential tests as an `if`/`elif` chain, so matching the last case takes several times longer than the first; a dict lookup takes the same time for every key. On Python 3.14, matching the tenth of ten string cases measured several times slower than a dict lookup.

- For mapping a value to a result, use a dict. For dispatching on structure, the cost of `match` is the same as writing the `isinstance` and `len` checks by hand.
- Put the most frequent case first when a `match` sits in a hot loop.
- Class patterns call `isinstance` and `getattr` per attribute; deep nested patterns on large inputs cost what the equivalent manual checks cost, not less.

## Matching versus polymorphism

When every case of a `match` on class types calls a different operation, the operation may belong on the classes as a method. `match` fits when the set of types is closed and the operations are many and live elsewhere (an interpreter over an AST, a renderer over a message union). A method fits when the set of types grows and the operation is central to each type. `functools.singledispatch` sits in between: open to new types, defined outside the classes.
