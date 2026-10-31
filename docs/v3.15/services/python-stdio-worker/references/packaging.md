---
title: Packaging a worker with a relocatable CPython
author: MOZGII, Inc.
lang: en
---

# Packaging a worker with a relocatable CPython

Ship the worker as a prebuilt, relocatable CPython with every dependency already installed, inside the app's resources, so nothing is resolved or downloaded on the user's machine. Installing dependencies at first launch fails offline, behind proxies and when an index changes. Everything here ran on macOS arm64: build, move to another folder, contract suite against the moved runtime. Windows and Linux follow the same steps and are marked where they differ.

## Contents

This reference has 9 sections.

1. Project and lockfile
2. Accelerator wheels (torch) per platform
3. Build script per target
4. Bytecode
5. Runtime flags, dev and packaged
6. Size budget
7. Licences
8. Hand-off to signing and packaging
9. Many worker kinds with conflicting dependencies

## 1. Project and lockfile

The worker is one uv project with one universal lockfile.

```bash
uv init --package --build-backend uv --python 3.14 --name my-app-worker workers   # src/ layout, backend, .python-version
cd workers                                                          # set requires-python to ">=3.14,<3.15" (the shipped minor)
uv add "psycopg[binary]" psycopg-pool && uv add --dev pytest pyright
uv lock                                                             # one universal uv.lock for every target
```

A `pyproject.toml` for the worker (the `uv_build` bound is the one `uv init` writes for uv 0.13):

```toml
[project]
name = "my-app-worker"
version = "0.1.0"
requires-python = ">=3.14,<3.15"
dependencies = ["psycopg[binary]>=3.3", "psycopg-pool>=3.3"]

[dependency-groups]
dev = ["pytest", "pyright"]

[build-system]
requires = ["uv_build>=0.13,<0.14"]
build-backend = "uv_build"
```

- `requires-python` with an upper bound pins the minor the lock was made for; the bundle and the lock must agree, because wheels with C extensions are built per minor.
- Use binary wheels (`psycopg[binary]`, not a build from source): python-build-standalone's `sysconfig` records build-machine paths, so compiling C extensions on another machine is fragile, and the user's machine has no compiler anyway.
- Workers whose dependencies conflict (two ML stacks that pin different torch or numpy) get separate projects and separate runtimes (`workers/media`, `workers/speech`), each with its own lock. It costs one runtime per kind in size.
- `uv lock --check` in CI fails when `pyproject.toml` changed without a new lock; the build then syncs with `--frozen`, which installs exactly what the lock says without re-resolving.

## 2. Accelerator wheels (torch) per platform

Accelerator wheels such as torch come from per-platform indexes. PyTorch publishes CPU, CUDA and other builds on separate indexes, encoded in the local version (`+cpu`, `+cu130`); macOS wheels come from PyPI and include MPS. Limit resolution to the targets you ship and route torch per platform:

```toml
[project]
name = "my-app-worker"
version = "0.1.0"
requires-python = ">=3.14,<3.15"
dependencies = ["torch"]                  # pin one version every index below carries (§2)

[tool.uv]
environments = ["sys_platform == 'darwin' and platform_machine == 'arm64'", "sys_platform == 'win32' and platform_machine == 'AMD64'", "sys_platform == 'linux' and platform_machine == 'x86_64'"]

[tool.uv.sources]
torch = [{ index = "pytorch-cu134", marker = "sys_platform != 'darwin'" }]

[[tool.uv.index]]
name = "pytorch-cu134"
url = "https://download.pytorch.org/whl/cu134"
explicit = true
```

- `explicit = true` keeps the PyTorch index for torch only; every other package still comes from PyPI.
- `tool.uv.environments` makes the universal lock cover only the shipped targets, which keeps resolution fast (4 s in the live run) and the lock small.
- One lock can hold different torch versions per platform: the live lock chose torch from PyPI for macOS and an older CUDA build for Windows and Linux, because that CUDA index's newest release was older. Pin `torch==<version>` that every chosen index carries, or move to a newer CUDA index, and check `grep -o 'torch-[^"]*\.whl' uv.lock`.
- Read wheel tags against the app's minimum OS: a `macosx_14_0_arm64` wheel needs macOS 14 or newer.
- CUDA wheels pull a dozen `nvidia-*` packages measured in gigabytes; a CPU-only Windows build and a CUDA Windows build are two different app builds (two environments or extras with `[tool.uv] conflicts`), decided before packaging.
- During development on one machine, the `python-ml-packages` skill routes installs by hardware; the bundle always follows the lock.
- torch 2.15 publishes `cpu`, `cu132`, `cu134`, `rocm7.14`, `rocm10.0` and `xpu` builds, with wheels for Python 3.11 and newer; macOS wheels exist for Apple silicon only (`macosx_14_0_arm64`).

## 3. Build script per target

The build script makes one runtime per target. Run on a machine (or CI runner) of each target: `uv sync` installs wheels for the interpreter it runs, and native wheels do not cross platforms.

```bash
#!/usr/bin/env bash
# Build resources/bin/<target>/python from workers/ on a machine of that target.
# Usage: scripts/build-python.sh darwin-arm64      (darwin-x64, linux-x64, win32-x64 under Git Bash)
set -euo pipefail
TARGET=${1:?usage: build-python.sh <platform-arch>}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$ROOT/resources/bin/$TARGET/python
PBS=$ROOT/build/pbs
cd "$ROOT/workers"
uv lock --check                                              # fail when pyproject.toml changed without a new lock
PYV=$(cat .python-version)
NAME=$(sed -n 's/^name = "\(.*\)"/\1/p' pyproject.toml | head -1)
uv python install "$PYV" --install-dir "$PBS" --no-bin --no-registry
SRC=$(ls -d "$PBS"/cpython-"$PYV".*-*/ | sort -V | tail -1)  # the versioned folder, not the minor-version link
case "$TARGET" in
  win32-*) PYBIN=$OUT/python.exe; LIB=$OUT/Lib; SITE=$OUT/Lib/site-packages ;;
  *)       PYBIN=$OUT/bin/python3; LIB=$OUT/lib; SITE=$OUT/lib/python$PYV/site-packages ;;
esac
rm -rf "$OUT" && mkdir -p "$(dirname "$OUT")" && cp -R "$SRC" "$OUT"
UV_PROJECT_ENVIRONMENT=$OUT uv sync --frozen --no-dev --no-editable --reinstall-package "$NAME" --python "$PYBIN"   # rebuild the worker: uv's cache keys only pyproject.toml
rm -rf "$SITE"/pip "$SITE"/pip-*.dist-info "$OUT"/bin/pip* "$LIB"/python"$PYV"/{idlelib,ensurepip,turtledemo}
"$PYBIN" -I -m compileall -q -j 0 -f --invalidation-mode unchecked-hash "$LIB" >/dev/null
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"$/ping","params":null}' \
  | "$PYBIN" -I -B -u -X utf8 -m my_app_worker 2>/dev/null | grep -q '"id":1,"result":{}' || { echo "smoke test failed"; exit 1; }
du -sh "$OUT"
```

- The runtime is installed into its own prefix (`UV_PROJECT_ENVIRONMENT` set to the CPython folder), not into a venv. A venv's `pyvenv.cfg` holds an absolute `home =` and `bin/python` is an absolute symlink: after `uv venv --relocatable` and a move, `bin/python` was "no such file or directory" in the live run. The prefix itself moved cleanly: `sys.prefix` followed, psycopg's bundled libpq loaded, and no old path remained in `site-packages`.
- `--reinstall-package "$NAME"` rebuilds the worker package on every build. uv rebuilds a local project only when `pyproject.toml`, `setup.py` or `setup.cfg` changes (or a `src` folder appears or disappears); without the flag a build after a code-only change shipped the previous `__main__.py` and no new methods in the live run. The alternative is `[tool.uv] cache-keys = [{ file = "pyproject.toml" }, { file = "src/**/*.py" }]`.
- `uv sync` accepted the managed interpreter's `EXTERNALLY-MANAGED` marker in project mode; `--no-editable` installs the worker package itself, so `-I -m my_app_worker` works from any cwd.
- Use the versioned folder (`cpython-<minor>.<patch>-...`), not the minor-version link next to it; with a relative `--install-dir` that link pointed at a wrong relative path in the live run.
- Windows layout: `python.exe` and `Lib/site-packages` at the top of the folder, no `bin/`; the managed install has no `pip.exe` (use `python -m pip` if ever needed). The Windows branch of the script has not been run here.
- `--no-registry` keeps a Windows build machine's registry clean (managed installs register themselves under PEP 514 otherwise).

Verify after every build, on the build machine:

```bash
mv resources /tmp/moved && /tmp/moved/bin/darwin-arm64/python/bin/python3 -I -c "import sys, my_app_worker; print(sys.prefix)"
WORKER_CMD="/tmp/moved/bin/darwin-arm64/python/bin/python3 -I -B -u -X utf8 -m my_app_worker" uv run pytest -q tests/test_contract.py
mv /tmp/moved resources
```

## 4. Bytecode

Bytecode mode decides whether a moved bundle recompiles every module at start.

| Mode | What Python checks at import | After a copy that drops mtimes, with `-B` |
|---|---|---|
| timestamp (default; also what `uv sync --compile-bytecode` writes) | source mtime and size recorded in the `.pyc` | stale: recompiled in memory on every start, never rewritten |
| checked-hash | hashes every source file and compares | valid, but every import reads and hashes the source |
| unchecked-hash | nothing: the `.pyc` is used if it exists | valid |

Measured start of `import psycopg, psycopg_pool, my_app_worker.runtime` with `-B`: no bytecode 130 ms, timestamp 72 ms, timestamp after `touch` 124 ms, checked-hash 75 ms, unchecked-hash 70–73 ms.

- Compile with `--invalidation-mode unchecked-hash` as the last step that changes files, before signing: the bundle is signed and replaced whole on update, so its sources never change under the bytecode.
- `-f` recompiles files uv already compiled; `-j 0` uses every core. Check the result: the 4-byte flags word after the magic number is `1` for unchecked-hash.
- `-B` at runtime stops Python from writing `__pycache__` into the signed, possibly read-only bundle.

## 5. Runtime flags, dev and packaged

Runtime flags replace the `PYTHON*` variables that `-I` ignores.

| Flag | Why | Replaces |
|---|---|---|
| `-I` | no user site-packages, no cwd or script dir on `sys.path`, every `PYTHON*` variable ignored | defence against `PYTHONPATH`, `~/.local` packages, a planted module in the cwd |
| `-B` | no `.pyc` writes into the bundle | `PYTHONDONTWRITEBYTECODE=1` (ignored under `-I`) |
| `-u` | stderr logs leave at once | `PYTHONUNBUFFERED=1` (ignored under `-I`) |
| `-X utf8` | UTF-8 for files, pipes and paths on every OS | `PYTHONUTF8=1` (ignored under `-I`) |

```bash
# the command line a host runs; packaged: <resources>/bin/<target>/python/bin/python3
"$PY" -I -B -u -X utf8 -m my_app_worker --role=media
```

- `-I` and `PYTHON*` variables in the host's environment: under `-I` those variables have no effect; the flags carry their meaning. The live check showed `PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 python -I` with both off, and `-I -B -u -X utf8` with both on.
- `-m my_app_worker` under `-I` needs the package installed in the interpreter: with only the cwd set to the project, `-I -m my_app_worker` failed with "No module named my_app_worker".
- Development: point the host's Python at `workers/.venv/bin/python` (`uv sync` in `workers/` installs the package editable; its `.pth` is in site-packages, which `-I` still reads, so the same args work and edits apply on restart). Run the built runtime before each release and in CI.
- Spawned process-pool children inherit `-I` and `-B` from the parent.

## 6. Size budget

The size budget is checked per target after every build. Measured on macOS arm64: managed CPython 50 MB as installed (pip 12 MB, `libpython` 17 MB, Tcl/Tk about 5 MB, `idlelib` 2.3 MB, `ensurepip` 1.8 MB); with psycopg and unchecked-hash bytecode after the trims in the script, 72 MB.

```bash
du -sh resources/bin/*/python                                      # total per target
du -sh resources/bin/darwin-arm64/python/lib/python*/site-packages/* | sort -h | tail -15
```

- Set a budget per target and fail the build above it; GPU stacks dominate (a CUDA torch install alone is several GB).
- Safe to remove: pip, `ensurepip`, `idlelib`, `turtledemo`, test folders. Remove `tkinter` with `lib/tcl*`, `lib/tk*` and `_tkinter` only if no dependency imports it (`grep -rl "import tkinter" site-packages`).
- Never delete `lib-dynload` modules or `.dylib`/`.so` files to save space without running the contract suite and the app's methods afterwards.

## 7. Licences

Licence notices for the bundled interpreter and wheels ship with the app.

- python-build-standalone builds CPython against libedit instead of readline and disables `_gdbm` to avoid GPL components; releases before 2023 may link readline.
- The managed install that uv downloads carries CPython's `lib/python<minor>/LICENSE.txt`, which does not cover OpenSSL, SQLite, libffi or xz. The `BUILD` file names the python-build-standalone release; take the component licence texts and `PYTHON.json` licence metadata from that release's full archive into the app's third-party notices.
- Each installed wheel carries its licences in `*.dist-info/licenses/`; collect them with the bundle (`uv pip list --python "$PY" --format json` for the inventory).

## 8. Hand-off to signing and packaging

The hand-off puts the runtime where the app's packager copies plain files.

- The runtime goes into the app's resources as plain files (in Electron, `extraResources`, never inside `app.asar`, which cannot hold executables that `spawn` runs), at a path such as `<resources>/bin/<platform>-<arch>/python`; the worker package is installed inside it.
- Every Mach-O in the runtime (the interpreter, `libpython`, each `.so`; 22 files in the measured build) must be signed with the hardened runtime before notarization; Windows `.pyd`/`.dll` files are signed too. Order: build → compile bytecode → sign → package. Signing and notarization follow the app packager's own steps.

## 9. Many worker kinds with conflicting dependencies

Worker kinds share an interpreter only when their dependencies resolve together; otherwise each group gets its own environment.

```
tasks/
  _runtime/            package my-app-runtime: the skeleton as a library, stdlib only
  _envs/ml/            group: pyproject.toml (virtual), uv.lock, .python-version
  _envs/base/          another group for light kinds
  embed-texts/         package my-app-embed-texts: its own deps, src/my_app_embed_texts/__main__.py, tests/
  transcribe/          package my-app-transcribe
```

A group is a virtual project whose dependencies are its member kinds:

```toml
# tasks/_envs/ml/pyproject.toml
[project]
name = "my-app-env-ml"
version = "0"
requires-python = "==3.14.*"
dependencies = ["my-app-runtime", "my-app-embed-texts", "my-app-transcribe"]

[tool.uv]
package = false

[tool.uv.sources]
my-app-runtime = { path = "../../_runtime" }
my-app-embed-texts = { path = "../../embed-texts" }
my-app-transcribe = { path = "../../transcribe" }
```

- A kind declares only its own dependencies; `uv lock` in the group resolves them together. A kind that cannot resolve with the others moves to a new group, which costs one more CPython in the bundle.
- `my-app-runtime` stays stdlib-only, so it never conflicts with a group.
