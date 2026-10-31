---
title: pydantic v2 in practice
author: MOZGII, Inc.
lang: en
---

# pydantic v2 in practice

pydantic validates and serializes data from type annotations, with the core written in Rust. This reference covers pydantic 2.14 (October 2026): fields, validators, aliases, unions, `TypeAdapter`, strict mode, JSON Schema, performance, subclasses, recursive types and migration from v1. Run 2.14.2 or newer: it fixes two validation bypasses (see Gotchas).

## Contents

This reference has 16 sections.

1. Fields and constraints
2. Field and model validators
3. Computed fields
4. Aliases and camelCase JSON
5. Discriminated unions
6. TypeAdapter for non-model types
7. Strict mode and coercion
8. JSON Schema
9. Performance
10. Field metadata: which form and which level
11. Validators: use the least powerful one that works
12. Subclasses in fields: validation and serialization
13. Collections and unions under lax mode
14. Forward references and recursive types
15. Migrating from pydantic v1
16. Gotchas

## 1. Fields and constraints

Constraints go in `Field(...)` or in `Annotated[..., Field(...)]`; the annotated form makes reusable constrained types.

```python
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field

Sku = Annotated[str, Field(pattern=r"^[A-Z]{2}\d{4}$")]
Quantity = Annotated[int, Field(gt=0, le=1000)]


class OrderLine(BaseModel):
    sku: Sku
    qty: Quantity = 1
    unit_price: Decimal = Field(ge=0, decimal_places=2)
    tags: list[str] = Field(default_factory=list, max_length=10)
    note: str | None = Field(default=None, max_length=500, description="free text shown on the invoice")
```

Common constraints: `gt`, `ge`, `lt`, `le`, `multiple_of` (numbers); `min_length`, `max_length`, `pattern` (strings and collections); `decimal_places`, `max_digits` (Decimal). Types such as `EmailStr` (needs `email-validator`), `HttpUrl`, `AnyUrl`, `UUID4`, `PositiveInt`, `FilePath` and `DirectoryPath` add checks of their own.

## 2. Field and model validators

A field validator checks or transforms one field; a model validator checks fields together.

```python
from datetime import date
from typing import Self

from pydantic import BaseModel, field_validator, model_validator


class Booking(BaseModel):
    name: str
    start: date
    end: date

    @field_validator("name", mode="after")         # after: the value is already a str
    @classmethod
    def normalise_name(cls, v: str) -> str:
        return " ".join(v.split()).title()

    @model_validator(mode="after")
    def end_after_start(self) -> Self:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self
```

- `mode="before"` runs on the raw input (for example to split `"a,b"` into a list); `mode="after"` on the parsed value; `mode="wrap"` around the default validation.
- Raise `ValueError` (or `PydanticCustomError` for a stable error type); pydantic turns it into a `ValidationError` with the field location.
- `Annotated[str, AfterValidator(fn)]` attaches a validator to a reusable type instead of a model.

## 3. Computed fields

A computed field is a property included in `model_dump()` and JSON output.

```python
from decimal import Decimal

from pydantic import BaseModel, computed_field


class Line(BaseModel):
    qty: int
    unit_price: Decimal

    @computed_field
    @property
    def total(self) -> Decimal:
        return self.qty * self.unit_price
```

## 4. Aliases and camelCase JSON

Aliases map external names (camelCase JSON, legacy keys) to Python attribute names.

```python
from datetime import datetime

from pydantic import AliasGenerator, BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Order(BaseModel):
    model_config = ConfigDict(
        alias_generator=AliasGenerator(validation_alias=to_camel, serialization_alias=to_camel),
        validate_by_name=True,                    # accept order_id as well as orderId
        validate_by_alias=True,
    )
    order_id: int
    created_at: datetime


order = Order.model_validate_json('{"orderId": 7, "createdAt": "2026-10-28T10:00:00Z"}')
order.model_dump(mode="json", by_alias=True)      # {"orderId": 7, "createdAt": "2026-10-28T10:00:00Z"}
```

Single fields take `Field(alias="userId")`, or `validation_alias=AliasChoices("user_id", "userId")` to accept several names.

## 5. Discriminated unions

A discriminated union picks the right model from a tag field instead of trying each member, which is faster and gives clear errors.

```python
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Card(BaseModel):
    kind: Literal["card"]
    last4: str


class Bank(BaseModel):
    kind: Literal["bank"]
    iban: str


class Payment(BaseModel):
    method: Annotated[Card | Bank, Field(discriminator="kind")]


Payment.model_validate({"method": {"kind": "bank", "iban": "DE00..."}}).method   # Bank(...)
```

## 6. TypeAdapter for non-model types

`TypeAdapter` validates and serializes any type, including `TypedDict`, dataclasses and `list[...]`, without defining a model.

```python
from typing import TypedDict

from pydantic import TypeAdapter


class UserPayload(TypedDict):
    id: int
    name: str


users_adapter = TypeAdapter(list[UserPayload])                    # build once, at module level
users = users_adapter.validate_json(b'[{"id": 1, "name": "Ann"}]')
schema = users_adapter.json_schema()
```

Creating a `TypeAdapter` compiles a validator; create it once per type, not per call.

## 7. Strict mode and coercion

By default pydantic coerces compatible input (`"5"` to `5`, `"true"` to `True`, ISO strings to `datetime`); strict mode refuses anything but the exact type.

```python
from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter


class Strict(BaseModel):
    model_config = ConfigDict(strict=True)        # the whole model
    count: int


class Mixed(BaseModel):
    count: StrictInt                              # one field
    ratio: float = Field(strict=True)


TypeAdapter(UserPayload).validate_python({"id": 1, "name": "Ann"}, strict=True)   # per call
```

JSON input is still parsed sensibly in strict mode (a JSON string can become a `datetime`), because JSON has no datetime type.

## 8. JSON Schema

Every model and adapter produces a JSON Schema (draft 2020-12), which documents an API or feeds a tool definition.

```python
import json

print(json.dumps(Order.model_json_schema(by_alias=True), indent=2))
```

`Field(description=..., examples=[...])` and the model docstring appear in the schema. `mode="serialization"` gives the output schema, which differs when computed fields or serializers exist.

## 9. Performance

pydantic's Rust core is fast, but a few habits matter on hot paths.

- `Model.model_validate_json(raw)` is faster than `json.loads` followed by `model_validate`.
- Build `TypeAdapter`s once; never inside a loop.
- Use discriminated unions instead of plain unions of models.
- `model_construct(**trusted)` skips validation for data you already validated (from your own database).
- For very high throughput decoding of fixed schemas, msgspec `Struct` is faster still.

## 10. Field metadata: which form and which level

Field settings fall into two groups: those about the type (`gt`, `max_length`, `pattern`, `description`) and those about the field itself (`alias`, `deprecated`, `exclude`). `Annotated` carries the first group well and travels with reusable types; the assignment form suits what a static type checker must see, namely `default`, `default_factory` and `alias`.

```python
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

CountryCode = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=2, max_length=2)]


class Address(BaseModel):
    country: CountryCode
    city: str = Field(description="shown on the label")              # required: Field() without a default sets none
    state: str | None = Field(default=None, alias="region")
    legacy_id: Annotated[int | None, Field(deprecated=True)] = None   # the whole field is deprecated
```

- Field-level settings go on the outermost type. `Annotated[int, Field(deprecated=True)] | None` raises `UnsupportedFieldAttributeWarning` and has no effect on the field, because the setting sits on one member of the union.
- `name: str = Field(description="...")` looks optional and is required. Reading `Annotated[str, Field(description="...")]` makes that plain.
- String clean-up belongs in `StringConstraints`, not in a validator calling `.strip()`: it covers `strip_whitespace`, `to_upper`, `to_lower`, `pattern` and length limits, and `Field()` does not offer the first three.

## 11. Validators: use the least powerful one that works

A validator is code pydantic cannot see into, so reach for one only after the declarative options run out. Built-in constraints show up in the JSON Schema; custom checks do not.

| Need | Use | Why not the next one down |
|---|---|---|
| a range, length or pattern | `Field(...)`, `StringConstraints`, `annotated_types.Gt` | declared, in the schema, no Python call per value |
| one rule, used on several fields | `Annotated[int, AfterValidator(fn)]` | sits beside the field and travels with the type |
| one rule on one model | `@field_validator("x", mode="after")` with `@classmethod` | the value already has its declared type |
| reshape raw input first | `mode="before"` | the input can be anything; a model validator may even get an arbitrary object rather than a dict |
| a rule across fields | `@model_validator(mode="after")` | `self` is fully validated |

Decorated validators on subclasses make the run order hard to follow, which is another reason to prefer the annotated form.

## 12. Subclasses in fields: validation and serialization

A field typed with a base class validates and serializes as that base class, even when it holds a subclass instance, so subclass fields disappear without an error.

```python
from pydantic import BaseModel


class Payment(BaseModel):
    amount: int


class CardPayment(Payment):
    last4: str


class Receipt(BaseModel):
    payment: Payment


Receipt(payment=CardPayment(amount=5, last4="4242")).model_dump()      # {'payment': {'amount': 5}}: last4 is gone
Receipt.model_validate({"payment": {"amount": 5, "last4": "4242"}})     # a plain Payment; last4 was ignored
```

Pick the first fix that fits.

| Fix | Write | Use when |
|---|---|---|
| discriminated union (section 5) | `payment: Annotated[CardPayment \| BankPayment, Field(discriminator="kind")]` | the set of subclasses is known; solves validation and serialization |
| generic model | `class Receipt[P: Payment](BaseModel): payment: P`, then `Receipt[CardPayment]` | each use knows its subclass |
| polymorphic serialization (2.13+) | `model_config = ConfigDict(polymorphic_serialization=True)` on the **base** class, or `model_dump(polymorphic_serialization=True)` | output only; validation still builds the base class |
| `SerializeAsAny` | `payment: SerializeAsAny[Payment]`, or `model_dump(serialize_as_any=True)` | code on older pydantic |

Setting `polymorphic_serialization=True` on `Receipt`, the holder, changes nothing: it belongs on the class being serialized.

## 13. Collections and unions under lax mode

Lax mode already converts between collection types, so annotations can stay simple.

- `list[str]` accepts tuples and sets as well, and yields a list. Abstract types such as `collections.abc.Sequence` are slower and gain nothing when the aim is only to accept both.
- Do not write `int | str` and convert in a validator: declare `int`, and `"123"` is coerced for you.
- Avoid unions in general where one type will do, because every reader of the field must then branch on its type.

## 14. Forward references and recursive types

Pydantic evaluates annotations at runtime, so anything that hides a name makes its job harder.

- Avoid `from __future__ import annotations` in modules that define models: every annotation becomes a string to resolve. Quote only the names not defined yet (`parent: "Node | None" = None`).
- On Python 3.14 and newer, annotations are deferred by the language, so quotes are unnecessary.
- A recursive alias needs a real alias type. A quoted `TypeAlias` generally cannot be resolved; `type` (3.12+) or `TypeAliasType` can.

```python
from pydantic import TypeAdapter

type Json = list[Json] | dict[str, Json] | str | int | float | bool | None   # older Python: typing_extensions.TypeAliasType

TypeAdapter(Json).validate_python({"tags": ["a", 1, None]})
```

## 15. Migrating from pydantic v1

pydantic v2 renamed most methods; the v1 names are removed or deprecated.

| v1 | v2 |
|---|---|
| `class Config:` | `model_config = ConfigDict(...)` |
| `.dict()`, `.json()` | `.model_dump()`, `.model_dump_json()` |
| `.parse_obj()`, `.parse_raw()` | `.model_validate()`, `.model_validate_json()` |
| `@validator`, `@root_validator` | `@field_validator`, `@model_validator` |
| `.copy(update=...)` | `.model_copy(update=...)` |
| `parse_obj_as(T, data)` | `TypeAdapter(T).validate_python(data)` |
| `BaseSettings` in pydantic | `pydantic-settings` package |
| `orm_mode = True` | `from_attributes=True` |

`bump-pydantic` (`uvx bump-pydantic src/`) rewrites most of these automatically; review its diff.

## 16. Gotchas

These are the pydantic surprises seen most.

1. **A validator never runs for the default value.** Defaults are not validated unless `validate_default=True` on the field or in the config.
2. **`ValidationError` text leaks input.** Error messages include the input value. Log `exc.errors(include_input=False)` for sensitive data.
3. **Mutating a model skips validation.** Assignment is not validated unless `validate_assignment=True`; frozen models refuse assignment entirely.
4. **Two models with the same field names validate the wrong member of a union.** Plain unions try members in "smart" mode. Add a `Literal` tag and a discriminator.
5. **A type imported under `TYPE_CHECKING` breaks the model.** pydantic needs annotations at runtime. Import it for real, or call `Model.model_rebuild()` after the name exists.
6. **Extra data passed validation it should have failed.** Before 2.14.2, with extras allowed, JSON keys of extra data skipped the key type on `__pydantic_extra__` (GHSA-qpmf-fgh3-ff25, from 2.13), and sending both a `TypedDict` or dataclass field's alias and its name let the unvalidated value win (GHSA-3r8g-cqwr-jr29, all of 2.x). Upgrade to 2.14.2 if models allow extras on untrusted input; `extra="forbid"` avoids both.
7. **`UnsupportedFieldAttributeWarning` at class creation.** A field-level setting sits inside a union or a nested type. Move it to the outermost annotation (section 10).
