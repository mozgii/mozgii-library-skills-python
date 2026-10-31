---
name: ruff
description: Lints and formats Python with Ruff - configuration in pyproject.toml or ruff.toml, the large default rule set since Ruff 0.16 and how to extend or narrow it, per-file ignores, safe and unsafe fixes, suppression comments (noqa, ruff ignore, file-ignore, disable and enable blocks), the formatter and its Markdown code-block formatting, import sorting, pre-commit, editor and CI setup, and adopting Ruff in an existing codebase. Use when setting up linting or formatting, replacing flake8, black, isort or pyupgrade, choosing rules, or when ruff check or ruff format reports something unexpected.
metadata:
  tags:
  - python
  - ruff
  - lint
  - format
  - isort
  - flake8
  - black
  - pre-commit
  - ci
  - code-quality
---

# Ruff linting and formatting

Ruff is Astral's linter and formatter for Python, written in Rust. `ruff check` replaces flake8 and its plugins, isort, pyupgrade and pydocstyle; `ruff format` replaces black with near-identical output. It runs in milliseconds on large codebases, so it belongs in the editor, pre-commit and CI at once.

## Step 0: read the installed version

Ruff's defaults changed a lot recently, so read the version before reasoning about which rules run.

```bash
uv run ruff --version              # e.g. ruff 0.17.3
uv run ruff check --show-settings src/my_app/__init__.py | grep -A3 "linter.rules.enabled"
```

| Version | Change that matters |
|---|---|
| 0.15 (Feb 2026) | formatter "2026 style"; `# ruff: disable[...]` / `# ruff: enable[...]` block suppression |
| 0.16 (Jul 2026) | default rule set grew from 59 to about 410 rules; Markdown code blocks formatted by default; `# ruff: ignore[...]` comments |
| 0.17 (Oct 2026) | default `target-version` is 3.11 when none is configured; `DTZ001`, `DTZ005`, `DTZ006`, `DTZ007`, `DTZ011`, `DTZ012` and `DTZ901` left the defaults; `full` output always shows unsafe and display-only fixes (applying them still needs `--unsafe-fixes`); `ruff-lsp` support removed |

Pin Ruff in the dev group (`uv add --dev ruff`) so the editor, pre-commit and CI run the same version.

## Work in a codebase you did not set up

Ruff changes files quickly, so decide first what a change should touch. A repository may use Ruff for linting only, or not at all, and a reformatted file buries the real diff.

| You find | Do | Why |
|---|---|---|
| `ruff` in the dev dependencies | `uv run ruff ...` | the pinned version gives the same findings as CI |
| no Ruff dependency, a quick look wanted | `uvx ruff ...` | adds nothing to the project |
| `[tool.ruff]`, `ruff.toml` or `.ruff.toml` | use it as written | that is the project's own rule choice |
| `ruff format --diff` would rewrite whole files | skip formatting in this change | the project is not formatted with Ruff yet; do that alone, in its own commit |
| `ruff check` reports problems across the repo | fix only the files you are changing: `ruff check --diff <files>` first | wide fixes belong in a separate, reviewable commit unless the task asks for them |

## Install and run

Ruff runs from the project environment or as a standalone tool.

```bash
uv add --dev ruff
uv run ruff check .                        # lint
uv run ruff check --fix .                  # apply safe fixes
uv run ruff check --fix --unsafe-fixes .   # also fixes that may change behaviour; review the diff
uv run ruff check --diff .                 # show fixes without writing
uv run ruff check --statistics .           # count per rule: plan a cleanup
uv run ruff format .                       # format
uv run ruff format --check --diff .        # CI: fail and show what would change
uv run ruff rule B006                      # explain one rule
uv run ruff linter                         # every rule group and its prefix
uv run ruff check --watch .                # relint on save
uv run ruff check --select E,F --ignore E501 .   # this run only: --select replaces the configured rules
uvx ruff check .                           # without adding it to the project
```

Ruff reads `requires-python` from `pyproject.toml` to decide which syntax upgrades (`UP` rules) are allowed; set `target-version` only when there is no `requires-python`.

## Configuration

Ruff reads `[tool.ruff]` in `pyproject.toml`, or a `ruff.toml` / `.ruff.toml` with the same keys without the `tool.ruff` prefix. The closest config file to each checked file wins.

```toml
[tool.ruff]
line-length = 100                          # default 88
extend-exclude = ["vendor", "migrations/versions"]

[tool.ruff.lint]
extend-select = [
  "I",      # isort: the full import sorting set (only I001 is default)
  "N",      # pep8-naming
  "PTH",    # pathlib over os.path
  "S",      # flake8-bandit security checks beyond the default three
  "ARG",    # unused arguments
  "T20",    # print and pprint left in code
  "ERA",    # commented-out code
]
ignore = ["COM812", "ISC001"]              # conflict with the formatter when selected
fixable = ["ALL"]
unfixable = ["F401"]                       # keep unused imports visible instead of deleting them on save

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "PLR2004", "ARG"]    # assert, magic numbers and fixture args are normal in tests
"__init__.py" = ["F401"]                   # re-exports
"scripts/**" = ["T20"]

[tool.ruff.lint.isort]
known-first-party = ["my_app"]

[tool.ruff.format]
docstring-code-format = true               # format code examples inside docstrings
```

- `select` replaces the default set; `extend-select` adds to it. Since 0.16 the default set is large and opinionated in a useful way, so most projects should extend it rather than replace it.
- To go back to the pre-0.16 minimum (for example while migrating), use `select = ["E4", "E7", "E9", "F"]`.
- `"ALL"` selects every stable rule, including conflicting ones; use it only with a deliberate `ignore` list.
- A nested `pyproject.toml` with `[tool.ruff]` starts a new config unless it says `extend = "../pyproject.toml"`.

## Which rules to add

Pick rule groups by what the project is; the default set already covers bugs (`F`, `B`, `PL`), upgrades (`UP`), simplifications (`SIM`, `C4`, `FURB`) and async mistakes (`ASYNC`).

| Project | Add with `extend-select` |
|---|---|
| any | `I` (import order), `PTH`, `T20` |
| library with public API | `D` with `[tool.ruff.lint.pydocstyle] convention = "google"` (or `numpy`, `pep257`), `ANN` for annotations, `N` |
| service or CLI handling input | `S` (security), `BLE` (blind except), `TRY` |
| test-heavy codebase | `PT` (pytest style) |
| data and notebooks | `NPY`, `PD` |

The full table of groups and when each pays off is in [rule-sets.md](references/rule-sets.md).

## Fixes: safe and unsafe

Each fix is marked safe (keeps behaviour) or unsafe (may change behaviour or remove comments).

- `--fix` applies safe fixes only. `--unsafe-fixes` adds the rest; run it on a clean git tree and read the diff.
- `fixable` and `unfixable` limit which rules may be fixed automatically; `extend-safe-fixes` and `extend-unsafe-fixes` reclassify rules.
- Editors run `source.fixAll.ruff` on save; keep risky rules (`F401` while editing) in `unfixable` if deletions on save annoy people.

## Suppressing a rule

A suppression is a recorded exception, so it names the rule and, where useful, says why.

```python
import math  # noqa: F401                        # classic, understood by other tools too
import json  # ruff: ignore[F401]                # Ruff 0.16+, same line
# ruff: ignore[N803]
def legacy(camelArg: int) -> int: ...            # applies to the next logical line

# ruff: file-ignore[S101] asserts are part of this module's contract
# ruff: disable[E722]
try:
    legacy_call()
except:                                          # third-party code raises BaseException subclasses
    log_and_continue()
# ruff: enable[E722]
```

- `RUF100` reports `noqa` comments that no longer suppress anything; it is in the default set.
- `ruff check --add-noqa` writes a `noqa` comment for every current violation: useful once, when adopting Ruff on a large codebase, never as a habit.
- Prefer `per-file-ignores` for whole folders (tests, migrations) over comments in each file.

## The formatter

`ruff format` is a black-compatible formatter; differences from black are small and documented as deliberate.

```bash
uv run ruff format .
uv run ruff format --check .               # exit 1 when a file would change
uv run ruff format --check --output-format github .   # annotations in GitHub CI (0.16+)
```

- Since 0.16 it also formats Python code blocks in Markdown files. Opt out per project with `extend-exclude = ["*.md"]`, or per block with `<!-- fmt: off -->` and `<!-- fmt: on -->` around it.
- `# fmt: off` / `# fmt: on` and `# fmt: skip` protect hand-aligned code (matrices, tables).
- `quote-style`, `indent-style` (`"tab"` exists), `line-ending` and `skip-magic-trailing-comma` are the main knobs; leave them at defaults unless the codebase already differs.
- Run the formatter after `ruff check --fix`, because some fixes produce code the formatter then rewraps.

## Pre-commit, editor and CI

The same Ruff version should run in all three places.

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.17.3
    hooks:
      - id: ruff-check
        args: [--fix]
      - id: ruff-format
```

```yaml
# a GitHub Actions step, after uv sync
- run: uv run ruff check --output-format github .
- run: uv run ruff format --check --output-format github .
```

- VS Code: the "Ruff" extension (`charliermarsh.ruff`) uses `ruff server`, the built-in language server; set `"editor.defaultFormatter": "charliermarsh.ruff"` for Python and `"editor.codeActionsOnSave": {"source.fixAll.ruff": "explicit", "source.organizeImports.ruff": "explicit"}`.
- Other editors: point the LSP client at `ruff server`.
- `prek` (0.5.9) is a faster drop-in runner for `.pre-commit-config.yaml` files if pre-commit's speed matters.

## Adopting Ruff in an existing codebase

Adopt in steps so the first commit is mechanical and reviewable.

1. Translate the old config: flake8 `select`/`ignore`, isort `known_first_party`, black `line-length` map to the keys above.
2. Run `uv run ruff format .` alone and commit it ("format with ruff"); add that commit to `.git-blame-ignore-revs`.
3. Run `uv run ruff check --statistics .` and decide per rule: fix now, fix later (`ignore` with a dated comment), or never.
4. Apply safe fixes rule by rule: `uv run ruff check --select UP --fix .`, run the tests, commit.
5. Enable the gate in CI only when `ruff check .` is clean.

The tools Ruff replaces translate command by command, with one catch: import sorting belongs to `ruff check`, so `ruff format` alone leaves imports as they are.

| Old command | Ruff command |
|---|---|
| `black src`, `black --check src`, `black --diff src` | `ruff format src`, `ruff format --check src`, `ruff format --diff src` |
| `flake8 src` | `ruff check src` |
| `flake8 --select E,F src` | `ruff check --select E,F src` |
| `isort src` | `ruff check --select I --fix src` |
| `isort --check src`, `isort --diff src` | `ruff check --select I src`, `ruff check --select I --diff src` |

## Verify

Prove the setup catches a real finding before trusting a green run, then check that CI's two commands pass on the project.

```bash
uv run ruff --version                                     # matches the pin and pre-commit's rev
printf 'import os\n' > /tmp/ruff_probe.py
uv run ruff check --isolated --select F401 /tmp/ruff_probe.py; echo "exit $?"    # expect F401 and exit 1
rm /tmp/ruff_probe.py
uv run ruff check .; echo "exit $?"                       # expect exit 0
uv run ruff format --check .; echo "exit $?"              # expect exit 0
uv run ruff check --show-settings src/my_app/__init__.py | grep -E "linter.unresolved_target_version|linter.preview"
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `select = ["ALL"]` as a start | extend the default set one group at a time |
| `select` when you meant to add rules | `extend-select`; `select` replaces the defaults |
| `--unsafe-fixes` across a dirty tree | a clean tree, then read the diff |
| a bare `# noqa` | `# noqa: F401`, or `# ruff: ignore[F401]` |
| `noqa` in every test file | `per-file-ignores` for the folder |
| black and isort beside Ruff | one tool, or imports keep moving |

## Checklist

Ruff is set up when every item holds.

- [ ] Ruff is pinned in the dev group, and pre-commit's `rev` matches it.
- [ ] `requires-python` is set, so `UP` rules target the right version.
- [ ] Config extends the default set; `ignore` entries have a reason.
- [ ] Tests and generated code have `per-file-ignores`, not comments in every file.
- [ ] CI runs `ruff check` and `ruff format --check`.
- [ ] Suppressions name their rule; `RUF100` keeps stale ones out.

## Gotchas

These are the Ruff behaviours that surprise people.

1. **Hundreds of new findings after upgrading to 0.16 or later.** The default rule set grew about sevenfold. Either fix with `--fix` in batches, or pin `select = ["E4", "E7", "E9", "F"]` and widen later.
2. **Ruff reformatted the README.** Markdown code blocks are formatted since 0.16. Exclude `*.md` or wrap blocks in `fmt: off`.
3. **Imports keep moving between Ruff and another tool.** isort or an editor's organize-imports also runs. Remove the other one; set `known-first-party` so Ruff classifies your package.
4. **A rule fires in the editor but not in CI.** Different Ruff versions, or the editor picked up another config file. Pin the version and check `ruff check --show-settings <file>`.
5. **`COM812` and the formatter fight over trailing commas.** Ruff warns about this combination; ignore `COM812` (and `ISC001`) when they are selected.
6. **`No module named ruff` after a conda update, from `python -m ruff` or `import ruff`.** Since 0.17 the conda-forge package ships without a Python dependency. Call `ruff` from `PATH`; PyPI installs and the standalone installer are unaffected.
7. **A `noqa` comment does not work.** The code is wrong or the comment is not on the reported line (multi-line strings report on the first line). Use `# ruff: ignore[...]` on the line above, or a `disable`/`enable` block.

## See also

- `ty`: type checking from the same team, with Ruff's `ANN` rules beside it
- `pre-commit`: running hooks locally and in CI
- `uv-projects`: pinning Ruff in the dev group
- `python-version-upgrade`: the `UP` rewrite after raising `requires-python`
- [rule-sets.md](references/rule-sets.md): the full table of rule groups
