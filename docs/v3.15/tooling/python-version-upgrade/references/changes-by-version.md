---
title: Python changes that break code, 3.12 to 3.16
author: MOZGII, Inc.
lang: en
---

# Python changes that break code, 3.12 to 3.16

The removals and changed defaults in each Python release that real projects run into, each with its replacement. It is a working list, not the full What's New: rare C API and internal changes are left out. Search the code for each removed name before upgrading.

## Contents

This reference has 6 sections.

1. Python 3.12
2. Python 3.13
3. Python 3.14
4. Python 3.15
5. Pending removal in 3.16 and 3.17
6. Finding removed names in a codebase

## 1. Python 3.12

Python 3.12 (October 2023) removed `distutils` and several long-deprecated modules, and made invalid escape sequences louder.

| Removed or changed | Replacement |
|---|---|
| `distutils` (PEP 632) | `setuptools` for building, `packaging.version.Version` for versions, `sysconfig` for paths, `shutil.which` for `find_executable` |
| `imp` | `importlib` (`importlib.util.spec_from_file_location`, `importlib.reload`) |
| `asyncore`, `asynchat` | `asyncio` |
| `smtpd` | the `aiosmtpd` package |
| `unittest` aliases `assertEquals`, `failUnless`, `assertRegexpMatches` and others | `assertEqual`, `assertTrue`, `assertRegex` |
| `configparser.SafeConfigParser`, `readfp` | `ConfigParser`, `read_file` |
| `ssl.wrap_socket()`, `ssl.match_hostname()` | `SSLContext.wrap_socket()`; hostname checks are built in |
| `locale.format()` | `locale.format_string()` |
| `setuptools` no longer preinstalled in venvs made by `venv` | declare it as a build or runtime dependency where needed |
| invalid escapes such as `"\d"` in normal strings: `SyntaxWarning` (was silent) | raw strings `r"\d"` |
| `datetime.utcnow()`, `utcfromtimestamp()` deprecated | `datetime.now(UTC)`, `fromtimestamp(ts, UTC)` |

## 2. Python 3.13

Python 3.13 (October 2024) removed the "dead batteries" listed in PEP 594 and a few more old tools.

| Removed | Replacement |
|---|---|
| `cgi`, `cgitb` | `email.message.EmailMessage` for headers, `urllib.parse.parse_qs` for queries, a web framework for forms; `legacy-cgi` on PyPI as a stopgap |
| `crypt` | `hashlib.scrypt` or the `argon2-cffi` / `bcrypt` packages for passwords |
| `imghdr`, `sndhdr` | `filetype` or `puremagic` packages; Pillow's `Image.open(...).format` |
| `telnetlib` | `telnetlib3` package, or `asyncio` streams |
| `pipes` | `shlex.quote` |
| `nntplib`, `nis`, `spwd`, `ossaudiodev`, `msilib` | no stdlib replacement; PyPI packages or OS tools |
| `aifc`, `audioop`, `chunk`, `sunau` | `soundfile`, `pydub` or `audioop-lts` packages |
| `mailcap` | `mimetypes` |
| `uu`, `xdrlib` | `base64`; the `xdrlib3` package |
| `lib2to3` and the `2to3` tool | `ast`, LibCST or Ruff for code changes |
| `tkinter.tix` | `tkinter.ttk` |
| `typing.io`, `typing.re` namespaces | `typing.IO`, `typing.Pattern` (or `re.Pattern`) |
| `locale.resetlocale()` | `locale.setlocale(locale.LC_ALL, "")` |

Behaviour changes worth testing: `locals()` in functions returns an independent snapshot each call (PEP 667), and docstrings have their common leading indentation stripped at compile time.

## 3. Python 3.14

Python 3.14 (October 2025) changed several defaults, most of them in asyncio, multiprocessing and annotations.

| Removed or changed | Replacement or fix |
|---|---|
| `asyncio.get_event_loop()` raises `RuntimeError` when no loop runs | `asyncio.run()`, `asyncio.Runner`, `asyncio.get_running_loop()` inside coroutines |
| asyncio child watchers (`get_child_watcher`, `SafeChildWatcher` and others) removed | nothing needed: subprocess support is built in |
| `multiprocessing` and `ProcessPoolExecutor` default to `forkserver` on Linux and other non-macOS POSIX | top-level, picklable worker functions; `get_context("fork")` only in programs without threads |
| annotations evaluated lazily (PEP 649, 749) | `annotationlib.get_annotations()` or `typing.get_type_hints()` instead of reading `__annotations__` before names exist |
| `ast.Num`, `ast.Str`, `ast.Bytes`, `ast.NameConstant`, `ast.Ellipsis` and `Constant.n` / `.s` removed | `ast.Constant` and `.value`; `visit_Constant` in visitors |
| `urllib.request.URLopener`, `FancyURLopener` removed | `urlopen()`, `urlretrieve()`, `build_opener()` |
| `pkgutil.find_loader()`, `get_loader()` removed | `importlib.util.find_spec()` |
| `sqlite3.version`, `version_info` removed; named placeholders with a sequence of parameters raise | `sqlite3.sqlite_version`; pass a dict for named placeholders |
| `itertools` iterators no longer copy, deepcopy or pickle | materialise to a list first |
| `pathlib.Path(...)` with extra keyword arguments raises | plain arguments |
| `int(x)` no longer calls `__trunc__` | implement `__int__` or `__index__` |
| `NotImplemented` in a boolean context raises `TypeError` | return or compare it, never `if NotImplemented` |
| `return`, `break` or `continue` leaving a `finally` block: `SyntaxWarning` (PEP 765) | move it out of `finally` |
| `functools.partial` objects are method descriptors | wrap in `staticmethod()` when stored as a class attribute that must not bind |
| `tarfile.extractall()` uses the `"data"` filter by default | pass `filter="tar"` or `"fully_trusted"` only for archives you trust and that need it |
| asyncio policy API (`set_event_loop_policy` and others) deprecated | `asyncio.run(main(), loop_factory=...)` |

New syntax that only runs on 3.14 and later, so it must wait until 3.14 is the minimum: template strings `t"..."` (PEP 750) and `except A, B:` without parentheses (PEP 758). The incremental garbage collector of 3.14.0 to 3.14.4 was reverted to the 3.13 collector in 3.14.5; memory-sensitive services should run 3.14.5 or later.

## 4. Python 3.15

Python 3.15 (October 2026) made UTF-8 the default encoding and finished several removals announced in 3.12 and 3.13.

| Removed or changed | Replacement or fix |
|---|---|
| UTF-8 is the default for `open()`, `Path.read_text()`, `subprocess` text mode and other I/O without `encoding=` (PEP 686) | pass `encoding=` explicitly; `encoding="locale"` keeps the platform's encoding; `PYTHONUTF8=0` restores the old default |
| `datetime.strptime()` with `%d` but no year directive raises `ValueError` | include the year in the format |
| `sqlite3.connect()` arguments after `database` are keyword-only | `sqlite3.connect(path, timeout=5.0)` |
| `argparse.add_argument("-f", "-foo")` takes `dest` from the single-dash long option (`foo`, was `f`) | pass `dest=` explicitly |
| `base64.urlsafe_b64decode()` no longer requires padding | pass `padded=True` where padding must be checked |
| `sre_compile`, `sre_constants`, `sre_parse` removed | `re` |
| `http.server.CGIHTTPRequestHandler` and `--cgi` removed | a WSGI or ASGI server |
| `importlib` loaders' `load_module()` removed | `exec_module()` |
| `importlib.resources.files(package=...)` keyword removed | positional `files("my_pkg")` |
| `NamedTuple("P", x=int)` keyword syntax and `TypedDict("TD")` with no fields removed | class syntax, or `TypedDict("TD", {})` |
| `pathlib.PurePath.is_reserved()` removed | `os.path.isreserved()` |
| `platform.java_ver()`, `glob.glob0()`, `glob.glob1()` removed | `glob.glob(pattern, root_dir=...)` |
| module attribute `__cached__` no longer set | `__spec__.cached` |
| `unittest` `assertWarns` no longer swallows non-matching warnings | add filters or more `assertWarns` blocks |
| `frozendict` builtin added; it is not a `dict` subclass | check `isinstance(x, Mapping)` rather than `dict` where both may arrive |

New syntax for 3.15 and later only: `[*row for row in rows]`, a comprehension that unpacks (PEP 798), and `lazy import x` (PEP 810). `re.match()` is soft deprecated in favour of `re.prefixmatch()`; it keeps working.

## 5. Pending removal in 3.16 and 3.17

These still work on 3.15 but warn, and the next releases remove them; fixing them now makes the next upgrade a no-op.

| Removed in | Name | Replacement |
|---|---|---|
| 3.16 | asyncio policy system: `get_event_loop_policy`, `set_event_loop_policy`, `WindowsSelectorEventLoopPolicy` and the others | `asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)` |
| 3.16 | `asyncio.iscoroutinefunction()` | `inspect.iscoroutinefunction()` |
| 3.16 | `~True`, `~False` | `not x` |
| 3.16 | custom logging handlers taking `strm=` | `stream=` |
| 3.16 | `array` format code `'u'` | `'w'` |
| 3.16 | `mimetypes.add_type()` with an extension without a dot | `".ext"` |
| 3.17 | `collections.abc.ByteString`, `typing.ByteString` | `collections.abc.Buffer`, or `bytes \| bytearray \| memoryview` |
| 3.17 | `profile` module | `cProfile` or `profiling.tracing` |
| 3.17 | `strptime()` with `%e` but no year | include the year |

## 6. Finding removed names in a codebase

A quick search over the code and the lockfile finds most upgrade work before the first test run.

```bash
# imports of modules removed in 3.12 to 3.15
grep -rnE "^\s*(import|from) (distutils|imp|asyncore|asynchat|smtpd|cgi|cgitb|crypt|imghdr|sndhdr|telnetlib|pipes|nntplib|audioop|aifc|chunk|sunau|uu|xdrlib|mailcap|lib2to3|sre_compile|sre_parse|sre_constants)\b" --include="*.py" .

# calls whose default changed
grep -rnE "get_event_loop\(\)|set_event_loop_policy|utcnow\(\)|utcfromtimestamp|ast\.(Num|Str|Bytes|NameConstant)\b" --include="*.py" .

# text I/O without an explicit encoding (Ruff can do this: PLW1514 unspecified-encoding)
uv run ruff check --select PLW1514 --preview .
```

`vermin` (`uvx vermin --no-tips -t=3.11- src`) reports the minimum Python each file needs, which helps check that new syntax has not crept in before the old version is dropped.
