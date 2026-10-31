---
name: python-cli
description: Builds command-line tools in Python with argparse, Typer or Click - choosing among them, console-script entry points and python -m, subcommands, typed options and arguments, stdin and stdout conventions, exit codes, error messages without tracebacks, Ctrl+C and broken pipes, configuration precedence across flags, environment and config files, per-user directories, colour and NO_COLOR, progress output on stderr, fast start-up, and testing with main(argv) or CliRunner. Use when writing a CLI or script with arguments, adding subcommands, making a tool installable as a command, or fixing a CLI that prints tracebacks, hangs on a pipe or starts slowly.
metadata:
  tags:
  - python
  - cli
  - argparse
  - typer
  - click
  - entry-points
  - exit-codes
  - rich
---

# Python command-line tools

A good CLI does one job, reads arguments and stdin, writes results to stdout and messages to stderr, and tells the shell what happened through its exit code. This skill builds that with the standard library's argparse or with Typer or Click; library-specific detail is in [typer-and-click.md](references/typer-and-click.md).

## Step 0: read the versions and how the tool starts

Argparse features depend on Python, and Typer changed its Click dependency, so read both before choosing, along with any entry point the project already has.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
uv pip list 2>/dev/null | grep -iE "^(typer|click|rich|platformdirs) "
grep -nA4 "project.scripts" pyproject.toml
```

Current releases (October 2026): Typer 0.27, Click 8.5, Rich 15.0, platformdirs 4.14.

## Choose a library

The three common choices differ in dependencies and in how options are declared.

| Library | Declares options by | Pick when |
|---|---|---|
| argparse (stdlib) | `add_argument` calls | no dependencies allowed, a script, a tool shipped with Python itself |
| Typer 0.27 | type hints and `Annotated` | a typed codebase; quick, readable subcommands; it vendors Click since 0.26 |
| Click 8.5 | decorators | complex option handling, plugins, a large existing Click codebase |

Python 3.14 gave argparse colour help and "did you mean" suggestions (`suggest_on_error`, on by default since 3.15), which narrows the gap for small tools.

## Make it a command

A console-script entry point turns a function into a command that `uv tool install`, `pipx` and `pip` put on `PATH`.

```toml
# pyproject.toml
[project.scripts]
my-app = "my_app.cli:run"
```

```python
# src/my_app/__main__.py: also allow `python -m my_app`
from my_app.cli import run

run()
```

During development: `uv run my-app --help`. For users: `uv tool install my-app` or `uvx my-app`.

## A complete argparse CLI

The pattern below separates parsing (`build_parser`), work (`main`, which returns an exit code and takes `argv` for tests) and process concerns (`run`, which handles Ctrl+C and broken pipes).

```python
from __future__ import annotations

import argparse
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="my-app", description="Inspect and convert price files.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('my-app')}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="more output (repeatable)")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="print a price file")
    show.add_argument("path", type=Path, help="file to read, or - for stdin")
    show.add_argument("--format", choices=["table", "json"], default="table")

    conv = sub.add_parser("convert", help="convert prices to another currency")
    conv.add_argument("path", type=Path)
    conv.add_argument("--rate", type=float, required=True)
    conv.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=False,
                      help="print the result and write no file")
    return parser


def read_input(path: Path) -> str:
    return sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")


def load_prices(path: Path) -> dict[str, float]:
    data = json.loads(read_input(path))
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object of SKU to price")
    for sku, price in data.items():
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise ValueError(f"price of {sku!r} is not a number")
    return data


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)          # exits with code 2 on a usage error
    try:
        prices = load_prices(args.path)
        if args.command == "show":
            if args.format == "json":
                print(json.dumps(prices, indent=2))
            else:
                for sku, price in prices.items():
                    print(f"{sku}\t{price:.2f}")
            return 0
        text = json.dumps({k: round(v * args.rate, 2) for k, v in prices.items()}, indent=2)
        if args.dry_run or str(args.path) == "-":
            print(text)
        else:
            args.path.with_suffix(".converted.json").write_text(text + "\n", encoding="utf-8")
        return 0
    except OSError as exc:                          # missing, a directory, no permission, disk full
        print(f"my-app: {args.path}: {exc.strerror or exc}", file=sys.stderr)
    except ValueError as exc:                       # JSONDecodeError and UnicodeDecodeError are ValueErrors
        print(f"my-app: {args.path} is not a price file: {exc}", file=sys.stderr)
    return 1


def run() -> None:
    try:
        code = main()
        sys.stdout.flush()                             # a closed pipe must fail here, inside the try
    except KeyboardInterrupt:
        sys.exit(130)                                  # 128 + SIGINT, what shells expect
    except BrokenPipeError:                            # `my-app show big.json | head`
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())          # stop Python complaining while flushing at exit
        sys.exit(1)
    sys.exit(code)
```

## Output conventions

Separate data from messages so the tool composes with pipes and scripts.

| Stream or signal | Carries |
|---|---|
| stdout | the result: data another program may parse |
| stderr | progress, warnings, errors, prompts |
| exit code 0 | success |
| exit code 1 | the operation failed (file missing, remote error) |
| exit code 2 | usage error (argparse and Click use it) |
| exit code 130 | interrupted by Ctrl+C |

- Offer `--format json` (or `--json`) for machine-readable output; keep the table for people.
- Accept `-` for stdin and stdout, so `cat a.json | my-app show -` works.
- Never print a traceback for a user error: catch the expected exceptions, print one line naming the tool, the problem and the fix, and return 1. Show the traceback only with `--verbose` or `--debug`.
- Check `sys.stdout.isatty()` before colours, progress bars or prompts, and respect `NO_COLOR` (any value disables colour) and `FORCE_COLOR`.

## Configuration precedence

A CLI that also reads environment variables and a config file applies them in a fixed order, most specific first.

1. Command-line flags.
2. Environment variables (`MY_APP_TOKEN`, prefixed with the tool name).
3. A project config file (`./my-app.toml` or `[tool.my-app]` in `pyproject.toml`).
4. A user config file in the platform's config directory.
5. Built-in defaults.

```python
from pathlib import Path

import platformdirs
import tomllib

config_file = Path(platformdirs.user_config_dir("my-app")) / "config.toml"   # macOS, Windows and Linux paths
settings = tomllib.loads(config_file.read_text(encoding="utf-8")) if config_file.exists() else {}
```

- Read TOML with the standard library's `tomllib`; write it with `tomli-w`.
- Cache and data go to `platformdirs.user_cache_dir(...)` and `user_data_dir(...)`, never into the current directory or the package folder.
- Secrets come from the environment or the OS keyring (`keyring` package), never from a flag (flags show up in `ps` and shell history).

## Progress and rich output

Progress goes to stderr, so it never mixes with the data on stdout.

```python
from pathlib import Path

from rich.console import Console
from rich.progress import track


def convert(path: Path) -> None:
    path.with_suffix(".out").write_bytes(path.read_bytes())


err = Console(stderr=True)
paths = sorted(Path(".").glob("*.csv"))
for path in track(paths, description="Converting", console=err):
    convert(path)
err.print("[green]done[/green]")
```

rich (15.0) switches off colours and animation automatically when stderr is not a terminal or `NO_COLOR` is set.

## Fast start-up

A CLI is started for every invocation, so import time is user-visible latency.

```bash
uv run python -X importtime -m my_app --help 2> imports.log
sort -t'|' -k2 -n imports.log | tail -10           # slowest imports, cumulative microseconds
```

- Import heavy packages (pandas, torch, cloud SDKs) inside the subcommand that needs them; on Python 3.15 `lazy import pandas as pd` at module level does the same (PEP 810).
- Keep `--help` and `--version` free of heavy imports.

## Testing a CLI

Test through `main(argv)` and the captured streams; it is fast and needs no subprocess.

```python
import pytest

from my_app.cli import main


def test_show_json(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "p.json"
    path.write_text('{"A": 3.5}', encoding="utf-8")
    assert main(["show", str(path), "--format", "json"]) == 0
    assert '"A": 3.5' in capsys.readouterr().out


def test_missing_file_is_a_clean_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "nope.json"]) == 1
    assert "no such file" in capsys.readouterr().err


def test_usage_error_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == 2
```

Add one end-to-end test that runs the installed command (`subprocess.run(["my-app", "--version"], check=True)`) to prove the entry point works. Typer and Click tests use `CliRunner` ([typer-and-click.md](references/typer-and-click.md)).

## Verify

Run the installed command the way a shell will and read the exit codes, in bash (`PIPESTATUS` is a bash feature).

```bash
uv run my-app --help > /dev/null; echo "exit $?"    # 0
uv run my-app frobnicate 2>/dev/null; echo "exit $?"   # 2: usage error
uv run my-app show nope.json; echo "exit $?"        # one line on stderr, exit 1, no traceback
uv run my-app show . ; echo "exit $?"               # a directory: the same
printf '\377' > bad.json; uv run my-app show bad.json; echo "exit $?"     # bad bytes: the same
echo '[1, 2]' > list.json; uv run my-app show list.json; echo "exit $?"   # the wrong shape: the same
uv run my-app convert good.json --rate 2 --dry-run; ls good.converted.json   # prints the result, writes no file
python3 -c "import json; print(json.dumps({f'k{i}': i for i in range(50000)}))" > big.json
uv run my-app show big.json 2> err.log | head -1 > /dev/null; echo "exit ${PIPESTATUS[0]}"   # expect 1
grep -c "Exception ignored" err.log                 # expect 0: the broken pipe ended quietly
uv run my-app show big.json --format json | python3 -m json.tool > /dev/null; echo "exit $?"   # stdout is pure data
rm big.json err.log
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `sys.exit(main())` with no flush | `sys.stdout.flush()` inside the `try`, as in `run()` |
| `print` of progress to stdout | stderr, so pipes get only data |
| a traceback for a missing file | one line naming the tool, the problem and the fix, then exit 1 |
| a `--token` flag | an environment variable or the OS keyring |
| `shell=True` around user arguments | a subprocess argument list |
| heavy imports at module level | imports inside the subcommand |
| colour with no check | `isatty()` and `NO_COLOR` |
| testing through `subprocess` only | `main(argv)`, plus one end-to-end test |

## Checklist

A CLI is ready when every item holds.

- [ ] `[project.scripts]` entry and `__main__.py`; `--help` and `--version` work.
- [ ] Results on stdout, messages on stderr; `--json` output for scripts.
- [ ] Exit codes 0, 1, 2 and 130 as above; no traceback for user errors.
- [ ] Ctrl+C and `| head` end quietly.
- [ ] Config precedence: flags, env, project file, user file, defaults; no secrets in flags.
- [ ] Colour and progress only on a terminal; `NO_COLOR` respected.
- [ ] `main(argv)` tests for success, user error and usage error.

## Gotchas

These are the CLI problems users notice first.

1. **`Exception ignored ... BrokenPipeError` and exit code 120 when piping to `head`.** Output small enough to sit in stdout's buffer is written at exit, after the reader closed the pipe, and `sys.exit(main())` already left the `try`. Flush inside the `try` in `run()`, catch `BrokenPipeError` there and redirect stdout to devnull, as above.
2. **Output appears in the wrong order or late in CI logs.** stdout is block-buffered when it is not a terminal. Flush after progress lines (`print(..., flush=True)`), or run with `-u`, or put progress on stderr.
3. **`UnicodeEncodeError` on Windows.** An older Python with a legacy console encoding. Python 3.15 defaults to UTF-8 (PEP 686); before that, set `PYTHONUTF8=1` or reconfigure: `sys.stdout.reconfigure(encoding="utf-8")`.
4. **The command takes a second before `--help` appears.** Heavy imports at module level. Import inside subcommands.
5. **A flag and an environment variable disagree silently.** No documented precedence. Implement the order above and show the effective config with a `config show` subcommand.
6. **Tests pass but the installed command fails with `ModuleNotFoundError`.** The entry point names the wrong module or function. Test the installed command once in CI.

## References

The reference files hold the longer recipes and the full tool detail.

- [typer-and-click.md](references/typer-and-click.md): Typer commands with `Annotated`, validation, environment variables, Click groups and options, `CliRunner` tests, shell completion

## See also

- `python-terminal-ui`: rich tables, progress bars and full-screen apps
- `python-configuration`: layered settings behind the flags
- `python-packaging`: publishing the entry point
- `python-subprocess`: running other programs safely
- `python-cross-platform`: encodings, signals and paths on Windows
