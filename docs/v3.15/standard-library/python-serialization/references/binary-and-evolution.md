---
title: Binary formats and schema evolution in Python
author: MOZGII, Inc.
lang: en
---

# Binary formats and schema evolution in Python

Reading and writing binary layouts with `struct`, reading binary files safely in exact-size pieces, bit fields, MessagePack timestamps and extension types, and keeping stored data readable across versions with version fields, migration chains, msgspec defaults and pydantic aliases.

## Contents

This reference has 6 sections.

1. struct format characters and byte order
2. A file format with a header and records
3. Bit fields and integers of any size
4. MessagePack timestamps and extension types
5. Versioned records and a migration chain
6. Tolerant readers with msgspec and pydantic

## 1. struct format characters and byte order

A `struct` format string starts with a byte-order character and lists one code per field. Use `<` or `>` (standard sizes, no padding) for anything stored or sent; native `@` (the default) follows the machine and inserts alignment padding.

| Code | C type | Size with `<` or `>` |
|---|---|---|
| `b` / `B` | signed / unsigned char | 1 |
| `h` / `H` | short | 2 |
| `i` / `I` | int | 4 |
| `q` / `Q` | long long | 8 |
| `e` / `f` / `d` | half / float / double | 2 / 4 / 8 |
| `?` | bool | 1 |
| `4s` | 4 bytes | 4 |
| `x` | pad byte | 1 |

```python
import struct

print(struct.calcsize("@bi"), struct.calcsize("<bi"))     # 8 5: native mode pads the int to 4-byte alignment
print(struct.pack(">I", 1).hex(), struct.pack("<I", 1).hex())   # 00000001 01000000
try:
    struct.pack("<B", 300)
except struct.error as exc:
    print(exc)                                           # values are range-checked
```

## 2. A file format with a header and records

A binary file format usually starts with a magic number and a version, then fixed-size records. Read exactly the number of bytes each part needs and fail clearly on a short read, which means a truncated or foreign file.

```python
import struct
from pathlib import Path
from typing import BinaryIO, NamedTuple

HEADER = struct.Struct("<4sHI")                          # magic, version, record count
RECORD = struct.Struct("<qd")                            # timestamp (ns), value


class Sample(NamedTuple):
    ts_ns: int
    value: float


def read_exact(f: BinaryIO, n: int) -> bytes:
    data = f.read(n)
    if len(data) != n:
        raise ValueError(f"truncated file: wanted {n} bytes, got {len(data)}")
    return data


def write_samples(path: Path, samples: list[Sample]) -> None:
    with path.open("wb") as f:
        f.write(HEADER.pack(b"SMPL", 1, len(samples)))
        for s in samples:
            f.write(RECORD.pack(*s))


def read_samples(path: Path) -> list[Sample]:
    with path.open("rb") as f:
        magic, version, count = HEADER.unpack(read_exact(f, HEADER.size))
        if magic != b"SMPL":
            raise ValueError(f"not a sample file: {magic!r}")
        if version != 1:
            raise ValueError(f"unsupported version {version}")
        body = read_exact(f, RECORD.size * count)
    return [Sample(*fields) for fields in RECORD.iter_unpack(body)]


p = Path("samples.bin")
write_samples(p, [Sample(1_793_178_000_000_000_000, 21.5), Sample(1_793_178_001_000_000_000, 21.7)])
print(read_samples(p))
p.write_bytes(p.read_bytes()[:-3])                       # cut the file
try:
    read_samples(p)
except ValueError as exc:
    print(exc)
```

- Check the declared count against the file size before allocating, so a corrupt header cannot ask for gigabytes.
- For very large files, read records in batches of a few thousand with `read_exact(f, RECORD.size * batch)`.

## 3. Bit fields and integers of any size

`int.from_bytes` and `int.to_bytes` convert integers of any width, and masks and shifts read bit fields packed into one integer.

```python
flags_byte = 0b1010_0110
compressed = bool(flags_byte & 0b1000_0000)              # bit 7
level = (flags_byte >> 4) & 0b0011                       # bits 4-5
print(compressed, level)

value = int.from_bytes(b"\x01\x02\x03", "big")           # a 24-bit integer: no struct code for it
print(value, value.to_bytes(3, "little").hex())
print((-2).to_bytes(2, "big", signed=True).hex())        # fffe
```

## 4. MessagePack timestamps and extension types

MessagePack has a native timestamp type; msgpack writes aware datetimes with `datetime=True` and reads them back as datetimes with `timestamp=3`. Other types travel as extension types with a numeric code you choose.

```python
from datetime import UTC, datetime
from decimal import Decimal

import msgpack

DECIMAL_CODE = 1


def default(obj: object) -> object:
    if isinstance(obj, Decimal):
        return msgpack.ExtType(DECIMAL_CODE, str(obj).encode())
    raise TypeError(f"cannot pack {type(obj).__name__}")


def ext_hook(code: int, data: bytes) -> object:
    if code == DECIMAL_CODE:
        return Decimal(data.decode())
    return msgpack.ExtType(code, data)


packed = msgpack.packb({"at": datetime(2026, 10, 28, 9, tzinfo=UTC), "price": Decimal("9.99")}, datetime=True, default=default)
print(msgpack.unpackb(packed, timestamp=3, ext_hook=ext_hook))
```

- `datetime=True` requires aware datetimes; a naive one raises.
- Without `timestamp=3`, msgpack returns `msgpack.Timestamp` objects; document which form your readers expect.

## 5. Versioned records and a migration chain

Every stored record carries a version; on read, small functions upgrade it one version at a time until it reaches the current shape. New code then deals with one shape only, and each migration is tested once.

```python
import json
from collections.abc import Callable

CURRENT = 3
Record = dict[str, object]


def v1_to_v2(r: Record) -> Record:                      # v2 split "name" into first and last
    first, _, last = str(r.pop("name")).partition(" ")
    return {**r, "first": first, "last": last}


def v2_to_v3(r: Record) -> Record:                      # v3 added "email", optional
    return {**r, "email": None}


MIGRATIONS: dict[int, Callable[[Record], Record]] = {1: v1_to_v2, 2: v2_to_v3}


def upgrade(r: Record) -> Record:
    version = int(r.get("version", 1))                  # records from before versioning count as v1
    if version > CURRENT:
        raise ValueError(f"record version {version} is newer than this program ({CURRENT})")
    while version < CURRENT:
        r = MIGRATIONS[version](r)
        version += 1
        r["version"] = version
    return r


print(upgrade(json.loads('{"name": "Ada Lovelace"}')))
print(upgrade({"version": 3, "first": "Bob", "last": "", "email": "b@example.com"}))
```

- Refuse records newer than the program instead of guessing; tell the person to update.
- Keep migrations forever, or until no stored data of that version can exist; delete them only with a documented minimum version.
- Write upgraded records back lazily (on next save) or in one batch job; both work if every reader runs `upgrade`.

## 6. Tolerant readers with msgspec and pydantic

Typed decoders make additive changes safe when new fields have defaults and unknown fields are ignored; renamed fields are read under both names during a transition.

```python
import msgspec
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class UserV3(msgspec.Struct):                            # unknown fields are ignored by default
    first: str
    last: str = ""
    email: str | None = None                             # new in v3: a default keeps old data loading


print(msgspec.json.decode(b'{"first": "Ada", "nickname": "A"}', type=UserV3))


class Customer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    full_name: str = Field(validation_alias=AliasChoices("full_name", "name"))   # read old and new names
    tier: str = "basic"


print(Customer.model_validate({"name": "Ada", "legacy": 1}))
print(Customer.model_validate({"full_name": "Bob"}).model_dump())             # always written with the new name
```

| Change | Safe for old readers | Safe for new readers |
|---|---|---|
| add an optional field with a default | yes, if they ignore unknown fields | yes |
| add a required field | yes | no: old data lacks it |
| remove a field | no, if they require it | yes, if they ignore unknown fields |
| rename a field | no | yes, with an alias for the old name |
| change a field's type | no | only with a migration |
