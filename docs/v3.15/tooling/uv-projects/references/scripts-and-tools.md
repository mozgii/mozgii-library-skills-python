---
title: uv scripts and tools
author: MOZGII, Inc.
lang: en
---

# uv scripts and tools

Single-file scripts that declare their own dependencies (PEP 723 inline metadata), and command-line tools run or installed with `uvx` and `uv tool`. Neither needs a project or a manually created virtual environment.

## Contents

This reference has 6 sections.

1. Inline script metadata (PEP 723)
2. Running and locking a script
3. Executable scripts with a shebang
4. Running tools with uvx
5. Installing tools with uv tool
6. Gotchas

## 1. Inline script metadata (PEP 723)

Inline script metadata is a TOML comment block at the top of a `.py` file that lists the Python version and dependencies the script needs.

```bash
uv init --script fetch.py --python 3.13     # create the file with an empty block
uv add --script fetch.py httpx rich         # add dependencies to the block
uv remove --script fetch.py rich
```

The block uv writes:

```python
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx>=0.28.1",
# ]
# ///
import httpx

print(httpx.get("https://pypi.org/pypi/uv/json").json()["info"]["version"])
```

- The block is standard (PEP 723), so pipx, hatch and other runners read it too.
- A script with a block ignores the surrounding project: it gets its own cached environment.
- `[tool.uv]` settings (indexes, `exclude-newer`) can go inside the block too.

## 2. Running and locking a script

`uv run` builds a cached environment for the script's block and runs it.

```bash
uv run fetch.py                          # environment from the block, cached by its content
uv run --with pandas analyse.py          # extra packages for this run only
uv run --python 3.12 fetch.py            # another interpreter
uv lock --script fetch.py                # writes fetch.py.lock next to the script
uv tree --script fetch.py
uv export --script fetch.py --format requirements.txt
```

- A script without a block, run with `uv run`, uses the project environment when it is inside a project, and a bare interpreter otherwise.
- Since uv 0.12, `uv run path/to/script.py` discovers the project relative to the script, not the current directory.
- Lock scripts that run unattended (cron, CI): `fetch.py.lock` freezes the versions until you run `uv lock --script` again.

## 3. Executable scripts with a shebang

A shebang that calls `uv run --script` makes the file runnable directly on macOS and Linux.

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["typer>=0.27"]
# ///
import typer

typer.run(lambda name: print(f"hello {name}"))
```

```bash
chmod +x hello.py && ./hello.py world
```

`env -S` splits the arguments; it exists in macOS and GNU coreutils 8.30+. Windows ignores shebangs: run `uv run hello.py` there.

## 4. Running tools with uvx

`uvx` (short for `uv tool run`) runs a command from a package in a temporary, cached environment.

```bash
uvx ruff check .                         # newest ruff
uvx ruff@0.17.3 check .                  # exact version
uvx --from 'ruff==0.17.3' ruff --version  # --from when the command and package differ, or to add a constraint
uvx --with mkdocs-material mkdocs serve  # a tool plus a plugin
uvx --python 3.14 pyright --version
```

- Use `uvx` for tools that do not need the project's packages (formatters, release helpers). Tools that import the project (pytest, pyright, mypy) run with `uv run` from the dev group, so they see the installed code.
- `uvx` names the command; when the package name differs, use `--from <package>`.
- `uvx` downloads and runs a PyPI package by name, so a typo runs whatever owns that name. Check an unfamiliar name on PyPI first, and pin a version (`uvx ruff@0.17.3`) in scripts and CI.

## 5. Installing tools with uv tool

`uv tool install` puts a tool's commands on `PATH` permanently, each in its own environment.

```bash
uv tool install ruff                     # commands go to ~/.local/bin (uv tool dir --bin)
uv tool install 'pre-commit>=4' --with pre-commit-uv
uv tool list --outdated
uv tool upgrade --all
uv tool uninstall ruff
uv tool update-shell                     # add the bin dir to PATH in shell config
```

- Each tool has an isolated environment, so tools never conflict with each other or with projects.
- Since uv 0.10, `uv tool install` and `uv tool run` honour a global Python pin (`uv python pin --global`).

## 6. Gotchas

These are the script and tool behaviours that surprise people.

1. **The script runs with the wrong packages.** It has no inline block and sits inside a project, so it used the project's environment. Add a block with `uv init --script` or `uv add --script`.
2. **`uvx pytest` cannot import the project.** `uvx` uses an isolated environment. Run `uv run pytest`.
3. **A shebang script fails on Linux with "No such file or directory".** An `env` without `-S` (BusyBox, very old distributions) passes `uv run --script` as one word. Call `uv run hello.py` explicitly there.
4. **A script's dependencies drift.** Unlocked scripts resolve the newest versions each time the block changes. Lock them with `uv lock --script`, or set `exclude-newer` in the block.
