---
name: python-data-models
description: Chooses and writes Python data models - dataclasses with frozen, slots and kw_only, NamedTuple, TypedDict, Enum and StrEnum, attrs, pydantic v2 models and TypeAdapter, msgspec Structs and Python 3.15 frozendict - with validation at the boundary, parse-don't-validate, immutability, defaults with default_factory, serialization to JSON, and settings from the environment. Use when defining classes that hold data, parsing JSON or config into typed objects, choosing between dataclass and pydantic, validating input, or converting models to and from dicts and JSON.
metadata:
  tags:
  - python
  - dataclasses
  - pydantic
  - typeddict
  - enum
  - attrs
  - msgspec
  - validation
  - serialization
---

# Python data models

A data model is a class whose job is to hold values with names and types. Python offers several kinds, and the choice depends on one question: does the data come from outside the program (and must be validated), or does your own code create it? This skill picks the right kind and shows each one done well. pydantic in depth is in [pydantic.md](references/pydantic.md).

## Step 0: read the versions and what the project already uses

Pydantic features depend on the installed minor, and a project should not gain a second modelling library by accident, so read both first.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
uv pip list 2>/dev/null | grep -iE "^(pydantic|pydantic-settings|attrs|msgspec) "
grep -rlE "BaseModel|msgspec\.Struct|@define\b|@dataclass" --include=*.py src | wc -l   # how many modules already use one
```

`frozendict` needs Python 3.15; every other kind here works on 3.11 and later.

## Choose the kind

Validate data where it enters the program, then pass typed, trusted objects inward.

| Kind | Validates at runtime | Use for |
|---|---|---|
| `@dataclass` | no (only what `__post_init__` checks) | values your code creates: domain objects, results, config after parsing |
| `typing.NamedTuple` | no | small immutable records that should also unpack like tuples |
| `typing.TypedDict` | no (static only) | dicts that must stay dicts: JSON payloads, `**kwargs` |
| `enum.Enum`, `StrEnum` | lookup by value | fixed sets of choices |
| pydantic `BaseModel` (2.14) | yes, with coercion and errors per field | input from JSON, HTTP, files, env vars; JSON Schema |
| pydantic `TypeAdapter` | yes | validating `TypedDict`, dataclasses, `list[...]` without a model class |
| attrs (26.1) | optional validators and converters | rich classes with many fields, where dataclasses fall short |
| msgspec `Struct` (0.22) | yes, on decode | very fast JSON or MessagePack decoding of known schemas |
| `frozendict` (3.15 builtin) | no | an immutable, hashable mapping |

## Dataclasses done well

A dataclass generates `__init__`, `__repr__` and `__eq__` from annotated fields; three options make it safer.

```python
from dataclasses import KW_ONLY, dataclass, field, replace
from decimal import Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class Money:
    amount: Decimal
    currency: str = "EUR"

    def __post_init__(self) -> None:
        if len(self.currency) != 3:
            raise ValueError(f"currency must be a 3-letter code, got {self.currency!r}")


@dataclass(slots=True)
class Cart:
    owner: str
    _: KW_ONLY                                   # every field after this is keyword-only
    items: list[str] = field(default_factory=list)


price = Money(amount=Decimal("9.99"))
discounted = replace(price, amount=Decimal("7.99"))     # frozen: make a changed copy
```

- `frozen=True` makes instances immutable and hashable, so they work as dict keys and cannot change behind your back.
- `slots=True` saves memory per instance and makes misspelled attribute assignments fail.
- `kw_only=True` (or `KW_ONLY`) forces `Money(amount=...)` at call sites, so adding a field never shifts positional arguments.
- Mutable defaults need `field(default_factory=list)`; `items: list[str] = []` raises `ValueError` at class creation.
- `dataclasses.asdict(obj)` converts recursively to dicts; `dataclasses.fields(cls)` lists fields.

## NamedTuple and TypedDict

`NamedTuple` is an immutable tuple with field names; `TypedDict` gives a plain dict a static shape.

```python
from typing import NamedTuple, NotRequired, TypedDict


class Point(NamedTuple):
    x: float
    y: float


x, y = Point(1.0, 2.0)                      # unpacks like a tuple


class UserPayload(TypedDict):
    id: int
    name: str
    email: NotRequired[str]
```

A `TypedDict` is erased at runtime: `UserPayload(**data)` checks nothing. Validate it with `TypeAdapter(UserPayload).validate_python(data)` when the data is untrusted.

## Enums

An enum names a fixed set of values; `StrEnum` members are also strings, which suits JSON and databases.

```python
from enum import StrEnum, auto, unique


@unique
class Status(StrEnum):
    QUEUED = auto()                         # value "queued"
    RUNNING = auto()
    DONE = auto()


Status("done")                              # Status.DONE; raises ValueError for unknown values
Status.DONE == "done"                       # True
```

Match on members with `match status: case Status.DONE: ...` and finish with `assert_never` (the `python-typing` skill) so a new member is a type error everywhere it is not handled.

## Validation at the boundary with pydantic

Parse untrusted input into a validated model once, where it enters, and let the rest of the program trust the types ("parse, don't validate").

```python
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Sku = Annotated[str, Field(pattern=r"^[A-Z]{2}\d{4}$")]


class OrderLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sku: Sku
    qty: int = Field(gt=0, le=1000)
    unit_price: Decimal = Field(ge=0, decimal_places=2)


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: int
    lines: list[OrderLine] = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def unique_skus(self) -> Self:
        skus = [line.sku for line in self.lines]
        if len(skus) != len(set(skus)):
            raise ValueError("duplicate sku in lines")
        return self


raw_bytes = b'{"order_id": 7, "lines": [], "created_at": "2026-10-31T09:00:00Z"}'
try:
    order = Order.model_validate_json(raw_bytes)          # parse and validate in one step, in Rust
except ValidationError as exc:
    errors = exc.errors(include_input=False, include_url=False, include_context=False)
    print([(e["loc"], e["msg"]) for e in errors])         # [(('lines',), 'List should have at least 1 item after validation, not 0')]
```

- `extra="forbid"` rejects unknown keys, which catches typos in input and config.
- pydantic coerces by default (`"5"` becomes `5`); `ConfigDict(strict=True)` or `Field(strict=True)` refuses that where types must match exactly.
- Plain `exc.errors()` carries each rejected value under `input`, and `str(exc)` prints it too. Return `exc.errors(include_input=False, include_url=False, include_context=False)` to callers and log that, so a secret in the input never reaches a response or a log.

## Serialization

Convert models to plain data only at the edge, where JSON or a database needs it.

| Model | To dict | To JSON |
|---|---|---|
| dataclass | `dataclasses.asdict(obj)` | `json.dumps(asdict(obj), default=str)` |
| pydantic | `obj.model_dump(mode="json")` | `obj.model_dump_json()` |
| msgspec | `msgspec.to_builtins(obj)` | `msgspec.json.encode(obj)` |
| TypedDict | it is a dict | `json.dumps(obj)` |

`mode="json"` turns `datetime`, `Decimal`, `UUID` and enums into JSON-safe values; plain `model_dump()` keeps Python objects.

## Settings from the environment

pydantic-settings reads configuration from environment variables and `.env` files into a validated model, with secrets kept out of `repr`.

```python
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MY_APP_", env_file=".env", extra="ignore")
    database_url: str = "postgresql://localhost/my_app"
    api_token: SecretStr                      # MY_APP_API_TOKEN; printed as **********
    debug: bool = False


settings = Settings()                         # fails fast at start-up if MY_APP_API_TOKEN is missing
token = settings.api_token.get_secret_value()
```

Create the settings object once at start-up and pass it down; never read `os.environ` deep inside the code.

## Verify

These attack cases prove the boundary refuses what it should and keeps rejected values out of what it reports. The snippet uses `Order` from the example above.

```python
import json

from pydantic import ValidationError

GOOD = {"order_id": 1, "created_at": "2026-10-31T09:00:00Z",
        "lines": [{"sku": "AB1234", "qty": 1, "unit_price": "9.99"}]}
for bad, why in [
    ({**GOOD, "is_admin": True}, "an extra key is refused"),
    ({**GOOD, "lines": []}, "an empty list is refused"),
    ({**GOOD, "lines": GOOD["lines"] * 2}, "a duplicate sku is refused"),
    ({**GOOD, "order_id": "1' OR '1'='1"}, "a hostile id is refused"),
    ({**GOOD, "lines": [{"sku": "sk-canary", "qty": 1, "unit_price": "1"}]}, "a bad sku is refused"),
]:
    try:
        Order.model_validate(bad)
    except ValidationError as exc:
        report = json.dumps(exc.errors(include_input=False, include_url=False, include_context=False))
        assert "canary" not in report and "OR" not in report, why
    else:
        raise AssertionError(why)
Order.model_validate(GOOD)
print("ok")
```

Then run the type checker (`uv run pyright src`) and grep for raw dict access past the boundary.

## Anti-patterns

| Instead of | Do |
|---|---|
| `dict[str, Any]` passed through the code | a typed model, parsed once |
| pydantic models for every internal value | dataclasses inside, pydantic at the edge |
| `items: list = []` | `field(default_factory=list)` |
| `exc.errors()` or `str(exc)` in a response or log | `exc.errors(include_input=False, include_url=False, include_context=False)` |
| validating the same data at every layer | once, where it enters |
| strings for a fixed set of choices | `StrEnum` |
| `extra="allow"` on untrusted input | `extra="forbid"` |
| a `TypedDict` trusted at run time | `TypeAdapter(...).validate_python(...)` |

## Checklist

Data models are in good shape when every item holds.

- [ ] External data (JSON, files, env, CLI) is validated once, at the boundary.
- [ ] Internal values are dataclasses, preferably `frozen`, `slots` and `kw_only`.
- [ ] No mutable default without `default_factory`.
- [ ] Unknown input keys are rejected (`extra="forbid"`) unless forward compatibility needs them.
- [ ] Choices are enums; matches over them are exhaustive.
- [ ] Secrets use `SecretStr` and never appear in `repr` or logs.

## Gotchas

These are the data-model bugs seen most.

1. **Every instance shares one list.** A mutable default outside a dataclass (`def __init__(self, items=[])`) or on a plain class attribute. Use `default_factory`, or `None` and create inside.
2. **A `TypedDict` let bad data through.** It has no runtime checks. Validate with `TypeAdapter`.
3. **pydantic accepted `"yes"` as `True`.** Lax mode coerces. Use strict fields where exactness matters.
4. **A frozen dataclass with a list field is still mutable inside.** `frozen` stops reassignment, not mutation of the list. Use `tuple[...]` or `frozenset`.
5. **`asdict` is slow on large trees.** It deep-copies recursively. Serialize with pydantic, msgspec or a hand-written `to_dict` on hot paths.
6. **`model_dump()` output fails `json.dumps`.** It still holds `datetime` and `Decimal`. Use `model_dump(mode="json")` or `model_dump_json()`.

## References

The reference files hold the longer recipes and the full tool detail.

- [pydantic.md](references/pydantic.md): pydantic v2 fields and constraints, validators, aliases and camelCase, discriminated unions, TypeAdapter, strict mode, JSON Schema, performance, migrating from v1

## See also

- `python-typing`: annotations, `TypedDict` and `Protocol` in depth
- `python-serialization`: JSON, msgpack and schema evolution
- `python-configuration`: settings layers
- `python-pattern-matching-enums`: enums and exhaustive matches
- `pyright-strict`: keeping types honest past the boundary
- `python-security`: handling untrusted input
