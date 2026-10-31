---
title: prek, the Rust runner for pre-commit configs
author: MOZGII, Inc.
lang: en
---

# prek, the Rust runner for pre-commit configs

prek is a git hook runner written in Rust (github.com/j178/prek), released as a single binary and as a wheel on PyPI. It reads `.pre-commit-config.yaml` and the hook repositories written for pre-commit, so a project can switch runners without changing its hooks. In October 2026 it is at 0.5.9: pre-1.0, released often, and used by CPython, Apache Airflow and FastAPI. This reference covers installing it, what differs from pre-commit, its built-in hooks, the native `prek.toml`, parallel hooks, monorepos and CI.

## Contents

This reference has 9 sections.

1. Install prek
2. Switch a project from pre-commit
3. What is compatible and what differs
4. Built-in hooks and the fast path
5. prek.toml
6. Parallel hooks with priority
7. Workspace mode for monorepos
8. Updating hooks and managing the cache
9. Gotchas

## 1. Install prek

prek needs no Python to run. Install it as a uv tool, or as a dev dependency so everyone gets the same version from the lock.

```bash
uv tool install prek          # on PATH for every project
uv add --dev prek             # pinned in uv.lock; run as `uv run prek`
uvx prek --version            # run once without installing
brew install prek             # macOS and Linux with Homebrew
winget install --id j178.Prek # Windows
```

Standalone installers (`prek-installer.sh`, `prek-installer.ps1`), conda-forge, Scoop, MacPorts, npm (`@j178/prek`) and cargo are also available. `prek self update` updates a standalone install; package-manager installs update through their manager.

## 2. Switch a project from pre-commit

The config stays as it is. Replace the shims and run everything once.

```bash
prek install -f                # overwrite the shims pre-commit wrote
prek install --prepare-hooks   # or: install and build every hook environment now
prek prepare-hooks             # build the environments only; writes no shim, so it suits a CI image or a container layer
prek run --all-files
```

- Shims written by prek call prek; `pre-commit install` later replaces them again. Pick one runner per clone.
- Contributors can keep using pre-commit with the same file as long as the config uses no prek-only keys (`priority`, `priorities`, `repo: builtin`, glob filters, `shell`, `orphan`, `env`, `minimum_prek_version`).
- prek honours `SKIP`, `--hook-stage`/`--stage`, `--files`, `--from-ref`/`--to-ref` and `--show-diff-on-failure` the same way.

## 3. What is compatible and what differs

prek's own documentation lists its differences; these are the ones that change daily use.

| Area | prek behaviour |
|---|---|
| Config files | `.pre-commit-config.yaml`, `.pre-commit-config.yml`, or `prek.toml` |
| Python hook environments | built with uv; toolchains (Python, Node, Go, Rust, Ruby, Bun) are downloaded and shared between hooks |
| `language_version` | accepts a version specifier and installs a matching toolchain |
| Filters | `files` and `exclude` accept glob lists as well as regexes |
| Scheduling | hooks with the same `priority` run in parallel |
| Commands | `prek update` instead of `autoupdate`; `prek cache gc`/`clean`/`dir`/`size`; `prek list`; `prek util identify`; `prek run --last-commit`, `--directory`, `--dry-run` |
| Cache | `~/.cache/prek` instead of `~/.cache/pre-commit` |
| Not implemented | `pre-commit hazmat` |

File identification has one deliberate divergence: a recognised extension wins over a loose name-prefix match, so `makefile.png` is an image, not a makefile.

## 4. Built-in hooks and the fast path

prek ships Rust versions of common hooks from `pre-commit/pre-commit-hooks`. There are two ways to use them.

| | `repo: builtin` | automatic fast path |
|---|---|---|
| Config | `repo: builtin`, no `rev` | the usual `pre-commit-hooks` repository and `rev` |
| Works with upstream pre-commit | no | yes |
| Network on first run | none | clones the repository |
| Turn off | use a remote or local hook instead | `PREK_NO_FAST_PATH=1` |

```yaml
repos:
  - repo: builtin
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-toml
      - id: check-added-large-files
```

`prek util list-builtins` prints the bundled hooks and their flags, including some not in pre-commit-hooks, such as `check-commit-trailers`. A builtin can differ from the upstream hook in edge cases or arguments; compare by turning the fast path off when a result surprises you. Keep the remote repository form if anyone on the project still runs pre-commit.

## 5. prek.toml

prek also reads a native TOML config with the same keys. `prek util yaml-to-toml` converts an existing YAML file, and `prek init` creates a starter `prek.toml` and installs the shim.

```toml
#:schema https://www.schemastore.org/prek.json
minimum_prek_version = "0.5.9"
default_install_hook_types = ["pre-commit", "pre-push"]
default_stages = ["pre-commit", "manual"]

[[repos]]
repo = "builtin"
hooks = [
  { id = "trailing-whitespace", args = ["--markdown-linebreak-ext=md"] },
  { id = "end-of-file-fixer" },
  { id = "check-toml" },
]

[[repos]]
repo = "https://github.com/astral-sh/ruff-pre-commit"
rev = "v0.17.3"
hooks = [{ id = "ruff-check", args = ["--fix"] }, { id = "ruff-format" }]

[[repos]]
repo = "local"
hooks = [
  { id = "pyright", name = "pyright", entry = "uv run --frozen pyright", language = "unsupported",
    types_or = ["python", "pyi"], pass_filenames = false, stages = ["pre-push", "manual"] },
]
```

- The `#:schema` line gives editors completion and validation through the JSON Schema Store.
- A `prek.toml` makes the project prek-only; pre-commit does not read it. Choose YAML while contributors use both runners.
- `prek validate-config prek.toml` checks either format; name the file, because with no argument it warns "No configs to check" and exits 0.

Five keys and filters exist only in prek. Each of them was run on prek 0.5.9; pre-commit 4.7.0 warns about the first three and refuses the glob form. With `env` it warns `Unexpected key(s) present on local => a: env`, then runs the hook with `MY_VAR` unset.

| Key | Where | What it does |
|---|---|---|
| `minimum_prek_version = "0.5.9"` | top level, or on one hook | an older prek stops with "`prek` version `0.5.9` or newer is required" instead of misreading a newer config; prek ignores `minimum_pre_commit_version` |
| `orphan = true` | top level of a nested config | that folder's files go to its own config alone (section 7) |
| `env = { MY_VAR = "hello" }` | on one hook | sets variables for the hook, `PATH` included, over the process environment and any `default_env` value; a hook environment that already exists is reused, so a changed value reaches its build step only at the next rebuild |
| `files = { glob = "src/**/*.py" }` | top level or on one hook | one glob, matched against the path relative to the config's folder; top-level filters apply before hook filters |
| `exclude = { glob = ["dist/**", "skip/**"] }` | top level or on one hook | a list matches when any entry does; plain strings stay regexes and stay portable to pre-commit |

## 6. Parallel hooks with priority

By default prek keeps pre-commit's order: each hook gets an implicit priority from its position. Give hooks an explicit `priority` (an integer, lower runs first) to let independent hooks run at the same time. Hooks with equal priority run concurrently, up to `PREK_CONCURRENT_HOOKS`.

```toml
[priorities]
fix = 0
check = 10

[[repos]]
repo = "local"
hooks = [
  { id = "ruff-format", name = "format", language = "unsupported", entry = "uv run --frozen ruff format", types = ["python"], priority = "fix" },
  { id = "lint", name = "lint", language = "unsupported", entry = "uv run --frozen ruff check", types = ["python"], priority = "check" },
  { id = "types", name = "types", language = "unsupported", entry = "uv run --frozen pyright", pass_filenames = false, types = ["python"], priority = "check" },
]
```

- Fixers that write files must not share a priority with hooks that read the same files; give fixers a lower number so they finish first.
- In one test two `sleep 2` hooks with the same priority finished in 2.3 s instead of 4 s.
- Priorities are compared within one config file; in workspace mode each project schedules its own hooks.

## 7. Workspace mode for monorepos

When prek runs from the repository root, it discovers every nested `prek.toml` or `.pre-commit-config.yaml`. Each directory with a config is a project; its hooks see only files under it, and a child config adds checks without overriding its parent.

```text
my-repo/
├── prek.toml                 # whitespace and hygiene for everything
├── services/api/prek.toml    # Ruff and Pyright for the Python service
└── web/prek.toml             # checks for the frontend
```

```bash
prek list                         # discovered projects and their hooks
prek run --all-files              # every project; independent projects at the same depth run concurrently
prek run services/api/ --all-files            # one project and its nested projects (trailing slash)
prek run services/api:pyright --all-files     # one hook in one project
prek run --skip web/ --all-files              # all but one project
```

- Install the shim once, from the root.
- `--config` selects one file and turns workspace discovery off.
- A `.prekignore` at the repository root stops discovery under the folders it names: with the line `legacy/`, a config inside `legacy/` is no project any more, yet root hooks still receive the files there, and Git still tracks them. After changing it run `prek run --all-files --refresh`.
- `orphan = true` in a nested config does the opposite: the folder stays a project, and parent hooks stop seeing its files. Nothing is inherited and skipping the project gives its files no fallback, so repeat any shared check in that config.
- `prek update` checks each hook repository once across all projects.

## 8. Updating hooks and managing the cache

`prek update` rewrites `rev` to the newest tag, like `pre-commit autoupdate`, with more control over what counts as newest.

```bash
prek update                          # all repositories
prek update --check                  # exit non-zero when updates exist; change nothing
prek update --freeze                 # pin commit SHAs with "# frozen: vX" comments
prek update --cooldown-days 7        # ignore tags younger than a week
prek update --repo https://github.com/astral-sh/ruff-pre-commit
prek update --exclude-tag "*rc*"     # skip release candidates
prek cache size && prek cache gc --dry-run
```

- `update` also validates frozen SHAs against the upstream refs, which detects a SHA that does not belong to the repository.
- Defaults for `--freeze`, tag filters and the cooldown can live in the project or user config (`~/.config/prek/prek.toml` on macOS and Linux) under `update`.
- `prek cache gc` removes repositories, environments and toolchains no config uses any more.

In GitHub Actions, `j178/prek-action` installs prek, verifies the download's checksum, caches environments and runs `prek run --show-diff-on-failure --color=always --all-files`. Since v3 it publishes exact version tags only; pin one (or a commit SHA) and set `prek-version` to match the version your contributors use.

```yaml
      - uses: j178/prek-action@v3.1.0
        with:
          prek-version: "0.5.9"
          extra-args: "--all-files --stage manual"
```

## 9. Gotchas

These are the prek problems seen most.

1. **pre-commit warns "Unexpected key(s) present on local => a: priority", or refuses the config with "Missing required key: rev" at `Repository(repo='builtin')`.** prek-only keys crept into a shared YAML file; pre-commit ignores `priority` with a warning and cannot read `repo: builtin` at all. Keep shared configs to pre-commit's keys, or move the project to prek fully.
2. **A built-in hook behaves differently from pre-commit's.** The Rust version differs in an edge case. Set `PREK_NO_FAST_PATH=1` to compare, or switch that hook to the remote form.
3. **Two hooks corrupt a file when run together.** A fixer shares a priority with another hook that reads or writes the same files. Give fixers a lower priority.
4. **Hooks still run with pre-commit after installing prek.** The old shim remains. Run `prek install -f`.
5. **A pinned old prek version reads a config with newer keys.** prek is pre-1.0 and adds features often. Pin `prek-version` in CI to the version in `uv.lock` or the contributor docs, and update both together.
