---
title: tox 4 with tox-uv
author: MOZGII, Inc.
lang: en
---

# tox 4 with tox-uv

tox is the long-standing alternative to nox: environments described in a config file instead of Python functions. tox 4 reads `tox.toml`, `tox.ini` or `[tool.tox]` in `pyproject.toml`, and the tox-uv plugin replaces virtualenv and pip with uv, including a runner that installs from `uv.lock`. This reference covers a complete `tox.toml`, the uv runners, posargs, labels, ordering, parallel runs, the `.venv` redirect file, and moving between tox and nox. Versions in October 2026: tox 4.67.5, tox-uv 1.37.2.

## Contents

This reference has 8 sections.

1. Install tox with tox-uv
2. A tox.toml for a uv project
3. The uv runners and lock settings
4. Running environments
5. Labels, depends and parallel runs
6. The .venv redirect file
7. Moving between tox and nox
8. Gotchas

## 1. Install tox with tox-uv

Install tox as a uv tool with the plugin beside it, so tox uses uv from the first run.

```bash
uv tool install tox --with tox-uv
tox --version                         # lists tox-uv among the plugins
```

- `requires = ["tox>=4.30", "tox-uv>=1.30"]` in the config makes a tox without the plugin build a provisioned environment containing it and rerun itself there; that first step still uses virtualenv and pip.
- `tox-uv` bundles a uv binary. `tox-uv-bare` is the plugin alone, for containers that already have uv; `TOX_UV_PATH` points at a specific uv.

## 2. A tox.toml for a uv project

This configuration runs the tests under coverage on three Pythons, lints, tests the oldest dependency versions, and combines coverage. Each test environment installs exactly what `uv.lock` pins.

```toml
labels = { check = ["lint"], test = ["3.14", "3.13", "3.12"] }
requires = ["tox>=4.30", "tox-uv>=1.30"]
env_list = ["3.14", "3.13", "3.12", "lint", "lowest"]
venv_redirect = false

[env_run_base]
description = "run the tests on {env_name}"
runner = "uv-venv-lock-runner"
dependency_groups = ["test"]
pass_env = ["CI"]
commands = [["coverage", "run", "--parallel-mode", "-m", "pytest", "-q", { replace = "posargs", extend = true }]]

[env.lint]
description = "lint and check formatting"
dependency_groups = ["lint"]
skip_install = true
commands = [["ruff", "check", "."], ["ruff", "format", "--check", "."]]

[env.lowest]
description = "tests against the oldest allowed dependency versions"
base_python = ["3.12"]
runner = "uv-venv-runner"
uv_resolution = "lowest-direct"
commands = [["pytest", "-q"]]

[env.coverage]
description = "combine coverage from the test environments"
depends = ["3.14", "3.13", "3.12"]
skip_install = true                 # coverage itself comes from the inherited "test" group
commands = [["coverage", "combine"], ["coverage", "report"]]
```

- An environment named `3.14` runs on Python 3.14; `base_python` sets it for other names.
- `[env_run_base]` holds defaults for every environment; `[env.<name>]` overrides them.
- `commands` is a list of argument lists: no shell, no quoting problems on Windows.
- `{ replace = "posargs", extend = true }` inserts the arguments after `--` on the command line, or nothing.
- `{env_name}` and other substitutions work inside strings.
- The lock runner ignores `deps`; everything comes from `uv.lock` through `dependency_groups` and `extras`.

## 3. The uv runners and lock settings

tox-uv adds runners; pick one per environment with `runner`.

| Runner | Installs | Use for |
|---|---|---|
| `uv-venv-lock-runner` | `uv sync` from `uv.lock` into the environment | tests, type checks: reproducible environments |
| `uv-venv-runner` | `uv pip install` of `deps`, groups and the package | linters with their own pins, resolution experiments |
| `uv-venv-pep-723` | a script's inline `# /// script` dependencies | tool scripts (`script = "tools/check.py"`) |

Settings for the lock runner:

| Setting | Effect |
|---|---|
| `dependency_groups = ["test"]` | groups from `[dependency-groups]` to sync |
| `extras = ["cli"]` | optional dependencies to include |
| `no_default_groups` | leave out uv's default groups (`dev`); `true` by default when `dependency_groups` is set |
| `only_groups` | sync only these groups, not the project's dependencies |
| `uv_sync_locked = true` | the default: `--locked`, so a stale lock fails |
| `uv_sync_flags = ["--no-editable"]` | extra flags for `uv sync` |
| `package` | `editable` (default), `wheel` (non-editable), or `skip` |

Settings for environment creation: `uv_seed = true` adds pip to the environment for tools that call it; `uv_python_preference` (`only-managed`, `managed`, `system`, `only-system`) chooses where uv finds interpreters. `uv_resolution` (`highest`, `lowest`, `lowest-direct`) applies to the `uv-venv-runner`'s installs.

## 4. Running environments

tox 4 commands have short aliases; `tox` alone runs `env_list`.

```bash
tox                              # every environment in env_list, one after another
tox run -e 3.14 -- -k parser -x  # one environment; arguments after -- become posargs
tox r -e 3.14,lint               # several
tox p                            # run-parallel: env_list in parallel
tox -m check                     # every environment with the label
tox list                         # environments with their descriptions
tox r -e 3.14 -r                 # recreate the environment first
```

`tox devenv -e 3.14 .venv-dev` creates a development environment from an environment's settings, for an editor to use.

## 5. Labels, depends and parallel runs

`labels` names groups of environments; `depends` orders them.

- `tox -m test` runs the three test environments; labels can also be set per environment with `labels = ["test"]`.
- `depends` only orders environments that run together: `tox p -e 3.14,3.13,3.12,coverage` runs the tests in parallel and `coverage` after them. `tox r -e coverage` alone does not run the tests first, and `coverage combine` then fails with "No data to combine".
- Parallel environments must not share output files; parallel coverage files (`--parallel-mode`) are safe.

## 6. The .venv redirect file

tox 4.64 and later write a `.venv` file (not a directory) in the project root that points at one tox environment, following the draft PEP 832, so editors find an interpreter. It picks an environment named `dev`, else one with a development install, else the first of `env_list`.

uv 0.13 does not read that file. With it present, `uv sync`, `uv lock` and `uv run` fail with "Project virtual environment directory `.venv` cannot be used because expected directory but found a file".

- In a uv project, set `venv_redirect = false` at the top of the tox config and delete the file.
- Or name an environment for it with `venv_redirect_env`, accepting that uv commands then need `UV_PROJECT_ENVIRONMENT` set to a real directory.
- The feature is provisional while PEP 832 is a draft; check tox's changelog after upgrading.

## 7. Moving between tox and nox

Both runners can drive the same `uv.lock` and dependency groups, so a project can move without touching its dependencies.

| tox | nox equivalent |
|---|---|
| `[env.lint]` with `commands` | `@nox.session def lint(session)` with `session.run(...)` |
| `env_list` | sessions with `default=True` (the default) |
| `runner = "uv-venv-lock-runner"`, `dependency_groups` | `uv sync --locked --group=...` into the session, or `@nox_uv.session(uv_groups=[...])` |
| environment names `3.12`, `3.13` | `@nox.session(python=[...])` or `nox.project.python_versions` |
| `{ replace = "posargs" }` | `*session.posargs` |
| `labels` | `tags=[...]` and `nox -t` |
| `depends` | `requires=[...]` |
| `tox p` | `nox -j auto` with `allow_parallel=True` |

nox ships a converter: `uvx --from "nox[tox_to_nox]" tox-to-nox --output noxfile.py` reads `tox.ini` and writes a starting noxfile. Review its output; conditional factors and plugin settings rarely convert cleanly.

## 8. Gotchas

These are the tox problems seen most with uv.

1. **`uv sync` fails with "expected directory but found a file".** tox wrote the `.venv` redirect file. Set `venv_redirect = false` and delete `.venv`.
2. **`lowest-direct` still installs the newest version of a dependency.** tox installs `dependency_groups` and `deps` first, at their newest versions, then installs the package's own dependencies in a second step; a dependency that pytest already pulled in at the newest version stays there. Test minimum versions with one resolution instead: a nox session that installs `-e . --group=test` in one `uv pip install --resolution lowest-direct` command, or `uv run --isolated --resolution lowest-direct pytest`.
3. **A lock-runner environment fails after editing `pyproject.toml`.** `uv_sync_locked` is on and the lock is stale. Run `uv lock`, commit it, rerun.
4. **`tox r -e coverage` finds no data.** `depends` orders environments only when they run in the same invocation. Run the test environments in the same command.
5. **The first run is slow and uses pip.** tox provisioned itself because the running tox lacked tox-uv. Install it with `uv tool install tox --with tox-uv`.
