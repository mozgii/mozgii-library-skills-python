# Enum recipes

This reference collects tested `enum` patterns beyond the basics: generated values, members that carry extra data, lenient lookup, aliases and renames, constants that are not members, `Flag` boundaries, the functional API, and how enums are stored in JSON, TOML, command-line options, databases and pydantic models. Examples were run on Python 3.14 and 3.15 with pydantic 2.14.

## auto and _generate_next_value_

`auto()` asks the enum class for the next value. `Enum` and `Flag` give integers (1, 2, 3 and 1, 2, 4), `StrEnum` gives the lower-cased member name. Define `_generate_next_value_` as a static method before the members to change the rule.

```python
from enum import StrEnum, auto


class Header(StrEnum):
    @staticmethod
    def _generate_next_value_(name: str, start: int, count: int, last_values: list[str]) -> str:
        return name.replace("_", "-").title()

    CONTENT_TYPE = auto()      # "Content-Type"
    USER_AGENT = auto()        # "User-Agent"
```

Use `auto()` only when the value is not stored anywhere. If the value is written to a database or a file, write it out literally: inserting a member in the middle of an integer `auto()` enum renumbers every member after it.

## A value plus extra data with __new__

`__init__` receives the tuple and sets attributes, but the member's `.value` stays the whole tuple. When the value must stay a simple code (for lookup and storage) and the member carries extra data, define `__new__` and set `_value_`.

```python
from enum import IntEnum


class HttpStatus(IntEnum):
    OK = 200, "Success"
    NOT_FOUND = 404, "Not found"

    def __new__(cls, code: int, phrase: str) -> "HttpStatus":
        obj = int.__new__(cls, code)
        obj._value_ = code                  # the value is the code alone
        obj.phrase = phrase
        return obj


HttpStatus(404).phrase      # 'Not found'
HttpStatus.OK == 200        # True
```

For a plain `Enum`, call `object.__new__(cls)` instead of `int.__new__`.

## Lenient lookup with _missing_

`_missing_(cls, value)` is called when a lookup by value finds nothing. Return a member to accept the value, or `None` to let the normal `ValueError` happen. Use it for case-insensitive input or legacy spellings.

```python
from enum import StrEnum, auto


class Level(StrEnum):
    DEBUG = auto()
    INFO = auto()

    @classmethod
    def _missing_(cls, value: object) -> "Level | None":
        if isinstance(value, str):
            return cls.__members__.get(value.upper())
        return None


Level("INFO") is Level.INFO      # True
Level("Debug") is Level.DEBUG    # True
```

Keep `_missing_` strict about types and return `None` rather than raising, so the standard error message still lists what was wrong.

## Aliases, unique and verify

Two members with the same value make the second an alias of the first: `Answer.Y is Answer.YES`. Aliases are skipped by iteration and `len()` but appear in `__members__` and lookups by name.

```python
from enum import CONTINUOUS, Enum, IntEnum, unique, verify


class Answer(Enum):
    YES = 1
    Y = 1                      # alias


@unique                         # ValueError: duplicate values found in <enum 'Codes'>: B -> A
class Codes(Enum):
    A = 1
    B = 1


@verify(CONTINUOUS)             # ValueError: invalid enum 'Gap': missing values 2
class Gap(IntEnum):
    A = 1
    C = 3
```

`verify` (3.11) also takes `UNIQUE` and, for flags, `NAMED_FLAGS`. Add `@unique` to enums whose values are copied by hand, where a duplicate is a typo.

## Renaming a member without breaking stored data

Stored names break when a member is renamed; pickles do not, because they hold the value (changing a value is what breaks them). Keep the old name as an alias so both spellings work, then remove it after old data has been migrated.

```python
from enum import StrEnum


class Status(StrEnum):
    DONE = "done"
    FINISHED = "done"       # old name, kept as an alias during migration


Status["FINISHED"] is Status.DONE    # True
```

Python 3.13 also allows extra values that map to an existing member: `Unit.METRE._add_value_alias_("meter")` makes `Unit("meter")` return `Unit.METRE`.

## Constants that are not members: nonmember

Every plain attribute assigned in an enum body becomes a member. Wrap a class constant in `enum.nonmember` (3.11) to keep it out of the member list.

```python
from enum import Enum, nonmember


class Size(Enum):
    S = 1
    M = 2
    L = 3
    DEFAULT = nonmember(2)


list(Size)              # [Size.S, Size.M, Size.L]
Size(Size.DEFAULT)      # Size.M
```

Methods, properties, `classmethod` and `staticmethod` objects are never members. `enum.member` does the opposite of `nonmember`: it forces something, such as a function or a nested class, to become a member, which is rarely needed.

## Flag boundaries and IntFlag

The `boundary` class argument decides what happens when a `Flag` is built from an integer with bits that no member defines.

| Boundary | `Flag(value)` with unknown bits | Default for |
|---|---|---|
| `STRICT` | raises `ValueError` | `Flag` |
| `CONFORM` | drops the unknown bits | |
| `EJECT` | returns a plain `int` | |
| `KEEP` | keeps the unknown bits in the flag | `IntFlag` |

```python
from enum import CONFORM, Flag, IntFlag, auto


class Perm(Flag, boundary=CONFORM):
    READ = auto()
    WRITE = auto()


Perm(7)        # Perm.READ|WRITE: bit 4 dropped


class Mode(IntFlag):
    R = 4
    W = 2
    X = 1


Mode(5)        # Mode.R|X; an IntFlag compares and computes like an int
```

Use `STRICT` (the `Flag` default) for values from your own storage, where unknown bits mean corruption, and `KEEP` or `CONFORM` for values from an OS or a file format that may add bits later.

## The functional API

`Enum("Name", ...)` builds an enum at runtime from a list of names, a mapping of names to values, or pairs. It suits enums generated from configuration or a schema; type checkers understand only literal arguments.

```python
from enum import Enum, Flag, StrEnum

Env = StrEnum("Env", ["DEV", "PROD"])                  # values 'dev', 'prod'
Http = Enum("Http", {"OK": 200, "NOT_FOUND": 404})
Perm = Flag("Perm", ["READ", "WRITE"])                 # values 1, 2
```

Pass `module=__name__` when the enum must be pickled and was not created at module level.

## Enums in JSON

The `json` module writes `StrEnum` and `IntEnum` members as their values because they are `str` and `int`. Plain `Enum` and `Flag` need a `default=` function. On the way in, JSON gives strings and numbers; convert them with `Status(value)` at the boundary.

```python
import json
from enum import Enum, Flag, StrEnum, auto


class Color(Enum):
    RED = "red"


class Status(StrEnum):
    DONE = "done"


class Perm(Flag):
    READ = auto()
    WRITE = auto()


def to_jsonable(o: object) -> object:
    if isinstance(o, Enum):
        return o.value
    raise TypeError(f"not serialisable: {type(o).__name__}")


text = json.dumps({"colour": Color.RED, "perm": Perm.READ | Perm.WRITE}, default=to_jsonable)
# '{"colour": "red", "perm": 3}'

status = Status(json.loads('{"status": "done"}')["status"])
```

## Enums in pydantic models

Pydantic validates enum fields from their values and serialises them back to values in JSON mode. Invalid input gives a validation error that lists the allowed values.

```python
from enum import Enum, StrEnum

import pydantic


class Color(Enum):
    RED = "red"


class Status(StrEnum):
    DONE = "done"


class Job(pydantic.BaseModel):
    status: Status
    colour: Color


job = Job.model_validate({"status": "done", "colour": "red"})
job.status is Status.DONE          # True: a real member
job.model_dump()                   # {'status': <Status.DONE: 'done'>, 'colour': <Color.RED: 'red'>}
job.model_dump(mode="json")        # {'status': 'done', 'colour': 'red'}
```

`model_config = ConfigDict(use_enum_values=True)` stores the raw value instead of the member. Avoid it: the field then holds a `str`, and `job.colour is Color.RED` is false.

## Enums in config files and command-line options

TOML, YAML and environment variables give strings, so convert with the enum's constructor and let the `ValueError` explain the problem. For argparse, pass the enum as `type` and its members as `choices`; the help text lists the values.

```python
import argparse
import tomllib
from enum import StrEnum, auto


class Level(StrEnum):
    DEBUG = auto()
    INFO = auto()


level = Level(tomllib.loads('log_level = "info"')["log_level"])

parser = argparse.ArgumentParser()
parser.add_argument("--level", type=Level, choices=list(Level), default=Level.INFO)
parser.parse_args(["--level", "debug"]).level        # Level.DEBUG
```

Typer and Click accept enum types directly for options (the `python-cli` skill).

## Enums in databases

Store the value in a column of the matching type: text for `StrEnum`, integer for `IntEnum` and `IntFlag`. `sqlite3` accepts `StrEnum` and `IntEnum` members as parameters because they are `str` and `int`; plain `Enum` members raise `sqlite3.ProgrammingError`. Pass `.value` explicitly to any driver, so the stored form never depends on the driver's adapters. Read back with `Status(row[0])`.

- Add a `CHECK` constraint or a database enum type listing the values, so other writers cannot insert unknown values.
- Adding a member is safe; removing or renaming one needs a data migration first.
- Never store `str(member)` or `repr(member)`: their format depends on the enum type and Python version.

## Overriding __str__ and __format__

A plain `Enum` prints as `Color.RED`. When members appear in user-facing text or log lines, define `__str__` to return the value, or use `StrEnum`, whose `str()` and `format()` already give the value.

```python
from enum import Enum


class Color(Enum):
    RED = "red"
    GREEN = "green"

    def __str__(self) -> str:
        return self.value


f"{Color.RED}"     # 'red'
```
