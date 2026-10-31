# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright 2026 MOZGII, Inc. (https://mozgii.com/)
"""Summarize pyright diagnostics by rule and by file, to plan a bottom-up fix order.

Usage:
    uv run pyright --outputjson > pyright.json; python3 pyright_summary.py pyright.json
    uv run pyright --outputjson | python3 pyright_summary.py -
    python3 pyright_summary.py pyright.json --rule reportUnknownMemberType --limit 20

Standard library only. Reads pyright's --outputjson format (generalDiagnostics).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import TypedDict, cast


class Position(TypedDict):
    line: int
    character: int


class Range(TypedDict):
    start: Position
    end: Position


class Diagnostic(TypedDict, total=False):
    file: str
    severity: str
    message: str
    range: Range
    rule: str


def load(source: str) -> list[Diagnostic]:
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
        report: object = json.loads(text)
    except OSError as exc:
        raise SystemExit(f"cannot read {source}: {exc.strerror or exc}") from None
    except ValueError as exc:                          # bad JSON, or bytes that are not UTF-8
        raise SystemExit(f"not pyright --outputjson output: {exc}") from None
    if not isinstance(report, dict):
        raise SystemExit("not pyright --outputjson output: expected a JSON object")
    diagnostics = cast("dict[str, object]", report).get("generalDiagnostics")
    if not isinstance(diagnostics, list):
        raise SystemExit("no generalDiagnostics key: run pyright with --outputjson")
    return [d for d in cast("list[Diagnostic]", diagnostics) if isinstance(d, dict)]


def main() -> int:
    cli = argparse.ArgumentParser(prog="pyright_summary.py", formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    cli.add_argument("report", help="pyright --outputjson file, or - for stdin")
    cli.add_argument("--rule", help="show only this rule, e.g. reportUnknownMemberType")
    cli.add_argument("--limit", type=int, default=10, help="messages to print per file (default 10)")
    cli.add_argument("--root", default=".", help="print file paths relative to this folder")
    args = cli.parse_args()

    root = Path(args.root).resolve()
    items = [d for d in load(args.report) if d.get("severity") in ("error", "warning")]
    if args.rule:
        items = [d for d in items if d.get("rule") == args.rule]
    if not items:
        print("0 diagnostics")
        return 0

    by_rule = Counter(d.get("rule", "(no rule)") for d in items)
    by_file: dict[str, list[Diagnostic]] = defaultdict(list)
    for d in items:
        path = Path(d.get("file", "?"))
        try:
            shown = str(path.resolve().relative_to(root))
        except ValueError:
            shown = str(path)
        by_file[shown].append(d)

    print(f"{len(items)} diagnostics in {len(by_file)} files\n\nBy rule:")
    for rule, count in by_rule.most_common():
        print(f"  {count:5d}  {rule}")
    print("\nBy file (fewest diagnostics first):")
    for shown, diags in sorted(by_file.items(), key=lambda kv: (len(kv[1]), kv[0])):
        print(f"\n{shown}  ({len(diags)})")
        for d in sorted(diags, key=lambda x: x["range"]["start"]["line"] if "range" in x else 0)[: args.limit]:
            line = d["range"]["start"]["line"] + 1 if "range" in d else 0
            first = d.get("message", "").splitlines()[0] if d.get("message") else ""
            print(f"  {line:5d}  {d.get('rule', '-')}: {first}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
