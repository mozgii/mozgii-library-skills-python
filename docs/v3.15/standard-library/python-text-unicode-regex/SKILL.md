---
name: python-text-unicode-regex
description: Handles text in Python correctly - str versus bytes, decoding at the edges, errors= handlers, UTF-8 mode, byte order marks, NFC and NFKC normalisation, casefold comparison, graphemes versus code points with unicodedata.iter_graphemes (3.15), the re module (raw strings, named groups, verbose mode, lookarounds, fullmatch versus match versus search, re.sub with a function), catastrophic backtracking with atomic groups and possessive quantifiers, the regex module, f-strings and t-strings (PEP 750). Use when text looks garbled, a comparison fails on accented or mixed-case text, a length or a cut is wrong for emoji, a regular expression hangs or misses matches, or when building strings safely from user values.
metadata:
  tags:
  - python
  - unicode
  - encoding
  - utf-8
  - regex
  - re
  - normalization
  - t-strings
---

# Text, Unicode and regular expressions in Python

A Python `str` is a sequence of Unicode code points and `bytes` a sequence of raw bytes; text bugs come from mixing them up, decoding with the wrong encoding, and treating one code point as one visible character. This skill gives the rules for text that is compared, measured and matched correctly, and for regular expressions that stay fast. Text files are in the python-files-pathlib skill.

## Step 0: read the versions

Text APIs grew in recent releases, so read the minimum Python first, and the `regex` package if the project uses it.

```bash
uv run python -c "import sys, unicodedata; print(sys.version_info[:2], unicodedata.unidata_version, sys.flags.utf8_mode)"
uv pip list 2>/dev/null | grep -iE "^regex "
```

| Version | Change that matters |
|---|---|
| 3.11 | atomic groups `(?>...)` and possessive quantifiers `*+`, `++`, `?+` in `re` |
| 3.12 | f-strings may reuse the outer quotes and span lines (PEP 701) |
| 3.13 | `re.PatternError` is the name of `re.error` |
| 3.14 | t-strings: `t"..."` builds a `string.templatelib.Template` (PEP 750) |
| 3.15 | UTF-8 mode on by default (PEP 686); `unicodedata.iter_graphemes`; Unicode 17; `re.prefixmatch` as the new name of `re.match` |

The third-party `regex` module is at 2026.9.29 (October 2026).

## str and bytes: decode at the edges

Text enters a program as bytes (files, sockets, pipes) and must be decoded once, at the edge, with a known encoding; inside, everything is `str`; on the way out it is encoded once.

```python
raw = "Grüße, 世界".encode("utf-8")          # str -> bytes
print(raw, len(raw))                         # 15 bytes for 9 code points
text = raw.decode("utf-8")                   # bytes -> str
print(text, len(text))
print(raw.decode("latin-1"))                 # wrong encoding: mojibake, no error raised
```

- Decoding with the wrong single-byte encoding gives garbage like `GrÃ¼ÃŸe` with no error. `latin-1` maps all 256 bytes, so it never fails; `cp1252` leaves five bytes undefined (0x81, 0x8D, 0x8F, 0x90, 0x9D) and raises `UnicodeDecodeError` on them.
- Never call `str(b"...")`: it gives the text `"b'...'"`, not a decoded string.

## Encoding errors and the errors= argument

The `errors=` argument decides what happens to bytes that are not valid in the encoding; the default `"strict"` raises `UnicodeDecodeError`.

| `errors=` | On decode | Use for |
|---|---|---|
| `"strict"` | raises | data that must be valid |
| `"replace"` | each bad byte becomes U+FFFD | showing text to a person |
| `"backslashreplace"` | `\xff` escapes | logs, where the bad bytes stay visible |
| `"surrogateescape"` | bad bytes become lone surrogates, and encode back to the same bytes | file names and data you must round-trip unchanged |
| `"ignore"` | drops bad bytes | rarely right: it hides data loss |

```python
data = b"caf\xe9 ok"                          # latin-1 bytes in a UTF-8 stream
print(data.decode("utf-8", errors="replace"))           # 'caf� ok'
s = data.decode("utf-8", errors="surrogateescape")
print(s.encode("utf-8", errors="surrogateescape") == data)   # True: lossless round trip
```

## UTF-8 mode and the default encoding

UTF-8 mode makes `open()`, `subprocess` text pipes and other locale-dependent defaults use UTF-8. It is on by default from Python 3.15 on every OS; on 3.11 to 3.14 the default was the locale encoding, often cp1252 on Windows.

- Turn it on for older Pythons with `PYTHONUTF8=1` or `python -X utf8`; turn it off on 3.15 with `PYTHONUTF8=0`.
- `python -X warn_default_encoding` emits `EncodingWarning` wherever code opens text without `encoding=`; run tests with it to find them.

## Byte order marks

A byte order mark (BOM) is U+FEFF at the start of a file; Excel and Windows Notepad add one to UTF-8 files, and UTF-16 files need one.

```python
raw = "\ufeffname,city\n".encode("utf-8")    # what Excel's "CSV UTF-8" writes
print(repr(raw.decode("utf-8")[:5]))          # '\ufeffname': the BOM breaks the first header
print(repr(raw.decode("utf-8-sig")[:4]))      # 'name': utf-8-sig strips it if present
```

Read unknown UTF-8 input with `"utf-8-sig"` (it works with or without a BOM); write with `"utf-8-sig"` only for files Excel on Windows must open.

## Unicode normalisation: NFC and NFKC

The same visible text can be stored as different code points: `é` is either one code point (U+00E9) or `e` plus a combining accent (U+0301). Normalise with `unicodedata.normalize` before comparing, hashing or using text as a key.

```python
import unicodedata

a, b = "café", "café"
print(a == b, len(a), len(b))                                      # False 4 5
print(unicodedata.normalize("NFC", a) == unicodedata.normalize("NFC", b))   # True
print(unicodedata.normalize("NFKC", "ﬁle ①Ｈ"))                     # 'file 1H'
```

| Form | Does | Use for |
|---|---|---|
| NFC | composes characters | storage, comparison, the default for text from people |
| NFD | decomposes into base plus accents | stripping accents (drop combining marks after NFD) |
| NFKC | NFC plus compatibility folding (ligatures, full-width, circled digits) | identifiers, search keys, user names |

- macOS keyboard input and most web text are NFC; file names from older macOS volumes (HFS+) come back in NFD, so normalise names you compare.
- NFKC changes meaning in places (`²` becomes `2`); never apply it to text you store for display.

## Case-insensitive comparison with casefold

`str.casefold()` is the Unicode way to compare text without case; `lower()` misses letters such as German `ß`, which casefolds to `ss`.

```python
import unicodedata


def caseless(s: str) -> str:
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", s).casefold())


def same_text(a: str, b: str) -> bool:
    return caseless(a) == caseless(b)


print("Straße".lower() == "STRASSE".lower(), same_text("Straße", "STRASSE"))   # False True
```

Casefolding has no locale: Turkish dotted and dotless `i` need locale-aware rules (ICU) if the product targets Turkish. Sorting for people is also locale-dependent; `sorted(names, key=str.casefold)` is a reasonable default, not a correct collation.

## Length: code points versus graphemes

`len(s)` counts code points, but a visible character (a grapheme cluster) can be several: an emoji with a skin tone, a flag, a letter with combining marks. Python 3.15 adds `unicodedata.iter_graphemes`, which splits text by the Unicode rules.

```python
import unicodedata

s = "é👍🏽🇫🇷"
print(len(s))                                            # 6 code points
print([s[g.start:g.end] for g in unicodedata.iter_graphemes(s)])   # 3 graphemes


def truncate(text: str, limit: int) -> str:
    """Cut to at most `limit` visible characters without splitting one."""
    out = []
    for i, g in enumerate(unicodedata.iter_graphemes(text)):
        if i == limit:
            break
        out.append(text[g.start:g.end])
    return "".join(out)


print(truncate("👍🏽👍🏽👍🏽", 2))
```

- Each item is a `unicodedata.Segment` with `start` and `end`; `str(segment)` gives its text.
- Before 3.15, the `regex` module's `\X` matches one grapheme: `regex.findall(r"\X", s)`.
- Width on a terminal is a third measure: East Asian wide characters take two columns (`unicodedata.east_asian_width(ch) in "WF"`).

## Regular expressions with re

Patterns are written as raw strings (`r"..."`) so backslashes reach the regex engine unchanged; compile once at module level when a pattern is used in a loop or a hot path.

```python
import re

LOG_LINE = re.compile(
    r"""
    (?P<date>\d{4}-\d{2}-\d{2})     # ISO date
    \s+
    (?P<level>INFO|WARN|ERROR)
    \s+
    (?P<msg>.*)                     # rest of the line
    """,
    re.VERBOSE,
)

m = LOG_LINE.fullmatch("2026-10-28  ERROR  disk full")
if m:
    print(m["level"], m.group("msg"), m.groupdict()["date"])
```

| Function | Matches |
|---|---|
| `fullmatch` | the whole string; the right choice for validation |
| `match` (`prefixmatch` on 3.15) | at the start only; `"abc!"` passes `^\w+` |
| `search` | anywhere; the first match |
| `finditer` | every match, as match objects with positions |
| `findall` | every match, as strings, or tuples when there are groups |

- `\d` and `\w` match any Unicode digit or letter in `str` patterns (`"٣"` is a digit); add `re.ASCII` or write `[0-9]` for ASCII only.
- `$` also matches before a final newline; `\Z` (3.14 accepts `\z` too) means the true end.
- `re.escape(user_text)` makes literal text safe to put inside a pattern.

## Lookarounds

A lookahead or lookbehind checks what surrounds a position without consuming it; `re` requires a lookbehind of fixed width.

```python
import re

print(re.findall(r"\d+(?= ?€)", "12 € or 30€ or 7 $"))        # ['12', '30']: numbers before €
print(re.findall(r"(?<!-)\b\d+\b", "5 and -3 and 8"))         # ['5', '8']: not preceded by '-'
print(bool(re.fullmatch(r"(?=.*\d)(?=.*[a-z]).{8,}", "secret42")))   # several conditions at once
```

## re.sub with a function

`re.sub` accepts a function instead of a replacement string; it receives each match and returns the replacement, which keeps escaping and logic in Python.

```python
import re

PRICES = {"apple": 0.5, "pear": 0.75}


def price(m: re.Match[str]) -> str:
    item = m["item"]
    return f"{item} (${PRICES[item]:.2f})" if item in PRICES else item


print(re.sub(r"\b(?P<item>[a-z]+)\b", price, "apple and pear and kiwi"))
print(re.sub(r"(?P<y>\d{4})-(?P<m>\d\d)", r"\g<m>/\g<y>", "2026-10"))   # named backreferences
```

## Catastrophic backtracking

Nested or overlapping quantifiers such as `(a+)+`, `(\w+\s?)*` or `(a|aa)+` make the engine try exponentially many ways to split a string that almost matches; one 40-character input can then run for minutes. This is a denial-of-service hole whenever the input comes from outside.

```python
import re
import time

text = "a" * 22 + "!"
for pattern in (r"(a+)+$", r"(?>a+)+$", r"(a++)+$"):    # nested, atomic group, possessive
    start = time.perf_counter()
    re.match(pattern, text)
    print(f"{pattern:10} {time.perf_counter() - start:.4f}s")
```

- An atomic group `(?>...)` and a possessive quantifier (`*+`, `++`, `?+`, 3.11+) never give back what they matched, so the engine cannot retry splits.
- Rewrite overlaps away: `(a|aa)+` becomes `a+`; `(\w+\s?)*` becomes `\w+(?:\s+\w+)*`.
- Cap input length before matching, and for user-supplied patterns use the `regex` module with `timeout=`; `re` has no timeout.

## The regex module

The third-party `regex` module (`uv add regex`) is a superset of `re` for what `re` lacks: grapheme matching, Unicode properties and scripts, full case folding, variable-length lookbehind, fuzzy matching and a timeout.

```python
import regex

print(regex.findall(r"\X", "é👍🏽"))                         # graphemes: ['é', '👍🏽']
print(regex.findall(r"\p{Script=Cyrillic}+", "abc привет"))        # ['привет']
print(bool(regex.fullmatch(r"(?fi)strasse", "STRAßE")))            # full case folding: True
print(regex.findall(r"(?<=\$\s*)\d+", "cost $  42 and $7"))         # variable-length lookbehind
print(regex.search(r"(?:hello){e<=1}", "helo world"))               # one edit allowed
try:
    regex.match(r"^(a|aa)+$", "a" * 40 + "!", timeout=0.5)
except TimeoutError:
    print("pattern took too long")
```

Use `re` by default; reach for `regex` when one of those features is needed or when patterns come from users.

## f-strings and t-strings

f-strings format values immediately into a `str`; t-strings (Python 3.14, PEP 750) keep the literal parts and the values apart in a `Template`, so a function can escape each value before joining. Use a t-string wherever an f-string would put untrusted values into HTML, SQL or a shell command.

```python
from html import escape
from string.templatelib import Interpolation, Template, convert


def html(t: Template) -> str:
    parts = []
    for item in t:                                       # literal strings and Interpolations, in order
        if isinstance(item, Interpolation):
            value = format(convert(item.value, item.conversion), item.format_spec)
            parts.append(escape(value))
        else:
            parts.append(item)
    return "".join(parts)


name = "<script>alert(1)</script>"
print(f"<p>Hello {name}</p>")                            # injected as is
print(html(t"<p>Hello {name}</p>"))                      # escaped: &lt;script&gt;...
```

- A t-string is not a `str`: passing it where a string is expected fails loudly, which is the point.
- f-string debugging: `f"{value=}"` prints `value=...`; `f"{total:,.2f}"` and `f"{when:%Y-%m-%d}"` use the format mini-language.
- More on the format mini-language and t-string processors is in [formatting-and-t-strings.md](references/formatting-and-t-strings.md).

## Verify

These asserts cover the failures this skill exists to prevent, and they run with the standard library on Python 3.11 or later.

```python
import re
import time
import unicodedata

assert b"\xef\xbb\xbfname".decode("utf-8-sig") == "name"                  # a BOM is stripped
assert unicodedata.normalize("NFC", "cafe\u0301") == "caf\u00e9"           # two spellings, one key
assert "Stra\u00dfe".casefold() == "STRASSE".casefold()
assert re.fullmatch(r"[0-9]+", "\u0663") is None and re.fullmatch(r"\d+", "\u0663")   # \d takes Arabic-Indic digits
assert re.match(r"^\w+", "abc!") and not re.fullmatch(r"\w+", "abc!")      # validation needs fullmatch

start = time.perf_counter()
re.match(r"(a++)+$", "a" * 5_000 + "!")                                   # possessive: no backtracking
assert time.perf_counter() - start < 0.5
print("text checks passed")
```

```bash
uv run python -X warn_default_encoding -W error::EncodingWarning -m pytest -q   # every open() names its encoding
grep -rnE '\([^()]*[+*][^()]*\)[+*]' src                                        # read each hit: nested quantifiers
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `open(path)` with no encoding | `encoding="utf-8"`, stated every time |
| `errors="ignore"` to silence a decode error | find the real encoding, or `"replace"` for display |
| `s.lower() == t.lower()` | NFC plus `casefold()` |
| `text[:limit]` for visible text | cut by graphemes |
| `re.match` for validation | `re.fullmatch` |
| an f-string building HTML, SQL or a shell command | a t-string processor, or parameters |

## Checklist

Text code is correct when every item holds.

- [ ] Bytes are decoded once at the edge with a stated encoding and `errors=` chosen on purpose.
- [ ] Text used as a key or compared is NFC-normalised and casefolded; identifiers use NFKC.
- [ ] Lengths and cuts shown to people use graphemes, not `len`.
- [ ] Patterns are raw strings; validation uses `fullmatch`; `\d` and `\w` are ASCII where only ASCII is valid.
- [ ] No nested quantifiers on untrusted input; input length capped; `regex` with `timeout=` for user patterns.
- [ ] Untrusted values reach HTML, SQL or commands through a t-string processor or parameters, never an f-string.

## Gotchas

These are the text bugs seen most often.

1. **`GrÃ¼ÃŸe` on screen.** UTF-8 bytes decoded as cp1252 or latin-1. Decode with `"utf-8"` at the source.
2. **`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0`.** The data is UTF-16 (with a BOM) or binary. Check the first bytes; decode `b"\xff\xfe"` data as `"utf-16"`.
3. **Two identical-looking names are not equal.** One is NFC and the other NFD (macOS file names, copied text). Normalise both.
4. **An emoji is cut in half, or a flag shows as two letters.** The text was sliced by code points. Cut by graphemes.
5. **A regular expression hangs the process.** Nested quantifiers backtrack exponentially. Use atomic groups or possessive quantifiers, rewrite the pattern, or cap input.
6. **`re.match` accepted invalid input.** It anchors only at the start. Use `fullmatch` for validation.
7. **`\d+` accepted Arabic-Indic digits that `int()` then parsed.** Unicode digits match `\d`. Use `[0-9]` or `re.ASCII` where only ASCII is valid.

## References

The reference files hold deeper material.

- [unicode-in-practice.md](references/unicode-in-practice.md): detecting encodings, file names and `surrogateescape`, removing accents, slugs, confusable characters, digits and numeric checks, terminal width
- [regex-cookbook.md](references/regex-cookbook.md): tested patterns for common jobs, tokenising with named alternatives, flags, splitting, performance and a backtracking repair table
- [formatting-and-t-strings.md](references/formatting-and-t-strings.md): the format mini-language, `format` versus `%` versus `Template`, and t-string processors for SQL, shell and logs

## See also

- `python-files-pathlib`: reading and writing text files
- `python-serialization`: CSV, JSON and Excel encodings
- `python-security`: input limits and regular expression denial of service
