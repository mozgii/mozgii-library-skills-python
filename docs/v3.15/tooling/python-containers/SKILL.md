---
name: python-containers
description: Builds small, fast, safe Docker images for Python applications with uv - multi-stage builds, the official uv images and copying the pinned uv binary, uv sync --locked --no-dev in cached layers, bytecode compilation, choosing slim, alpine (musl wheels) or distroless bases, a non-root user, PID 1 and signal handling with exec form, tini or --init, HEALTHCHECK without curl, .dockerignore, image size, multi-architecture builds from Apple silicon, and reproducible builds with SOURCE_DATE_EPOCH. Use when writing or reviewing a Dockerfile for a Python service, worker or CLI, when docker stop hangs for ten seconds, when images are too big or rebuild all dependencies on every change, or when moving a project to uv in containers.
metadata:
  tags:
  - python
  - docker
  - containers
  - uv
  - dockerfile
  - distroless
  - alpine
  - healthcheck
---

# Docker images for Python applications

A good Python image builds dependencies in a cached layer, ships only the virtual environment and the interpreter, runs as a non-root user, stops within a second on `SIGTERM`, and reports its health. This skill goes beyond the basic two-step `uv sync` Dockerfile in the `uv-projects` skill (its CI and Docker reference): base images, users, signals, health checks, size and reproducibility. Shutdown logic inside the program is the `python-services-shutdown` skill.

## Step 0: read the versions

Pin uv and the Python base image to what the project already uses, so the image matches local runs and CI.

```bash
uv --version && cat .python-version 2>/dev/null
grep -n "requires-python" pyproject.toml
docker version --format '{{.Server.Version}}' && docker buildx version
```

Current releases (October 2026): uv 0.13.6, Python 3.15.2 and 3.14.x images (`python:3.14-slim-trixie`, Debian 13 based), Docker Engine 29 with BuildKit and buildx 0.33. The uv images are `ghcr.io/astral-sh/uv:0.13.6` (just `/uv` and `/uvx`) and derived ones such as `ghcr.io/astral-sh/uv:python3.14-trixie-slim`.

## Choosing a base image

The base image fixes the C library, the Python build and the size. The final stage must have the same Python at the same path as the build stage, because a virtual environment points at its interpreter.

| Base | Compressed size (amd64) | C library | Use when |
|---|---|---|---|
| `python:3.14-slim-trixie` | about 44 MB | glibc | the default: manylinux wheels install, a shell for debugging |
| `python:3.14-alpine` | about 18 MB | musl | every dependency has a `musllinux` wheel and you test on Alpine |
| `gcr.io/distroless/python3-debian13` | about 23 MB | glibc | no shell or package manager in production; ships Debian's Python 3.13 |
| `debian:trixie-slim` plus uv-managed Python | base plus the interpreter | glibc | a Python version no official image offers |
| `python:3.14-trixie` (full) | about 416 MB | glibc | a build stage that compiles C extensions, never the final stage |

Sizes are Docker Hub's and the registry's compressed amd64 figures for October 2026. Details on Alpine, distroless and managed Python are in [base-images-and-size.md](references/base-images-and-size.md).

## Multi-stage build with uv

A multi-stage build installs dependencies with uv in a build stage and copies only the finished virtual environment into a clean final stage, so uv, caches and source files stay out of the image.

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.14-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_DEV=1
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

FROM python:3.14-slim-trixie
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /app/.venv /app/.venv
COPY scripts/healthcheck.py /app/healthcheck.py
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONFAULTHANDLER=1
WORKDIR /app
USER app
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-I", "/app/healthcheck.py", "--file", "/tmp/heartbeat", "--max-age", "60"]
ENTRYPOINT ["invoice-sync"]
```

- `COPY scripts/healthcheck.py` needs the file in your build context: copy this skill's `scripts/healthcheck.py` into your repository first, or drop that line and use a one-line `HEALTHCHECK` from the health check section.
- `UV_NO_DEV=1` leaves the dev group out (the same as `--no-dev`); `--locked` fails the build when `uv.lock` is out of date instead of silently re-resolving.
- `--no-editable` installs the project itself as a normal package, so the final stage needs `.venv` and no source tree.
- The venv is copied as root-owned and read-only for `app`; the program writes only to `/tmp` or mounted volumes.
- Pin uv by version (or by digest, in the reference on reproducible builds); `:latest` changes resolution behaviour under you.

## Layer order and the build cache

Docker reuses a layer only when everything before it is unchanged, so order steps from rarely to often changing: base, system packages, dependencies, then application code.

- The bind mounts of `uv.lock` and `pyproject.toml` make the dependency layer depend on those two files only; editing code reuses it.
- `--mount=type=cache,target=/root/.cache/uv` keeps uv's download cache across builds without putting it in the image; `UV_LINK_MODE=copy` avoids hardlink warnings because the cache is on another file system.
- A workspace uses `--no-install-workspace` and `--frozen` in the first step (uv cannot check the lock without every member's `pyproject.toml`), then `--locked` after `COPY`.
- On CI, export the cache with `docker buildx build --cache-to type=gha,mode=max --cache-from type=gha` (GitHub Actions) or a registry cache; cache mounts alone do not survive fresh CI runners.

## Bytecode compilation

`UV_COMPILE_BYTECODE=1` writes `.pyc` files at install time, so the container does not compile every module on its first start; that shortens cold starts at the cost of build time and some image size.

- The official `python` images do not ship a compiled standard library; it compiles on first import and, with a read-only root file system, on every start. `RUN python -m compileall -q /usr/local/lib/python3.14` in the final stage (as root, before `USER`) fixes that.
- With a read-only file system, set `PYTHONDONTWRITEBYTECODE=1` so Python does not try to write caches it cannot keep.
- Bytecode embeds source timestamps; for reproducible images build with `ARG SOURCE_DATE_EPOCH` so Python writes hash-based `.pyc` files ([reproducible-and-secure-builds.md](references/reproducible-and-secure-builds.md)).

## Run as a non-root user

A container process running as root is root on the files it can reach, including mounted volumes, and a breakout starts with more privileges. Create a user with a fixed numeric UID and switch to it before `ENTRYPOINT`.

- Use a fixed UID and GID (10001 here) so volume permissions and Kubernetes `runAsUser` are predictable; `useradd --system` warns for UIDs above 999.
- Keep application files owned by root and not writable by the app user; the app writes to `/tmp`, a volume, or a directory created for it with `install -d -o app /data`.
- Kubernetes: `runAsNonRoot: true`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, and an `emptyDir` for `/tmp`. Docker: `docker run --read-only --tmpfs /tmp`.
- Distroless images include a `nonroot` user (UID 65532) and `:nonroot` tags.

## PID 1 and signals

The first process in a container is PID 1, and the kernel gives PID 1 no default signal actions: a Python program without a `SIGTERM` handler ignores `docker stop`, waits out the timeout (10 s) and is killed. Measured with `docker stop -t 5`: a Python PID 1 without a handler took 5.2 s, with a handler 0.2 s, and with `docker run --init` 0.4 s.

```dockerfile
# exec form: Python is PID 1 and receives SIGTERM itself
ENTRYPOINT ["invoice-sync"]

# shell form: /bin/sh is PID 1, does not forward SIGTERM, and the stop waits for the kill
# ENTRYPOINT invoice-sync
```

- Always use the exec (JSON array) form for `ENTRYPOINT` and `CMD`. If a shell script must run first, end it with `exec "$@"` or `exec python -m app` so Python replaces the shell.
- Handle `SIGTERM` in the program (the `python-services-shutdown` skill); the drain must fit in `docker stop -t`, Compose `stop_grace_period` or Kubernetes `terminationGracePeriodSeconds`.
- A program that starts child processes also needs an init to reap zombies: `docker run --init` or Compose `init: true` add Docker's tini; for Kubernetes, install `tini` in the image (`apt-get install -y --no-install-recommends tini`) and use `ENTRYPOINT ["tini", "--", "invoice-sync"]`.
- `STOPSIGNAL SIGINT` makes `docker stop` send `SIGINT`, for programs that only handle Ctrl+C.
- `uv run` as the entry point adds a process and a project check at every start; call the venv's console script or `python -m` directly.

## Health checks without curl

`HEALTHCHECK` runs a command inside the container; exit 0 is healthy, 1 unhealthy. Slim and distroless images have no `curl` or `wget`, so check with Python itself: [scripts/healthcheck.py](scripts/healthcheck.py) probes an HTTP URL, a TCP port or a heartbeat file with the standard library. Without the script (a search-only install has no `scripts/` folder), one line of Python does each job; an uncaught exception exits 1, which Docker reads as unhealthy.

```dockerfile
# heartbeat file: unhealthy when older than 60 seconds or missing
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-I", "-c", "import os, sys, time; sys.exit(time.time() - os.stat('/tmp/heartbeat').st_mtime > 60)"]
# HTTP: urlopen raises on a status of 400 or above
# CMD ["python", "-I", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
# TCP port
# CMD ["python", "-I", "-c", "import socket; socket.create_connection(('127.0.0.1', 5432), timeout=3).close()"]
```

- A heartbeat file touched by the main loop proves the work loop runs, not only that a thread answers HTTP; use it for workers.
- `--start-period` gives slow starts time before failures count; `--start-interval` (Docker 25+) probes more often during that period.
- Docker only reports health (`docker ps`, `docker inspect`); restarting unhealthy containers is the orchestrator's job. Kubernetes ignores `HEALTHCHECK` and uses its own probes, which can run the same script.

## .dockerignore

The `.dockerignore` file keeps files out of the build context: a host `.venv` copied into the image breaks it (wrong platform, absolute paths), and `.git` or `.env` leak history and secrets.

```text
.venv
.git
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.mypy_cache/
dist/
build/
.env
*.local
```

Check what is sent with `docker buildx build --progress=plain .` (the "transferring context" size) and keep it small.

## Environment variables for Python in containers

A few environment variables make Python behave well in containers; set them in the final stage.

| Variable | Effect |
|---|---|
| `PYTHONUNBUFFERED=1` | stdout and stderr lines reach `docker logs` at once |
| `PYTHONFAULTHANDLER=1` | a segfault or a fatal error prints the Python stack |
| `PYTHONDONTWRITEBYTECODE=1` | no `.pyc` writes at run time (read-only file systems) |
| `PATH="/app/.venv/bin:$PATH"` | the venv's `python` and console scripts first; no "activate" needed |
| `TZ`, with `tzdata` as a dependency | slim images have a zone database; distroless and Alpine may not |

Python 3.15 uses UTF-8 by default; on 3.14 and older the official images set `LANG=C.UTF-8`, so file and console encoding are UTF-8 there too.

## Building for another architecture

An Apple silicon Mac builds `linux/arm64` images by default; most servers run `linux/amd64`. Build for the target explicitly, or for both in one multi-platform image.

```bash
docker buildx build --platform linux/amd64 -t registry.example.com/invoice-sync:1.4.0 --push .
docker buildx build --platform linux/amd64,linux/arm64 -t registry.example.com/invoice-sync:1.4.0 --push .
```

Emulated builds (QEMU) of the other architecture are several times slower, and source builds of C extensions under emulation can take very long; prefer dependencies with wheels for both, or native CI runners per architecture.

## Verify

Build the image, then prove the user, the stop time, the health state and the size. `invoice-sync` is this skill's example program.

```bash
docker build -t invoice-sync:probe .
docker image inspect --format '{{.Config.User}}' invoice-sync:probe    # app, never empty or root
docker run --rm --entrypoint id invoice-sync:probe -u                   # 10001
docker run -d --name probe invoice-sync:probe
sleep 35 && docker inspect --format '{{.State.Health.Status}}' probe    # healthy: the first probe runs after --interval
time docker stop probe                                                  # well under the 10 s timeout
docker rm probe
docker history --no-trunc invoice-sync:probe | grep -ciE "password|token|secret"   # expect 0
docker image ls invoice-sync:probe                                      # the size you expected
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `FROM python:latest` | a pinned tag or digest |
| `COPY . /app` before the dependency sync | the lock and `pyproject.toml` first, code after |
| running as root | a fixed non-root UID |
| shell-form `ENTRYPOINT` | exec form |
| `uv run` as the entry point | the venv's console script |
| a secret in `ENV`, `ARG` or a `COPY` | `--mount=type=secret` at build, the orchestrator at run time |
| `apk`, a compiler and an hour of builds for numpy | a slim glibc base with wheels |
| `curl` installed for a health check | Python's standard library |

## Checklist

An image is ready when every item holds.

- [ ] Multi-stage: uv and caches stay in the build stage; the final stage gets `.venv` built against the same Python path.
- [ ] `uv sync --locked --no-dev`, dependencies in their own cached layer, project installed `--no-editable`.
- [ ] uv and the base image are pinned (version or digest); `.dockerignore` excludes `.venv`, `.git` and `.env`.
- [ ] Runs as a fixed non-root UID; app files are read-only for it.
- [ ] Exec-form `ENTRYPOINT`; the program handles `SIGTERM` and stops within the grace period; `--init` or tini when it spawns children.
- [ ] `HEALTHCHECK` (or orchestrator probes) checks the work loop, not just the process.
- [ ] Built for the deployment architecture; size checked with `docker image ls` and `docker history`.

## Gotchas

These are the Python image problems seen most.

1. **`docker stop` always takes 10 seconds.** Python is PID 1 without a `SIGTERM` handler, or the shell-form `ENTRYPOINT` made `/bin/sh` PID 1. Use exec form and handle `SIGTERM`, or run with `--init`.
2. **`exec /app/.venv/bin/invoice-sync: no such file or directory` in the final stage.** The venv's shebang points at a Python path that does not exist there (different base image). Use the same base image in both stages.
3. **Every code change reinstalls all dependencies.** `COPY . /app` comes before the dependency sync. Sync with `--no-install-project` first.
4. **`uv sync` downloads Python inside the build.** `.python-version` asks for another version than the image has. Match them and set `UV_PYTHON_DOWNLOADS=never`.
5. **Alpine build compiles numpy or cryptography for twenty minutes, then fails.** No `musllinux` wheel for that version and no compiler. Use a slim glibc image, or pin versions with musl wheels.
6. **`PermissionError` writing logs or caches as the app user.** The program writes into its install directory or `$HOME` (none exists). Write to `/tmp` or a volume, or set `XDG_CACHE_HOME=/tmp/cache`.
7. **The image runs on the Mac but fails on the server with `exec format error`.** Built for arm64, run on amd64. Pass `--platform linux/amd64`.

## References

The reference files hold the longer comparisons and the supply-chain detail.

- [base-images-and-size.md](references/base-images-and-size.md): slim, Alpine and musl wheels, distroless with matching Python, uv-managed Python on a plain Debian base, system packages, and measuring and cutting image size
- [reproducible-and-secure-builds.md](references/reproducible-and-secure-builds.md): pinning by digest, `SOURCE_DATE_EPOCH` and hash-based `.pyc`, what stays non-reproducible with uv, build secrets, SBOMs and provenance, scanning, and signing
- [scripts/healthcheck.py](scripts/healthcheck.py): a standard-library health check for HTTP, TCP or a heartbeat file

## See also

- `uv-projects`: the basic Dockerfile, locks and CI
- `python-services-shutdown`: handling `SIGTERM` in the program
- `python-configuration`: settings from the environment
- `python-security`: secrets and supply chain
- `python-observability-otel`: telemetry from the container
