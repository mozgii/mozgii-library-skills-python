---
name: python-pattern-matching-enums
description: Uses structural pattern matching and enums in Python correctly - the match statement with literal, capture, wildcard, value, sequence, mapping, class, OR and AS patterns, guards, __match_args__ and dataclasses, the dotted-name versus capture trap, str not matching sequence patterns, exhaustive matching with assert_never, and enum.Enum, IntEnum, StrEnum, Flag and IntFlag with auto, unique and verify, members that carry values, methods and properties, _missing_ for lenient lookup, aliases, and serialising enums to JSON, databases and APIs. Use when writing a match statement, parsing commands, JSON or events by shape, replacing if/elif chains or string constants, choosing an enum type, making a type checker prove every case is handled, or when a case matches everything, an enum fails to serialise or compares unequal to its value.
metadata:
  tags:
  - python
  - pattern-matching
  - match-statement
  - enum
  - strenum
  - flag
  - exhaustiveness
---

# Python pattern matching and enums

The `match` statement (3.10) compares a value against patterns that describe its shape: a list of two items, a dict with a `"type"` key, a `Point` with `x == 0`. It binds parts of the value to names as it matches. Enums give a closed set of named values, and the two fit together: match on an enum and a type checker can prove every member is handled. This skill covers both and their traps. Choosing between `Enum`, `Literal` and data classes for a model is in the `python-data-models` skill; `Literal` types in the `python-typing` skill.

## Step 0: read the Python version

Pattern matching needs 3.10 and several enum features are newer, so read the oldest supported Python first.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
grep -n "requires-python" pyproject.toml
```

| Version | Change that matters |
|---|---|
| 3.10 | `match` statement |
| 3.11 | `StrEnum`, `verify`, `member`, `nonmember`, `enum.property`; `str()` of `IntEnum` members gives the number; `typing.assert_never` |
| 3.12 | `value in EnumClass` checks values (`1 in Color` is `True`), not only members |
| 3.13 | `EnumDict` public; `_add_alias_` and `_add_value_alias_` |

## How the match statement works

`match subject:` evaluates the subject once, then tries each `case` from top to bottom; the first pattern that matches (and whose guard is true) runs, and no other case does. When nothing matches, nothing happens: there is no error, so add `case _:` when "no match" is a bug.

```python
def command(text: str) -> str:
    match text.split():
        case []:
            return "empty"
        case ["quit" | "exit"]:                     # OR pattern of two literals
            return "bye"
        case ["go", direction]:                     # binds the second word
            return f"going {direction}"
        case ["drop", *items] if items:             # star capture plus a guard
            return f"dropping {', '.join(items)}"
        case ["drop"]:
            return "drop what?"
        case _:
            return f"unknown: {text!r}"
```

Names bound in a pattern stay bound after the `match`, like a `for` loop variable, even when a later guard failed. Do not read them after the block.

## Pattern kinds

Each pattern kind tests something different; they nest freely.

| Pattern | Example | Matches when |
|---|---|---|
| literal | `case 0:`, `case "go":`, `case None:` | `subject == literal` (`is` for `None`, `True`, `False`) |
| capture | `case x:` | always; binds the subject to `x` |
| wildcard | `case _:` | always; binds nothing |
| value | `case Color.RED:`, `case http.HTTPStatus.OK:` | `subject == value`; needs a dotted name |
| sequence | `case [a, b]:`, `case [first, *rest]:` | a sequence (not `str`, `bytes` or `bytearray`) of that length |
| mapping | `case {"type": "click", "x": x}:` | a mapping with those keys; extra keys are allowed |
| class | `case Point(x=0, y=y):`, `case int(n):` | `isinstance(subject, Point)` and the attributes match |
| OR | `case "quit" \| "exit":` | any alternative matches; each must bind the same names |
| AS | `case [int() as n, *_]:` | the inner pattern matches; binds the matched part to `n` |
| guard | `case [a, b] if a > b:` | the pattern matches and the condition is true |

Full rules for each kind, with the cases that surprise people, are in [match-patterns.md](references/match-patterns.md).

## Matching on data shapes: JSON and events

Mapping patterns match dictionaries by keys and value shapes, so one `match` validates and unpacks a decoded JSON message. Class patterns such as `int(x)` and `str(s)` check the value's type and bind it.

```python
from typing import Any


def handle(event: dict[str, Any]) -> str:
    match event:
        case {"type": "click", "pos": {"x": int(x), "y": int(y)}}:
            return f"click at {x},{y}"
        case {"type": "key", "key": str(key), **rest}:      # rest holds the other keys
            return f"key {key}, extra {sorted(rest)}"
        case {"type": str(kind)}:
            return f"unhandled {kind}"
        case _:
            return "malformed"


handle({"type": "click", "pos": {"x": "1", "y": 2}})        # 'unhandled click': "1" is not an int
```

Mapping patterns ignore extra keys, which suits evolving APIs. For input from outside the program, a validating model (pydantic, msgspec) gives better error messages than a fall-through `case _:`; use `match` after validation to branch on the shape.

## Class patterns and __match_args__

A class pattern `Point(x=0, y=y)` checks `isinstance` and then compares attributes by keyword. Positional sub-patterns `Point(0, y)` need `__match_args__`, a tuple naming the attributes in order; dataclasses and named tuples generate it from their fields.

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    x: float
    y: float                     # Point.__match_args__ == ('x', 'y')


def where(p: Point) -> str:
    match p:
        case Point(0, 0):
            return "origin"
        case Point(x=0, y=v) | Point(x=v, y=0):
            return f"on an axis at {v}"
        case Point(x, y) if x == y:
            return "diagonal"
        case Point():
            return "elsewhere"
```

- For a plain class, set `__match_args__ = ("x", "y")` yourself. Without it, positional sub-patterns raise `TypeError` at match time.
- Built-in types (`int`, `str`, `float`, `bytes`, `list`, `dict`, `tuple`, `bool` and others) match their single positional sub-pattern against the whole value: `case int(n):` binds the integer.
- `bool` is a subclass of `int`, so `case int():` also matches `True`. Put `case bool():` first when the two must differ.
- `case Point:` without parentheses is a capture pattern that matches anything; always write `Point()`.

## The dotted-name versus capture trap

A bare name in a pattern is always a capture, never a comparison with a constant of that name. `case RED:` binds the subject to a new variable `RED` and matches everything. Python catches it when another case follows (`SyntaxError: name capture 'RED' makes remaining patterns unreachable`), but a bare name in the last case silently matches anything.

```python
RED = 1

match colour:
    case RED:                  # WRONG: always matches and rebinds RED to colour
        ...

match colour:
    case Color.RED:            # RIGHT: a dotted name is a value pattern, compared with ==
        ...
```

Compare against constants through a dotted name: an enum member, a module attribute (`config.DEFAULT_PORT`) or a class attribute. A local constant without a dot needs a guard: `case x if x == RED:`.

## Exhaustive matching with assert_never

`typing.assert_never(value)` in the last case makes a type checker prove that every possibility was handled: when a new enum member or union variant is added, every `match` that misses it becomes a type error. At runtime it raises `AssertionError` if reached.

```python
from typing import assert_never


type Shape = Circle | Rect | Triangle


def area(shape: Shape) -> float:
    match shape:
        case Circle(radius=r):
            return 3.14159 * r * r
        case Rect(w=w, h=h):
            return w * h
        case _:
            assert_never(shape)    # pyright: "Triangle" is not assignable to "Never"
```

This works for enums, `Literal` unions, unions of classes and `bool`. Pyright reports the missing case with the type name; mypy needs `--enable-error-code exhaustive-match` to report it without the `assert_never` line.

## When to use match and when not

`match` pays off when the branches depend on the shape of the data: nested structures, several fields, type and value at once. For a single value compared with constants, a `dict` lookup or an `if`/`elif` chain is shorter.

| Situation | Use |
|---|---|
| branch on structure, unpack at the same time | `match` |
| branch on enum members with exhaustiveness | `match` + `assert_never` |
| map a key to a function or value | a `dict` |
| behaviour belongs to the type | a method on the class, or `functools.singledispatch` |
| two or three simple conditions | `if`/`elif` |

## Choosing an enum type

`enum.Enum` is the default: members are singletons that compare only with themselves. The mixed-in types (`IntEnum`, `StrEnum`) also behave as numbers or strings, which helps at boundaries and weakens type safety inside.

| Type | Members are | Compare equal to raw values | Use for |
|---|---|---|---|
| `Enum` | plain objects | no: `Color.RED != 1` | internal states and options |
| `StrEnum` | `str` | yes: `Status.DONE == "done"` | values stored in JSON, databases, config files, CLI options |
| `IntEnum` | `int` | yes, and order like ints | protocol codes, C APIs, existing integer columns |
| `Flag` | combinable bit sets | no | permissions, feature sets |
| `IntFlag` | combinable ints | yes | OS flags, bit masks in binary formats |

```python
from enum import Enum, IntEnum, StrEnum, auto


class Status(StrEnum):
    QUEUED = auto()            # "queued": auto() in a StrEnum gives the lower-cased name
    RUNNING = auto()
    DONE = auto()


class Priority(IntEnum):
    LOW = 1
    HIGH = 3


Status("done") is Status.DONE      # lookup by value
Status["DONE"] is Status.DONE      # lookup by name
Priority.HIGH > Priority.LOW       # True
```

- Lookup of an unknown value raises `ValueError: 'paused' is not a valid Status`. Catch it at the boundary and report the allowed values (`[s.value for s in Status]`).
- Compare members with `is` or `==`; both work because members are singletons.
- `str(Status.DONE)` and `f"{Status.DONE}"` give `"done"`; for a plain `Enum`, `str(Color.RED)` gives `"Color.RED"`. Use `.value` or `.name` explicitly in user-facing text.

## Enum members with values, methods and properties

An enum is a class, so members can carry several values and the class can have methods and properties. Give the class an `__init__` that unpacks a tuple value into attributes.

```python
from enum import Enum


class Planet(Enum):
    MERCURY = (3.303e23, 2.4397e6)
    EARTH = (5.976e24, 6.37814e6)

    def __init__(self, mass: float, radius: float) -> None:
        self.mass = mass
        self.radius = radius

    @property
    def surface_gravity(self) -> float:
        return 6.673e-11 * self.mass / (self.radius * self.radius)


Planet.EARTH.surface_gravity     # 9.8 (rounded)
```

To keep a simple `.value` (a code) while attaching extra data (a label), use `__new__`; the recipe is in [enum-recipes.md](references/enum-recipes.md) with `_missing_`, aliases, `Flag` boundaries and the functional API.

## Flag and IntFlag

`Flag` members combine with `|`, `&` and `^` into sets; `in` tests membership and iteration lists the single flags in a value.

```python
from enum import Flag, auto


class Perm(Flag):
    READ = auto()        # 1
    WRITE = auto()       # 2
    EXEC = auto()        # 4
    RW = READ | WRITE    # a named combination (an alias, not listed in iteration)


granted = Perm.READ | Perm.WRITE
Perm.WRITE in granted            # True
list(granted)                    # [Perm.READ, Perm.WRITE]
bool(Perm(0))                    # False: the empty set
```

`auto()` in a `Flag` gives powers of two. Store the integer `.value`, and rebuild with `Perm(value)`.

## Serialising enums

Enums cross program boundaries as their value or their name, never as the Python object. `StrEnum` and `IntEnum` serialise as their value without help, because they are strings and integers.

```python
import json
from enum import Enum


json.dumps({"status": Status.DONE})           # '{"status": "done"}'
json.dumps({"colour": Color.RED})             # TypeError: Object of type Color is not JSON serializable


def default(o: object) -> object:
    if isinstance(o, Enum):
        return o.value
    raise TypeError(f"not serialisable: {type(o).__name__}")


json.dumps({"colour": Color.RED}, default=default)    # '{"colour": 1}'
```

- Store values, not names, when the value is the stable identifier; names change in refactors. Never store `str(member)`.
- `sqlite3` and most database drivers accept `StrEnum`/`IntEnum` members as parameters and reject plain `Enum`; pass `.value`.
- Pydantic and msgspec serialise enum fields as values and validate them on input.
- Pickle stores a member by class and value: changing a value breaks old pickles with `ValueError: 1 is not a valid Color`, and renaming a member does not. `enum.pickle_by_enum_name` switches one class to names.

## Verify

Prove exhaustiveness with a planted gap: if the checker stays quiet here, it will stay quiet when a real member is added.

```bash
cat > /tmp/match_probe.py <<'PY'
from enum import Enum
from typing import assert_never


class Color(Enum):
    RED = 1
    BLUE = 2


def name(color: Color) -> str:
    match color:
        case Color.RED:
            return "red"
        case _:
            assert_never(color)
PY
uv run pyright /tmp/match_probe.py; echo "exit $?"          # expect "Color" ... "Literal[Color.BLUE]" and exit 1
rm /tmp/match_probe.py
grep -rnE '^\s*case [A-Z][A-Z0-9_]*:' src                   # expect nothing: a bare constant is a capture
uv run python -c "from my_app.models import Status; import json; print(json.dumps(Status.DONE))"   # the stored form
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `case RED:` for a constant | `case Color.RED:`, or a guard `case x if x == RED:` |
| `case _: pass` on an enum | `case _: assert_never(value)`, so a new member is a type error |
| `match` for a value compared with constants | a `dict` or `if`/`elif` |
| `str(member)` written to a file or database | `.value`, on purpose |
| `IntEnum` for internal states | plain `Enum`, so `Color.RED == 1` stays false |
| changing a stored member's value | keep the value; add a new member and migrate |

## Checklist

Pattern-matching and enum code is correct when every item holds.

- [ ] Constants in patterns are dotted names (`Color.RED`), never bare names.
- [ ] Class patterns have parentheses (`Point()`), and positional ones rely on `__match_args__`.
- [ ] A `match` over an enum or union ends with `case _: assert_never(x)`, and the type checker runs in CI.
- [ ] `case bool()` comes before `case int()` where they must differ; `str` is matched with `str()`, not a sequence pattern.
- [ ] Plain `Enum` for internal states; `StrEnum`/`IntEnum` where values cross a boundary.
- [ ] Unknown values from outside raise a clear error listing the allowed values.
- [ ] Enums are serialised by `.value` (or name, by choice), never with `str()`.

## Gotchas

These are the pattern-matching and enum bugs seen most often.

1. **A case matches every value.** It is a bare name (`case RED:`) or a class without parentheses (`case Point:`), which are capture patterns. Use `Color.RED` or `Point()`.
2. **A string never matches `case [first, *rest]`.** `str`, `bytes` and `bytearray` are excluded from sequence patterns on purpose. Use `case str(s):` and slice.
3. **`case int():` catches `True`.** `bool` is a subclass of `int`. Put `case bool():` first.
4. **A new enum member silently falls into `case _:`.** The default branch hides missing cases. End with `assert_never` and run pyright or mypy.
5. **`Color.RED == 1` is `False`, or a database driver rejects a member.** Plain `Enum` members are not their values. Use `.value`, or `IntEnum`/`StrEnum` when the value must act as one.
6. **`json.dumps` raises for an enum.** Plain `Enum` is not a JSON type. Use `StrEnum` or a `default=` function returning `.value`.
7. **Text shows `Color.RED` instead of `red`.** `str()` of a plain `Enum` member is `Class.NAME`. Format `.value` or `.name` explicitly.

## References

The reference files hold the deeper material.

- [match-patterns.md](references/match-patterns.md): every pattern kind with its exact rules, sequences and star patterns, mapping patterns and `**rest`, class patterns for built-ins and custom `__match_args__`, OR and AS patterns, guards, matching tuples of values, and how matching performs
- [enum-recipes.md](references/enum-recipes.md): `auto` and `_generate_next_value_`, `__new__` for a value plus extra data, `_missing_`, aliases with `unique` and `verify`, `member` and `nonmember`, `Flag` boundaries and `IntFlag`, the functional API, and enum storage in JSON, databases, pydantic and config files

## See also

- `python-typing`: `Literal`, unions and `assert_never` in the type system
- `python-data-models`: `Enum`, `Literal` and data classes for a model
- `python-serialization`: enums in JSON and other formats
- `pyright-strict`: the checker that proves a `match` complete
