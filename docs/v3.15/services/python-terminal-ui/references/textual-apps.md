---
title: Textual apps in Python
author: MOZGII, Inc.
lang: en
---

# Textual apps in Python

How to build and test a Textual 8 application: layout and CSS files, events and custom messages, screens and modal dialogs, reactive attributes with validation and computed values, workers for slow work, Pilot and snapshot tests, the devtools, inline mode and handing the terminal to another program. Every example ran with Textual 8.2.8 under its headless test driver.

## Contents

This reference has 9 sections.

1. Layout and CSS
2. Events, the @on decorator and custom messages
3. Screens and modal dialogs
4. Reactive attributes: watch, validate, compute, recompose
5. Workers in depth
6. Tables with DataTable
7. Pilot and snapshot tests
8. Devtools and logging
9. Inline mode, suspending and Windows

## 1. Layout and CSS

Textual lays widgets out with containers (`Horizontal`, `Vertical`, `Grid`, `VerticalScroll` in `textual.containers`) and styles them with a CSS dialect (Textual CSS). Small apps put it in the `CSS` class variable; larger ones keep it in a `.tcss` file named by `CSS_PATH`, a path relative to the module.

```python
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Log


class JobsApp(App[None]):
    CSS_PATH = "jobs.tcss"

    def compose(self) -> ComposeResult:
        with Horizontal(id="actions"):
            yield Button("Run", id="run", variant="primary")
            yield Button("Stop", id="stop", variant="error")
        yield Log(id="log")
```

The stylesheet beside it:

```css
/* jobs.tcss */
#actions { height: auto; dock: top; }
Button { margin: 0 1; }
Log { border: round $accent; height: 1fr; }
```

- Selectors work as in web CSS: type (`Button`), id (`#run`), class (`.item`), pseudo-classes (`:focus`, `:hover`).
- Units: cells (`20`), percentages, `fr` fractions of remaining space, `auto`.
- Theme variables (`$accent`, `$error`, `$primary`, `$surface`) follow the active theme, so colours stay readable when the person switches themes.
- A reusable widget ships its own styles in `DEFAULT_CSS`; app CSS overrides them.
- `textual run --dev app.py` (from textual-dev) reloads the CSS file on save.

## 2. Events, the @on decorator and custom messages

Widgets talk upward by posting messages. A handler is either a method named `on_<widget>_<message>` or any method decorated with `@on(Message, "selector")`, which also filters by the sending widget.

```python
from textual import on
from textual.app import App, ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Label


class Uploader(Widget):
    class Finished(Message):
        def __init__(self, name: str, ok: bool) -> None:
            super().__init__()
            self.name = name
            self.ok = ok

    def compose(self) -> ComposeResult:
        yield Button("Upload", id="upload")

    @on(Button.Pressed, "#upload")
    def upload(self) -> None:
        self.post_message(self.Finished("report.pdf", ok=True))


class UploadApp(App[None]):
    def compose(self) -> ComposeResult:
        yield Uploader()
        yield Label(id="status")

    @on(Uploader.Finished)
    def finished(self, event: Uploader.Finished) -> None:
        self.query_one("#status", Label).update(f"{event.name}: {'ok' if event.ok else 'failed'}")
```

- Messages bubble from the widget to its parents up to the app; `event.stop()` ends the bubbling.
- A custom message is a nested `Message` subclass, so its handler name follows the widget (`on_uploader_finished`).
- `post_message` is thread-safe, so a thread worker can report through a message as well as through `call_from_thread`.
- `on_mount` runs once after the widget is on screen: load data or start timers there, not in `__init__`.

## 3. Screens and modal dialogs

A screen is a full layer of widgets; a `ModalScreen[T]` is drawn over the current screen and returns a value of type `T` with `dismiss`.

```python
from textual import on, work
from textual.containers import Grid
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmDelete(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmDelete { align: center middle; }
    #dialog { grid-size: 2; grid-gutter: 1 2; padding: 1 2; width: 50; height: auto; border: thick $error; }
    #question { column-span: 2; }
    """

    def __init__(self, title: str) -> None:
        super().__init__()
        self.task_title = title

    def compose(self) -> ComposeResult:
        with Grid(id="dialog"):
            yield Label(f"Delete {self.task_title!r}?", id="question")
            yield Button("Delete", variant="error", id="yes")
            yield Button("Cancel", id="no")

    @on(Button.Pressed, "#yes")
    def confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def cancel(self) -> None:
        self.dismiss(False)
```

The caller receives the result either through a callback or by awaiting it inside a worker:

```python
def ask_with_callback(self) -> None:
    def done(answer: bool | None) -> None:
        if answer:
            self.delete_selected()
    self.push_screen(ConfirmDelete("Buy milk"), done)


@work
async def action_delete(self) -> None:
    if await self.push_screen_wait(ConfirmDelete("Buy milk")):
        self.delete_selected()
```

- `push_screen_wait` must run in a worker (`@work`), because it waits for the dialog without blocking the event loop.
- `self.app.pop_screen()` closes a screen without a result; `switch_screen` replaces the current one.
- Since Textual 8, `push_screen(..., mode=...)` can push to a mode that is not active.

## 4. Reactive attributes: watch, validate, compute, recompose

A `reactive` class attribute turns an assignment into an update: Textual calls the matching hooks and repaints. Four hooks exist per attribute.

```python
from textual.reactive import reactive


class TasksApp(App[None]):
    tasks: reactive[list[str]] = reactive(list)       # a callable default makes a fresh list per instance
    filter_text = reactive("")
    shown = reactive(0)

    def validate_filter_text(self, value: str) -> str:   # runs before the value is stored
        return value.strip().lower()

    def compute_shown(self) -> int:                      # recomputed when the attributes it reads change
        return sum(1 for t in self.tasks if self.filter_text in t.lower())

    def watch_shown(self, shown: int) -> None:           # runs after the value changed
        self.query_one("#status", Label).update(f"{shown} shown")
```

| `reactive(...)` option | Effect |
|---|---|
| `recompose=True` | rebuild the widget's children by calling `compose` again on every change |
| `layout=True` | recalculate layout, for values that change size |
| `init=False` | do not call the watcher with the initial value at mount |
| `always_update=True` | call the watcher even when the new value equals the old |
| `bindings=True` | refresh the key bindings (and the Footer) on change |

- Watchers take `(self)`, `(self, new)` or `(self, old, new)`.
- Mutating a list in place (`self.tasks.append(x)`) does not trigger anything; assign a new list (`self.tasks = [*self.tasks, x]`) or call `self.mutate_reactive(TasksApp.tasks)`.
- `recompose=True` is the simplest way to render a list of children from state; for long lists prefer a `DataTable` or `ListView` and update rows.

## 5. Workers in depth

A worker runs a function concurrently with the UI. `@work` on an `async def` runs it as an asyncio task; `@work(thread=True)` on a plain `def` runs it in a thread for blocking code.

```python
import asyncio

from textual import work
from textual.worker import Worker, WorkerState, get_current_worker


@work(exclusive=True, group="load")
async def load_tasks(self) -> None:
    await asyncio.sleep(0.05)                       # an async HTTP call in a real app
    self.tasks = ["Write report", "Buy milk", "Call Ana"]


@work(thread=True, exclusive=True)
def run_checksum(self, n: int) -> None:
    worker = get_current_worker()
    result = slow_checksum(n)
    if not worker.is_cancelled:
        self.call_from_thread(self.query_one("#log", Log).write_line, f"checksum({n}) = {result}")


def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
    if event.state == WorkerState.ERROR:
        self.notify(f"{event.worker.name} failed", severity="error")
```

- States are `PENDING`, `RUNNING`, `CANCELLED`, `ERROR` and `SUCCESS`; `Worker.StateChanged` messages report each change.
- `exit_on_error=True` (the default) exits the app when a worker raises. Set it to `False` and handle `ERROR` for work that may fail, such as a network call.
- `exclusive=True` cancels running workers in the same `group` before starting the new one.
- `self.run_worker(coro_or_fn, thread=..., exclusive=...)` starts a worker without the decorator.
- CPU-heavy pure-Python work in a thread still competes for the GIL and makes the UI sluggish; run it in a process pool (see the python-subprocess skill) and post the result back.

## 6. Tables with DataTable

`DataTable` shows rows and columns with a cursor, sorting and fast updates for thousands of rows.

```python
from textual.widgets import DataTable


def on_mount(self) -> None:
    table = self.query_one(DataTable)
    table.cursor_type = "row"
    table.add_columns("Task", "Due")
    for task in tasks:
        table.add_row(task.title, task.due, key=task.id)


def selected_id(self) -> str | None:
    table = self.query_one(DataTable)
    if table.row_count == 0:
        return None
    return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
```

- Give each row a `key` so updates and deletions do not depend on the row's position.
- `table.clear()` removes rows but keeps columns; `clear(columns=True)` removes both.
- `DataTable.RowSelected` is posted when the person presses Enter on a row.

## 7. Pilot and snapshot tests

`App.run_test()` runs the app with the headless driver at 80 x 24 cells by default and yields a `Pilot`. Tests drive it with keys and clicks, then assert on the app's state.

```python
import pytest

from tasks_app import TasksApp


@pytest.mark.asyncio
async def test_filter_and_delete() -> None:
    app = TasksApp()
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()          # the loading worker finished
        await pilot.pause()                            # its UI updates were processed
        assert app.query_one("DataTable").row_count == 3
        await pilot.click("#filter")
        await pilot.press(*"milk")                     # one key per character
        await pilot.pause()
        assert app.shown == 1
        await pilot.press("tab", "d")
        await pilot.pause()
        await pilot.click("#yes")                      # the modal dialog's button
        await pilot.pause()
        assert app.tasks == ["Write report", "Call Ana"]
```

- `Pilot` also has `hover`, `double_click`, `resize_terminal` and `wait_for_animation`.
- `run_test(size=(120, 40))` tests another terminal size; `notifications=True` turns notifications on, which tests leave off by default.
- Tests need an async test runner: pytest-asyncio (`@pytest.mark.asyncio`) or anyio's plugin.

Snapshot tests with pytest-textual-snapshot (1.1) render the app to SVG and compare it with a stored file, catching layout and style regressions that state assertions miss.

```python
from pathlib import Path

APP = Path(__file__).with_name("counter_app.py")


def test_counter_looks_right(snap_compare) -> None:
    assert snap_compare(APP, press=["plus", "plus"], terminal_size=(80, 24))
```

Run `pytest --snapshot-update` once to write the snapshots into `__snapshots__/`, review them, and commit them. A failing snapshot writes an HTML report (`snapshot_report.html` by default) showing both versions.

## 8. Devtools and logging

The textual-dev package (1.8) adds a development console. Run `textual console` in one terminal and `textual run --dev app.py` in another; the console shows `self.log(...)` output, `print` calls and events while the app owns the screen.

- `self.log("loaded", count=n)` and `self.log.error(...)` go to the devtools console, not the screen.
- `TEXTUAL_LOG=/path/to/file.log` appends logs to a file when the devtools console is not running.
- `textual keys` shows the key names the terminal sends, useful when a binding does not fire.
- `textual diagnose` prints the environment (versions, terminal, colour support) for bug reports.

## 9. Inline mode, suspending and Windows

`app.run(inline=True)` draws the app under the shell prompt instead of taking the whole screen, which suits small pickers and prompts. Inline mode is not available on Windows: there the app runs full screen. `inline_no_clear=True` keeps the final output on screen after exit.

`with self.suspend():` hands the terminal back to another program, for example the person's editor, and resumes the app afterwards.

```python
import os
import subprocess


def action_edit(self) -> None:
    editor = os.environ.get("EDITOR", "notepad" if os.name == "nt" else "vi")
    with self.suspend():
        subprocess.run([editor, str(self.note_path)], check=False)
    self.reload_note()
```

- Suspending works on Unix-like systems and on Windows; it raises `SuspendNotSupported` where the driver cannot do it, such as Textual Web.
- Textual quits with `ctrl+q`. `ctrl+c` copies selected text in inputs and otherwise shows "Press ctrl+q to quit".
- `NO_COLOR` turns a Textual app monochrome.
- On Windows, Textual turns on virtual terminal processing itself. Windows Terminal gives the best results; the legacy console has fewer colours and fonts.
