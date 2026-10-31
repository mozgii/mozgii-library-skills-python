---
title: rich recipes for Python terminal output
author: MOZGII, Inc.
lang: en
---

# rich recipes for Python terminal output

Longer recipes for rich 15: the Console options that matter, markup and escaping, themes, the common renderables (panels, trees, code, Markdown, JSON), progress for files and several tasks, prompts, logging through `dictConfig`, and testing what a program prints. Every snippet here was run with rich 15.0.0.

## Contents

This reference has 8 sections.

1. Console options
2. Markup, styles and escaping
3. Themes for consistent colours
4. Panels, trees, code, Markdown and JSON
5. Progress for files and several tasks
6. Prompts and confirmations
7. Logging configuration with dictConfig
8. Testing rich output

## 1. Console options

`rich.console.Console` takes its decisions from the stream and the environment; constructor options override them for tests and special cases.

| Option | Default | Use |
|---|---|---|
| `stderr=True` | stdout | status, progress and logs on stderr |
| `file=` | `sys.stdout` | write to a file or `io.StringIO` |
| `width=`, `height=` | detected, else `COLUMNS`/`LINES`, else 80 x 25 | fixed layout in tests and logs |
| `force_terminal=` | auto | `True` or `False` to override terminal detection |
| `force_interactive=` | auto | animation and live updates on or off (`TTY_INTERACTIVE` env var) |
| `color_system=` | `"auto"` | `None`, `"standard"`, `"256"` or `"truecolor"` |
| `no_color=` | `NO_COLOR` set | strip colours, keep bold, italic and underline |
| `highlight=` | `True` | automatic highlighting of numbers, strings, paths |
| `markup=` | `True` | interpret `[style]` markup |
| `record=` | `False` | keep everything printed for `export_text` and `export_html` |
| `theme=` | default theme | named styles (section 3) |

`console.is_terminal`, `console.is_interactive`, `console.color_system`, `console.width` and `console.is_dumb_terminal` report what rich decided.

## 2. Markup, styles and escaping

Console markup is BBCode-like: `[bold red]text[/]` styles text, `[/]` closes the last tag, and `[link=https://example.com]docs[/link]` makes a terminal hyperlink. The danger is outside text that happens to contain brackets.

```python
from rich.console import Console
from rich.markup import escape
from rich.text import Text

console = Console()
user_text = "[red]not markup[/red] and C:\\path\\[x]"

console.print(f"Saved {escape(user_text)}")          # escaped: printed exactly as given
console.print(user_text, markup=False)                # markup off for this call
console.print(Text("literal [brackets] kept"))        # a Text object is never parsed as markup
console.print("a.b(c)", highlight=False)              # no automatic colouring
```

- An unmatched closing tag raises `MarkupError: closing tag '[/red]' at position 5 doesn't match any open tag`. A bracketed word that looks like a style name (`[bold]`) silently disappears.
- `escape` also handles a trailing backslash before a bracket, as in Windows paths.
- Build styled output from `Text` objects (`Text.assemble(("ok", "green"), " ", name)`) when most of a line is data; then nothing needs escaping.
- Styles combine: `"bold underline #ff8800 on grey11"`.

## 3. Themes for consistent colours

A `Theme` names styles once, so code says what text means (`"warning"`) and the colour lives in one place.

```python
from rich.console import Console
from rich.theme import Theme

theme = Theme({"info": "cyan", "warning": "magenta", "danger": "bold red"})
console = Console(theme=theme)
console.rule("[info]Report")
console.print("Disk almost full", style="danger")
console.print("[warning]3 files skipped[/warning]")
```

Pick colours that read on both dark and light terminal backgrounds, and never carry meaning by colour alone: `NO_COLOR` users and screen readers see only the words.

## 4. Panels, trees, code, Markdown and JSON

rich ships renderables for the common shapes of terminal output. This example draws one of each.

```python
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.tree import Tree

console.print(Panel.fit("Saved 3 files", title="sync", border_style="green"))

tree = Tree("project")
tree.add("src").add("app.py")
console.print(tree)

console.print(Syntax("def f(x):\n    return x * 2\n", "python", line_numbers=True))
console.print(Markdown("# Title\n\n- one\n- two"))
console.print_json('{"ok": true, "n": [1, 2]}')
```

- `Panel.fit` sizes the box to its content; `Panel(...)` fills the width.
- `Syntax.from_path("app.py")` guesses the language from the file name.
- `print_json` takes a JSON string (or `data=` for a Python object) and pretty-prints it; for machine-readable output print `json.dumps` with plain `print` instead.
- `rich.inspect(obj, methods=True)` prints an object's attributes, handy in a REPL, not in program output.

## 5. Progress for files and several tasks

`rich.progress.open` and `wrap_file` show progress while a file is read; a `Progress` with several tasks shows parallel work.

```python
from rich.console import Console
from rich.progress import (BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn,
                           TransferSpeedColumn, open as progress_open)

err = Console(stderr=True)

with progress_open("big.bin", "rb", description="Reading", console=err) as f:
    data = f.read()

columns = (TextColumn("{task.description}"), BarColumn(), DownloadColumn(), TransferSpeedColumn(), TimeRemainingColumn())
with Progress(*columns, console=err) as progress:
    tasks = {name: progress.add_task(name, total=size) for name, size in downloads.items()}
    for name, chunk in receive_chunks():
        progress.update(tasks[name], advance=len(chunk))
```

- `progress.add_task(name, total=None)` shows an indeterminate pulse until a total is known; `progress.update(task, total=n)` sets it later.
- `Progress` is thread-safe for `update` and `advance`, so worker threads can report into one display.
- `progress.console.print(...)` prints above the bars without breaking them.
- When stderr is not a terminal, rich prints the final state once; pass `disable=not err.is_terminal` to print nothing at all.

## 6. Prompts and confirmations

`rich.prompt` asks a question, validates the answer and repeats until it is valid. Prompts need a person at a terminal, so check `console.is_interactive` first and take a flag (`--yes`) for scripts.

```python
from rich.prompt import Confirm, IntPrompt, Prompt

name = Prompt.ask("Name", default="world")
count = IntPrompt.ask("How many copies")                    # repeats "Please enter a valid integer number"
env = Prompt.ask("Environment", choices=["dev", "prod"], default="dev")
token = Prompt.ask("Token", password=True)                  # input not echoed
if Confirm.ask("Delete 3 files?", default=False):
    delete_files()
```

- Every `ask` accepts `console=` and `stream=`, so tests can feed answers from `io.StringIO("Ana\n")`.
- `password=True` hides the input but the value is still a normal string in memory; do not log it.
- Prompts go to the console's stream, so a prompt on a stderr console keeps stdout clean.

## 7. Logging configuration with dictConfig

`RichHandler` works in `logging.config.dictConfig` through the `"()"` factory key, which lets one configuration choose rich output for terminals and plain or JSON output elsewhere.

```python
import logging.config
import sys

from rich.console import Console

handler = (
    {"()": "rich.logging.RichHandler", "rich_tracebacks": True, "show_path": False, "console": Console(stderr=True)}
    if sys.stderr.isatty()
    else {"class": "logging.StreamHandler", "stream": "ext://sys.stderr", "formatter": "plain"}
)
logging.config.dictConfig({
    "version": 1,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": handler},
    "root": {"level": "INFO", "handlers": ["console"]},
})
```

| `RichHandler` option | Default | Effect |
|---|---|---|
| `markup` | `False` | keep it off: messages carry outside data |
| `rich_tracebacks` | `False` | render `log.exception` tracebacks with source context |
| `tracebacks_show_locals` | `False` | keep it off where secrets may be in scope |
| `show_path` | `True` | file and line column on the right |
| `show_time`, `omit_repeated_times` | `True`, `True` | time column, blank when unchanged |

The `"()"` form passes every other key, including a `Console` object, to the handler's constructor. Log levels, `extra` fields and handler design are in the python-logging skill.

## 8. Testing rich output

rich output is tested by giving the code a `Console` that writes to memory with a fixed width and no colour, then asserting on the text.

```python
import io

from rich.console import Console
from rich.table import Table


def render_report(console: Console, rows: list[tuple[str, int]]) -> None:
    table = Table("name", "count")
    for name, count in rows:
        table.add_row(name, str(count))
    console.print(table)


def test_report_text() -> None:
    buffer = io.StringIO()
    render_report(Console(file=buffer, width=40, color_system=None), [("apples", 3)])
    assert "apples" in buffer.getvalue()


def test_capture() -> None:
    console = Console(width=40)
    with console.capture() as capture:
        console.print("[bold]hi[/]")
    assert capture.get() == "hi\n"


def test_record_export() -> None:
    console = Console(record=True, width=40, file=io.StringIO())
    console.print("[red]error[/]")
    assert console.export_text(clear=False) == "error\n"
    assert "<span" in console.export_html()
```

- Pass the console into the code under test instead of using a module-level one; tests then never touch the real terminal.
- `export_text()` and `export_html()` clear the record by default; pass `clear=False` to export twice.
- `export_svg()` writes a picture of the output for documentation.
- Fix `width` in tests, or tables wrap differently on each machine.
