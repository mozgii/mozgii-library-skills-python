---
title: Base images and image size for Python containers
author: MOZGII, Inc.
lang: en
---

# Base images and image size for Python containers

How to choose and combine base images for a Python application: why the build and final stages must share an interpreter path, Debian slim with system packages, Alpine and musl wheels, distroless images and their Debian Python, uv-managed Python on a plain Debian base, and how to measure and cut image size. Sizes and versions were read from Docker Hub, gcr.io and PyPI in October 2026.

## Contents

This reference has 7 sections.

1. The rule: same interpreter path in both stages
2. Debian slim and system packages
3. Alpine and musl wheels
4. Distroless with Debian's Python
5. uv-managed Python on a plain Debian base
6. Measuring image size
7. Cutting image size

## 1. The rule: same interpreter path in both stages

A virtual environment is not portable: `.venv/bin/python` is a symlink to the interpreter that created it, and every console script starts with a shebang holding the venv's absolute path. Copying `.venv` into a stage where that interpreter is missing gives `no such file or directory` at start.

| Build stage | Final stage | Works |
|---|---|---|
| `python:3.14-slim-trixie` | `python:3.14-slim-trixie` | yes: same `/usr/local/bin/python3.14` |
| `ghcr.io/astral-sh/uv:python3.14-trixie-slim` | `python:3.14-slim-trixie` | yes: built on the same official image |
| `python:3.14-slim-trixie` | `python:3.13-slim-trixie` | no: other version |
| `python:3.13-slim-trixie` | `gcr.io/distroless/python3-debian13` | no: `/usr/local/bin/python3.13` versus Debian's `/usr/bin/python3.13` |
| uv-managed Python in `/python` | any glibc image plus `COPY --from=build /python /python` | yes, with the same `UV_PYTHON_INSTALL_DIR` |

Also keep the C library family the same: wheels built for glibc (`manylinux`) do not load on musl (Alpine) and the reverse.

## 2. Debian slim and system packages

The slim images contain Python and the minimum Debian runtime. Packages your wheels need at run time (a database client library, fonts, `ffmpeg`) go into the final stage; compilers and headers go only into the build stage.

```dockerfile
FROM python:3.14-slim-trixie AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*
# ... uv sync as in the skill: packages without wheels compile here ...

FROM python:3.14-slim-trixie
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 tini \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/.venv /app/.venv
```

- `--no-install-recommends` skips optional packages that often double the size.
- Remove `/var/lib/apt/lists/*` in the same `RUN`; a later `RUN rm` cannot shrink a layer already written.
- Prefer wheels that bundle their libraries (`psycopg[binary]`, `opencv-python-headless`) when you do not need the system version; then the final stage needs no extra packages.
- The full `python:3.14-trixie` image (about 416 MB compressed) has compilers and headers; it is a build stage, never a final stage.

## 3. Alpine and musl wheels

Alpine uses musl instead of glibc, so it needs `musllinux` wheels; a package without one is compiled from source during the build, which needs a compiler and can take a long time or fail. Many popular packages publish musl wheels now, so check before deciding.

```bash
# resolve with wheels only, for musl on x86_64; fails and names the package if any wheel is missing
uv pip compile pyproject.toml --python-version 3.14 --python-platform x86_64-unknown-linux-musl --only-binary :all: -o /dev/null
uv pip compile pyproject.toml --python-version 3.14 --python-platform aarch64-unknown-linux-musl --only-binary :all: -o /dev/null
```

- `python:3.14-alpine` is about 18 MB compressed against about 44 MB for slim; the difference shrinks once your dependencies are in, and the slim image's glibc compatibility is usually worth more.
- musl differs from glibc in its memory allocator, DNS resolver and locale support; test the real workload on Alpine rather than assuming it behaves like Debian.
- Alpine's own `apk` Python packages are a different build from the official `python:*-alpine` image; do not mix them.

## 4. Distroless with Debian's Python

Distroless images contain an application's runtime dependencies and nothing else: no shell, no package manager, no `ls`. `gcr.io/distroless/python3-debian13` (about 23 MB compressed) runs Debian 13's Python at `/usr/bin/python3.13`, which is also its default entry point.

```dockerfile
FROM debian:trixie-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/uv
ENV UV_PYTHON=/usr/bin/python3.13 UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-editable

FROM gcr.io/distroless/python3-debian13:nonroot
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
ENTRYPOINT ["/app/.venv/bin/python", "-m", "invoice_sync"]
```

- The project must support Python 3.13 (`requires-python`), because that is the Python the distroless image has; newer Pythons need the approach in section 5.
- `:nonroot` tags run as UID 65532; `:debug` tags add a busybox shell for troubleshooting (`docker run --entrypoint sh`).
- `HEALTHCHECK` and `ENTRYPOINT` must use the exec form: there is no `/bin/sh` to run a shell form.
- Pin the image by digest; the tag moves with every Debian security update.

## 5. uv-managed Python on a plain Debian base

uv can install a standalone CPython build into a fixed directory in the build stage; the final stage copies that directory and the venv onto a plain `debian:trixie-slim`. This gives any Python version (3.15 the day it is released) on a base that tracks Debian security updates.

```dockerfile
FROM ghcr.io/astral-sh/uv:0.13.6-trixie-slim AS build
ENV UV_PYTHON_INSTALL_DIR=/python UV_PYTHON_PREFERENCE=only-managed \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1
RUN uv python install 3.15
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-editable

FROM debian:trixie-slim
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /python /python
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
ENTRYPOINT ["invoice-sync"]
```

`uv python install --compile-bytecode` also compiles the managed interpreter's standard library, which the official images leave uncompiled. This layout follows uv's own `standalone` Docker example.

## 6. Measuring image size

Measure before cutting: most of a Python image is usually a few large wheels, not the base.

```bash
docker image ls invoice-sync                      # total, uncompressed
docker history --no-trunc --format '{{.Size}}\t{{.CreatedBy}}' invoice-sync | head -20   # per layer
docker run --rm --entrypoint sh invoice-sync -c \
  'du -sh /app/.venv/lib/python3*/site-packages/* | sort -h | tail -15'                 # biggest packages
```

`docker image ls` shows the unpacked size; registries and pulls deal in compressed layers, which are smaller. Tools such as `dive` show which files each layer adds.

## 7. Cutting image size

These changes cut size in order of typical effect; each one removes bytes that the program never uses.

1. Multi-stage builds: compilers, headers, uv, caches and source stay in the build stage.
2. Smaller variants of big dependencies: `opencv-python-headless` instead of `opencv-python`, CPU-only PyTorch wheels for CPU servers (the `python-ml-packages` skill), `psycopg[binary]` instead of system libraries plus a compiler.
3. Dependency groups: test, lint and docs tools in groups that `--no-dev` or `--no-group` leave out.
4. `--no-install-recommends` and cleaned apt lists in the same layer.
5. No large data in the image: models and datasets come from a volume or object storage at start.

Deleting files from installed packages (tests, `.pyi`, docs) saves little and can break packages that import their own test helpers; measure first.
