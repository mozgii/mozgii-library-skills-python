---
name: python-ml-packages
description: Detects the machine (Apple silicon, Intel Mac, Linux or Windows with NVIDIA CUDA, AMD ROCm or CPU only) and the Python version, then routes ML package installs to the right builds - MLX on Apple silicon, the PyTorch CUDA or ROCm index that matches the driver, CPU wheels otherwise - with uv or pip commands, uv project config for per-platform torch, and checks for packages that cannot install on the machine. Use before installing or adding torch, MLX, transformers, bitsandbytes, xformers, flash-attn, triton or onnxruntime, or when a GPU package fails to install or does not see the GPU.
metadata:
  tags:
  - python
  - packages
  - mlx
  - pytorch
  - cuda
  - rocm
  - apple-silicon
  - uv
  - ml
  - gpu
---

# Routing ML packages by hardware

ML packages ship different builds per platform and accelerator: PyTorch publishes CPU, CUDA, ROCm and XPU wheels on separate indexes, MLX runs natively on Apple silicon and has CUDA and CPU builds on Linux, and some packages exist only for NVIDIA. Installing the default build from PyPI on the wrong machine either fails or silently runs on the CPU. This skill detects the machine first and routes each install.

## When to use this skill

Run it before any install of an accelerator-dependent package, and when a model runs unexpectedly slowly.

- Adding `torch`, `torchvision`, `torchaudio`, `mlx`, `mlx-lm`, `mlx-vlm`, `transformers`, `bitsandbytes`, `xformers`, `flash-attn`, `triton`, `onnxruntime`, `vllm` or `llama-cpp-python`.
- `torch.cuda.is_available()` is `False` on a machine with an NVIDIA GPU.
- A lockfile or `requirements.txt` moves between a Mac and a Linux GPU box.

## Step 0: detect the environment

Detection reads the OS, CPU architecture, accelerator driver and the Python version the project will run.

```bash
python3 scripts/detect_env.py            # label, driver versions, suggested torch index and MLX command
python3 scripts/detect_env.py --json     # the same as JSON
```

A library installed from a pack holds passages, not files, so that script may not be on disk. This inline version prints the label and the torch index the same way, with nothing to install:

```bash
python3 - <<'PY'
import platform, re, shutil, subprocess


def out(*cmd: str) -> str:
    if shutil.which(cmd[0]) is None:
        return ""
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return done.stdout if done.returncode == 0 else ""


system, machine = platform.system(), platform.machine().lower()
translated = out("sysctl", "-n", "sysctl.proc_translated").strip() == "1"
found = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", out("nvidia-smi"))
cuda = (int(found[1]), int(found[2])) if found else None
tags = (("cu134", (13, 4)), ("cu132", (13, 2)), ("cu130", (13, 0)), ("cu126", (12, 6)))
index = next((tag for tag, needed in tags if cuda and cuda >= needed), "cpu")
if system == "Darwin":
    arm = machine == "arm64" or translated
    label, index = ("apple-silicon", "PyPI") if arm else ("macos-intel", "torch<=2.2.2 from PyPI")
elif system == "Linux" and cuda:
    label = "linux-cuda"
elif system == "Linux" and shutil.which("rocm-smi"):
    label, index = "linux-rocm", "rocm10.0"
elif system == "Linux":
    label = "linux-cpu"
else:
    label = "windows-cuda" if cuda else "windows-cpu"
print(label, platform.python_version(), "torch index:", index, "(rosetta)" if translated else "")
PY
```

The same facts by hand:

```bash
uname -s; uname -m                                   # Darwin/Linux, arm64/x86_64/aarch64
sysctl -n sysctl.proc_translated 2>/dev/null         # 1 = this shell runs under Rosetta on Apple silicon
nvidia-smi | grep "CUDA Version"                     # highest CUDA version the driver supports
rocm-smi --showdriverversion 2>/dev/null; cat /opt/rocm/.info/version 2>/dev/null
uv run python -c "import sys, platform; print(sys.version, platform.machine())"
cat .python-version 2>/dev/null; grep -n "requires-python" pyproject.toml 2>/dev/null
```

Use the most specific Python version found: the project's pin (`.python-version`, `requires-python`) beats the active interpreter.

| Label | Conditions |
|---|---|
| `apple-silicon` | Darwin and arm64 (or x86_64 under Rosetta: fix the interpreter first) |
| `macos-intel` | Darwin and x86_64, not translated |
| `linux-cuda` | Linux and `nvidia-smi` reports a CUDA version |
| `linux-rocm` | Linux with ROCm installed (`/opt/rocm`) and an AMD GPU |
| `linux-cpu` | Linux without a supported GPU |
| `windows-cuda` / `windows-cpu` | Windows with or without an NVIDIA driver |

## Python version limits

Current ML packages have dropped old Python versions, so check the version before choosing package versions.

| Python | Status (October 2026) |
|---|---|
| 3.9 and older | end of life; current torch, MLX, pydantic and numpy do not support it |
| 3.10 | end of life since October 2026; torch 2.15 needs 3.11 or newer (2.14 is the last with `cp310` wheels), MLX still ships them, many newer packages need 3.11 |
| 3.11 to 3.13 | broadly supported; `mlx-lm`, `onnxruntime` and `triton` need 3.11 or newer |
| 3.14 | supported by torch 2.15, MLX, onnxruntime; torch 2.15.0 ships free-threaded `cp314t` wheels on PyPI |
| 3.15 | new (October 2026); torch and MLX publish `cp315` wheels, torch 2.15.0 also `cp315t` on PyPI; many packages lag: check PyPI before choosing it for ML |

If the project's Python is outside a package's range, say so and pick the newest package version that supports it, or move the project's Python.

## Step 1: route the packages

Each label gets its own source for the accelerator-dependent packages; everything else comes from PyPI.

### apple-silicon

Apple silicon has two native paths: MLX (Apple's array framework) and PyTorch with the MPS backend.

| Need | Install |
|---|---|
| LLM inference and fine-tuning | `uv add mlx mlx-lm` (MLX needs macOS 14+ and a native arm64 Python 3.10+; `mlx-lm` needs 3.11+) |
| vision-language models | `uv add mlx-vlm` |
| PyTorch code | `uv add torch torchvision torchaudio` from PyPI; the macOS wheels include MPS |
| quantization | MLX's own quantization (`mlx_lm.convert --quantize`); `bitsandbytes` has a macOS arm64 wheel, but check that the method you need runs on MPS |
| `flash-attn`, `xformers`, `triton` | not available: remove them; use PyTorch's `scaled_dot_product_attention` |

Check the interpreter is native: `python -c "import platform; print(platform.machine())"` must print `arm64`. An `x86_64` result on an Apple silicon Mac means a Rosetta Python, which cannot load MLX or MPS.

### linux-cuda and windows-cuda

NVIDIA machines take PyTorch from the CUDA index that the driver supports; the driver's "CUDA Version" is the maximum, and older CUDA builds run on newer drivers.

| `nvidia-smi` CUDA Version | Index | Newest torch there |
|---|---|---|
| 13.4 or newer | `https://download.pytorch.org/whl/cu134` | 2.15 |
| 13.2 or 13.3 | `https://download.pytorch.org/whl/cu132` | 2.15 |
| 13.0 or 13.1 | `https://download.pytorch.org/whl/cu130` | 2.14: pin `torch==2.14.1`, or update the driver for 2.15 |
| 12.6 to 12.9 | `https://download.pytorch.org/whl/cu126` | 2.14: pin `torch==2.14.1` |
| older than 12.6 | update the driver, or use an older torch with that CUDA build | |

```bash
uv pip install torch torchvision torchaudio --torch-backend=auto     # uv picks the index from the driver
uv pip install torch --index-url https://download.pytorch.org/whl/cu134   # pip-compatible, explicit
pip install torch --index-url https://download.pytorch.org/whl/cu134
```

`--torch-backend` in uv 0.13.6 stops at `cu132` and `rocm7.2`, so it cannot select `cu134`, `rocm7.14` or `rocm10.0` yet; use `--index-url` for those.

- `bitsandbytes`, `xformers`, `triton` and `vllm` are fine on Linux with CUDA; `flash-attn` ships only a source distribution and compiles against your CUDA toolkit (set `MAX_JOBS`, expect a long build) unless you use a prebuilt wheel matching torch, CUDA and Python exactly.
- MLX also runs here: `uv add "mlx[cuda12]"` or `"mlx[cuda13]"` (SM 7.5+ GPU, glibc 2.35+).
- `onnxruntime-gpu` replaces `onnxruntime`; never install both.

### linux-rocm

AMD GPUs on Linux use PyTorch's ROCm index: `uv pip install torch --index-url https://download.pytorch.org/whl/rocm10.0` (torch 2.15 also has `rocm7.14`; `rocm7.2` stops at torch 2.14). `torch.cuda.is_available()` is `True` on ROCm builds (HIP reuses the CUDA API names). CUDA-only packages (`flash-attn` CUDA builds, `bitsandbytes` CUDA kernels, `xformers`) need their ROCm variants or must go.

### linux-cpu, windows-cpu and macos-intel

Without a GPU, use CPU wheels so you do not download gigabytes of CUDA libraries.

```bash
uv pip install torch --torch-backend=cpu
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

- Intel Macs: PyTorch publishes no macOS x86_64 wheels after 2.2.2. Pin `torch<=2.2.2` with a Python it supports (3.12 at most), or run the model with ONNX Runtime or llama.cpp instead.
- Remove `bitsandbytes` CUDA paths, `flash-attn`, `triton` and `xformers`; they need a GPU runtime.
- On Linux CPU, `uv add "mlx[cpu]"` gives MLX's CPU backend.

## Step 2: put the routing into the project

A uv project records the routing in `pyproject.toml`, so every machine resolves the same way and `uv sync` needs no flags.

```toml
[project]
name = "my-app"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["torch==2.15.0", "torchvision"]

[tool.uv.sources]
torch = [
  { index = "pytorch-cpu", marker = "sys_platform == 'win32'" },
  { index = "pytorch-cu134", marker = "sys_platform == 'linux'" },
]                                        # macOS falls through to PyPI, which carries the MPS build
torchvision = [
  { index = "pytorch-cpu", marker = "sys_platform == 'win32'" },
  { index = "pytorch-cu134", marker = "sys_platform == 'linux'" },
]

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true                          # only packages that name this index come from it

[[tool.uv.index]]
name = "pytorch-cu134"
url = "https://download.pytorch.org/whl/cu134"
explicit = true
```

- `explicit = true` keeps the PyTorch index for torch packages only; every other dependency still resolves from PyPI.
- When one platform needs both a CPU and a CUDA variant (two app builds), declare them as extras (`cpu`, `cu134`) and add `[tool.uv] conflicts = [[{ extra = "cpu" }, { extra = "cu134" }]]`, then `uv sync --extra cu134`.
- Pin one torch version that every chosen index carries; otherwise the lock can hold different versions per platform. Check with `grep -o 'torch-[^"]*\.whl' uv.lock | sort -u`.
- `--torch-backend` is a `uv pip` option (and `UV_TORCH_BACKEND`); projects use `[tool.uv.sources]` as above.

## Step 3: validate the dependency files

Scan `pyproject.toml`, `requirements*.txt` and `uv.lock` for entries the machine cannot use before installing.

| Label | Flag and fix |
|---|---|
| `apple-silicon` | `flash-attn`, `xformers`, `triton`, `onnxruntime-gpu`, any `+cuXXX` or `nvidia-*` pin: remove or mark with `sys_platform == 'linux'` |
| `macos-intel` | torch newer than 2.2.2: pin or replace |
| CPU labels | CUDA-only packages and `+cuXXX` local versions: switch to the CPU index |
| `linux-cuda` | a `+cuXXX` newer than the driver supports: pick an older index or update the driver |
| every label | pinned versions whose `requires-python` excludes the project's Python: choose the newest version that fits |

Environment markers keep one file valid on every machine: `"triton; sys_platform == 'linux'"`.

## Step 4: check the device in code

Pick the device at runtime so the same code runs on every label.

```python
import torch


def pick_device() -> torch.device:
    if torch.cuda.is_available():                 # NVIDIA CUDA and AMD ROCm builds
        return torch.device("cuda")
    if torch.backends.mps.is_available():         # Apple silicon
        return torch.device("mps")
    return torch.device("cpu")
```

```python
import mlx.core as mx

print(mx.default_device())                        # Device(gpu, 0) on Apple silicon or a CUDA build
```

Verify after every install: `uv run python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.backends.mps.is_available())"`. A `+cpu` suffix on a GPU machine means the wrong index was used.

## Verify

Prove the install by running a real operation on the device, because a successful install says nothing about the GPU.

```bash
uv run python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.backends.mps.is_available())"
uv run python - <<'PY'
import torch

device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
print(device, (torch.ones(2, device=device) * 2).sum().item())   # expect your accelerator and 4.0
PY
uv run python -c "import mlx.core as mx; print(mx.default_device(), mx.array([1, 2]).sum().item())"   # expect Device(gpu, 0) and 3
grep -o 'torch-[^"]*\.whl' uv.lock | sort -u               # one torch version across the indexes
```

A `+cpu` suffix on a GPU machine, or `cpu` from the second command, means the wrong index was used.

## Anti-patterns

| Instead of | Do |
|---|---|
| `pip install torch` before detecting the machine | detect first, then pick the index |
| `--extra-index-url` for torch | `--index-url`, or an `explicit = true` uv index |
| a `+cu134` pin in a shared dependency file | `[tool.uv.sources]` with platform markers |
| `onnxruntime` and `onnxruntime-gpu` together | one of them |
| a Rosetta Python on Apple silicon | a native arm64 interpreter |
| different torch versions per platform by accident | one pin that every chosen index carries |

## Pre-merge checklist

Check each item before reporting an install as done.

- [ ] The label and the project's Python version were detected, not assumed.
- [ ] torch came from the index that matches the label; the version check printed the expected suffix and device.
- [ ] No package the machine cannot run is left in the dependency files without a platform marker.
- [ ] Per-platform routing lives in `[tool.uv.sources]` for uv projects, with `explicit = true` indexes.
- [ ] One torch version is pinned across all indexes the lock uses.

## Gotchas

These are the install failures this routing prevents.

1. **CUDA libraries on a CPU box.** Linux PyPI torch wheels pull several GB of `nvidia-*` packages. Use the CPU index on machines without a GPU.
2. **`torch.cuda.is_available()` is False with a GPU present.** A `+cpu` build was installed, or the build's CUDA is newer than the driver. Reinstall from the matching index.
3. **MLX import fails on a new Mac.** The Python is x86_64 under Rosetta, or macOS is older than 14. Use a native arm64 Python.
4. **The lock resolves different torch versions per OS.** Indexes carry different newest versions. Pin `torch==<version>` that all of them carry.
5. **`flash-attn` build runs for an hour or fails.** It compiles from source against the local CUDA toolkit. Prefer PyTorch SDPA, or a prebuilt wheel matching torch, CUDA and Python exactly.
6. **A torch build that depends on luck.** With pip's `--extra-index-url`, every index is equal and pip takes the best match from any of them, so the build you get depends on versions and wheel tags, and a same-named package on the extra index can shadow PyPI. Install torch alone with `--index-url`, or use uv's `explicit = true` index scoped to the torch packages.

## See also

- `uv-projects`: indexes, sources and lockfiles
- `python-native-extensions`: why wheels differ per platform and ABI
- `python-version-upgrade`: moving the project's Python
- `python-containers`: GPU base images and the same routing in Docker
- `numpy`: the array layer most of these packages share
