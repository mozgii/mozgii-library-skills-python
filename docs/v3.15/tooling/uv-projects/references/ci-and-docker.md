---
title: uv in CI and Docker
author: MOZGII, Inc.
lang: en
---

# uv in CI and Docker

Running uv projects in continuous integration and container images: pinned uv, cached downloads, locked installs, a test matrix, and small images with a separate dependency layer.

## Contents

This reference has 6 sections.

1. GitHub Actions workflow
2. Test matrix across Python versions and OSes
3. Caching outside GitHub Actions
4. Docker image with a dependency layer
5. Environment variables worth knowing
6. Gotchas

## 1. GitHub Actions workflow

The GitHub Actions workflow installs a pinned uv with `astral-sh/setup-uv`, which also caches uv's download cache between runs.

```yaml
# .github/workflows/ci.yml
name: ci
on: [push, pull_request]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
        with:
          version: "0.13.6"            # pin uv; bump on purpose
          enable-cache: true
      - run: uv lock --check
      - run: uv sync --locked --all-groups
      - run: uv run ruff check --output-format github .
      - run: uv run ruff format --check .
      - run: uv run pyright
      - run: uv run pytest -q
```

- `uv sync` installs the Python from `.python-version`, so `actions/setup-python` is not needed.
- `actions/checkout@v7` is a moving major tag. `astral-sh/setup-uv` publishes exact tags only, so `@v10` does not exist: use a full release such as `v10.3.3` (October 2026). Pin third-party actions to a commit SHA in security-sensitive repositories.

## 2. Test matrix across Python versions and OSes

A matrix runs the suite on every supported Python and OS; `UV_PYTHON` overrides the pinned version per job.

```yaml
  matrix:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python: ["3.11", "3.12", "3.13", "3.14", "3.15"]
    runs-on: ${{ matrix.os }}
    env:
      UV_PYTHON: ${{ matrix.python }}
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
        with: { version: "0.13.6", enable-cache: true }
      - run: uv sync --locked
      - run: uv run pytest -q
```

Test the lowest version in `requires-python` with the lowest allowed dependency versions too: `uv sync --resolution lowest-direct` in one extra job catches lower bounds that were never true.

## 3. Caching outside GitHub Actions

Other CI systems cache uv's cache directory between runs and prune it before saving.

```bash
export UV_CACHE_DIR="$CI_PROJECT_DIR/.uv-cache"   # inside the workspace, so the CI can cache it
uv sync --locked
uv run pytest -q
uv cache prune --ci                               # keep wheels built from source, drop downloaded ones
```

`uv cache prune --ci` removes downloaded wheels (fast to fetch again) and keeps wheels built from source distributions (slow to rebuild), which keeps the cache small.

## 4. Docker image with a dependency layer

A two-step sync puts dependencies in their own layer, so changing application code does not reinstall them.

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.14-slim
RUN useradd --create-home app
COPY --from=build --chown=app:app /app/.venv /app/.venv
USER app
ENV PATH="/app/.venv/bin:$PATH"
CMD ["my-app"]
```

- `--no-install-project` installs only the dependencies in the first layer; the second sync adds the project.
- `--no-editable` installs the project as a regular package, so the final stage needs only `.venv`, not the source.
- `UV_COMPILE_BYTECODE=1` trades build time for faster container start; `UV_LINK_MODE=copy` avoids hardlink warnings across the cache mount.
- The build and final stages must use the same Python (same image), because `.venv` points at its interpreter.
- Add a `.dockerignore` with `.venv`, `.git` and `__pycache__`.

## 5. Environment variables worth knowing

These variables change uv's behaviour without flags, which is useful in CI and containers.

| Variable | Effect |
|---|---|
| `UV_PYTHON` | Python version or path for this run |
| `UV_PYTHON_DOWNLOADS=never` | never download Python; use the system or image one |
| `UV_CACHE_DIR` | cache location |
| `UV_NO_CACHE=1` | no cache at all (slow; for debugging) |
| `UV_PROJECT_ENVIRONMENT` | install into this path instead of `.venv` (an existing interpreter prefix or a shared venv) |
| `UV_COMPILE_BYTECODE=1` | write `.pyc` files at install |
| `UV_LINK_MODE=copy` | copy instead of hardlink from the cache |
| `UV_EXCLUDE_NEWER` | ignore uploads after a date or duration |
| `UV_INDEX_<NAME>_USERNAME`, `UV_INDEX_<NAME>_PASSWORD` | credentials for a named index |
| `UV_FROZEN=1`, `UV_LOCKED=1` | the `--frozen` and `--locked` behaviour everywhere |
| `UV_NO_DEV=1` | skip the dev group |

## 6. Gotchas

These are the CI and container failures seen most.

1. **The CI cache grows without bound.** Saving the whole cache every run. Use `enable-cache` in setup-uv, or `uv cache prune --ci` before saving.
2. **`uv sync` downloads Python in the container although the image has it.** The pin in `.python-version` differs from the image's Python. Match them, and set `UV_PYTHON_DOWNLOADS=never` to fail loudly.
3. **The final image cannot find the interpreter.** `.venv` was built on a different base image. Build and run on the same Python image.
4. **Every code change reinstalls all dependencies.** `COPY . /app` came before the dependency sync. Sync with `--no-install-project` first, then copy the code.
