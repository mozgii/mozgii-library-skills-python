---
title: Python regular expression cookbook
author: MOZGII, Inc.
lang: en
---

# Python regular expression cookbook

Tested `re` patterns for everyday jobs, a tokenizer built from named alternatives, the flags and their inline forms, splitting that keeps separators, when a string method beats a regex, and a table for repairing patterns that backtrack.

## Contents

This reference has 7 sections.

1. Patterns for common jobs
2. A tokenizer with named alternatives
3. Flags and inline flags
4. Splitting with re.split
5. When string methods beat a regex
6. Repairing patterns that backtrack
7. Testing patterns

## 1. Patterns for common jobs

These patterns are written for `re` with `str` input and checked with `fullmatch` or `sub` as shown.

```python
import re

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")                        # terminal colour codes
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?", re.ASCII)
ISO_DATE = re.compile(r"(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", re.ASCII)
QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')                               # "..." with \" escapes
KEY_VALUE = re.compile(r"(\w+)=(\"[^\"]*\"|\S+)")
CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

print(ANSI.sub("", "\x1b[1;31mError\x1b[0m: disk full"))                  # Error: disk full
print(SEMVER.fullmatch("1.4.0-rc.1+build.7").groups())
print(bool(ISO_DATE.fullmatch("2026-13-01")))                             # False: month 13
print(QUOTED.findall(r'say "hi \"there\"" and "bye"'))
print(dict(KEY_VALUE.findall('level=warn msg="disk full" code=7')))
print(CAMEL.sub("_", "parseHTTPResponseCode").lower())                   # parse_http_response_code
print(" ".join("  too   many\tspaces \n".split()))                        # whitespace: no regex needed
```

- An ISO date pattern accepts `2026-02-31`; check real dates with `datetime.date.fromisoformat` after the pattern.
- Do not validate email addresses with a large regex: check for one `@` and a dot in the domain, then send a confirmation message.
- `QUOTED` returns the escaped content; unescape it with the format's own rules (`json.loads` for JSON strings).

## 2. A tokenizer with named alternatives

A tokenizer combines one named group per token kind into a single alternation; `m.lastgroup` names the kind that matched, and a final catch-all group reports errors with their position.

```python
import re
from collections.abc import Iterator
from typing import NamedTuple


class Token(NamedTuple):
    kind: str
    text: str
    pos: int


SPEC = [
    ("NUMBER", r"\d+(?:\.\d+)?"),
    ("NAME", r"[A-Za-z_]\w*"),
    ("OP", r"[+\-*/()=]"),
    ("SKIP", r"\s+"),
    ("ERROR", r"."),
]
TOKEN = re.compile("|".join(f"(?P<{name}>{pattern})" for name, pattern in SPEC))


def tokenize(text: str) -> Iterator[Token]:
    for m in TOKEN.finditer(text):
        kind = m.lastgroup or "ERROR"
        if kind == "SKIP":
            continue
        if kind == "ERROR":
            raise SyntaxError(f"unexpected {m.group()!r} at {m.start()}")
        yield Token(kind, m.group(), m.start())


print([t[:2] for t in tokenize("total = price * 1.2")])
```

Order matters in an alternation: the first alternative that matches wins, so put longer operators (`**`, `==`) before shorter ones (`*`, `=`).

## 3. Flags and inline flags

Flags change how a whole pattern matches; each has an inline form, and the scoped form `(?i:...)` limits a flag to part of the pattern.

| Flag | Inline | Effect |
|---|---|---|
| `re.IGNORECASE` / `re.I` | `(?i)` | case-insensitive, simple Unicode case folding |
| `re.MULTILINE` / `re.M` | `(?m)` | `^` and `$` match at every line |
| `re.DOTALL` / `re.S` | `(?s)` | `.` matches newline too |
| `re.VERBOSE` / `re.X` | `(?x)` | whitespace ignored, `#` comments allowed |
| `re.ASCII` / `re.A` | `(?a)` | `\d`, `\w`, `\s`, `\b` are ASCII only |
| `re.NOFLAG` (3.11) | | `0`, for code that builds flags conditionally |

```python
import re

text = "Name: Ada\nname: Linus\nNAME: Grace"
print(re.findall(r"(?m)^name: (\w+)$", text))                 # ['Linus']: case-sensitive
print(re.findall(r"(?mi)^name: (\w+)$", text))                # all three
print(re.findall(r"(?m)^(?i:name): ([A-Z]\w+)$", text))       # flag scoped to the label only
```

- Global inline flags must be at the start of the pattern; elsewhere they are an error since 3.11.
- In verbose mode, a literal space or `#` must be escaped (`\ `, `\#`) or put in a class (`[ ]`).

## 4. Splitting with re.split

`re.split` splits on a pattern; capturing groups in the pattern keep the separators in the result, which is how to split and rebuild text.

```python
import re

print(re.split(r"[,;]\s*", "a, b;c ,d"))                       # ['a', 'b', 'c ', 'd']
print(re.split(r"([,;])\s*", "a, b;c"))                         # ['a', ',', 'b', ';', 'c']
print(re.split(r"\s+", "  leading space"))                      # ['', 'leading', 'space']
print(re.split(r",", "a,b,c,d", maxsplit=2))                   # keyword: positional is deprecated
```

Since 3.13, passing `maxsplit`, `count` or `flags` positionally to `re.split` and `re.sub` raises a `DeprecationWarning`; always use keywords.

## 5. When string methods beat a regex

A string method is faster, clearer and cannot backtrack; use a regex only when the rule needs one.

| Job | Without a regex |
|---|---|
| starts or ends with one of several strings | `s.startswith(("http://", "https://"))` |
| contains a substring | `"needle" in s` |
| collapse whitespace | `" ".join(s.split())` |
| split once on the first separator | `key, _, value = s.partition("=")` |
| remove a prefix | `s.removeprefix("v")` |
| map or delete characters | `s.translate(str.maketrans("", "", "-_ "))` |
| all characters from a set | `set(s) <= allowed` or `s.isalnum()` |

When a regex is right, anchor it (`^`, `\A`, `fullmatch`) so failure is found quickly, and put a literal prefix first so the engine can skip ahead.

## 6. Repairing patterns that backtrack

Exponential backtracking comes from a quantified group whose inside can match the same text in more than one way. Each repair below removes the ambiguity or forbids giving characters back.

| Slow pattern | Why | Repair |
|---|---|---|
| `(a+)+` | the inner and outer `+` split runs of `a` many ways | `a+` |
| `(a\|aa)+` | two alternatives cover the same text | `a+` |
| `(\w+\s?)+$` | `\s?` optional, so words split anywhere | `\w+(?:\s+\w+)*$` |
| `(.*,)*x` | `.*` can also match commas | `(?:[^,]*,)*x` |
| `"(.\|\n)*"` | `.` and `\n` overlap with the quote | `"[^"]*"` |
| any of these on 3.11+ | | wrap the group atomic `(?>...)` or make the quantifier possessive `++`, `*+` |

```python
import re
import time

text = "x" * 22 + "!"                                       # almost matches, then fails
for pattern in (r"(\w+\s?)+$", r"\w+(?:\s+\w+)*\s*$", r"(?>\w+\s?)+$"):
    start = time.perf_counter()
    re.match(pattern, text)
    print(f"{pattern:22} {time.perf_counter() - start:.4f}s")
```

Measure with input that almost matches and ends in a character that makes it fail; matching input finishes fast even with a bad pattern.

## 7. Testing patterns

Patterns are code: test the matches you want and the near-misses you must reject, and test speed with a long failing input.

```python
import re
import time

import pytest

USERNAME = re.compile(r"[a-z][a-z0-9_]{2,31}", re.ASCII)


@pytest.mark.parametrize("name", ["ada", "linus_t", "a12"])
def test_accepts(name: str) -> None:
    assert USERNAME.fullmatch(name)


@pytest.mark.parametrize("name", ["", "1ada", "Ada", "ad", "ada!", "аda"])   # last one: Cyrillic а
def test_rejects(name: str) -> None:
    assert not USERNAME.fullmatch(name)


def test_fails_fast_on_long_input() -> None:
    start = time.perf_counter()
    USERNAME.fullmatch("a" * 10_000 + "!")
    assert time.perf_counter() - start < 0.05


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
```
