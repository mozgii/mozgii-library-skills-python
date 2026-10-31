---
title: Typer and Click command-line apps
author: MOZGII, Inc.
lang: en
---

# Typer and Click command-line apps

Typer builds a CLI from type-annotated functions; Click builds one from decorators. Since Typer 0.26 Typer vendors its own copy of Click, so Click-specific plugins and types no longer plug into Typer apps. Versions in October 2026: Typer 0.27 (Python 3.10 or later), Click 8.5. Install `typer` itself: `typer-slim` and `typer-cli` are deprecated and now only install `typer`.

## Contents

This reference has 8 sections.

1. A Typer app with subcommands
2. Typer options: validation, choices, environment variables
3. Errors and exit codes in Typer
4. A Click app with groups
5. Testing with CliRunner
6. Shell completion
7. Rich and Markdown in Typer help
8. Gotchas

## 1. A Typer app with subcommands

A Typer app is a `typer.Typer()` object; each decorated function becomes a subcommand, and its docstring becomes the help text.

```python
from pathlib import Path
from typing import Annotated, Literal

import typer

app = typer.Typer(help="Inspect and convert price files.", no_args_is_help=True)


@app.command()
def show(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True, help="price file")],
    fmt: Annotated[Literal["table", "json"], typer.Option("--format", "-f")] = "table",
) -> None:
    """Print a price file."""
    typer.echo(f"{path.name} as {fmt}")


if __name__ == "__main__":
    app()
```

```toml
[project.scripts]
my-app = "my_app.cli:app"          # a Typer app object is callable
```

- Create a `typer.Typer()` app even for one command. `typer.run(fn)` has nowhere to put a second command, and moving later changes the entry point.
- Use `Annotated[...]` for parameters; the older `= typer.Option(...)` default style still works but mixes defaults and metadata, and `= typer.Argument(default=...)` is the old way to mark a required value. In the `Annotated` style, no default means required.
- A Typer app with one command runs that command directly; add a callback (`@app.callback()`) or a second command to get subcommands.
- Nest apps for command groups: `app.add_typer(users_app, name="users")` gives `my-app users add`.

## 2. Typer options: validation, choices, environment variables

Typer derives parsing and validation from the annotation and the `Option`/`Argument` settings.

```python
@app.command()
def convert(
    path: Path,
    rate: Annotated[float, typer.Option(min=0.0, help="exchange rate")],          # required: no default
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run")] = False,
    token: Annotated[str, typer.Option(envvar="MY_APP_TOKEN", hidden=True)] = "",  # from the environment
    tags: Annotated[list[str] | None, typer.Option("--tag")] = None,             # repeatable: --tag a --tag b
) -> None:
    """Convert prices to another currency."""
```

| Need | Write |
|---|---|
| fixed choices | `Literal["table", "json"]` (Typer 0.19+) or a `StrEnum` |
| number range | `typer.Option(min=1, max=10)` |
| existing file | `typer.Argument(exists=True, dir_okay=False)` |
| value from env | `typer.Option(envvar="MY_APP_X")` |
| prompt when missing | `typer.Option(prompt=True)`; `hide_input=True` for passwords |
| boolean pair | `"--force/--no-force"` |

## 3. Errors and exit codes in Typer

`typer.Exit(code=...)` ends the command with an exit code; `typer.BadParameter` reports a usage error (exit 2) against a specific parameter.

```python
@app.command()
def fetch(url: str) -> None:
    if not url.startswith("https://"):
        raise typer.BadParameter("only https URLs are allowed", param_hint="url")
    try:
        body = download(url)
    except DownloadError as exc:
        typer.echo(f"my-app: {exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(body)
```

Typer shows rich tracebacks for unexpected exceptions; since 0.23 they hide local variables by default (locals can hold secrets). `TYPER_STANDARD_TRACEBACK=1` prints a plain traceback.

## 4. A Click app with groups

A Click app is a group of commands built with decorators; options are declared above the function.

```python
import click


@click.group()
@click.version_option(package_name="my-app")
def cli() -> None:
    """Inspect and convert price files."""


@cli.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table", show_default=True)
def show(path: str, fmt: str) -> None:
    """Print a price file."""
    click.echo(f"{path} as {fmt}")


if __name__ == "__main__":
    cli()
```

- `click.ClickException("message")` prints `Error: message` and exits 1; `click.UsageError` exits 2.
- `@click.pass_context` gives commands a shared `ctx.obj` for state set up by the group (a client, loaded config).
- `auto_envvar_prefix="MY_APP"` in `cli(auto_envvar_prefix=...)` maps every option to an environment variable.

## 5. Testing with CliRunner

`CliRunner` invokes the app in-process and captures output and the exit code; stdout and stderr are separate since Click 8.2.

```python
from typer.testing import CliRunner          # Click apps: from click.testing import CliRunner

from my_app.cli import app

runner = CliRunner()


def test_convert() -> None:
    result = runner.invoke(app, ["convert", "p.json", "--rate", "0.5"])
    assert result.exit_code == 0, result.output
    assert "converted" in result.stdout


def test_zero_rate_is_an_error() -> None:
    result = runner.invoke(app, ["convert", "p.json", "--rate", "0"], env={"MY_APP_TOKEN": "x"})
    assert result.exit_code == 1
    assert "rate must be positive" in result.stderr
```

- `result.exception` holds an unexpected exception; pass `catch_exceptions=False` to let it propagate into pytest's traceback.
- `runner.isolated_filesystem()` gives a temporary working directory for commands that write files.

## 6. Shell completion

Both libraries generate completion scripts for bash, zsh, fish and PowerShell.

```bash
my-app --install-completion            # Typer: installs for the current shell
my-app --show-completion               # Typer: prints the script
_MY_APP_COMPLETE=zsh_source my-app > ~/.my-app-complete.zsh   # Click: generate, then source it
```

## 7. Rich and Markdown in Typer help

Typer renders help through Rich when Rich is installed. `rich_markup_mode` picks what docstrings and `help=` text may contain.

```python
import typer

app = typer.Typer(rich_markup_mode="markdown")


@app.command(help="**Purge** old price files.")
def purge(days: int = 30) -> None:
    """Internal note: this docstring is ignored, because help= is set."""
```

| `rich_markup_mode` | Help text may use | Default when |
|---|---|---|
| `"rich"` | `[bold green]...[/bold green]` markup and `:emoji:` codes | Rich is installed |
| `"markdown"` | Markdown | never: set it |
| `None` | plain text | Rich is not installed |

Typer's own guidance is to drop the settings that came from Click (`expose_value`, `shell_complete`, `show_choices`, `errors`, `prompt_required`, `is_flag`, `flag_value`, `allow_from_autoenv`) and Click extensions, now that Click is vendored. `typer.Option` still accepts these names in 0.27, so old code runs; refactor it rather than copying the pattern.

## 8. Gotchas

These are the Typer and Click surprises seen most.

1. **A Click plugin or custom `click.ParamType` stopped working in a Typer app.** Typer 0.26 vendors Click, so external Click objects are not Typer's Click. Use Typer's own parser options, or write the app in Click.
2. **The single command does not accept a subcommand name.** Typer collapses a one-command app. Add `@app.callback()` to force a group.
3. **`result.output` mixes stdout and stderr.** It contains both; assert on `result.stdout` or `result.stderr` instead.
4. **`list[str]` option is `None` when not given.** Typer passes `None` for an omitted repeatable option with a `None` default; normalise with `tags or []`.
