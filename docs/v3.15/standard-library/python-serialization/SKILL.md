---
name: python-serialization
description: Reads and writes data formats in Python correctly - json with default= and object_hook, ensure_ascii, big integers, Decimal and NaN, JSON Lines, orjson and msgspec, TOML with tomllib (TOML 1.1 in 3.15) and tomli-w, YAML with yaml.safe_load and ruamel.yaml round trips, CSV with newline='', dialects and Excel encodings, pickle and why never on untrusted input, struct for binary formats, MessagePack, and schema evolution. Use when saving or loading JSON, TOML, YAML or CSV, choosing a file or wire format, making serialization faster, exchanging data with Excel or JavaScript, parsing binary files, or changing a stored format without breaking old data.
metadata:
  tags:
  - python
  - serialization
  - json
  - toml
  - yaml
  - csv
  - msgpack
  - struct
---

# Serialization in Python: JSON, TOML, YAML, CSV and binary formats

Serialization turns Python objects into bytes or text and back. The bugs are at the edges: a `datetime` JSON cannot encode, a big integer JavaScript rounds, a YAML `no` that becomes `False`, a CSV Excel opens as mojibake, a pickle that runs code. This skill picks the format and shows the safe call for each. Models to dicts is in the python-data-models skill; pickle and `yaml.load` risks in depth are in python-security.

## Step 0: read the versions

Format support depends on the Python version and on optional packages, so read both before choosing.

```bash
uv run python -c "import sys, pickle; print(sys.version_info[:2], 'pickle default protocol', pickle.DEFAULT_PROTOCOL)"
uv pip list 2>/dev/null | grep -iE "^(orjson|msgspec|tomli-w|tomlkit|pyyaml|ruamel.yaml|msgpack) "
```

| Version | Change that matters |
|---|---|
| 3.11 | `tomllib` reads TOML in the standard library |
| 3.14 | `pickle` default protocol is 5; `python -m json` replaces `python -m json.tool` |
| 3.15 | `tomllib` reads TOML 1.1 (newlines and trailing commas in inline tables, `\e`, times without seconds) |

October 2026 releases: orjson 3.13, msgspec 0.22, tomli-w 1.2, tomlkit 0.15, PyYAML 6.0.3, ruamel.yaml 0.19, msgpack 1.2.

## Choose a format

Pick the format by who reads it and whether it must be edited by hand.

| Need | Format | Library |
|---|---|---|
| API payloads, logs, data between programs | JSON | `json`; orjson or msgspec when speed matters |
| configuration a person edits | TOML | `tomllib` to read, `tomli-w` to write, `tomlkit` to edit keeping comments |
| configuration in an ecosystem that uses YAML (CI, Kubernetes) | YAML | `yaml.safe_load`; ruamel.yaml to edit keeping comments |
| tables for spreadsheets | CSV | `csv` |
| compact binary messages between your own programs | MessagePack | `msgpack` or `msgspec.msgpack` |
| a file format defined byte by byte | binary | `struct` |
| caching Python objects for the same program version | pickle | `pickle`, never on untrusted input |

## JSON with the json module

`json.dumps` and `json.loads` cover JSON in the standard library; set `ensure_ascii=False` to keep non-ASCII text readable, and add a `default` function for types JSON has no notation for.

```python
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4


class Status(Enum):
    PAID = "paid"


def to_json(obj: object) -> object:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (Decimal, UUID)):
        return str(obj)                                  # Decimal as a string keeps every digit
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    raise TypeError(f"{type(obj).__name__} is not JSON serializable")


@dataclass
class Invoice:
    id: UUID
    total: Decimal
    status: Status
    issued: datetime


doc = Invoice(uuid4(), Decimal("19.99"), Status.PAID, datetime(2026, 10, 28, 9, 0, tzinfo=UTC))
text = json.dumps({"invoice": doc, "note": "Grüße"}, default=to_json, ensure_ascii=False, indent=2)
print(text)
print(json.loads(text, parse_float=Decimal)["invoice"]["total"])   # parse_float: exact decimals on read
```

- `parse_float=Decimal` reads every JSON number with a fraction as `Decimal`, the right choice for money.
- `json.dumps` writes `NaN` and `Infinity`, which strict parsers (JavaScript's `JSON.parse`, Postgres) reject; pass `allow_nan=False` to raise instead.

## Decoding JSON into objects

`object_hook` receives every decoded JSON object (a `dict`) and may return something else, which is how tagged objects or dates are revived; `object_pairs_hook` gets the key-value pairs in order, which also detects duplicate keys.

```python
import json
from datetime import datetime


def revive(d: dict[str, object]) -> object:
    if d.get("__type__") == "datetime":
        return datetime.fromisoformat(str(d["value"]))
    return d


def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate keys: {sorted({k for k in keys if keys.count(k) > 1})}")
    return dict(pairs)


print(json.loads('{"at": {"__type__": "datetime", "value": "2026-10-28T09:00:00+00:00"}}', object_hook=revive))
try:
    json.loads('{"role": "user", "role": "admin"}', object_pairs_hook=no_duplicates)
except ValueError as exc:
    print(exc)                                           # the default keeps the last value silently
```

For more than a few types, decode into typed models with msgspec or pydantic instead of hooks.

## Big integers, floats and JavaScript

JSON numbers have no size limit, but readers do: JavaScript loses precision above 2**53, and Python refuses to parse integers longer than 4300 digits (a guard against slow conversion).

```python
import json

big_id = 2**63 + 1
print(json.dumps({"id": big_id}))                        # exact in Python, rounded by JavaScript
print(json.dumps({"id": str(big_id)}))                   # send ids above 2**53 as strings
try:
    json.loads("1" * 5000)
except ValueError as exc:
    print(str(exc)[:60])                                 # Exceeds the limit (4300 digits) ...
```

`sys.set_int_max_str_digits(0)` lifts the limit; do it for trusted input only.

## JSON Lines for large or streamed data

JSON Lines stores one JSON value per line, so files can be appended to, streamed and processed one record at a time without loading everything.

```python
import json
from pathlib import Path

path = Path("events.jsonl")
with path.open("w", encoding="utf-8") as f:
    for i in range(3):
        f.write(json.dumps({"n": i, "msg": "ok"}, ensure_ascii=False) + "\n")

with path.open(encoding="utf-8") as f:
    for line_no, line in enumerate(f, 1):
        if line.strip():
            record = json.loads(line)
            print(line_no, record["n"])
```

A crash can leave a last line without its newline; skip a final line that fails to parse.

## Faster JSON with orjson and msgspec

orjson and msgspec are compiled JSON libraries several times faster than `json`. orjson encodes dataclasses, datetimes, UUIDs and enums natively and returns `bytes`; msgspec also decodes straight into typed `Struct` classes with validation.

```python
from datetime import UTC, datetime

import msgspec                                           # uv add msgspec
import orjson                                            # uv add orjson

print(orjson.dumps({"at": datetime(2026, 10, 28, tzinfo=UTC), "n": 1}, option=orjson.OPT_SORT_KEYS))
print(orjson.loads(b'{"n": 1}'))


class Order(msgspec.Struct, frozen=True):
    id: int
    item: str
    quantity: int = 1


decoder = msgspec.json.Decoder(list[Order])              # build once, reuse
orders = decoder.decode(b'[{"id": 1, "item": "pen"}, {"id": 2, "item": "ink", "quantity": 3}]')
print(orders[1], msgspec.json.encode(orders[0]))
try:
    decoder.decode(b'[{"id": "x", "item": "pen"}]')
except msgspec.ValidationError as exc:
    print(exc)                                           # Expected `int`, got `str` - at `$[0].id`
```

- orjson refuses integers beyond 64 bits and has no `parse_float`; keep `json` with `Decimal` for exact money values or send them as strings.
- Pick msgspec when decoding into types is the goal; orjson when you serialise existing dicts and dataclasses fast. More options are in [json-in-depth.md](references/json-in-depth.md).

## TOML with tomllib and tomli-w

`tomllib` (3.11+) reads TOML; it opens files in binary mode and returns plain dicts. Writing needs `tomli-w`, and editing a file while keeping its comments needs `tomlkit`.

```python
import tomllib
from pathlib import Path

import tomli_w                                           # uv add tomli-w

Path("app.toml").write_text('[server]\nhost = "0.0.0.0"\nport = 8080\n# comment\n', encoding="utf-8")
with open("app.toml", "rb") as f:                         # binary mode is required
    config = tomllib.load(f)
config["server"]["port"] = 9090
Path("app.out.toml").write_bytes(tomli_w.dumps(config).encode("utf-8"))
print(Path("app.out.toml").read_text(encoding="utf-8"))   # the comment is gone: use tomlkit to keep it
```

TOML has no null: a missing key means "not set". A file using TOML 1.1 syntax loads on 3.15 and fails on older Pythons.

## YAML safely with PyYAML and ruamel.yaml

YAML is read with `yaml.safe_load`, which builds only plain types; `yaml.load` with the full loader can construct arbitrary objects. PyYAML implements YAML 1.1, where `no`, `off` and `y` are booleans and `12:30` is a base-60 number.

```python
import yaml                                              # uv add pyyaml

data = yaml.safe_load("country: no\nversion: 1.10\nat: 12:30\nname: Ada\n")
print(data)                                              # {'country': False, 'version': 1.1, 'at': 750, 'name': 'Ada'}
print(yaml.safe_dump({"name": "Zoë", "tags": ["a", "b"]}, sort_keys=False, allow_unicode=True), end="")
```

- Quote strings that look like other types (`"no"`, `"1.10"`, `"12:30"`), or use ruamel.yaml, which reads YAML 1.2 and keeps `no` a string.
- To change a YAML file a person maintains, use ruamel.yaml's round-trip mode: it keeps comments, order and quoting. The recipe is in [csv-yaml-toml-recipes.md](references/csv-yaml-toml-recipes.md).

## CSV with the csv module

The `csv` module handles quoting, embedded commas and newlines inside fields; open files with `newline=""` so it controls line endings, and pick the encoding for whoever opens the file.

```python
import csv
from pathlib import Path

rows = [{"name": "Zoë", "city": "Paris, FR", "note": "line1\nline2"}, {"name": "Ada", "city": "London", "note": ""}]
with Path("people.csv").open("w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig: Excel detects UTF-8
    writer = csv.DictWriter(f, fieldnames=["name", "city", "note"])
    writer.writeheader()
    writer.writerows(rows)

with Path("people.csv").open(newline="", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        print(row["name"], "|", row["city"], "|", repr(row["note"]))
```

- Without `newline=""`, Windows gets blank lines between rows and quoted newlines break.
- Excel in many European locales expects `;` as the delimiter; `csv.Sniffer` and dialects are in [csv-yaml-toml-recipes.md](references/csv-yaml-toml-recipes.md).

## pickle: only for your own data

`pickle` serialises almost any Python object, and loading a pickle can run arbitrary code chosen by whoever wrote the file. Use it only for data your own program wrote and will read back, such as a cache or multiprocessing messages.

```python
import pickle
from pathlib import Path

cache = Path("cache.pkl")
cache.write_bytes(pickle.dumps({"model": "v3", "weights": [0.1, 0.2]}, protocol=pickle.HIGHEST_PROTOCOL))
print(pickle.loads(cache.read_bytes()))
```

- Never unpickle a file from a user, the network, an upload or a shared folder; use JSON or MessagePack there.
- Pickles break when a class is renamed or moved; never use them for long-term storage.

## Binary formats with struct

`struct` packs and unpacks fixed-layout binary data, with an explicit byte order: `<` little-endian, `>` or `!` big-endian (network order). Always state the byte order; the default follows the machine and adds padding.

```python
import struct

HEADER = struct.Struct("<4sHHI")                         # magic, version, flags, record count; 12 bytes
raw = HEADER.pack(b"MZG1", 2, 0b01, 1000)
print(len(raw), raw.hex(" "))
magic, version, flags, count = HEADER.unpack(raw)
print(magic, version, flags, count)
print(list(struct.iter_unpack("<hh", b"\x01\x00\x02\x00\x03\x00\x04\x00")))   # repeated records
```

## MessagePack

MessagePack is a binary JSON: the same data model, smaller and faster to parse, with a native bytes type. Use it between programs you control; people cannot read it.

```python
import msgpack                                           # uv add msgpack

packed = msgpack.packb({"id": 7, "blob": b"\x00\x01", "tags": ["a"]})
print(len(packed), msgpack.unpackb(packed))
```

`msgspec.msgpack` does the same with typed `Struct` classes.

## Schema evolution

Stored and transmitted data outlives the code that wrote it. Put a version number in every stored record, make changes additive, and convert old versions on read.

- Adding a field: give it a default so old records still load.
- Removing a field: stop writing it; keep reading old records that have it (decoders that reject unknown fields need to allow it).
- Renaming a field: read both names for a release, write the new one.
- Changing a type or meaning: bump the version and add a migration function from the old version.

The migration chain and how msgspec and pydantic handle defaults and unknown fields are in [binary-and-evolution.md](references/binary-and-evolution.md).

## Verify

Round-trip the values that break formats, and make sure the unsafe loaders are gone. This script needs only PyYAML beside the standard library.

```python
import csv
import io
import json
from decimal import Decimal

import yaml

money = json.dumps({"total": "19.99", "id": str(2**63 + 1)})
assert json.loads(money)["id"] == "9223372036854775809"            # big ids survive as strings
assert json.loads('{"total": 19.99}', parse_float=Decimal)["total"] == Decimal("19.99")

out = io.StringIO(newline="")
csv.writer(out).writerow(["Paris, FR", 'say "hi"', "line1\nline2"])
out.seek(0)
assert next(csv.reader(out)) == ["Paris, FR", 'say "hi"', "line1\nline2"]

def safe_cell(value: str) -> str:                                  # formula injection: csv-yaml-toml-recipes.md section 4
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r", "\n", "\uff1d", "\uff0b", "\uff0d", "\uff20")) else value
assert [safe_cell(v) for v in ("=1+1", "\n=1+1", "\uff1d1+1", "\uff20A1", "Ada")] == ["'=1+1", "'\n=1+1", "'\uff1d1+1", "'\uff20A1", "Ada"]

assert yaml.safe_load('country: "no"')["country"] == "no"          # quoted stays a string
try:
    yaml.safe_load("!!python/object/apply:os.system [id]")
except yaml.constructor.ConstructorError:
    print("all formats round-trip; safe_load refused the object tag")
```

```bash
grep -rnE 'pickle\.loads?\(|yaml\.load\(|yaml\.unsafe_load|marshal\.loads?\(' src    # each hit must read your own files only
```

Keep one sample file per stored version in `tests/data/` and load each in every release.

## Anti-patterns

| Instead of | Do |
|---|---|
| pickle for data that crosses a trust boundary | JSON or MessagePack |
| `float` for money in JSON | `Decimal` as a string, or `parse_float=Decimal` on read |
| `yaml.load` with a full loader | `yaml.safe_load` |
| `line.split(",")` for CSV | the `csv` module with `newline=""` |
| a hand-written JSON string | `json.dumps` |
| a stored format with no version field | a version in every record, migrated on read |

## Checklist

Serialization code is correct when every item holds.

- [ ] The format matches the reader: JSON for programs, TOML for people, CSV for spreadsheets.
- [ ] JSON uses `ensure_ascii=False`, a `default` for extra types, `allow_nan=False` for strict consumers, and strings for ids above 2**53.
- [ ] Money is `Decimal` and travels as a string or with `parse_float=Decimal`.
- [ ] YAML is read with `safe_load` (or ruamel.yaml); ambiguous scalars are quoted.
- [ ] CSV files are opened with `newline=""` and an explicit encoding.
- [ ] pickle and full YAML loaders never see outside input.
- [ ] Binary layouts state the byte order; stored records carry a version.

## Gotchas

These are the serialization bugs seen most often.

1. **`TypeError: Object of type datetime is not JSON serializable`.** No `default`. Add a `default` function or use orjson.
2. **Country code `NO` became `False` (the Norway problem).** PyYAML follows YAML 1.1. Quote the value or use ruamel.yaml.
3. **An id changed after a round trip through a browser.** JavaScript rounds integers above 2**53. Send them as strings.
4. **Excel shows `ZoÃ«` instead of `Zoë`.** The CSV has no BOM, so Excel assumed the local code page. Write with `encoding="utf-8-sig"`.
5. **Blank lines between CSV rows on Windows.** The file was opened without `newline=""`.
6. **`TypeError: File must be opened in binary mode` from `tomllib.load`.** Open with `"rb"`.
7. **Old saved files fail to load after a release.** A required field was added or renamed. Give new fields defaults and migrate by version.

## References

The reference files hold deeper material.

- [json-in-depth.md](references/json-in-depth.md): custom encoders and decoders, canonical JSON for hashing, orjson options, msgspec Structs, tagged unions and performance tips
- [csv-yaml-toml-recipes.md](references/csv-yaml-toml-recipes.md): CSV dialects, sniffing, Excel and formula injection, ruamel.yaml round-trip edits, tomlkit edits that keep comments
- [binary-and-evolution.md](references/binary-and-evolution.md): struct layouts and file headers, MessagePack extension types, versioned records and migration chains with msgspec and pydantic

## See also

- `python-data-models`: models to dicts and validation on input
- `python-security`: why pickle and `yaml.load` are dangerous
- `python-text-unicode-regex`: encodings and byte order marks
- `python-configuration`: reading settings from TOML and the environment
