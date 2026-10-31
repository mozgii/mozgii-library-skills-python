# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright 2026 MOZGII, Inc. (https://mozgii.com/)
"""Inspect built wheels: tags, extension modules and common mistakes.

Reads each wheel (a zip file) with the standard library only and reports:
- the Python, ABI and platform tags from the file name and the WHEEL metadata,
- every compiled extension module inside (.so, .pyd) and the ABI its suffix names,
- problems: a platform tag PyPI refuses (linux_x86_64), a macOS deployment target
  newer than --max-macos, an abi3 wheel holding version-specific modules, a
  free-threaded wheel without a free-threaded module (or the reverse), and a
  pure-Python tag on a wheel that contains compiled code.

Exit status: 0 when no problems were found, 1 when any wheel has a problem,
2 on bad arguments or unreadable files.

Examples:
    python3 -I wheel_tags.py dist/*.whl
    python3 -I wheel_tags.py --max-macos 11.0 --json wheelhouse/*.whl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

EXT_RE = re.compile(r"\.(?:(?P<tag>[A-Za-z0-9_\-]+)\.)?(?P<ext>so|pyd)$")
MACOS_RE = re.compile(r"^macosx_(\d+)_(\d+)_")


@dataclass
class Report:
    wheel: str
    name: str = ""
    version: str = ""
    python_tags: list[str] = field(default_factory=list)
    abi_tags: list[str] = field(default_factory=list)
    platform_tags: list[str] = field(default_factory=list)
    root_is_purelib: str = ""
    extensions: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def parse_name(path: Path, report: Report) -> None:
    parts = path.name[: -len(".whl")].split("-")
    if len(parts) not in (5, 6):
        report.problems.append("file name does not have 5 or 6 dash-separated parts")
        return
    report.name, report.version = parts[0], parts[1]
    py, abi, plat = parts[-3:]
    report.python_tags = py.split(".")
    report.abi_tags = abi.split(".")
    report.platform_tags = plat.split(".")


def module_abi(member: str) -> str:
    match = EXT_RE.search(member)
    if match is None:
        return ""
    tag = match.group("tag")
    if match.group("ext") == "pyd":
        return tag or "abi3 or untagged (Windows)"
    return tag or "untagged"


def check(report: Report, max_macos: tuple[int, int] | None) -> None:
    compiled = bool(report.extensions)
    if compiled and report.abi_tags == ["none"]:
        report.problems.append("contains compiled modules but the ABI tag is 'none'")
    if compiled and report.root_is_purelib.lower() == "true":
        report.problems.append("contains compiled modules but WHEEL says Root-Is-Purelib: true")
    for plat in report.platform_tags:
        if plat.startswith("linux_"):
            report.problems.append(f"platform tag {plat!r} is refused by PyPI; repair with auditwheel (manylinux)")
        mac = MACOS_RE.match(plat)
        if mac and max_macos and (int(mac.group(1)), int(mac.group(2))) > max_macos:
            report.problems.append(
                f"{plat!r} needs macOS {mac.group(1)}.{mac.group(2)} or newer; "
                f"set MACOSX_DEPLOYMENT_TARGET={max_macos[0]}.{max_macos[1]} when building"
            )
    abis = set(report.extensions.values())
    if "abi3" in report.abi_tags and any(re.match(r"(?:cpython-|cp)\d", a) for a in abis):
        report.problems.append("abi3 wheel holds version-specific modules: " + ", ".join(sorted(abis)))
    wheel_ft = any(re.fullmatch(r"cp\d+t|abi3t", t) for t in report.abi_tags)
    module_ft = any(re.search(r"(?:cpython-|^cp)\d+t\b|^abi3t$", a) for a in abis)
    if compiled and wheel_ft != module_ft:
        report.problems.append("free-threaded ABI tag and module suffixes disagree")


def inspect(path: Path, max_macos: tuple[int, int] | None) -> Report:
    report = Report(wheel=path.name)
    parse_name(path, report)
    with zipfile.ZipFile(path) as zf:
        for member in zf.namelist():
            if member.endswith(".dist-info/WHEEL"):
                for line in zf.read(member).decode("utf-8", "replace").splitlines():
                    if line.startswith("Root-Is-Purelib:"):
                        report.root_is_purelib = line.split(":", 1)[1].strip()
            abi = module_abi(member)
            if abi:
                report.extensions[member] = abi
    check(report, max_macos)
    return report


def parse_version(text: str) -> tuple[int, int]:
    major, _, minor = text.partition(".")
    try:
        return int(major), int(minor or 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a version like 11.0, got {text!r}") from exc


def main(cli_args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wheel_tags.py", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("wheels", nargs="+", type=Path, help="wheel files to inspect")
    parser.add_argument(
        "--max-macos",
        type=parse_version,
        default=None,
        metavar="X.Y",
        help="flag macOS wheels that need a newer macOS than X.Y (e.g. 11.0)",
    )
    parser.add_argument("--json", action="store_true", help="print one JSON object per wheel")
    args = parser.parse_args(cli_args)

    status = 0
    for path in args.wheels:
        if path.suffix != ".whl" or not path.is_file():
            print(f"{path}: not a wheel file", file=sys.stderr)
            return 2
        try:
            report = inspect(path, args.max_macos)
        except zipfile.BadZipFile:
            print(f"{path}: not a valid zip archive", file=sys.stderr)
            return 2
        if report.problems:
            status = 1
        if args.json:
            print(json.dumps(asdict(report)))
            continue
        print(report.wheel)
        print(
            f"  python {'.'.join(report.python_tags)}  abi {'.'.join(report.abi_tags)}  "
            f"platform {'.'.join(report.platform_tags)}  purelib {report.root_is_purelib or '?'}"
        )
        for member, abi in sorted(report.extensions.items()):
            print(f"  module {member}  [{abi}]")
        if not report.extensions:
            print("  no compiled modules")
        for problem in report.problems:
            print(f"  PROBLEM: {problem}")
    return status


if __name__ == "__main__":
    sys.exit(main())
