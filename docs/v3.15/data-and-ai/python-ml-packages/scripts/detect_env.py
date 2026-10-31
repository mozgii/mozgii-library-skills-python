# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright 2026 MOZGII, Inc. (https://mozgii.com/)
"""Detect the hardware and Python environment and suggest where ML packages should come from.

Usage:
    python3 detect_env.py            # human-readable summary
    python3 detect_env.py --json     # one JSON object, for scripts

Standard library only; never installs anything. Runs nvidia-smi and rocm-smi only when they are on PATH.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TypedDict


class Env(TypedDict):
    label: str
    os: str
    machine: str
    python: str
    python_arch_native: bool
    os_version: str
    cuda_driver: str | None
    rocm: str | None
    torch_index: str | None
    mlx_install: str | None
    notes: list[str]


def run(cmd: list[str]) -> str:
    """Run a tool and return stdout, or "" when it is missing or fails."""
    if shutil.which(cmd[0]) is None:
        return ""
    try:
        done = subprocess.run(cmd, check=False, timeout=10, capture_output=True, text=True)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout if done.returncode == 0 else ""


def cuda_driver_version() -> str | None:
    """The highest CUDA version the installed NVIDIA driver supports, from nvidia-smi's header."""
    match = re.search(r"CUDA Version:\s*([0-9.]+)", run(["nvidia-smi"]))
    return match.group(1) if match else None


def rocm_version() -> str | None:
    if run(["rocm-smi", "--showdriverversion"]) or os.path.isdir("/opt/rocm"):
        try:
            return Path("/opt/rocm/.info/version").read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            return "unknown"
    return None


def rosetta() -> bool:
    """True when this Python is an x86_64 process translated by Rosetta on an Apple silicon Mac."""
    return run(["sysctl", "-n", "sysctl.proc_translated"]).strip() == "1"


def torch_index_for(cuda: str | None) -> str:
    """Pick the newest PyTorch CUDA index the driver can run (torch 2.15: cu132, cu134; cu126 and cu130 stop at 2.14)."""
    if cuda is None:
        return "https://download.pytorch.org/whl/cpu"
    major, _, minor = cuda.partition(".")
    version = (int(major), int(minor or 0))
    for tag, needed in (("cu134", (13, 4)), ("cu132", (13, 2)), ("cu130", (13, 0)), ("cu126", (12, 6))):
        if version >= needed:
            return f"https://download.pytorch.org/whl/{tag}"
    return "https://download.pytorch.org/whl/cpu"


def detect() -> Env:
    system = platform.system()
    machine = platform.machine().lower()
    notes: list[str] = []
    native = True
    cuda = cuda_driver_version() if system in ("Linux", "Windows") else None
    rocm = rocm_version() if system == "Linux" else None
    torch_index: str | None = None
    mlx: str | None = None

    if system == "Darwin":
        os_version = platform.mac_ver()[0]
        if machine == "arm64" or rosetta():
            label = "apple-silicon"
            if rosetta():
                native = False
                notes.append("This Python runs under Rosetta (x86_64): install a native arm64 Python for MLX and MPS.")
            torch_index = "PyPI (macOS wheels include MPS)"
            mlx = "pip install mlx mlx-lm" if os_version.split(".")[0].isdigit() and int(os_version.split(".")[0]) >= 14 else None
            if mlx is None:
                notes.append("MLX needs macOS 14 or newer.")
        else:
            label = "macos-intel"
            notes.append("PyTorch publishes no macOS x86_64 wheels after 2.2: use torch<=2.2.2 or another backend.")
    elif system == "Linux":
        os_version = "glibc " + (platform.libc_ver()[1] or "unknown")
        if cuda:
            label = "linux-cuda"
            torch_index = torch_index_for(cuda)
            mlx = "pip install 'mlx[cuda13]'" if float(cuda) >= 13 else "pip install 'mlx[cuda12]'"
        elif rocm:
            label = "linux-rocm"
            torch_index = "https://download.pytorch.org/whl/rocm10.0"
        else:
            label = "linux-cpu"
            torch_index = torch_index_for(None)
            mlx = "pip install 'mlx[cpu]'"
    elif system == "Windows":
        os_version = platform.version()
        label = "windows-cuda" if cuda else "windows-cpu"
        torch_index = torch_index_for(cuda)
    else:
        os_version = platform.version()
        label = "unknown"
        notes.append(f"Unrecognised OS {system!r}: install CPU builds and check each package's wheels.")

    if sys.version_info < (3, 10):
        notes.append("Python older than 3.10: current torch, MLX and most ML packages no longer support it.")
    gil_on = getattr(sys, "_is_gil_enabled", lambda: True)()
    if not gil_on:
        notes.append("Free-threaded Python: many ML packages have no free-threaded (cp3XXt) wheels yet.")

    return {
        "label": label,
        "os": system,
        "machine": machine,
        "python": platform.python_version(),
        "python_arch_native": native,
        "os_version": os_version,
        "cuda_driver": cuda,
        "rocm": rocm,
        "torch_index": torch_index,
        "mlx_install": mlx,
        "notes": notes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="detect_env.py", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args()
    env = detect()
    if args.json:
        print(json.dumps(env, indent=2))
        return 0
    for key, value in env.items():
        if key != "notes":
            print(f"{key:20s} {value}")
    for note in env["notes"]:
        print(f"note: {note}")
    return 0


if __name__ == "__main__":
    status = main()
    sys.exit(status)
