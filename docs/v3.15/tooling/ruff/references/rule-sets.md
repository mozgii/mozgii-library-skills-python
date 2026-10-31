---
title: Ruff rule groups and when to enable them
author: MOZGII, Inc.
lang: en
---

# Ruff rule groups and when to enable them

Ruff organises its 900-plus rules in groups named after the tools they came from. Since Ruff 0.16 about 410 of them run by default; this reference says what each group catches, whether the default set already includes it, and when to add the rest.

## Contents

This reference has 7 sections.

1. Groups in the default set
2. Groups to add for most projects
3. Groups for libraries and public APIs
4. Groups for security-sensitive code
5. Groups for tests, data and notebooks
6. Groups that are noisy or conflict with the formatter
7. Example configurations

## 1. Groups in the default set

The default set (Ruff 0.16+) already includes most bug-finding and modernising rules, so these need no configuration.

| Prefix | Source | Catches | Default |
|---|---|---|---|
| `F` | Pyflakes | undefined names, unused imports and variables | about 40 rules |
| `E7`, `E9`, `W605` | pycodestyle | syntax-level errors, bare `except` (`E722`), invalid escapes | a few; style rules left to the formatter |
| `B` | flake8-bugbear | mutable default arguments, `zip()` without `strict`, loop-variable closures | about 30 |
| `PL` | Pylint | wrong `__init__` returns, `global` misuse, comparison to literals | about 75 |
| `UP` | pyupgrade | old syntax for the target version: `Optional[X]` → `X \| None`, `typing.List` → `list` | about 40 |
| `SIM`, `C4`, `FURB`, `PIE`, `PERF` | simplify, comprehensions, refurb | needless constructs and slow idioms | about 65 |
| `RUF` | Ruff's own | unused `noqa` (`RUF100`), mutable class defaults, ambiguous characters | about 35 |
| `ASYNC` | flake8-async | blocking calls (`time.sleep`, `open`, `requests`) inside `async def` | 10 |
| `PYI` | flake8-pyi | mistakes in `.pyi` stubs | about 45 |
| `LOG`, `G` | logging checks | f-strings in log calls, wrong logger methods | 9 |
| `I001` | isort | unsorted import blocks | 1 |
| `TC` | type-checking imports | imports only used in annotations | 4 |

## 2. Groups to add for most projects

These groups are worth adding almost everywhere; each is cheap to satisfy in new code.

| Prefix | Adds | Why |
|---|---|---|
| `I` | the full isort set (required imports, `from __future__` placement) | one tool for imports |
| `PTH` | `pathlib` instead of `os.path` and `open()` built from strings | cross-platform paths |
| `T20` | `print` and `pprint` left behind | debug output in production |
| `ERA` | commented-out code | dead code in comments |
| `ARG` | unused function arguments | stale parameters after refactors |
| `RET` | inconsistent returns | the full set; only one rule is default |
| `DTZ` | naive `datetime` without a time zone | the rest of the group left the defaults in 0.17; add it when times cross time zones |

## 3. Groups for libraries and public APIs

Libraries have users who read docstrings and rely on names, so these groups pay off there.

| Prefix | Adds | Configure |
|---|---|---|
| `D` | docstring presence and style (pydocstyle) | `[tool.ruff.lint.pydocstyle] convention = "google"` (or `"numpy"`, `"pep257"`); ignore `D100`/`D104` for module and package docstrings if unwanted |
| `ANN` | missing annotations | redundant when pyright or mypy strict runs; useful when no type checker does |
| `N` | PEP 8 naming | `ignore-names` for framework callbacks (`setUp`) |
| `FBT` | boolean positional arguments (`f(True)`) | make them keyword-only: `def f(*, force: bool = False)` |
| `A` | shadowing builtins (`id`, `type`, `list`) | `builtins-ignorelist` for deliberate cases |
| `CPY` | missing copyright header (`CPY001`, stable since Ruff 0.16) | only when the licence requires it |

## 4. Groups for security-sensitive code

Code that handles input, files, subprocesses or secrets should add these.

| Prefix | Catches |
|---|---|
| `S` (flake8-bandit) | `subprocess` with `shell=True`, `pickle` and `yaml.load`, `eval`, hard-coded passwords, weak hashes, `tempfile.mktemp`, `assert` used for checks, SQL built from strings |
| `BLE` | `except Exception` that swallows everything |
| `TRY` | raising vague exceptions, `raise` without `from` inside `except`, long messages in `raise` |
| `EM` | f-strings in exception messages (they repeat in tracebacks) |
| `INP` | missing `__init__.py` that makes a folder an implicit namespace package |

Ruff's `S` rules match Bandit's checks by code (`S602` is Bandit's `B602`); the `python-security` skill covers what to do about each finding.

## 5. Groups for tests, data and notebooks

These groups target specific kinds of code and usually go in `per-file-ignores` or a nested config.

| Prefix | For | Catches |
|---|---|---|
| `PT` | pytest suites | `pytest.raises` without `match`, fixtures with wrong scope syntax, `assert` in `except` |
| `NPY` | numpy | legacy `np.random` functions, deprecated aliases |
| `PD` | pandas | `inplace=True`, `.values`, chained indexing |
| `AIR`, `DJ`, `FAST` | Airflow, Django, FastAPI | framework-specific mistakes |

Notebooks (`.ipynb`) are linted and formatted by default; exclude them with `extend-exclude = ["*.ipynb"]` if they are scratch work.

## 6. Groups that are noisy or conflict with the formatter

Some groups fight the formatter or produce many low-value findings; enable them only on purpose.

| Prefix or rule | Problem |
|---|---|
| `COM812`, `ISC001` | conflict with `ruff format`; Ruff warns when both are on |
| `E501` (line length) | the formatter already wraps code; long strings and URLs remain and are rarely worth splitting |
| `W191`, `E111`, `E114`, `E117`, `D206`, `D300`, `Q000`-`Q003` | formatting rules redundant with the formatter |
| `CPY`, `FIX`, `TD` | policy rules (copyright, TODO format) that need team agreement first; `CPY001` is stable since Ruff 0.16, so `select = ["ALL"]` turns it on with no preview flag |
| `PLR0913`, `PLR0912`, `C901` | complexity thresholds: useful as a signal, noisy as a gate; tune `max-args`, `max-branches`, `max-complexity` |

## 7. Example configurations

Three starting points, each extending the default set.

An application or service:

```toml
[tool.ruff.lint]
extend-select = ["I", "PTH", "T20", "ERA", "ARG", "S", "BLE", "TRY", "DTZ"]
ignore = ["TRY003"]                         # long messages in raise are fine in an app

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "PLR2004", "ARG", "TRY"]
```

A typed library:

```toml
[tool.ruff.lint]
extend-select = ["I", "PTH", "T20", "D", "N", "FBT", "A", "PT"]
ignore = ["D105", "D107"]                   # magic methods and __init__ need no docstring

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["D", "S101", "PLR2004", "FBT"]
```

A legacy codebase being adopted:

```toml
[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "I"]       # the pre-0.16 minimum plus imports; widen group by group
```
