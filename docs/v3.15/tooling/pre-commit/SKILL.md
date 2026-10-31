---
name: pre-commit
description: Sets up git hooks for a Python project with pre-commit 4.7 or prek 0.5, the faster Rust runner that reads the same .pre-commit-config.yaml - a config with Ruff lint and format, Pyright or mypy as local hooks that see the project's dependencies, a uv lock check, whitespace, end-of-file, YAML, TOML and large-file hooks, local hooks with language unsupported, installing pre-commit and pre-push hooks, autoupdate with frozen revisions and a cooldown, running the same hooks in CI, keeping hooks fast, skipping one hook safely with SKIP or a manual stage, and why git commit --no-verify is never the fix. Use when adding pre-commit hooks, choosing between pre-commit and prek, a hook is slow or fails only in CI, updating hook versions, or someone wants to bypass the hooks.
metadata:
  tags:
  - python
  - pre-commit
  - prek
  - git-hooks
  - ruff
  - pyright
  - ci
  - code-quality
---

# Git hooks with pre-commit and prek

pre-commit runs checks on the files you are about to commit and refuses the commit when one fails or changes a file. The same config runs in CI on every file, so a check that passes locally passes there. prek is a newer runner written in Rust that reads the same `.pre-commit-config.yaml` and runs it faster. This skill covers the config for a Python project, both runners, CI, speed and skipping. Ruff's own rules and settings are the ruff skill; type checker configuration is the pyright-strict and python-typing skills.

## Step 0: read the installed versions

Hook configs depend on the runner's version (`language: unsupported` needs pre-commit 4.4+), so read what is installed and what the config pins.

```bash
uv run python -c "import sys; print(sys.version)"
pre-commit --version 2>/dev/null || uvx pre-commit --version     # pre-commit 4.7.0
prek --version 2>/dev/null || uvx prek --version                 # prek 0.5.9
grep -nE "repo:|rev:|minimum_pre_commit_version" .pre-commit-config.yaml
ls .git/hooks | grep -v sample                                    # which hooks are installed
```

Current releases (October 2026): pre-commit 4.7.0 (needs Python 3.11+), prek 0.5.9, pre-commit-hooks v6.0.0, ruff-pre-commit v0.17.3, uv-pre-commit 0.13.6, pyright-python v1.1.414, mirrors-mypy v2.4.1.

## pre-commit or prek

Both runners read `.pre-commit-config.yaml`, clone the same hook repositories and install the same `.git/hooks` shims. Pick one per project; contributors may use either.

| | pre-commit 4.7 | prek 0.5 |
|---|---|---|
| Written in | Python; installs as a Python tool | Rust; one binary, also on PyPI as a wheel |
| Maturity | the original, stable since 2014 | pre-1.0 but used by CPython, Apache Airflow and FastAPI |
| Speed | serial hooks, Python environments built with virtualenv | hooks of the same `priority` run in parallel, environments built with uv, Rust versions of common hooks |
| Config | `.pre-commit-config.yaml` | the same, or a native `prek.toml` |
| Update command | `pre-commit autoupdate` | `prek update` (adds `--cooldown-days`, `--check`) |
| Monorepos | one config per repository | workspace mode with a config per project |

Once hook environments exist, both are quick on a small repository; in one test the config below took 1.2 s with pre-commit and 0.18 s with prek. More on prek: [prek.md](references/prek.md).

## A .pre-commit-config.yaml for a Python project

This configuration checks text hygiene, lints and formats with Ruff, verifies that `uv.lock` matches `pyproject.toml`, and runs Pyright before a push. It runs unchanged under pre-commit 4.4+ and prek.

```yaml
minimum_pre_commit_version: "4.4.0"
default_install_hook_types: [pre-commit, pre-push]
default_stages: [pre-commit, manual]

repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: trailing-whitespace
        args: [--markdown-linebreak-ext=md]
      - id: end-of-file-fixer
      - id: mixed-line-ending
        args: [--fix=lf]
      - id: check-yaml
      - id: check-toml
      - id: check-merge-conflict
      - id: check-added-large-files
        args: [--maxkb=500]

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.17.3
    hooks:
      - id: ruff-check
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/astral-sh/uv-pre-commit
    rev: 0.13.6
    hooks:
      - id: uv-lock
        args: [--check]

  - repo: local
    hooks:
      - id: pyright
        name: pyright
        entry: uv run --frozen pyright
        language: unsupported
        types_or: [python, pyi]
        pass_filenames: false
        stages: [pre-push, manual]
```

- `rev` pins every hook repository to a tag; never point at a branch.
- `default_install_hook_types` makes `pre-commit install` add both the commit and the push shim.
- `default_stages` applies to hooks without their own `stages`. Including `manual` lets CI run every hook with one `--hook-stage manual` command; with only `pre-commit` there, that command would skip Ruff and the lock check.
- Order matters: `ruff-check --fix` runs before `ruff-format`, because fixes can leave code that needs formatting.

## Ruff hooks

`ruff-check` lints and `ruff-format` formats, both from `astral-sh/ruff-pre-commit`. The `rev` is the Ruff version.

- Keep `rev` equal to the Ruff version in your dev dependency group, so the editor, the hook and CI agree. The old hook id `ruff` still works as an alias of `ruff-check`.
- `--fix` lets the hook repair safe issues; the commit is still refused, so you review the change and stage it.
- Ruff reads its settings from `pyproject.toml`; never repeat them in `args`.
- `--force-exclude` is already in the hook's entry, so `extend-exclude` in the Ruff config also applies to files pre-commit passes.

## Type checkers as local hooks

A type checker needs your project's dependencies to resolve imports. The published mirror hooks (`mirrors-mypy`, `pyright-python`) run in an isolated environment that has none of them unless you list each one in `additional_dependencies`, which drifts from `uv.lock`. A local hook that runs the checker from the project environment avoids that.

```yaml
  - repo: local
    hooks:
      - id: mypy
        name: mypy
        entry: uv run --frozen mypy
        language: unsupported        # run as is, on PATH; "system" in pre-commit before 4.4
        types_or: [python, pyi]
        pass_filenames: false        # check the whole project, with its config, not single files
        require_serial: true
        stages: [pre-push, manual]
```

- `pass_filenames: false` matters: mypy and Pyright give different answers when handed a subset of files, because they then miss modules that the subset imports.
- Whole-project type checks take seconds to minutes; put them on `pre-push` or `manual` and always in CI, and keep the `pre-commit` stage for fast hooks.
- `uv run --frozen` uses the locked environment without re-resolving, so the hook does not change `uv.lock`.

## Lock file check with uv-lock

The `uv-lock` hook from `astral-sh/uv-pre-commit` runs `uv lock` when `pyproject.toml`, `uv.lock` or `uv.toml` changes. With `args: [--check]` it fails when the lock is out of date instead of rewriting it, so a dependency change cannot be committed without its lock.

- The hook's `rev` is the uv version it installs, independent of the uv on your PATH.
- Other hooks in that repository: `uv-export` writes `requirements.txt` from the lock, `uv-sync` syncs the environment after checkout or merge, `uv-audit` checks the lock for known vulnerabilities.

## Local hooks

A local hook (`repo: local`) runs a command from your repository. The `language` decides how pre-commit prepares its environment.

| `language` | Runs | Use for |
|---|---|---|
| `unsupported` (alias `system`) | `entry` as found on PATH | tools you already manage: `uv run ...`, `make`, `cargo` |
| `unsupported_script` (alias `script`) | a script file in the repository | a project-specific check in `scripts/` |
| `python` | `entry` in a fresh virtualenv with `additional_dependencies` | a small Python tool with no project dependencies |
| `pygrep` | a regex over file contents; fails on a match | forbidding `breakpoint()`, `print(` in library code, or `# type: ignore` without a code |

This pygrep hook refuses a commit that leaves a `breakpoint()` call in source code.

```yaml
      - id: no-breakpoint
        name: no breakpoint() calls
        language: pygrep
        entry: '\bbreakpoint\('
        types: [python]
        exclude: ^tests/
```

`system` and `script` still work; pre-commit 4.4 added the `unsupported` names to say plainly that pre-commit does not manage those environments. Field reference and writing a reusable hook repository: [hooks-and-ci.md](references/hooks-and-ci.md).

## Install the git hooks

Hooks only run after the shims are installed in each clone; the config alone does nothing.

```bash
uv tool install pre-commit --with pre-commit-uv   # pre-commit-uv builds Python hook envs with uv
pre-commit install --install-hooks                # shims for default_install_hook_types, envs built now
pre-commit run --all-files                        # once, on the whole tree, after adding or changing hooks
```

With prek: `uv tool install prek`, then `prek install --prepare-hooks` and `prek run --all-files`. Both write `.git/hooks/pre-commit` and `.git/hooks/pre-push`; a second `install` with the other runner replaces the shim.

- Add the install step to your contributor setup (`make dev`, a nox session, the README); new clones have no hooks.
- Hook environments live in `~/.cache/pre-commit` (move it with `PRE_COMMIT_HOME`) or `~/.cache/prek`.

## Update hook versions

`pre-commit autoupdate` rewrites each `rev` to the repository's latest tag. Run it on a schedule, review the diff and run all hooks before committing it.

```bash
pre-commit autoupdate --freeze          # pin to commit SHAs, with the tag as a comment
pre-commit autoupdate --repo https://github.com/astral-sh/ruff-pre-commit
prek update --cooldown-days 7           # skip releases younger than a week
prek update --check                     # exit non-zero when updates exist; changes nothing
```

- `--freeze` pins SHAs: a moved or re-pointed tag cannot change the code you run. This is the supply-chain-safe default for hooks, which run with your user's permissions.
- A cooldown delays brand-new releases, which are where hijacked packages and broken releases show up first.
- Update Ruff's `rev` together with the dev-group Ruff version, or the editor and the hook disagree.

## Run the hooks in CI

CI runs every hook on every file, so a contributor without hooks installed, or one who bypassed them, is caught. This GitHub Actions job runs the same config.

```yaml
  hooks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
      - run: uv sync --locked                  # local hooks call uv run
      - run: uvx --with pre-commit-uv pre-commit run --all-files --show-diff-on-failure --hook-stage manual
```

- `--hook-stage manual` runs every hook whose stages include `manual`: the type checker by its own `stages`, the rest through `default_stages`. Without `stages` or `default_stages` a hook runs at every stage.
- `--show-diff-on-failure` prints what a fixer changed, so the log explains the failure.
- Cache `~/.cache/pre-commit` keyed on a hash of `.pre-commit-config.yaml`, or use `j178/prek-action`, which installs prek, caches and runs `prek run --all-files`. Details: [hooks-and-ci.md](references/hooks-and-ci.md).

## Keep hooks fast

A commit hook that takes more than a few seconds gets bypassed. Measure, then move slow work.

- Run whole-project tools (type checkers, test suites) on `pre-push` or `manual` plus CI, not on every commit.
- Use `types`, `types_or`, `files` and `exclude` so each hook sees only the files it understands.
- Prefer hooks that take file lists (Ruff) over hooks that scan the tree.
- Switch to prek, or install `pre-commit-uv`, when environment creation dominates; both use uv to build Python hook environments.
- Never run the test suite in a commit hook; it belongs in CI and in `pre-push` at most.

## Skip a hook safely

Sometimes one hook is wrong for one commit: a work-in-progress branch, a known false positive, a hook that needs the network on a plane. Skip that hook by name, never all hooks.

```bash
SKIP=pyright,uv-lock git commit -m "wip: parser rewrite"     # pre-commit and prek both honour SKIP
pre-commit run --all-files --hook-stage manual               # run the manual-stage hooks on demand
```

On Windows PowerShell set it first: `$env:SKIP = "pyright"; git commit -m "..."`, then `Remove-Item Env:SKIP`.

- A hook that is regularly skipped is misconfigured: move it to `manual` or `pre-push`, narrow its `files`, or fix its false positives.
- A file that must never be checked by a hook goes in that hook's `exclude`, reviewed like code.

## Why never git commit --no-verify

`git commit --no-verify` (or `-n`) skips every pre-commit and commit-msg hook at once: the secret scanner, the merge-conflict check and the lock check, not only the slow hook you meant. It leaves no trace in the commit, so nobody reviewing it knows which checks did not run.

- Use `SKIP=<hook-id>` for the one hook that is in the way; the others still run.
- If a hook is broken, fix or narrow it in the config, in a commit that goes through review.
- CI runs all hooks anyway, so `--no-verify` only moves the failure to a slower, more public place.
- For agents and scripts the rule is absolute: never pass `--no-verify`; report the failing hook instead.

## Verify

Prove that the hooks are installed and that one can refuse something, with a file that is never committed.

```bash
pre-commit validate-config .pre-commit-config.yaml; echo "exit $?"   # expect 0; with no file name it checks nothing and exits 0 even on broken YAML (prek behaves the same)
ls .git/hooks/pre-commit .git/hooks/pre-push        # both exist in this clone
pre-commit run --all-files --hook-stage manual; echo "exit $?"   # expect 0
printf 'x = 1   \n' > probe.py
pre-commit run trailing-whitespace --files probe.py; echo "exit $?"   # expect 1: the hook fixed the file
rm probe.py
grep -nE "^\s*rev: (main|master|HEAD)\s*$" .pre-commit-config.yaml   # expect nothing
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `git commit --no-verify` | `SKIP=<hook-id>` for the one hook |
| `rev: main` | a tag, or a SHA from `autoupdate --freeze` |
| the mirror mypy hook with a long `additional_dependencies` | a local hook with `uv run --frozen mypy` |
| the test suite in the commit stage | `pre-push` or CI |
| Ruff settings in hook `args` | `pyproject.toml` |
| hooks that run only on contributors' machines | the same config in CI on all files |
| a Ruff `rev` that differs from the dev group | one version in both |

## Checklist

A project's hooks are set up well when every item holds.

- [ ] `.pre-commit-config.yaml` pins every `rev` to a tag or a frozen SHA; `minimum_pre_commit_version` is set.
- [ ] Ruff's hook `rev` matches the Ruff version in the dev group.
- [ ] Type checkers run as local hooks with `pass_filenames: false`, on `pre-push` or `manual`, and in CI.
- [ ] `uv-lock --check` refuses a `pyproject.toml` change without its lock.
- [ ] CI runs all hooks on all files, including the manual stage.
- [ ] The commit stage finishes in a few seconds; contributors know `SKIP=` and never use `--no-verify`.

## Gotchas

These are the hook problems seen most.

1. **The commit is refused, but nothing looks wrong.** A fixer hook (`end-of-file-fixer`, `ruff-check --fix`) changed files; the changes are unstaged. Review them, `git add`, commit again.
2. **mypy or Pyright passes locally and fails in the hook with "Import could not be resolved".** The mirror hook runs in an isolated environment without your dependencies. Use a local hook with `uv run --frozen`.
3. **Hooks run in CI but not locally.** The shims were never installed in this clone. Run `pre-commit install` (or `prek install`).
4. **"Executable `uv` not found" in a local hook.** `language: unsupported` uses the PATH of the shell that ran git; GUI git clients often have a shorter PATH. Install uv where the client sees it, or start the client from a terminal.
5. **A hook passes on changed files but fails with `--all-files`.** Old files never went through it. Run `--all-files` once after adding a hook and commit the fixes on their own.
6. **Line endings flip on every commit on Windows.** `core.autocrlf` and `mixed-line-ending --fix=lf` fight. Set `* text=auto eol=lf` in `.gitattributes`.
7. **`autoupdate` broke the build.** A new Ruff release enabled rules or changed formatting. Update one repository at a time with `--repo`, and use a cooldown.

## References

The reference files hold the full field list, CI recipes and the prek details.

- [hooks-and-ci.md](references/hooks-and-ci.md): every hook field, file filters and identify tags, the meta hooks, writing a reusable hook repository, CI recipes with caching, changed-files-only runs and pre-commit.ci
- [prek.md](references/prek.md): installing prek, its compatibility with pre-commit, built-in Rust hooks and `repo: builtin`, `prek.toml`, parallel hooks with `priority`, workspace mode for monorepos, `prek update` options and the GitHub Action

## See also

- `ruff`: the rules and settings the Ruff hooks apply
- `pyright-strict`: the checker behind the local type hook
- `python-typing`: mypy configuration
- `ty`: another checker with its own hook
- `uv-projects`: `uv.lock`, which the `uv-lock` hook guards
- `nox-sessions`: slower checks run on demand
