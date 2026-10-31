---
title: CSV, YAML and TOML recipes in Python
author: MOZGII, Inc.
lang: en
---

# CSV, YAML and TOML recipes in Python

Recipes for the text formats people open and edit: CSV dialects, sniffing a delimiter, typed reading, Excel quirks and formula injection; YAML documents, custom types and round-trip edits with ruamel.yaml; and TOML edits that keep comments with tomlkit.

## Contents

This reference has 7 sections.

1. CSV dialects and the semicolon question
2. Guessing the delimiter with csv.Sniffer
3. Typed CSV reading and quoting styles
4. CSV formula injection
5. YAML documents and custom types with PyYAML
6. Round-trip YAML edits with ruamel.yaml
7. TOML edits that keep comments with tomlkit

## 1. CSV dialects and the semicolon question

A CSV dialect bundles the delimiter, quote character and line terminator. Excel uses `,` in English locales and `;` in many European ones (where `,` is the decimal separator), so a file for German or French Excel needs `delimiter=";"`.

```python
import csv
import io

csv.register_dialect("excel_eu", delimiter=";", quoting=csv.QUOTE_MINIMAL)

buf = io.StringIO(newline="")
writer = csv.writer(buf, dialect="excel_eu")
writer.writerow(["Produkt", "Preis"])
writer.writerow(["Kaffee; stark", "3,50"])               # the ; inside a field is quoted
print(buf.getvalue())
print(list(csv.reader(io.StringIO(buf.getvalue(), newline=""), dialect="excel_eu")))
```

| Dialect | Delimiter | Line end | Use |
|---|---|---|---|
| `csv.excel` (default) | `,` | `\r\n` | most tools |
| `csv.excel_tab` | tab | `\r\n` | TSV for Excel |
| `csv.unix_dialect` | `,` | `\n` | Unix tools; quotes every field |

The `\r\n` line ending is what RFC 4180 specifies; the reader accepts any line ending, so leave it unless a Unix tool needs `\n`.

## 2. Guessing the delimiter with csv.Sniffer

`csv.Sniffer` guesses the dialect from a sample and whether the first row is a header. It is a heuristic: give it a few kilobytes and limit the candidate delimiters.

```python
import csv
import io

sample = "name;city;age\nAda;London;36\nZoë;Paris;29\n"
dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
print(repr(dialect.delimiter), csv.Sniffer().has_header(sample))
rows = list(csv.DictReader(io.StringIO(sample, newline=""), dialect=dialect))
print(rows[1])
```

When the sniffer raises `csv.Error: Could not determine delimiter`, fall back to `,` and report the file to the person, rather than guessing further.

## 3. Typed CSV reading and quoting styles

Every value the `csv` module reads is a `str`. Convert each column with its own function, and report the row number on failure so a person can fix the file.

```python
import csv
import io
from datetime import date
from decimal import Decimal

CONVERT = {"day": date.fromisoformat, "amount": Decimal, "count": int}
text = "day,amount,count,note\n2026-10-28,19.99,3,ok\n2026-10-29,abc,1,bad\n"

for line_no, row in enumerate(csv.DictReader(io.StringIO(text, newline="")), start=2):
    try:
        typed = {k: CONVERT.get(k, str)(v) for k, v in row.items()}
    except (ValueError, ArithmeticError) as exc:
        print(f"line {line_no}: {exc!r}")
        continue
    print(typed)

out = io.StringIO(newline="")
csv.writer(out, quoting=csv.QUOTE_STRINGS).writerow(["Ada", 36, None, 1.5])   # 3.12: quote only str values
csv.writer(out, quoting=csv.QUOTE_NOTNULL).writerow(["Ada", 36, None, 1.5])   # 3.12: quote all but None
print(out.getvalue())
```

- `Decimal("abc")` raises `decimal.InvalidOperation`, an `ArithmeticError`, not a `ValueError`; catch both.
- `QUOTE_STRINGS` and `QUOTE_NOTNULL` (3.12) let readers tell an empty string (`""`) from a missing value (nothing).

## 4. CSV formula injection

A cell that starts with `=`, `+`, `-`, `@`, a tab, a carriage return, a line feed, or a full-width `＝`, `＋`, `－` or `＠` (the list in OWASP's CSV injection page) is run as a formula when a spreadsheet opens the file, so exported user text can execute commands or leak data. Neutralise such values on export by prefixing a single quote.

```python
import csv
import io

DANGEROUS = ("=", "+", "-", "@", "\t", "\r", "\n", "\uff1d", "\uff0b", "\uff0d", "\uff20")   # the last four: full-width = + - @


def safe_cell(value: object) -> object:
    if isinstance(value, str) and value.startswith(DANGEROUS):
        return "'" + value                               # shown as text, not evaluated
    return value


buf = io.StringIO(newline="")
csv.writer(buf).writerow([safe_cell(v) for v in ["Ada", '=HYPERLINK("http://evil.example","x")', -5, "-5"]])
print(buf.getvalue())
assert safe_cell("\n=1+1") == "'\n=1+1" and safe_cell("\uff1d1+1") == "'\uff1d1+1"
```

Numbers stay numbers; only strings are prefixed. Apply this to exports a person opens in Excel, LibreOffice or Google Sheets, not to CSV read by programs.

## 5. YAML documents and custom types with PyYAML

`yaml.safe_load_all` reads a stream of documents separated by `---`, as Kubernetes manifests use; `SafeDumper` gets representers for your own types so `safe_dump` can write them.

```python
from dataclasses import asdict, dataclass

import yaml

stream = "kind: Service\nname: api\n---\nkind: Deployment\nname: api\nreplicas: 3\n"
for doc in yaml.safe_load_all(stream):
    print(doc["kind"], doc.get("replicas", "-"))


@dataclass
class Endpoint:
    host: str
    port: int


yaml.SafeDumper.add_representer(Endpoint, lambda dumper, ep: dumper.represent_dict(asdict(ep)))
print(yaml.safe_dump({"endpoints": [Endpoint("db", 5432)]}, sort_keys=False), end="")
```

Loading back into `Endpoint` is a validation step: read with `safe_load`, then build the objects with a typed model (msgspec `convert` or pydantic), never with a custom constructor that takes tags from the file.

## 6. Round-trip YAML edits with ruamel.yaml

ruamel.yaml's default round-trip mode (`YAML()`, `typ="rt"`) loads YAML 1.2 into dict- and list-like objects that remember comments, key order and formatting, so a program can change one value and write the file back with the rest untouched.

```python
import io

from ruamel.yaml import YAML                             # uv add ruamel.yaml

source = """\
# deployment settings
country: no        # stays a string in YAML 1.2
replicas: 2
image: "api:1.4"
ports:
  - 80
  - 443
"""
yaml = YAML()                                            # round-trip mode
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)
data = yaml.load(source)
data["replicas"] = 3
data["ports"].append(8443)
out = io.StringIO()
yaml.dump(data, out)
print(out.getvalue())
```

- `YAML(typ="safe")` gives plain types like PyYAML's `safe_load`, with YAML 1.2 rules; never use `typ="unsafe"` on outside input.
- Write the result with an atomic replace (python-files-pathlib skill) so a crash does not leave half a config file.

## 7. TOML edits that keep comments with tomlkit

`tomlkit` parses TOML into a document that keeps comments, whitespace and order; change values like a dict and dump it back. Use it when a program updates a file people also edit, such as `pyproject.toml`.

```python
import tomlkit                                           # uv add tomlkit

source = '''# server settings
[server]
host = "0.0.0.0"  # listen everywhere
port = 8080
'''
doc = tomlkit.parse(source)
doc["server"]["port"] = 9090
doc["server"].add("workers", 4)
print(tomlkit.dumps(doc))
print(doc.unwrap())                                      # plain dicts and ints for the rest of the program
```

`tomllib` stays the right choice for reading configuration; reach for tomlkit only for edits, since it is slower and returns its own container types.
