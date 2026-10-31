---
title: Unicode in practice in Python
author: MOZGII, Inc.
lang: en
---

# Unicode in practice in Python

Recipes for the Unicode problems real programs meet: bytes of unknown encoding, file names that are not valid UTF-8, removing accents and making slugs, look-alike characters, the three kinds of "digit", line breaks beyond `\n`, and the width of text in a terminal.

## Contents

This reference has 7 sections.

1. Detecting the encoding of unknown bytes
2. File names and surrogateescape
3. Removing accents and making slugs
4. Look-alike characters and identifiers
5. Digits: isdecimal, isdigit and isnumeric
6. Line breaks beyond \n
7. Text width in a terminal

## 1. Detecting the encoding of unknown bytes

Bytes carry no label, so detection is a guess: try strict UTF-8 first (random non-UTF-8 text almost never decodes as valid UTF-8), check for a byte order mark, and only then fall back to a statistical detector or a known legacy encoding.

```python
import codecs

BOMS = [(codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")]


def decode_unknown(data: bytes, fallback: str = "cp1252") -> tuple[str, str]:
    for bom, name in BOMS:
        if data.startswith(bom):
            return data.decode(name), name
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode(fallback, errors="replace"), fallback


print(decode_unknown("naïve".encode("utf-8")))
print(decode_unknown("naïve".encode("cp1252")))
print(decode_unknown("naïve".encode("utf-16")))
```

For text in other scripts (Cyrillic in cp1251, Japanese in Shift_JIS), a fixed fallback is wrong; `charset-normalizer` (3.5 in October 2026) scores candidate encodings by how plausible the decoded text is.

```python
from charset_normalizer import from_bytes          # uv add charset-normalizer

match = from_bytes("Привет, как дела? Всё хорошо, спасибо.".encode("cp1251")).best()
print(match.encoding if match else None, str(match))   # cp1251 and the decoded text
```

- Detection on short inputs is unreliable; ask the source for its encoding when you can, and store what you decided with the data.
- Never re-encode a file just to "fix" it without keeping the original bytes.

## 2. File names and surrogateescape

File names on Linux are arbitrary bytes, so a name may not be valid UTF-8. Python decodes such names with `surrogateescape`, which maps each bad byte to a lone surrogate so `os.listdir` never fails and the name encodes back to the same bytes.

```python
import os
import sys

raw = b"report-\xff.txt"                            # not valid UTF-8
name = os.fsdecode(raw)                             # what os.listdir would return on Linux
print(repr(name), os.fsencode(name) == raw)         # round-trips to the same bytes
try:
    name.encode("utf-8")                            # printing or sending it as UTF-8 fails
except UnicodeEncodeError as exc:
    print("not encodable:", exc.reason)
print(name.encode("utf-8", errors="backslashreplace").decode())   # safe for logs
print(sys.getfilesystemencoding(), sys.getfilesystemencodeerrors())
```

| OS | File name storage | Invalid names possible |
|---|---|---|
| Linux | bytes, usually UTF-8 by convention | yes |
| macOS (APFS) | UTF-8, normalisation preserved but insensitive | no |
| Windows (NTFS) | UTF-16, decoded by Python with `surrogatepass` | only lone surrogates |

Show such names with `errors="backslashreplace"` or `"replace"`; pass them to `open` and `os` functions unchanged.

## 3. Removing accents and making slugs

Removing accents means decomposing with NFD and dropping combining marks (category `Mn`); letters that are not composed from a base and an accent, such as `ø`, `ł` and `ß`, need an explicit table.

```python
import re
import unicodedata

EXTRA = str.maketrans({"ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "ß": "ss", "æ": "ae", "đ": "d"})


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.translate(EXTRA))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def slugify(text: str, max_len: int = 60) -> str:
    ascii_text = strip_accents(unicodedata.normalize("NFKC", text)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "untitled"


print(strip_accents("Crème brûlée in Łódź, Ørsted"))     # Creme brulee in Lodz, Orsted
print(slugify("Ünïcode & Straße: 2026 Edition!"))        # unicode-strasse-2026-edition
print(slugify("Привет"))                                  # untitled: no Latin letters at all
```

Slugs for non-Latin text need transliteration (a library such as `text-unidecode` or `anyascii`), or keep Unicode in URLs, which browsers support.

## 4. Look-alike characters and identifiers

Different code points can look the same: Latin `a` and Cyrillic `а`, Latin `o` and Greek `ο`, the digit `0` and the letter `O`. NFKC does not merge them, so user names, domain names and package names need a script check.

```python
import unicodedata


def scripts(text: str) -> set[str]:
    """First word of each letter's Unicode name: LATIN, CYRILLIC, GREEK..."""
    return {unicodedata.name(ch).split()[0] for ch in text if ch.isalpha()}


for user in ("paypal", "pаypal"):                         # the second has a Cyrillic 'а'
    print(user, scripts(user), "mixed" if len(scripts(user)) > 1 else "ok")
```

- Reject or flag names that mix scripts; the `regex` module's `\p{Script=Latin}` gives exact script data where the name prefix is too rough.
- Python itself normalises identifiers with NFKC (PEP 3131), so `ﬁle` and `file` are the same variable name.
- Invisible characters (U+200B zero width space, U+00AD soft hyphen, U+FEFF) survive copy and paste; strip category `Cf` from identifiers.

## 5. Digits: isdecimal, isdigit and isnumeric

Python has three digit tests, and `int()` accepts every Unicode decimal digit, so "only digits" needs a decision.

| Text | `isdecimal()` | `isdigit()` | `isnumeric()` | `int(text)` |
|---|---|---|---|---|
| `"42"` | True | True | True | 42 |
| `"٤٢"` (Arabic-Indic) | True | True | True | 42 |
| `"²"` | False | True | True | ValueError |
| `"½"`, `"Ⅻ"` | False | False | True | ValueError |

```python
def parse_ascii_int(text: str) -> int:
    if not (text.isascii() and text.isdecimal()):
        raise ValueError(f"not an ASCII number: {text!r}")
    return int(text)


print(int("٤٢"), parse_ascii_int("42"))
```

Use `isascii() and isdecimal()` for account numbers, codes and ports; `int()` alone for quantities typed by people in any script.

## 6. Line breaks beyond \n

`str.splitlines()` splits on more than `\n` and `\r\n`: also `\r`, `\v`, `\f`, `\x1c` to `\x1e`, `\x85`, U+2028 and U+2029. That is right for text shown to people and wrong for formats defined by `\n`, such as JSON Lines or CSV rows.

```python
data = 'a\u2028b\nc\x0cd'
print(data.splitlines())                                  # ['a', 'b', 'c', 'd']
print(data.split("\n"))                                   # ['a\u2028b', 'c\x0cd']: the format's own rule
```

File objects in text mode split lines only on `\n`, `\r` and `\r\n` (universal newlines), not on U+2028.

## 7. Text width in a terminal

A terminal cell holds one narrow character; East Asian wide characters and most emoji take two cells, and combining marks take none. Pad and align tables by display width, not `len`.

```python
import unicodedata


def cell_width(text: str) -> int:
    width = 0
    for ch in text:
        if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


for word in ("hello", "日本語", "é"):
    print(f"{word}|{' ' * (8 - cell_width(word))}|", cell_width(word))
```

This covers most text; emoji sequences and terminal-specific rules vary, and the `wcwidth` package (0.9 in October 2026) tracks them more closely when exact alignment matters.
