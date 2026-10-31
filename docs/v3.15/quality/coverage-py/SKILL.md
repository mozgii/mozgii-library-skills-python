---
name: coverage-py
description: Measures Python test coverage with coverage.py 7.17 and reads the result honestly - configuration in pyproject.toml or .coveragerc.toml, branch coverage, source_pkgs, source_dirs and omit, pytest-cov versus coverage run, measuring subprocesses with patch = subprocess, multiprocessing and parallel data files, implicit combining, combining data from CI jobs on several Python versions and operating systems with [paths], fail_under, exclusion rules and pragmas, platform-specific exclusions, html, xml, json, lcov and markdown reports, who-tested-this-line contexts, and the sys.monitoring core on Python 3.14+. Use when setting up coverage, a coverage number looks wrong, code run in a subprocess shows as missed, combining coverage across CI jobs, choosing a coverage floor, or deciding what a coverage percentage proves.
metadata:
  tags:
  - python
  - coverage
  - coverage-py
  - pytest-cov
  - branch-coverage
  - testing
  - ci
  - sys-monitoring
---

# Test coverage with coverage.py

coverage.py records which lines and branches ran while your tests ran. It answers one question well: "which code did no test execute?" It does not tell you whether the code that ran was checked. This skill covers configuration, measuring across processes and machines, exclusions, and reading the reports. Writing the tests themselves is the pytest-testing skill; this skill goes deeper on coverage only.

## Step 0: read the installed versions

Behaviour changed a lot between 7.10 and 7.17 (subprocess patching, the default core, implicit combining), so read what is installed before you configure anything.

```bash
uv run python -c "import sys; print(sys.version)"
uv run coverage --version                      # "Coverage.py, version 7.17.0 with C extension"
uv run python -c "import pytest_cov; print(pytest_cov.__version__)"
uv run coverage debug config | grep -E "^\s+(branch|core|patch|source|parallel)"
```

Current releases (October 2026): coverage 7.17.0 (Python 3.11+), pytest-cov 7.1.0.

| Version | Change that matters |
|---|---|
| coverage 7.9.1 | on Python 3.14+ the `sysmon` core is the default when your settings allow it |
| coverage 7.10 | `[run] patch` option (`subprocess`, `fork`, `execv`, `_exit`); `...` bodies and `if TYPE_CHECKING:` excluded by default |
| coverage 7.11.1 | a conflicting setting makes a defaulted `sysmon` fall back to `ctrace` with no warning; a `core = "sysmon"` you asked for raises an error |
| coverage 7.13 | `.coveragerc.toml` config file |
| coverage 7.14 | `report`, `html`, `xml`, `json` and `lcov` combine parallel data files on their own |
| coverage 7.16 | `combine` converts path separators, so Windows and POSIX data merge with less `[paths]` setup |
| pytest-cov 7.0 | dropped its own subprocess support; use coverage's `patch = ["subprocess"]` |

## Configure coverage.py in pyproject.toml

coverage.py reads `[tool.coverage.*]` tables from `pyproject.toml` (or `.coveragerc`, `.coveragerc.toml`, `setup.cfg`, `tox.ini`). This configuration suits a src-layout library or application whose tests start subprocesses.

```toml
[tool.coverage.run]
branch = true                     # measure both outcomes of every if, while, for, match
source_pkgs = ["shop"]            # measure the importable package, wherever it is installed
patch = ["subprocess"]            # measure Python subprocesses too; implies parallel = true
relative_files = true             # store relative paths, so data from other machines combines

[tool.coverage.paths]
source = ["src/", "*/site-packages/"]   # first entry is the canonical path

[tool.coverage.report]
show_missing = true
skip_covered = true
fail_under = 90
exclude_also = [
  "raise NotImplementedError",
  "if __name__ == .__main__.:",
  "@(abc\\.)?abstractmethod",
]
```

- Unknown keys only warn ("Unrecognized option"); `coverage debug config` prints the effective settings.
- Keep the configuration in the repository, never only in CI flags, so local runs and CI agree.

## Choose what to measure: source_pkgs, source_dirs, omit

The `source` settings tell coverage.py which code is yours, so files that were never imported still appear as 0% instead of disappearing from the report.

| Setting | Use for |
|---|---|
| `source_pkgs = ["shop"]` | an importable package; found wherever it is installed (site-packages or `src/`) |
| `source_dirs = ["scripts"]` | a directory of code that is not a package; an error if it does not exist |
| `source = [...]` | older form that guesses between package and directory; prefer the two above |
| `omit = ["*/_vendor/*", "*/migrations/*"]` | file patterns to leave out entirely |

- Without any `source` setting, coverage.py measures every file outside the standard library and site-packages that ran, and unimported files are invisible.
- `omit` hides code from the total; use it for vendored or generated code only.

## Branch coverage

Branch coverage records, for each line that can jump, which destinations were taken. Line coverage alone marks `if x: y()` as fully covered when the test only ran the true case.

```python
def total(prices: list[float], discount: float = 0.0) -> float:
    subtotal = sum(prices)
    if discount:                       # line 3
        subtotal *= 1 - discount
    return round(subtotal, 2)          # line 5
```

If every test passes a discount, the report shows `3->5` in Missing: the jump from line 3 straight to line 5 (no discount) never happened. Turn branches on in every project; the cost is small, and the `BrPart` column lists every partial branch.

## pytest-cov or coverage run

pytest-cov is a pytest plugin that starts coverage before collection and prints the report at the end; `coverage run -m pytest` does the same from the outside. Both read the same `[tool.coverage]` settings.

```bash
uv run pytest --cov --cov-report=term-missing           # pytest-cov
uv run coverage run -m pytest && uv run coverage report  # coverage alone
```

| Need | Use |
|---|---|
| pytest-xdist workers measured automatically | pytest-cov |
| `--cov-context=test` (which test ran each line) | pytest-cov |
| measuring a script or a non-pytest program | `coverage run` |
| one fewer plugin, same command in tox, nox and CI | `coverage run` |

- `pytest --cov` with no value takes `source` from the config; `--cov=shop` overrides it.
- `--no-cov` turns measurement off for a debugging session.

## Measure subprocesses and multiprocessing

Code run in a child process is measured only if coverage starts inside that child. Since coverage 7.10 one setting does it: `patch = ["subprocess"]` sets an environment variable and installs a `.pth` hook, so every Python child starts coverage with the parent's configuration and writes its own data file.

```python
import subprocess
import sys


def test_cli_runs_in_a_subprocess() -> None:
    out = subprocess.run([sys.executable, "-m", "shop.price", "2", "3"],
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == "5.0"
```

Without the patch, the `main()` function this test runs shows as missed; with it, the report counts it.

- `patch = ["subprocess"]` turns on `parallel = true` for you: each process writes `.coverage.<host>.<pid>.<random>`.
- `concurrency = ["multiprocessing"]` is still the way to measure `multiprocessing` and `ProcessPoolExecutor` workers; it also implies parallel mode.
- `patch = ["_exit"]` saves data when a child ends with `os._exit()`, which skips normal shutdown. `fork` and `execv` patches exist for POSIX and are refused on Windows.
- A child killed with SIGKILL saves nothing; `--save-signal=USR1` (POSIX only) makes a long-running server write data on demand.

## Parallel data files and combine

In parallel mode every process writes its own data file, and `coverage combine` merges them into `.coverage`. Since 7.14 the reporting commands combine on their own, so the usual sequence is just run, then report.

```bash
uv run coverage run -m pytest      # leaves .coverage.<host>.pid1556.... and .coverage.<host>.pid1572....
uv run coverage report             # prints "Combined 2 files", then the table; the parts are deleted
uv run coverage report --keep-combined   # keep the part files, e.g. to combine again later
uv run coverage combine --keep     # explicit combine, keeping the inputs
```

- "Can't combine statement coverage data with branch data" means one process ran without `branch = true`; since 7.17 the message names both files. Usually a child read a different config file.
- Combining across CI jobs, Python versions and operating systems: [combining-in-ci.md](references/combining-in-ci.md).

## fail_under: a floor, not a target

`fail_under` makes `coverage report` exit with status 2 (and pytest-cov fail the run) when the total drops below the number.

- Set it to the current total rounded down, and raise it when coverage grows; it stops regressions without inviting padding tests.
- The total with `branch = true` counts statements and branches together, so it is lower than line-only coverage. Do not compare numbers across that switch.
- `precision = 1` under `[tool.coverage.report]` reports one decimal; set it when your floor has a decimal, such as `fail_under = 87.5`.
- A floor on the whole project hides a new untested module inside a well-tested codebase. Pair it with a diff coverage check on pull requests ([combining-in-ci.md](references/combining-in-ci.md)).

## Exclude code from measurement

Exclusion regexes remove lines (and the blocks they start) from the count. Use `exclude_also` to add to the defaults; `exclude_lines` replaces them, which silently drops `# pragma: no cover`.

These patterns are excluded by default in 7.17:

| Default pattern | Excludes |
|---|---|
| `# pragma: no cover` | the line, or the whole block when on a `def`, `class`, `if` or `with` line |
| a body that is only `...` | Protocol methods, overload stubs, abstract placeholders |
| `if TYPE_CHECKING:` | the whole typing-only block |

```python
import os
import sys


def config_dir() -> str:
    if sys.platform == "win32":        # pragma: no cover
        return os.environ["APPDATA"]
    return os.path.expanduser("~/.config")
```

- `# pragma: no branch` on a line marks its branch as never partial, for loops that always run at least once or conditions that are always true in tests.
- Exclude only what you could defend in review: code that cannot run, debug-only or typing-only code.
- Platform-specific exclusions with an environment variable, partial-branch rules and the regex details: [reports-and-exclusions.md](references/reports-and-exclusions.md).

## Read the coverage report

The terminal report lists each file with five numbers; `show_missing` adds the lines and branch arcs that never ran.

```
Name                    Stmts   Miss Branch BrPart  Cover   Missing
-------------------------------------------------------------------
src/shop/price.py           9      2      2      1    73%   9->11, 15-16
```

- `Stmts`, `Miss`: executable statements and those never run. `Branch`, `BrPart`: possible branch destinations and lines with at least one destination never taken.
- `15-16` is a range of missed lines; `9->11` is a jump from line 9 to line 11 that never happened; `9->exit` means the function never returned from line 9.
- `coverage html` shows the same with colour, per-file pages and a filter; open `htmlcov/index.html`. The `xml` (Cobertura), `json` and `lcov` reports feed CI tools and editors. `coverage report --format=markdown` writes a table for a pull-request comment, and `--format=total` prints only the number.

```bash
uv run coverage html                                              # then open htmlcov/index.html
uv run coverage json -o coverage.json --pretty-print
uv run coverage report --format=total                             # e.g. 87
```

## What a coverage number proves

A covered line ran at least once during the tests. That is all.

- A test with no assertion covers as many lines as a good one. Coverage finds untested code; mutation testing (mutmut) measures whether tests notice a change.
- 100% branch coverage still misses inputs: a covered `total(prices)` says nothing about an empty list or `NaN`.
- Uncovered code is a list of questions: dead code to delete, an error path to test, or code that only runs on another OS or Python (measure there and combine).
- Reasonable floors: 85 to 95% for libraries, lower for UI glue; 100% only where every branch matters (parsers, money, security checks).

## The sys.monitoring core on Python 3.12+

coverage.py has three measurement cores. `sysmon` uses `sys.monitoring` (PEP 669), where Python reports each line once and coverage then disables the callback, so the overhead is far lower than tracing.

| Core | When it is used |
|---|---|
| `sysmon` | the default on CPython 3.14+ when nothing below forbids it |
| `ctrace` | the C tracer: the default on 3.11 to 3.13, and the fallback on 3.14+ |
| `pytrace` | pure Python; only with `timid = true` or when the C extension is missing |

- Branch measurement needs 3.14's `sys.monitoring` branch events. On 3.12 and 3.13, `core = "sysmon"` with `branch = true` warns "Can't use core=sysmon: sys.monitoring can't measure branches in this version" and falls back to ctrace.
- `sysmon` does not support dynamic contexts (`dynamic_context = "test_function"`, `--cov-context=test`), gevent, eventlet or greenlet concurrency, or file-tracer plugins such as Cython's and Django templates'. With those settings coverage uses ctrace instead.
- Force a core with `core = "ctrace"` in `[tool.coverage.run]` or `COVERAGE_CORE=ctrace`; check which one ran with `coverage run --debug=sys -m pytest 2>&1 | grep "core:"`.

## Verify

Prove that the floor can fail and that subprocess code is counted, rather than trusting one green number.

```bash
uv run coverage erase
uv run coverage run -m pytest -q
uv run coverage report; echo "exit $?"                 # expect 0 at or above fail_under
uv run coverage report --fail-under=100; echo "exit $?"   # expect 2 below 100: the floor can fail
uv run coverage debug config | grep -E "^\s+(branch|source_pkgs|patch)"   # the settings that took effect
uv run coverage report --include="*/cli.py"            # a module run through subprocess is not 0%
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `exclude_lines = [...]` | `exclude_also`, which keeps the defaults and `# pragma: no cover` |
| raising the number with `omit` | omit only vendored or generated code |
| a test with no assertion, to cover a line | a test that checks the behaviour, or leave the line visible |
| `fail_under` set far above today's total | the current total rounded down, raised as coverage grows |
| comparing totals across `branch = true` | one baseline per setting |
| one job's data as the project's number | combine every Python and OS before checking the floor |
| `# pragma: no cover` on code you could test | a test; pragmas are for code that cannot run here |

## Checklist

Coverage is set up well when every item holds.

- [ ] `[tool.coverage]` lives in the repository with `branch = true` and `source_pkgs` or `source_dirs`.
- [ ] Code run in subprocesses is measured (`patch = ["subprocess"]`, or `concurrency = ["multiprocessing"]`).
- [ ] `exclude_also` (not `exclude_lines`) adds exclusions, each one defensible in review.
- [ ] `fail_under` is the current total rounded down, checked in CI; pull requests also get a diff coverage check.
- [ ] Data from every CI job (each Python and OS) is combined with `relative_files = true` and `[paths]` before the floor is checked.
- [ ] Nobody treats the number as proof that the tests check behaviour.

## Gotchas

These are the coverage problems people hit most.

1. **0% for the package, or "Module shop was never imported".** `source` names a folder while tests import the installed package, or a plugin imported the package before coverage started. Use `source_pkgs = ["shop"]` and start through `pytest --cov` or `coverage run`.
2. **A CLI tested through `subprocess` shows as missed.** Only the parent process was measured; pytest-cov 7 no longer patches children. Add `patch = ["subprocess"]`.
3. **Old part files inflate or break the report.** A previous run left `.coverage.*` files that were combined into this one. Run `coverage erase` first, and add `.coverage*` to `.gitignore`.
4. **"Can't combine statement coverage data with branch data".** One process measured without branches, usually because it read another config or ran `coverage run` with different flags. Keep all settings in one config file.
5. **The CI combine job finds no data.** `actions/upload-artifact` skips hidden files, and `.coverage.*` starts with a dot. Set `include-hidden-files: true` on the upload step.
6. **`# pragma: no cover` stopped working.** `exclude_lines` replaced the default list. Use `exclude_also`.
7. **Coverage is much slower on 3.13 than on 3.14.** On 3.13 branch coverage uses the C tracer; 3.14 can use `sys.monitoring`. Upgrade the Python that runs the coverage job, or accept the cost.
8. **The number dropped after adding `branch = true`.** Branches are now counted in the total. Reset `fail_under` to the new baseline once.

## References

The reference files hold the multi-job recipes and the report details.

- [combining-in-ci.md](references/combining-in-ci.md): combining data from a matrix of Python versions and operating systems, `[paths]` remapping, GitHub Actions upload and combine jobs, diff coverage on pull requests, coverage under nox and tox
- [reports-and-exclusions.md](references/reports-and-exclusions.md): every report format and its options, the JSON report's fields, contexts that record which test ran each line, exclusion and partial-branch regexes, platform- and version-specific exclusions with environment variables

## See also

- `pytest-testing`: the tests that coverage measures
- `nox-sessions`: a session per Python and a combining session
- `python-subprocess`: running the children that `patch = ["subprocess"]` measures
- `python-cross-platform`: code that only runs on one OS, and the CI matrix that measures it
