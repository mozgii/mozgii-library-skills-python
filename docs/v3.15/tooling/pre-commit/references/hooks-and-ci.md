---
title: pre-commit hook fields, hook repositories and CI recipes
author: MOZGII, Inc.
lang: en
---

# pre-commit hook fields, hook repositories and CI recipes

The detail behind `.pre-commit-config.yaml`: the top-level keys, every field a hook accepts, how file filters and identify tags choose files, the meta hooks that check the config itself, how to publish a reusable hook repository, and CI recipes for GitHub Actions and pre-commit.ci. Everything here works with pre-commit 4.7 and with prek, which reads the same file.

## Contents

This reference has 8 sections.

1. Top-level keys
2. Hook fields
3. Choosing files: files, exclude, types and identify tags
4. Stages and git hook types
5. Meta hooks that check the config
6. Writing a reusable hook repository
7. CI recipes
8. Gotchas

## 1. Top-level keys

These keys sit beside `repos:` at the top of `.pre-commit-config.yaml`.

| Key | Meaning |
|---|---|
| `repos` | the list of hook repositories, `local` and `meta` |
| `minimum_pre_commit_version` | refuse to run on an older pre-commit; set `"4.4.0"` when you use `language: unsupported` |
| `default_install_hook_types` | shims `pre-commit install` writes, e.g. `[pre-commit, pre-push, commit-msg]` |
| `default_stages` | stages for hooks without their own `stages`; default is every stage |
| `default_language_version` | e.g. `{python: python3.14}` for all `language: python` hooks |
| `files`, `exclude` | regexes applied to every hook before its own filters |
| `fail_fast` | stop after the first failing hook; also `pre-commit run --fail-fast` |
| `ci` | settings read only by pre-commit.ci |

## 2. Hook fields

A hook entry under a remote repository needs only `id`; everything else overrides the hook's manifest. A local hook needs `id`, `name`, `entry` and `language`.

| Field | Meaning |
|---|---|
| `id` | the hook's identifier; used by `SKIP` and `pre-commit run <id>` |
| `name` | the label printed while it runs |
| `entry` | the command; filenames are appended unless `pass_filenames: false` |
| `language` | how the environment is built: `python`, `node`, `rust`, `golang`, `docker_image`, `pygrep`, `unsupported`, `unsupported_script`, and others |
| `args` | extra arguments placed after `entry` and before the filenames |
| `additional_dependencies` | extra packages for the hook's environment (`language: python` and similar) |
| `language_version` | interpreter for the environment, e.g. `python3.14` |
| `files`, `exclude` | regexes on the file path, matched with `re.search` |
| `types`, `types_or`, `exclude_types` | identify tags: all of `types`, at least one of `types_or`, none of `exclude_types` |
| `pass_filenames` | `false` runs the command once with no file list |
| `always_run` | run even when no file matches |
| `require_serial` | one process with all files; otherwise pre-commit may split the list across processes |
| `stages` | git hook stages where the hook runs, plus `manual` |
| `verbose` | print the hook's output even when it passes |
| `log_file` | also write the output to this file |

- pre-commit splits long file lists into batches and may run batches in parallel. A tool that must see all files at once (a type checker, a duplicate-name check) needs `require_serial: true` or `pass_filenames: false`.
- `entry` is run directly, not through a shell, on every OS: `&&`, pipes and `$VAR` do not work. Call a script instead.

## 3. Choosing files: files, exclude, types and identify tags

Each hook sees the staged files (or all tracked files with `--all-files`) after four filters: the top-level `files`/`exclude`, then the hook's `files`/`exclude`, then its type tags.

The identify library assigns tags from the file name, extension, shebang and content. Read a file's tags before writing a filter.

```bash
uvx --from identify identify-cli uv.lock     # ["file", "non-executable", "text", "toml", "uv-lock"]
prek util identify uv.lock                   # the same through prek
```

```yaml
      - id: check-toml
        exclude: ^tests/fixtures/
      - id: my-notebook-check
        types: [jupyter]
      - id: ruff-check
        types_or: [python, pyi, jupyter]      # any of these
```

- `files` and `exclude` are regexes against the path relative to the repository root with forward slashes, also on Windows. Anchor them with `^`.
- In YAML use single quotes for regexes so backslashes stay literal: `'\.pyi$'`.
- A Python script without a `.py` extension is tagged `python` when its shebang names Python.

## 4. Stages and git hook types

A stage is the git hook that triggers a run. `stages` on a hook (or `default_stages`) lists where it runs; `default_install_hook_types` or `pre-commit install -t <type>` installs the shims.

| Stage | Runs on | Typical hooks |
|---|---|---|
| `pre-commit` | `git commit`, on staged files | formatters, linters, hygiene |
| `pre-push` | `git push`, on files changed in the pushed commits | type checkers, slow linters |
| `commit-msg` | the commit message file | message format, required trailers |
| `post-checkout`, `post-merge` | after checkout or merge | `uv-sync` to update the environment |
| `manual` | only `pre-commit run --hook-stage manual` | expensive checks for CI and on demand |

Setting `stages` without installing the matching shim does nothing locally. The deprecated names `commit`, `push` and `merge-commit` from pre-commit 3 print warnings; `pre-commit migrate-config` rewrites them.

## 5. Meta hooks that check the config

The `meta` repository holds hooks that check the configuration itself. They need no `rev`.

```yaml
  - repo: meta
    hooks:
      - id: check-hooks-apply          # fails if a hook matches no file in the repository
      - id: check-useless-excludes     # fails if an exclude pattern matches no file
```

`check-useless-excludes` catches a typo in an `exclude` regex, which otherwise silently checks files you meant to skip; it reports "The exclude pattern '^tests/' for no-breakpoint does not match any files". `identity` prints the file list a hook receives, useful while debugging filters.

## 6. Writing a reusable hook repository

A hook repository is a git repository with a `.pre-commit-hooks.yaml` manifest at its root. For a Python hook, the repository is also an installable package whose console script is the hook's `entry`.

```yaml
# .pre-commit-hooks.yaml
- id: check-license-header
  name: check SPDX license header
  description: Fails when a Python file does not start with an SPDX license line.
  entry: check-license-header
  language: python
  types: [python]
```

The package exposes the script through `[project.scripts]`; the hook receives filenames as arguments and returns non-zero to fail.

```python
import argparse
import sys
from collections.abc import Sequence

HEADER = "# SPDX-License-Identifier:"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("filenames", nargs="*")
    args = parser.parse_args(argv)
    missing = []
    for name in args.filenames:
        with open(name, encoding="utf-8") as f:
            if not f.readline().startswith(HEADER):
                missing.append(name)
    for name in missing:
        print(f"{name}: missing '{HEADER}' on line 1")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
```

```toml
[project.scripts]
check-license-header = "license_header:main"
```

- Try the hook from another repository before tagging it: `pre-commit try-repo ../hookrepo check-license-header --all-files` installs it from the working tree and runs it.
- Tag releases (`v1.0.0`); users pin that tag in `rev`.
- When the build backend is `uv_build` and the import name differs from the project name, set `[tool.uv.build-backend] module-name`, or the hook's install fails with "Expected a Python module at ...".
- Keep runtime dependencies small: every user's hook environment installs them.

## 7. CI recipes

CI runs every hook on every file, so it catches commits made without hooks.

This GitHub Actions job caches hook environments by the config's hash and runs all stages, including `manual`.

```yaml
  hooks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
      - uses: actions/cache@v6
        with:
          path: ~/.cache/pre-commit
          key: pre-commit-${{ runner.os }}-${{ hashFiles('.pre-commit-config.yaml') }}
      - run: uv sync --locked
      - run: uvx --with pre-commit-uv pre-commit run --all-files --show-diff-on-failure --hook-stage manual
```

With prek, `j178/prek-action` installs prek, caches its environments and runs `prek run --show-diff-on-failure --color=always --all-files`; its `extra-args` input replaces `--all-files`. From v3 the action publishes exact tags only, so pin a full version or a commit SHA.

On a large repository, a pull request can check only the files it changed:

```bash
pre-commit run --from-ref origin/main --to-ref HEAD --show-diff-on-failure
```

Check out with `fetch-depth: 0` so `origin/main` exists. Run the full `--all-files` check on the main branch, or a file the diff did not touch can still be broken by a hook update.

pre-commit.ci is a hosted service that runs the config on pull requests, pushes fixes back as a commit and opens weekly autoupdate pull requests. It reads the `ci:` key.

```yaml
ci:
  autofix_prs: true
  autoupdate_schedule: monthly
  skip: [pyright, uv-lock]        # hooks that need the project environment or the network
```

Its runners have no project environment and no network during hooks, so local hooks that call `uv run` must be skipped there and run in your own CI.

## 8. Gotchas

These are the configuration problems seen most.

1. **A hook never runs and shows "(no files to check)Skipped".** Its `types` or `files` match nothing. Check the tags with `identify-cli`, and add `check-hooks-apply` from `meta`.
2. **An `exclude` pattern is ignored.** It is unanchored, has a typo, or was written for Windows backslashes. Paths use `/`; anchor with `^`; add `check-useless-excludes`.
3. **A tool gives different results in the hook than when run by hand.** pre-commit split the file list into batches. Set `require_serial: true` or `pass_filenames: false`.
4. **`entry: ruff check . && pyright` fails.** `entry` does not go through a shell. Make two hooks, or call a script.
5. **CI passes but local commits fail on the same files.** CI ran only `--hook-stage manual` hooks while `default_stages` lacks `manual`, so most hooks never ran in CI. Add `manual` to `default_stages`, or run CI twice, without and with the stage.
6. **pre-commit.ci fails on a local hook with "Executable `uv` not found".** Its runners do not have your tools. List the hook under `ci: skip`.
