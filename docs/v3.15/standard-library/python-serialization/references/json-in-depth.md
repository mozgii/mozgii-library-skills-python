---
title: JSON in depth in Python
author: MOZGII, Inc.
lang: en
---

# JSON in depth in Python

Deeper JSON techniques: an extensible encoder with `functools.singledispatch`, canonical JSON for hashing and signatures, the `python -m json` command, orjson options, msgspec Structs with renamed fields and tagged unions, JSON Schema from types, and performance tips.

## Contents

This reference has 6 sections.

1. An extensible encoder with singledispatch
2. Canonical JSON for hashes and signatures
3. Pretty-printing and checking JSON from the command line
4. orjson options
5. msgspec Structs: renames, tagged unions and unknown fields
6. Performance tips

## 1. An extensible encoder with singledispatch

A `default` function built on `functools.singledispatch` lets each module register how its own types become JSON, instead of one function that imports everything.

```python
import json
from datetime import date, datetime
from decimal import Decimal
from functools import singledispatch
from ipaddress import IPv4Address


@singledispatch
def to_json(obj: object) -> object:
    raise TypeError(f"{type(obj).__name__} is not JSON serializable")


@to_json.register
def _(obj: datetime | date) -> str:
    return obj.isoformat()


@to_json.register
def _(obj: Decimal) -> str:
    return str(obj)


@to_json.register(IPv4Address)                           # another module can register its own types
def _(obj: IPv4Address) -> str:
    return str(obj)


print(json.dumps({"when": date(2026, 10, 28), "price": Decimal("1.10"), "ip": IPv4Address("10.0.0.1")}, default=to_json))
```

Subclassing `json.JSONEncoder` and overriding `default` does the same; the function form is easier to test and to pass to orjson, which accepts the same `default` callable.

## 2. Canonical JSON for hashes and signatures

Two encodings of the same data must produce the same bytes before you hash or sign them: sort the keys, drop insignificant whitespace, and fix the text encoding.

```python
import hashlib
import json


def canonical(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


a = {"b": 1, "a": [1, 2], "name": "Zoë"}
b = {"name": "Zoë", "a": [1, 2], "b": 1}
print(canonical(a))
print(hashlib.sha256(canonical(a)).hexdigest() == hashlib.sha256(canonical(b)).hexdigest())   # True
```

- Floats are the weak point: `0.1 + 0.2` encodes as `0.30000000000000004`; hash data with integers or decimal strings where possible.
- Another language must follow the same rules to verify the hash; RFC 8785 (JSON Canonicalization Scheme) defines them precisely when interoperability matters.

## 3. Pretty-printing and checking JSON from the command line

`python -m json` (3.14; `python -m json.tool` before) validates and pretty-prints JSON from a file or stdin, which is handy in scripts and CI.

```bash
echo '{"b": 1, "a": "Zoë"}' | python -m json --sort-keys --no-ensure-ascii
python -m json --json-lines events.jsonl          # one JSON value per line
python -m json --compact data.json > data.min.json
```

A parse error prints the line and column and exits with status 1, so `python -m json file.json > /dev/null` is a quick validity check.

## 4. orjson options

orjson takes options as a bit mask (`option=orjson.OPT_A | orjson.OPT_B`) and returns `bytes`. These are the ones used most.

| Option | Effect |
|---|---|
| `OPT_INDENT_2` | pretty-print with two spaces |
| `OPT_SORT_KEYS` | sorted keys |
| `OPT_NON_STR_KEYS` | allow `int`, `UUID`, `datetime` and other keys, converted to strings |
| `OPT_NAIVE_UTC` | treat naive datetimes as UTC instead of writing them without an offset |
| `OPT_UTC_Z` | write UTC as `Z` instead of `+00:00` |
| `OPT_OMIT_MICROSECONDS` | drop microseconds from datetimes |
| `OPT_APPEND_NEWLINE` | add `\n`, for JSON Lines |
| `OPT_SERIALIZE_NUMPY` | encode NumPy arrays and scalars natively |
| `OPT_PASSTHROUGH_DATACLASS`, `OPT_PASSTHROUGH_DATETIME` | send those types to `default` instead of the built-in encoding |
| `OPT_STRICT_INTEGER` | refuse integers beyond 53 bits, the JavaScript-safe range |

```python
from datetime import UTC, datetime
from decimal import Decimal

import orjson

record = {"at": datetime(2026, 10, 28, 9, tzinfo=UTC), 7: "seven", "price": Decimal("9.99")}


def default(obj: object) -> object:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError                                       # orjson turns this into JSONEncodeError


out = orjson.dumps(record, default=default, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_UTC_Z | orjson.OPT_APPEND_NEWLINE)
print(out)                                                # b'{"at":"2026-10-28T09:00:00Z","7":"seven","price":"9.99"}\n'
print(orjson.dumps(float("nan")))                         # b'null'
try:
    orjson.dumps(2**60, option=orjson.OPT_STRICT_INTEGER)
except orjson.JSONEncodeError as exc:
    print(exc)
```

- orjson writes `NaN` and `Infinity` as `null` instead of failing; check floats yourself if `null` would be wrong.
- `orjson.loads` accepts `bytes`, `bytearray`, `memoryview` and `str`; pass the raw bytes from a file or socket without decoding.

## 5. msgspec Structs: renames, tagged unions and unknown fields

msgspec decodes JSON straight into typed `Struct` classes and validates while it parses. Struct options map Python names to wire names, choose between types by a tag field, and decide what happens to unknown fields.

```python
import msgspec


class Base(msgspec.Struct, rename="camel", omit_defaults=True, forbid_unknown_fields=True):
    pass


class Created(Base, tag="created"):
    order_id: int
    customer_name: str


class Cancelled(Base, tag="cancelled"):
    order_id: int
    reason: str = "unspecified"


Event = Created | Cancelled
decoder = msgspec.json.Decoder(list[Event])
events = decoder.decode(b'[{"type": "created", "orderId": 1, "customerName": "Ada"}, {"type": "cancelled", "orderId": 1}]')
print(events)
print(msgspec.json.encode(events[1]))                     # tag written, default omitted
try:
    decoder.decode(b'[{"type": "created", "orderId": 2, "customerName": "Bob", "extra": 1}]')
except msgspec.ValidationError as exc:
    print(exc)
print(msgspec.json.schema(Event)["$defs"].keys())        # JSON Schema generated from the types
```

- The tag field is `"type"` by default; set `tag_field="kind"` on the base class to use another name.
- `forbid_unknown_fields=True` catches typos in config files; leave it off for messages from newer versions of a service, so old readers tolerate new fields.
- `msgspec.convert(data, type=Order)` validates already-decoded Python data (a dict from YAML or TOML) into the same types.

## 6. Performance tips

JSON speed matters for large files and hot request paths; these choices give most of the gain.

- Reuse encoder and decoder objects (`msgspec.json.Decoder(T)`, `json.JSONDecoder()`), created once at module level.
- Work in `bytes` end to end: read files in binary mode and give the bytes to orjson or msgspec, which parse UTF-8 directly.
- Decode only what you need: a msgspec `Struct` with three fields skips the rest of a large object faster than building a full dict.
- For gigabyte files, use JSON Lines and process record by record instead of one huge array.

```python
import json
import time

import msgspec
import orjson

data = [{"id": i, "name": f"user{i}", "tags": ["a", "b"], "score": i * 0.5} for i in range(50_000)]
for name, dumps in (("json", lambda d: json.dumps(d).encode()), ("orjson", orjson.dumps), ("msgspec", msgspec.json.encode)):
    start = time.perf_counter()
    dumps(data)
    print(f"{name:8} {time.perf_counter() - start:.3f}s")
```

Measure with your own data: gains depend on the mix of strings, numbers and nesting.
