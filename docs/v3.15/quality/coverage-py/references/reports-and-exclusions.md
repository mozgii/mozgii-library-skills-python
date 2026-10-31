---
title: Coverage reports, contexts and exclusion rules
author: MOZGII, Inc.
lang: en
---

# Coverage reports, contexts and exclusion rules

The detail behind coverage.py's output: every report format and the options that change it, the JSON report's structure for scripts, contexts that record which test ran each line, and the exact rules for excluding lines and branches, including exclusions that depend on the platform or Python version.

## Contents

This reference has 9 sections.

1. Report formats and when to use each
2. Report options in [tool.coverage.report]
3. The JSON report's structure
4. Contexts: which test ran this line
5. How exclusion regexes match
6. Partial branches and pragma: no branch
7. Platform- and version-specific exclusions
8. Excluding whole files
9. Gotchas

## 1. Report formats and when to use each

coverage.py writes the same data in several formats; every reporting command reads the same `.coverage` file and combines parallel files first.

| Command | Output | Use for |
|---|---|---|
| `coverage report` | text table on stdout | the terminal and the CI gate |
| `coverage report --format=markdown` | Markdown table | `$GITHUB_STEP_SUMMARY`, pull-request comments |
| `coverage report --format=total` | one number | badges, scripts |
| `coverage html` | `htmlcov/` site | reading missed lines and branches with colour |
| `coverage xml` | Cobertura `coverage.xml` | diff-cover, Codecov, GitLab and Azure coverage views |
| `coverage json` | `coverage.json` | your own scripts and checks |
| `coverage lcov` | `coverage.lcov` | editor gutters and lcov tools |
| `coverage annotate` | a `,cover` copy of each file | rarely; marks each line as run or missed |

pytest-cov produces the same formats with `--cov-report=TYPE[:DEST]`, repeatable: `--cov-report=term-missing:skip-covered --cov-report=xml:build/coverage.xml`. `--cov-report=` (empty) prints nothing, useful when another step reports.

## 2. Report options in [tool.coverage.report]

These settings apply to every report unless the command line overrides them.

| Option | Effect |
|---|---|
| `show_missing = true` | adds the Missing column (`-m`) |
| `skip_covered = true` | hides files at 100%; the footer says how many were skipped |
| `skip_empty = true` | hides files with no statements, such as empty `__init__.py` |
| `sort = "cover"` | sort by `name`, `stmts`, `miss`, `branch`, `brpart` or `cover`; a leading `-` reverses |
| `precision = 1` | digits after the decimal point |
| `fail_under = 90` | exit status 2 below this total |
| `omit`, `include` | file patterns for reporting only, separate from `[run]` |
| `ignore_errors = true` | skip source files that cannot be read or parsed instead of failing |

The `[tool.coverage.html]` table takes `directory`, `title`, `skip_covered`, `skip_empty`, `extra_css` and `show_contexts`; `[tool.coverage.xml]` and `[tool.coverage.json]` take `output`; `[tool.coverage.json]` also takes `pretty_print` and `show_contexts`.

## 3. The JSON report's structure

`coverage json` writes a document with three keys: `meta`, `files` and `totals`. Scripts that enforce custom rules (a per-package floor, a list of modules that must stay at 100%) read it instead of parsing text. This excerpt is real output from coverage 7.17 with branch coverage on.

```json
{
  "meta": {"format": 3, "version": "7.17.0", "branch_coverage": true, "show_contexts": false},
  "files": {
    "src/shop/price.py": {
      "executed_lines": [1, 7, 8, 9, 10, 11, 14, 15, 16, 23],
      "missing_lines": [24, 25, 26],
      "excluded_lines": [2, 3, 4, 19, 20],
      "executed_branches": [[9, 10], [9, 11]],
      "missing_branches": [[24, 25], [24, 26]],
      "summary": {"num_statements": 13, "missing_lines": 3, "num_branches": 4,
                  "percent_covered": 70.588, "percent_statements_covered": 76.923,
                  "percent_branches_covered": 50.0},
      "functions": {"total": {}, "main": {}, "tax": {}},
      "classes": {}
    }
  },
  "totals": {"covered_lines": 10, "num_statements": 13, "percent_covered": 70.588,
             "percent_covered_display": "71"}
}
```

This script fails when any module under a critical package is below 100%.

```python
import json
import sys

data = json.load(open("coverage.json", encoding="utf-8"))
bad = [
    f"{name}: {info['summary']['percent_covered']:.1f}%"
    for name, info in data["files"].items()
    if name.startswith("src/shop/billing/") and info["summary"]["percent_covered"] < 100
]
if bad:
    sys.exit("billing must be fully covered:\n" + "\n".join(bad))
```

- `functions` and `classes` hold the same fields per function and per class, so a rule can target one function.
- Since 7.12 the summaries carry `percent_statements_covered` and `percent_branches_covered` beside the combined `percent_covered`.
- `percent_covered_display` is the rounded string the text report shows; compare against `percent_covered`.

## 4. Contexts: which test ran this line

A context labels each recorded line with what was running. Static contexts label a whole run (`coverage run --context=linux-3.14`); dynamic contexts label each test.

```bash
COVERAGE_CORE=ctrace uv run pytest --cov --cov-context=test --cov-report=
uv run coverage html --show-contexts               # each line lists the tests that ran it
uv run coverage json --show-contexts -o ctx.json
uv run coverage report --contexts="discount"       # only lines run by matching contexts
```

- With pytest-cov's `--cov-context=test`, a context looks like `tests/test_price.py::test_total_with_discount|run` (`setup`, `run` or `teardown`).
- Without pytest-cov, `dynamic_context = "test_function"` under `[tool.coverage.run]` labels each test function.
- The `sysmon` core cannot record dynamic contexts. From the config, coverage falls back to `ctrace` by itself; through pytest-cov on Python 3.14 it warns "Dynamic contexts aren't supported with core=sysmon; context data may be incomplete". Set `COVERAGE_CORE=ctrace` or `core = "ctrace"` for context runs.
- Contexts grow the data file; turn them on for an investigation ("which tests touch this function before I change it?"), not on every CI run.

## 5. How exclusion regexes match

Each exclusion entry is a Python regular expression searched in every source line, so it can match anywhere in the line. A matched line is excluded; a matched line that introduces a block (`def`, `class`, a decorator, `if`, `else`, `with`, `for`, `while`, `try`, `except`, `case`) excludes the whole block.

```toml
[tool.coverage.report]
exclude_also = [
  "raise NotImplementedError",          # abstract-style methods
  "if __name__ == .__main__.:",         # dots match the quotes, either style
  "@(abc\\.)?abstractmethod",           # the decorated method's whole body
  "except ImportError:",                # optional-dependency fallbacks
]
```

- `exclude_also` adds to the defaults; `exclude_lines` replaces them, so `# pragma: no cover`, the `...` body rule and `if TYPE_CHECKING:` stop working unless you list them again.
- Patterns are regexes: escape `(`, `)`, `.`, `[`; in TOML basic strings write `\\` for one backslash, or use single-quoted literal strings.
- After adding a pattern, check that it matched: the `excluded_lines` lists in the JSON report, or the grey lines in the HTML report.
- Exclusions apply when reporting, not when measuring, so changing them needs no new test run.

## 6. Partial branches and pragma: no branch

A partial branch is a line with more than one possible next line where the tests took only some. Some partials are expected: a `while True:` loop never exits through its condition, and a retry loop that always returns never finishes normally.

| Mechanism | Effect |
|---|---|
| `# pragma: no branch` on the line | the line is never reported as a partial branch (the default `partial_branches` rule) |
| `partial_also = ["regex"]` | adds patterns to the partial-branch rules without replacing the default |
| `partial_branches = [...]` | replaces the rules; the default pragma stops working |
| built-in "always" rules | `while True:`, `while 1:`, `if True:` and similar at the start of a statement are never partial |

```python
import time
from collections.abc import Callable


def retry(try_once: Callable[[], bool]) -> None:
    for attempt in range(3):  # pragma: no branch  (always ends through return or raise)
        if try_once():
            return
        time.sleep(2**attempt)
    raise TimeoutError("gave up after 3 attempts")
```

Since 7.17 the built-in rules match only at the start of a statement, so a comment that mentions `while True:` no longer hides a real partial branch.

## 7. Platform- and version-specific exclusions

Code for another operating system or an older Python cannot run in this job. The best answer is to run it elsewhere and combine (the combining-in-ci reference). When that is impossible, put an environment variable inside an exclusion pattern: coverage.py expands `$VAR`, `${VAR}`, `${VAR-default}` and `${VAR?}` (an error if unset) in configuration values.

```python
import os
import sys


def config_dir() -> str:
    if sys.platform == "win32":  # pragma: win32 only
        return os.environ["APPDATA"]
    return os.path.expanduser("~/.config")  # pragma: posix only
```

```toml
[tool.coverage.report]
exclude_also = ["# pragma: ${COVERAGE_NOT_PLATFORM?} only"]
```

```bash
COVERAGE_NOT_PLATFORM=win32 uv run coverage report     # macOS and Linux: Windows-only code excluded
```

On Windows set `COVERAGE_NOT_PLATFORM=posix` in the job (`$env:COVERAGE_NOT_PLATFORM = "posix"` in PowerShell). The `?` form makes a run without the variable fail with "Variable COVERAGE_NOT_PLATFORM is undefined" instead of quietly excluding nothing. The same idea works for versions: tag pre-3.14 fallbacks `# pragma: lt-py314 only` and set the variable per job.

## 8. Excluding whole files

Leave files out with `omit` patterns; they match file paths with shell-style wildcards, not regexes.

```toml
[tool.coverage.run]
omit = [
  "*/_vendor/*",
  "*/migrations/*",
  "src/shop/_version.py",      # generated by the build backend
]
```

- `[run] omit` stops measurement; `[report] omit` hides measured data from reports only.
- `# pragma: no cover` on the first line of a module excludes that line, not the module; use `omit`.
- Test files are not measured when `source_pkgs` names only the package. Measuring tests too (`source_dirs = ["tests"]`) finds test code that never runs, such as a helper nothing calls or a misnamed test pytest does not collect.

## 9. Gotchas

These are the report and exclusion problems seen most.

1. **`# pragma: no cover` on a decorator line excluded more than expected.** A decorator introduces the function, so the whole decorated function is excluded. Put the pragma on a line inside the body if only that line should go.
2. **An exclusion pattern for `if __name__ == "__main__":` does nothing.** The quotes in the regex did not match the code's quotes. Use `.` in place of each quote character.
3. **The HTML report links to pages that no longer exist.** `htmlcov/` kept pages from an earlier run while `skip_covered` changed the file list. Delete `htmlcov/` before generating it in CI.
4. **`coverage xml` fails the CI step.** Every reporting command applies `fail_under`. Pass `--fail-under=0` to artifact commands and gate with one `coverage report`.
5. **Contexts are empty or incomplete on 3.14.** The `sysmon` core does not record dynamic contexts. Run that job with `COVERAGE_CORE=ctrace`.
6. **A missed branch shows as `24->exit`.** The function never left from line 24 that way, often a loop whose body always returned. Add the missing case, or a `# pragma: no branch` with a reason.
