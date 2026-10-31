---
name: ty
description: Type checks Python with ty, Astral's Rust type checker and language server - deciding whether ty fits a project today, running it through uv, rule levels and the [tool.ty] configuration, per-path overrides, suppression with ty ignore comments, the type ignore comments it also honours, --add-ignore and baselines for adopting it on old code, what replaces mypy strict and Pyright strict, ANN rules in Ruff for missing annotations, exit codes, CI output formats, the pre-commit hook, editors and monorepos. Use when adding or configuring ty, moving from mypy or Pyright, reading a ty diagnostic, or when ty check, uv check or uvx ty reports something unexpected.
metadata:
  tags:
  - python
  - ty
  - typing
  - type-checker
  - uv
  - mypy
  - pyright
  - language-server
---

# Type checking with ty

ty is a type checker and language server for Python from the team behind uv and Ruff, written in Rust and fast enough to run on every save. It is in beta on `0.0.x` versions, which changes how you adopt it: the speed is real, and any release may add or change a diagnostic. This skill decides whether ty fits a project, sets it up so upgrades stay deliberate, and shows how to bring an old codebase in without drowning in findings.

## Step 0: read the installed version and the project's setup

Advice about ty goes stale within weeks, so start from what the project runs. These commands show the version, which Python ty will target and whether another checker is already the gate.

```bash
uv run ty version 2>/dev/null || uvx ty version     # e.g. ty 0.0.95
grep -n "requires-python" pyproject.toml
grep -nE '^\[tool\.(ty|mypy|pyright)\]' pyproject.toml; ls ty.toml pyrightconfig.json mypy.ini 2>/dev/null
uv run ty explain rule invalid-return-type          # the installed docs for one rule, with its default level
```

Pin ty exactly (`ty==0.0.95`), because a `0.0.x` update can turn a green build red with no code change. ty 0.0.95 ships 140 rules: 97 default to `error`, 25 to `warn` and 18 are off. Read today's numbers with `ty explain rule --output-format json`.

## Should this project use ty

ty suits fast local feedback today and can gate CI on projects that accept its pace of change. Choose by what the project already depends on.

| Situation | Choice | Why the other loses here |
|---|---|---|
| New project, plain Python, uv | ty as the gate, pinned | mypy and Pyright work too, but Astral measures ty at 10 to 100 times their speed |
| Large codebase that wants faster editor feedback | ty in the editor, existing checker stays the CI gate | switching the gate at once means new findings and lost plugins together |
| Relies on mypy plugins (Django stubs, custom plugins) | stay on mypy | ty has no plugin system and none is planned; it has built-in Pydantic support |
| Library promising types to users | keep mypy or Pyright as the gate, add ty as advice | users run those checkers, so those are the results that must be clean |
| Needs a stable output contract (a compliance report, a parser of diagnostics) | Pyright or mypy | ty has no stable API before 1.0, and diagnostics may change between any two versions |
| `reportUnknown*` style checks are the point | Pyright strict | ty has no rule for unknown types yet; it treats them like `Any` |

Running two checkers as gates costs more than it finds, because they disagree on edge cases. Pick one gate; the other is advice.

## Run ty

ty runs from the project environment, so third-party imports resolve and the version is the pinned one.

```bash
uv add --dev "ty==0.0.95"
uv run ty check                       # the project, from the configured source roots
uv run ty check src tests             # explicit paths; these are checked even if excluded
uv run ty check --watch               # recheck on save
uv run ty check --python-version 3.12 --python-platform all   # a target other than this machine
uvx ty check                          # a quick look at a project without adding it
uv check                              # uv's own command: syncs the project, then runs ty (experimental in uv 0.13)
```

`uv check` accepts `--ty-version`, `--fix` and uv's group and package flags, which makes it the shortest route inside a workspace. It prints an experimental warning; `--preview-features check-command` silences it.

ty finds its target Python in this order: `--python-version` or `python-version` in config, the lower bound of `requires-python`, the metadata of the virtual environment, then the newest stable version it knows.

## Worked example: bring a project under ty

This walkthrough adds ty to a small order service and ends with a strict, clean check. Start with the configuration, which lives in `pyproject.toml` under `[tool.ty]`, or in `ty.toml` without that prefix. A `ty.toml` next to a `pyproject.toml` wins and the `[tool.ty]` table is ignored.

```toml
[dependency-groups]
dev = ["ty==0.0.95", "pytest>=9"]

[tool.ty.environment]
python-version = "3.12"                       # the oldest version the code must run on

[tool.ty.src]
include = ["src", "tests"]
exclude = ["generated"]                       # gitignore-style patterns; directories like .venv are excluded already

[tool.ty.rules]
missing-type-argument = "error"               # off by default: bare `list` in a signature
unsound-return-statement = "error"            # off by default: returning a value typed Any from a typed function
possibly-unresolved-reference = "warn"        # off by default: a name bound on only some paths
unused-ignore-comment = "error"               # default warn: an ignore comment that no longer does anything

[[tool.ty.overrides]]
include = ["tests/**"]

[tool.ty.overrides.rules]
missing-type-argument = "ignore"              # helpers in tests may stay loose
```

The code below has two bugs a type checker should find: `label` calls a method on a value that can be `None`, and `count` takes a bare `list`.

```python
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Line:
    sku: str
    qty: int
    price: Decimal


def total(lines: Iterable[Line]) -> Decimal:
    return sum((line.qty * line.price for line in lines), Decimal(0))


def first_sku(lines: list[Line]) -> str | None:
    return lines[0].sku if lines else None


def label(lines: list[Line]) -> str:
    return first_sku(lines).upper()


def count(lines: list) -> int:
    return len(lines)
```

`--output-format concise` prints one finding per line, which suits scripts and agents:

```text
$ uv run ty check --output-format concise
src/orders/__init__.py:22:12: error[unresolved-attribute] Attribute `upper` is not defined on `None` in union `str | None`
src/orders/__init__.py:25:18: error[missing-type-argument] Missing type argument for generic class `list` (expected 1 type argument)
Found 2 diagnostics
```

Fix the causes rather than the messages: `(first_sku(lines) or "").upper()` handles the empty case, and `lines: list[Line]` states the element type. `uv run ty check` then prints `All checks passed!` and exits 0.

## Rule levels and strictness

Each rule is `error`, `warn` or `ignore`, set per rule in config or per run with `--error`, `--warn` and `--ignore`; later flags override earlier ones.

```bash
uv run ty explain rule                                 # every rule with its level and documentation
uv run ty check --ignore redundant-cast --warn deprecated
```

ty has no `--strict` flag, and it already does what the other checkers call strict: it checks the bodies of unannotated functions and infers `[1, "a"]` as a list of both types. To tighten it, turn on the rules that are off by default. `all = "error"` or `--error all` turns on every rule, including the noisy ones, so it is rarely what you want.

| Goal | Setting |
|---|---|
| close to `mypy --strict` or Pyright strict | `missing-type-argument`, `unsound-return-statement`, `dynamic-function-decorator-return` at `error`, `possibly-unresolved-reference` at `warn`, plus Ruff's `ANN` and `PYI` |
| stricter still | add `blanket-ignore-comment`, `unsound-assignment` and `unsound-yield` at `error`, and `strict-equality-semantics` and `strict-generic-narrowing` under `[tool.ty.analysis]` |
| known false-positive sources, on at your own risk | `division-by-zero`, `possibly-missing-attribute`, `possibly-missing-import` at `warn` |

ty never reports a missing annotation: it infers what it can and treats the rest as `Unknown`. Requiring annotations is Ruff's job, so add `ANN` to `extend-select` (`disallow_untyped_defs` in mypy, `reportMissingParameterType` in Pyright).

## Exit codes and warnings

ty exits 1 on warnings as well as errors by default, which surprises people coming from other tools.

| Exit code | Means |
|---|---|
| 0 | no finding at `warn` or above |
| 1 | at least one finding at `warn` or above |
| 2 | bad options, bad configuration or an I/O error |
| 101 | an internal error in ty |

`--exit-zero-on-warning` (or `error-on-warning = false` under `[tool.ty.terminal]`) lets warnings pass, and `--exit-zero` never fails. `--error-on-warning` only restates the default, so it adds no strictness.

## Suppress one finding

A suppression names the rule and sits on the first or last line of the finding; everything else about the line stays checked.

```python
total([Line("a", "2", Decimal("1.50"))])  # ty: ignore[invalid-argument-type]   checks that bad input raises
save(record, retries=3)  # ty: ignore[unknown-argument, invalid-argument-type]
value = compute()  # ty: ignore[invalid-assignment]  # fmt: skip
```

- On its own line before any code, `# ty: ignore[rule]` covers the whole file.
- `# type: ignore` also suppresses ty, for everything on the line. `# type: ignore[arg-type, ty:invalid-argument-type]` carries a mypy code and a ty rule together; codes without `ty:` mean nothing to ty.
- `respect-type-ignore-comments = false` under `[tool.ty.analysis]` stops ty honouring `type: ignore`, which helps when another checker's comments hide real findings.
- `@typing.no_type_check` silences a whole function; it is not supported on a class.
- Keep `unused-ignore-comment` on, and `uv run ty check --fix` deletes the comments it reports.

## Adopt ty on old code

A backlog should never block the gate. Pick one of two routes.

```bash
uv run ty check --add-ignore            # writes a ty: ignore[rule] comment for each current finding
uv run ty check --update-baseline       # preview: records findings in the file named by baseline = "ty-baseline.json"
```

Comments are visible where they apply, and they show up in review. A baseline leaves the code untouched and filters matching findings from the CLI, but it is a preview feature and its file format may change between releases. `ty check --python-version` lets you see what a planned Python upgrade will surface before you change `requires-python`.

From mypy or Pyright, translate settings rather than comments.

| From | To |
|---|---|
| `# type: ignore[code]`, `# pyright: ignore[reportXyz]` | `# ty: ignore[rule]`; rule names differ, so use `ty explain rule` |
| `disable_error_code`, `reportXyz = "none"` | `<rule> = "ignore"` under `[tool.ty.rules]` |
| Pyright `"information"`, basedpyright `"hint"` | `warn`; ty has only `error`, `warn` and `ignore` |
| `check_untyped_defs`, `analyzeUnannotatedFunctions` | nothing to set: always on |
| `ignore_missing_imports` | `allowed-unresolved-imports` under `[tool.ty.analysis]`; `replace-imports-with-any` treats a resolvable module as `Any` |
| `disallow_untyped_defs` | Ruff `ANN` rules |
| mypy `--strict` | the rules in the strictness table, not `--error-on-warning` |

## CI, pre-commit and editors

Pin the same version in all three so a developer and CI see the same findings.

```yaml
# a GitHub Actions step, after uv sync
- run: uv run ty check --output-format github
```

```yaml
# .pre-commit-config.yaml; the rev is the ty version
repos:
  - repo: https://github.com/astral-sh/ty-pre-commit
    rev: v0.0.95
    hooks:
      - id: ty
```

- Output formats beyond `full` and `concise`: `github`, `gitlab`, `junit` and `sarif`; `TY_OUTPUT_FORMAT` sets one for every run.
- The hook checks the whole project rather than the staged files, because a change in one file can create a finding in another.
- VS Code: the `astral-sh.ty` extension turns off the Python extension's language server. To keep Pylance for navigation and use ty only for diagnostics, set `"python.languageServer": "Pylance"` and `"ty.disableLanguageServices": true`.
- Other editors: point the LSP client at `ty server`.

## Monorepos and scripts

ty has limited discovery of nested projects, so state the layout.

```bash
uv run ty check --project packages/billing      # one member, with its own pyproject.toml
uvx --with-requirements fetch.py ty check fetch.py   # one PEP 723 script, with its declared dependencies
```

- `root = ["packages/a", "packages/b"]` under `[tool.ty.environment]` treats members as one project, so ty may accept an import that fails at runtime. Running per member is safer.
- Several PEP 723 scripts in one project share ty's view of dependencies; `exclude-scripts = true` under `[tool.ty.src]` leaves them out unless passed by name.

## Verify

Prove the setup catches a real bug rather than trusting a green run.

```bash
uv run ty version                                   # matches the pin in pyproject.toml
printf 'def f(x: int) -> str:\n    return x\n' > /tmp/ty_probe.py
uv run ty check /tmp/ty_probe.py; echo "exit $?"    # expect invalid-return-type and exit 1
rm /tmp/ty_probe.py
uv run ty check; echo "exit $?"                     # expect exit 0 on the project
grep -rn "ty: ignore\b" src | grep -v "ignore\[" # expect nothing: every ignore names its rule
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `ty` unpinned in the dev group | `ty==<version>`, upgraded on purpose in its own commit |
| a bare `# ty: ignore` or `# type: ignore` | `# ty: ignore[rule]`, so other findings on the line still surface |
| `--error all` as a starting point | the strictness table, one rule at a time |
| `uvx ty check` as the CI gate | `uv run ty check`, so CI uses the lock and the project's packages |
| ty and mypy both failing the build | one gate, the other as advice |
| expecting ty to flag missing annotations | Ruff's `ANN` rules |

## Pre-merge checklist

ty is set up when every item holds.

- [ ] `ty` is pinned exactly in the dev group, and any pre-commit `rev` matches it.
- [ ] `requires-python` or `python-version` names the oldest supported Python.
- [ ] Rules raised above their defaults are listed in `[tool.ty.rules]`, each with a reason.
- [ ] Generated code is excluded under `[tool.ty.src]`, not hidden with comments.
- [ ] Every suppression names its rule, and `unused-ignore-comment` is on.
- [ ] CI runs `uv run ty check --output-format github` and exits non-zero on a planted bug.

## Gotchas

These are the ty behaviours that surprise people.

1. **`Cannot resolve imported module`, though the package is installed.** ty did not find the environment. It reads `VIRTUAL_ENV` or `.venv` in the project root, so run through `uv run`, pass `--python .venv`, and use `-v` to see the search paths. Source outside the root or `src/` needs `root = ["./app"]` under `[tool.ty.environment]`.
2. **The build failed on warnings only.** Warnings exit 1 by default. Fix them, or pass `--exit-zero-on-warning` if warnings are advisory.
3. **A green build turned red after a ty update.** A `0.0.x` release changed a diagnostic. Pin exactly, read the changelog, then upgrade alone in its own commit with `uv lock --upgrade-package ty`.
4. **A name bound only in `if sys.platform == "win32":` is an error on Linux and a warning elsewhere.** ty narrows to the current platform: there the branch cannot run, so the name is `unresolved-reference`. `--python-platform all` assumes nothing and reports `possibly-unresolved-reference` when that rule is on.
5. **A compiled package shows as unresolved.** ty reads `.py` and `.pyi` files. A package that ships only `.so` or `.pyd` needs stubs, or `allowed-unresolved-imports`.
6. **`mypy --strict` passes and ty fails, or the reverse.** The strict modes differ, and several mypy and Pyright checks have no ty rule yet (exhaustive `match`, overlapping overloads, `reportUnknown*`). Treat ty's rule list as its own, using `ty explain rule`.
7. **`error[invalid-baseline] Failed to read baseline`.** `baseline` is set in config but the file does not exist yet. Run `uv run ty check --update-baseline` once and commit the file.
8. **`ty.toml` settings are ignored, or `[tool.ty]` is.** A `ty.toml` beside `pyproject.toml` wins entirely; the two are not merged. User config (`~/.config/ty/ty.toml`, `%APPDATA%\ty\ty.toml`) is merged, with the project winning.

## See also

- `python-typing`: what to write in annotations, and mypy configuration
- `pyright-strict`: Pyright strict mode and working through large error backlogs
- `ruff`: the `ANN` and `PYI` rules that sit beside ty
- `uv-projects`: dependency groups, `uv check` and locking the pinned version
