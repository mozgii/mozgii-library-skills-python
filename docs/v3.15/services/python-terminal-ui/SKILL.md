---
name: python-terminal-ui
description: Builds terminal output and full-screen text user interfaces in Python with rich and Textual - choosing plain print, rich or Textual, detecting a terminal and honouring NO_COLOR and FORCE_COLOR, rich Console on stdout and stderr, tables, progress bars and spinners, RichHandler for logging, rich tracebacks without local variables, Live displays, escaping markup in user text, Textual apps with compose, CSS and key bindings, reactive attributes with watch, validate and compute, workers for blocking work with call_from_thread, testing with run_test, Pilot and snapshots, and Windows terminal support. Use when adding colour, tables or progress bars to a Python script or CLI, making log output readable, building a TUI or terminal dashboard, testing a Textual app, or when output shows raw escape codes, broken brackets or garbage in a pipe or CI log.
metadata:
  tags:
  - python
  - rich
  - textual
  - terminal
  - tui
  - progress-bar
  - console
  - cli
---

# Terminal output and text UIs in Python

Most terminal programs need three things: readable results, progress for slow work, and clear errors. rich does all three for scripts and CLIs; Textual builds full-screen interactive apps (TUIs) on top of rich. Both must degrade to plain text when output goes to a pipe, a file or a CI log. This skill covers output and interfaces; argument parsing, exit codes and stdout conventions for CLIs are in the python-cli skill. Longer recipes are in [rich-recipes.md](references/rich-recipes.md) and [textual-apps.md](references/textual-apps.md).

## Step 0: read the installed versions

Read the Python version and the installed rich and Textual versions first; Textual in particular renames things between major versions.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
uv run python -c "import importlib.metadata as m; print('rich', m.version('rich'), '| textual', m.version('textual'))"
```

| Package | Version (October 2026) | Notes |
|---|---|---|
| rich | 15.0 | dropped Python 3.8; 14.3.4 made `import rich` faster with lazy loading |
| textual | 8.2 | needs Python 3.9 or later; 8.0 renamed `Select.BLANK` to `Select.NULL`, and `Select.BLANK` now reads as `False`; quit is `ctrl+q` |
| textual-dev | 1.8 | `textual run --dev` and `textual console`, development only |

## Choose print, rich or Textual

The output's reader decides the tool: another program needs plain text, a person watching a command needs readable formatting, and a person working in the terminal for minutes needs an interface.

| Situation | Use |
|---|---|
| output another program reads (JSON, CSV, one value per line) | plain `print` to stdout, no colour |
| a script or CLI a person runs: results, tables, progress, errors | rich |
| log lines for a person at a terminal | `logging` with `rich.logging.RichHandler` |
| an interactive app: lists to browse, forms, panes, key bindings | Textual |
| a server, a worker or anything under a supervisor | plain `logging`, no rich (see the python-logging skill) |

Keep data and decoration apart: data on stdout, progress and status on stderr. Then `tool | jq` works while the person still sees progress.

## Detect a terminal and honour NO_COLOR

Colour, animation and prompts make sense only when a person is watching a terminal. rich detects this itself, and environment variables override the detection.

| Variable | rich 15 | Python 3.13+ own colour (tracebacks, REPL; argparse from 3.14) |
|---|---|---|
| `NO_COLOR` (any non-empty value) | drops colours, keeps bold and italic | no colour |
| `FORCE_COLOR` (any non-empty value, even `0`) | treats output as a terminal | colour on, unless `NO_COLOR` is set |
| `TTY_COMPATIBLE=1` or `0` | forces terminal mode on or off, checked before `FORCE_COLOR` | not read |
| `PYTHON_COLORS=0` or `1` | not read | off or on, checked first |
| `TERM=dumb` | no cursor movement or animation | no colour |
| `COLUMNS`, `LINES` | override the detected width and height | not read |

Code that writes its own escape sequences checks the stream first:

```python
import os
import sys


def use_colour(stream=sys.stdout) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return stream.isatty() and os.environ.get("TERM") != "dumb"
```

A rich `Console` exposes the result as `console.is_terminal`, `console.is_interactive` and `console.color_system` (`None` when colour is off).

## Print with a rich Console

A `Console` writes styled text, tables and other renderables; create one for stdout (results) and one for stderr (status, progress, logs) and reuse them.

```python
from rich.console import Console
from rich.markup import escape

out = Console()                    # results on stdout
err = Console(stderr=True)         # progress, logs, warnings on stderr

out.print("[bold green]Saved[/] 3 files")
out.print(f"Opened {escape(user_path)}")          # user text with [brackets] stays literal
out.print(user_text, markup=False, highlight=False)
```

- Square brackets are markup: `[red]` changes style, and text such as `C:\data\[old]` or `list[int]` from users, files or exceptions can vanish or raise `MarkupError`. Escape it with `rich.markup.escape`, or print with `markup=False`.
- rich highlights numbers, strings and paths automatically; `highlight=False` turns that off for one call or the whole console.
- When output is not a terminal, rich writes plain text at a width of 80 columns (or `COLUMNS`), so tables stay readable in logs.
- `rich.print` replaces the built-in `print` for quick scripts; in a program keep explicit `Console` objects.

## Tables

`rich.table.Table` lays out columns that fit the terminal width, wrapping or truncating cells as configured.

```python
from rich.table import Table

table = Table(title="Releases")
table.add_column("Package", style="cyan", no_wrap=True)
table.add_column("Version", justify="right")
table.add_column("Status")
for name, version, ok in rows:
    table.add_row(escape(name), version, "[green]ok[/]" if ok else "[red]failed[/]")
out.print(table)
```

Rows that come from data still need `escape`. For output another program will parse, print CSV or JSON instead of a table.

## Progress bars and spinners

`rich.progress.track` wraps an iterable with a progress bar; `Progress` runs several bars with chosen columns; `Console.status` shows a spinner for work of unknown length. All of them belong on stderr.

```python
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeRemainingColumn, track

for item in track(items, description="Indexing", console=err):
    index(item)

with Progress(TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(), TimeRemainingColumn(),
              console=err, transient=True) as progress:
    task = progress.add_task("Download", total=size)
    for chunk in chunks:
        progress.update(task, advance=len(chunk))

with err.status("Connecting...", spinner="dots"):
    connect()
```

- `transient=True` removes the bar when it finishes.
- When stderr is not a terminal, rich prints the bar once at the end instead of animating it, so CI logs stay short; pass `disable=True` to hide it entirely.
- Print other output through the same console (`progress.console.print(...)`) while a bar is running, or lines interleave with the bar.

## Logging with RichHandler

`rich.logging.RichHandler` formats log records with time, level and source location in columns, for a person reading a terminal. Attach it once at the application's entry point.

```python
import logging

from rich.console import Console
from rich.logging import RichHandler

logging.basicConfig(
    level="INFO", format="%(message)s", datefmt="[%X]",
    handlers=[RichHandler(console=Console(stderr=True), rich_tracebacks=True, markup=False)],
)
logging.getLogger("app").info("cache warmed: %d entries", 42)
```

- `markup=False` is the default and should stay so: log messages hold data from outside, and brackets in it would be read as markup.
- `rich_tracebacks=True` renders `log.exception(...)` tracebacks with source context.
- Use a plain formatter (or JSON) when logs go to a file or a log collector; choosing handlers is the python-logging skill.

## Tracebacks

`rich.traceback.install()` replaces `sys.excepthook` so uncaught exceptions print with syntax-highlighted source around each frame.

```python
import click
from rich.traceback import install

install(show_locals=False, suppress=[click], max_frames=20)   # suppress library frames by module
```

Keep `show_locals=False` (the default) in anything that handles credentials or personal data: local variables printed in a traceback end up in terminals, screenshots and bug reports. Install it in the application's entry point only, never in a library.

## Live displays

`rich.live.Live` redraws a renderable in place, for a status board that updates while work runs.

```python
from rich.live import Live

with Live(build_table(jobs), console=err, refresh_per_second=4, transient=True) as live:
    while not all_done(jobs):
        poll(jobs)
        live.update(build_table(jobs))
```

A live display needs a terminal; when output is not a terminal rich prints only the final state. Once the board grows interactive (selection, scrolling, keys), move to Textual.

## Textual apps

A Textual app is a class that composes widgets, styles them with CSS and maps keys to actions. It runs full screen and gives the terminal back when it exits.

```python
from textual.app import App, ComposeResult
from textual.reactive import reactive
from textual.widgets import Button, Footer, Header, Label


class CounterApp(App[None]):
    CSS = """
    Screen { align: center middle; }
    #count { width: 20; content-align: center middle; text-style: bold; }
    """
    BINDINGS = [("plus", "increment", "Add one")]

    count = reactive(0)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(id="count")
        yield Button("+1", id="inc", variant="primary")
        yield Footer()

    def watch_count(self, old: int, new: int) -> None:
        self.query_one("#count", Label).update(f"Count: {new}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.action_increment()

    def action_increment(self) -> None:
        self.count += 1


if __name__ == "__main__":
    CounterApp().run()
```

- `compose` yields the widget tree; `query_one("#id", Type)` finds a widget.
- `BINDINGS` maps keys to `action_<name>` methods; the `Footer` shows them. `ctrl+q` quits; `ctrl+c` shows a hint instead of quitting, because it copies text in inputs.
- A reactive attribute calls `watch_<name>` when it changes, so the screen follows the state. `validate_<name>` and `compute_<name>` are in [textual-apps.md](references/textual-apps.md).
- Put larger stylesheets in a `.tcss` file and point `CSS_PATH` at it.

## Workers for blocking work

Textual runs on an asyncio event loop, so a blocking call in a handler freezes the whole interface. Run it in a worker: `@work(thread=True)` for blocking code, `@work` for `async` code.

```python
from textual import work
from textual.worker import get_current_worker


@work(thread=True, exclusive=True)
def run_checksum(self, n: int) -> None:
    worker = get_current_worker()
    result = slow_checksum(n)                       # blocking I/O or a C call
    if not worker.is_cancelled:
        self.call_from_thread(self.query_one("#log", Log).write_line, f"checksum({n}) = {result}")
```

- `exclusive=True` cancels the previous worker in the same group, so pressing a key twice does not run the job twice.
- Widgets are not thread-safe: from a thread worker, change the UI only through `self.call_from_thread(...)`.
- A thread cannot be killed: check `worker.is_cancelled` between steps so it stops soon after cancellation.

## Test a Textual app

`App.run_test()` runs the app headless and returns a `Pilot` that presses keys and clicks widgets; tests then assert on the app's state.

```python
import pytest

from counter_app import CounterApp


@pytest.mark.asyncio
async def test_button_and_key_increment() -> None:
    app = CounterApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.click("#inc")
        await pilot.press("plus")
        assert app.count == 2
```

After starting a worker, `await app.workers.wait_for_complete()` then `await pilot.pause()` lets its UI updates land. Snapshot tests with pytest-textual-snapshot compare rendered screens; they are in [textual-apps.md](references/textual-apps.md). For rich output, test against `Console(file=io.StringIO(), width=80)`.

## Windows terminal support

rich and Textual run on Windows, macOS and Linux, with a few differences on Windows.

- rich checks whether the Windows console supports virtual terminal (VT) sequences. Windows Terminal and current Windows 10 and 11 consoles do; on an old console without them, rich falls back to Win32 console calls with fewer colours (`legacy_windows`).
- Textual switches on VT processing itself. Its inline mode (`app.run(inline=True)`, the app drawn under the prompt) is not available on Windows; the app runs full screen instead.
- Python 3.13+ colours its own tracebacks on Windows only when the console supports VT sequences.
- Box-drawing and emoji need a UTF-8 capable terminal font; Windows Terminal has one by default, the legacy console may show `?`. Python 3.15 uses UTF-8 by default, so encoding errors on output are rarer there.

## Verify

Prove the output in the two places a terminal hides problems: a pipe and hostile text. `my-tool` stands for your command.

```bash
uv run my-tool 2>/dev/null | od -c | grep -c '033'     # expect 0: no escape codes on stdout in a pipe
NO_COLOR=1 uv run my-tool 2>&1 | od -c | grep -c '033 \[ 3'  # expect 0: no colour codes with NO_COLOR
uv run pytest -q                                        # Pilot tests, for a Textual app
```

Markup escaping is a unit test against a console that writes to a string:

```python
import io

from rich.console import Console
from rich.markup import escape


def test_outside_text_prints_literally() -> None:
    out = io.StringIO()
    console = Console(file=out, width=80)
    hostile = "[/oops] C:\\data\\[old] [red]"
    console.print(f"Opened {escape(hostile)}")
    assert hostile in out.getvalue() and "\x1b" not in out.getvalue()
```

Run a Textual app in a real terminal once: resize it, quit with `ctrl+q`, and check that the shell prompt comes back clean.

## Anti-patterns

| Instead of | Do |
|---|---|
| results printed through a rich table for scripts to parse | plain CSV or JSON on stdout |
| hand-written `\x1b[31m` codes | rich, or a check of `isatty` and `NO_COLOR` first |
| `force_terminal=True` in shipped code | let rich detect; `FORCE_COLOR` belongs to the person running it |
| `show_locals=True` in a program with secrets | `show_locals=False`, the default |
| `requests.get` in a Textual handler | `@work(thread=True)` and `call_from_thread` |
| `print` while a progress bar runs | `progress.console.print` |

## Checklist

Terminal output is right when every item holds.

- [ ] Data goes to stdout as plain text; progress, status and logs go to a stderr console.
- [ ] Output in a pipe or CI log has no escape codes and no animation; `NO_COLOR` turns colour off.
- [ ] Every piece of outside text printed with markup goes through `escape`, or markup is off.
- [ ] Progress bars and spinners use the same console as other output while they run.
- [ ] Tracebacks and log handlers never show local variables in programs that handle secrets.
- [ ] Textual handlers never block: slow work runs in a worker, UI changes from threads use `call_from_thread`.
- [ ] Textual apps have Pilot tests; key screens have snapshot tests.

## Gotchas

These are the terminal output failures seen most often.

1. **Text in square brackets disappears, or `MarkupError: closing tag ... doesn't match any open tag`.** Outside text was printed with markup on. Use `escape(text)` or `markup=False`.
2. **A log file or CI log is full of `\x1b[` codes.** Colour was forced (`FORCE_COLOR`, `force_terminal=True`) or escape codes were written by hand. Let rich detect the terminal, and check `isatty` in your own code.
3. **Progress bar lines are repeated or mixed with other output.** Something printed with plain `print` while the bar was live. Print through `progress.console`.
4. **A Textual app freezes while it loads data.** A blocking call ran in a handler. Move it into `@work(thread=True)` or make it async with `@work`.
5. **Odd redraws or errors after a thread worker updates the screen.** Textual objects are not thread-safe, and changing them from a thread gives unpredictable results. Use `self.call_from_thread` or `post_message`.
6. **A Pilot test fails because the screen has not updated yet.** The worker or refresh had not run. Await `app.workers.wait_for_complete()` and `pilot.pause()` before asserting.
7. **`if select.value == Select.BLANK` is never true, with no error.** Textual 8 renamed the empty value to `Select.NULL`, and `Select.BLANK` still resolves to the inherited `Widget.BLANK`, which is `False`, so old comparisons fail silently. Use `Select.NULL`.

## References

The reference files hold the longer recipes.

- [rich-recipes.md](references/rich-recipes.md): Console options, markup and escaping, themes, panels, trees, syntax, Markdown and JSON, progress for files and several tasks, prompts, logging with dictConfig, and testing rich output
- [textual-apps.md](references/textual-apps.md): app layout and CSS files, the @on decorator and messages, screens and modal dialogs, reactive validate and compute, async and thread workers, Pilot tests, snapshot tests, devtools, inline mode and suspending the app

## See also

- `python-cli`: arguments, exit codes and stdout conventions
- `python-logging`: handlers and formats away from a terminal
- `pytest-testing`: fixtures and async tests around Pilot
- `python-cross-platform`: terminals and encodings on Windows
