---
title: A generic to_json function with functools.singledispatch
author: MOZGII, Inc.
lang: en
---

# A generic to_json function with functools.singledispatch

`functools.singledispatch` picks an implementation by the type of the first argument, so a serialiser for several types needs no `isinstance` chain and other modules can add types. Handlers register from their annotation.

```python
from datetime import date
from decimal import Decimal
from functools import singledispatch
from typing import Any


@singledispatch
def to_json(value: object) -> Any:
    raise TypeError(f"cannot serialise {type(value).__name__}")


@to_json.register
def _(value: Decimal) -> str:
    return str(value)


@to_json.register
def _(value: date) -> str:                     # datetime is a subclass, so it is covered too
    return value.isoformat()


@to_json.register
def _(value: list) -> list:
    return [to_json(v) for v in value]
```

`to_json([Decimal("1.50"), date(2026, 10, 31)])` returns `['1.50', '2026-10-31']`; `to_json(object())` raises `TypeError: cannot serialise object`.
