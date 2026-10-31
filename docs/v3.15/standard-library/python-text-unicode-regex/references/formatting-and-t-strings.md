---
title: String formatting and t-strings in Python
author: MOZGII, Inc.
lang: en
---

# String formatting and t-strings in Python

The format mini-language that f-strings, `format()` and `str.format` share, which formatting style fits which job, and how to write t-string (PEP 750, Python 3.14) processors that build SQL, shell commands and structured log records from templates safely.

## Contents

This reference has 6 sections.

1. The format mini-language
2. Choosing a formatting style
3. The anatomy of a t-string
4. A t-string processor for SQL parameters
5. t-string processors for shell commands and logs
6. Gotchas

## 1. The format mini-language

A format spec after the colon in `f"{value:spec}"` has the shape `[[fill]align][sign][z][#][0][width][grouping][.precision[grouping]][type]`; every part is optional.

```python
from datetime import datetime

n, x, when = 1234567, -0.0001234, datetime(2026, 10, 28, 9, 5)
print(f"[{n:>12,}]")              # right-aligned, comma groups:   [   1,234,567]
print(f"{n:_x} {255:#011_b}")       # hex in groups of 4: 12_d687; binary: 0b1111_1111
print(f"{x:.2e} {x:z.2f} {0.256:.1%}")   # 'z' turns -0.00 into 0.00 (3.11)
print(f"{1234.56789:,.4f} {1234.56789:.6_f}")   # grouping in the fraction part needs 3.14
print(f"{'title':*^11}")          # fill '*', centred: ***title***
print(f"{when:%Y-%m-%d %H:%M}")   # types with __format__ read their own spec
print(f"{n=:,} {'café'!a}")       # self-documenting '=', and ascii() conversion
```

| Part | Values |
|---|---|
| align | `<` left, `>` right, `^` centre, `=` pad after the sign |
| sign | `+` always, `-` negative only (default), space for a leading space |
| grouping | `,` or `_` every three digits (four for `x`, `o`, `b` with `_`) |
| type, integers | `d`, `x`, `X`, `o`, `b`, `c`, `n` (locale) |
| type, floats | `f`, `e`, `g`, `%`, `n` (locale) |
| conversion | `!r` repr, `!s` str, `!a` ascii, applied before the spec |

A spec may contain nested fields, evaluated first: `f"{value:>{width}.{digits}f}"`.

## 2. Choosing a formatting style

Python has four ways to put values into text; each fits one job.

| Style | Use for |
|---|---|
| f-string `f"{x}"` | almost everything written in code |
| t-string `t"{x}"` (3.14) | text where each value must be escaped or kept apart: HTML, SQL, shell, structured logs |
| `"{name}".format(**values)` | a template stored outside the code, filled later; only for trusted templates |
| `"%s" % value` | `logging` calls, which format lazily: `log.info("saved %s", path)` |
| `string.Template("$name")` | templates that non-programmers edit; no attribute access, so no code reaches inside objects |

```python
from string import Template

subject = Template("Hello $name, your order $order_id has shipped")
print(subject.safe_substitute(name="Ada"))       # missing keys stay as $order_id instead of raising
```

Never call `str.format` on a template that a user wrote: `"{obj.__class__.__init__.__globals__}"` reads attributes of the objects you pass in.

## 3. The anatomy of a t-string

A t-string evaluates its expressions immediately, like an f-string, but returns a `string.templatelib.Template` holding the literal parts and an `Interpolation` per expression, so a processor decides how each value becomes text.

```python
from string.templatelib import Interpolation, Template

user, total = "Ada", 12.5
t = t"User {user!r} owes {total:.2f}"
print(t.strings)                    # ('User ', ' owes ', ''): always one more than interpolations
print(t.values)                     # ('Ada', 12.5)
i = t.interpolations[1]
print(i.value, i.expression, i.conversion, i.format_spec)   # 12.5 total None .2f
print([type(part).__name__ for part in t])   # iteration skips empty strings
print(isinstance(t, Template), isinstance(t.interpolations[0], Interpolation))
```

- The conversion and format spec are stored, not applied; a processor applies them, usually with `string.templatelib.convert(value, conversion)` and `format(value, spec)`.
- `Template + Template` concatenates; `Template + str` raises `TypeError`, so a plain string cannot slip in unescaped.
- `Template` has no `__str__` that renders it: `str(t)` gives a repr, which makes forgetting the processor visible.

## 4. A t-string processor for SQL parameters

A SQL processor turns each interpolation into a placeholder and collects the values as parameters, so the database driver handles quoting and injection is impossible.

```python
import sqlite3
from string.templatelib import Interpolation, Template


def sql(t: Template) -> tuple[str, list[object]]:
    query: list[str] = []
    params: list[object] = []
    for part in t:
        if isinstance(part, Interpolation):
            query.append("?")                       # the driver's placeholder style
            params.append(part.value)
        else:
            query.append(part)
    return "".join(query), params


db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE users (name TEXT, role TEXT)")
db.execute("INSERT INTO users VALUES ('ada', 'admin'), ('bob', 'user')")
name = "bob' OR '1'='1"                              # an injection attempt
query, params = sql(t"SELECT role FROM users WHERE name = {name}")
print(query, params)
print(db.execute(query, params).fetchall())         # []: the attempt is just a name
```

Table and column names cannot be parameters; check them against a fixed set before building the query, never interpolate them.

## 5. t-string processors for shell commands and logs

A shell processor quotes each value with `shlex.quote` for a POSIX shell; a log processor renders the message and also returns the values as fields, so logs stay searchable.

```python
import shlex
from string.templatelib import Interpolation, Template, convert


def sh(t: Template) -> str:
    out = []
    for part in t:
        if isinstance(part, Interpolation):
            out.append(shlex.quote(str(convert(part.value, part.conversion))))
        else:
            out.append(part)
    return "".join(out)


def log_record(t: Template) -> tuple[str, dict[str, object]]:
    text = "".join(
        format(convert(p.value, p.conversion), p.format_spec) if isinstance(p, Interpolation) else p
        for p in t
    )
    fields = {p.expression: p.value for p in t.interpolations}
    return text, fields


path = "my file; rm -rf ~"
print(sh(t"ls -l {path}"))                          # ls -l 'my file; rm -rf ~'
order_id, amount = 42, 9.5
print(log_record(t"order {order_id} paid {amount:.2f}"))
```

- `shlex.quote` is for POSIX shells only; on Windows, and everywhere when you can, pass an argument list to `subprocess.run` instead of building a command string (python-subprocess skill).
- `expression` is the source text inside the braces, so `{order_id}` gives the field name `order_id`; complex expressions make poor field names.

## 6. Gotchas

These are the formatting mistakes seen most often.

1. **`ValueError: Invalid format specifier` for a valid-looking spec.** The spec uses a feature newer than the running Python, such as grouping in the fraction (3.14). Check the minimum version.
2. **A t-string prints as `Template(strings=..., interpolations=...)`.** It was passed to `print` or `str` without a processor. Call the processor.
3. **`TypeError: can only concatenate string.templatelib.Template (not "str")`.** A plain string was added to a template. Wrap it in a t-string or pass it as a value.
4. **A log line formats even when the level is off.** An f-string in a `logging` call is built eagerly. Use `%s` arguments.
5. **A user template leaked data.** `str.format` with a user-written template reached object attributes. Use `string.Template`.
