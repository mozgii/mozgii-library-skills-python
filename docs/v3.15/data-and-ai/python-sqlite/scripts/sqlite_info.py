# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright 2026 MOZGII, Inc. (https://mozgii.com/)
"""Print the Python and SQLite versions, the SQLite build options that matter, and the settings of a database file.

The file is opened read-only, so the script never creates or changes it.

Usage:
    python3 -I sqlite_info.py                 # versions and build options only
    python3 -I sqlite_info.py app.db          # plus the file's PRAGMAs and tables
    python3 -I sqlite_info.py app.db --check  # plus PRAGMA quick_check (reads the whole file)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

FILE_PRAGMAS = ("journal_mode", "user_version", "application_id", "page_size", "page_count", "freelist_count",
                "auto_vacuum", "encoding")
BUILD_FLAGS = ("ENABLE_FTS5", "ENABLE_FTS4", "ENABLE_RTREE", "ENABLE_MATH_FUNCTIONS", "THREADSAFE")


def build_info() -> list[str]:
    with closing(sqlite3.connect(":memory:")) as con:
        options = [row[0] for row in con.execute("PRAGMA compile_options")]
    wanted = [o for o in options if o.startswith(BUILD_FLAGS)]
    return [
        f"python          {sys.version.split()[0]} ({sys.platform})",
        f"sqlite          {sqlite3.sqlite_version}",
        f"threadsafety    {sqlite3.threadsafety}",
        f"build options   {', '.join(wanted) or '(none of interest)'}",
        f"json functions  {'yes' if sqlite3.sqlite_version_info >= (3, 38, 0) else 'only if built with JSON1'}",
    ]


def file_info(path: Path, check: bool) -> list[str]:
    uri = path.resolve().as_uri() + "?mode=ro"
    lines = [f"file            {path}"]
    with closing(sqlite3.connect(uri, uri=True)) as con:
        for name in FILE_PRAGMAS:
            value = con.execute(f"PRAGMA {name}").fetchone()[0]
            lines.append(f"{name:<15} {value}")
        size = con.execute("PRAGMA page_size").fetchone()[0] * con.execute("PRAGMA page_count").fetchone()[0]
        lines.append(f"{'size':<15} {size / 1_048_576:.1f} MiB")
        rows = con.execute(
            "SELECT type, name, sql LIKE '%STRICT' FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        for kind, name, strict in rows:
            lines.append(f"{kind:<15} {name}{'  (STRICT)' if strict else ''}")
        if check:
            result = [r[0] for r in con.execute("PRAGMA quick_check")]
            lines.append(f"{'quick_check':<15} {'; '.join(result[:10])}")
    for suffix in ("-wal", "-shm"):
        side = path.with_name(path.name + suffix)
        if side.exists():
            lines.append(f"{suffix[1:]:<15} {side.stat().st_size} bytes")
    return lines


def main(cli_args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.add_argument("database", nargs="?", type=Path, help="SQLite file to inspect (opened read-only)")
    parser.add_argument("--check", action="store_true", help="run PRAGMA quick_check on the file")
    args = parser.parse_args(cli_args)
    lines = build_info()
    if args.database is not None:
        if not args.database.is_file():
            print(f"error: no such file: {args.database}", file=sys.stderr)
            return 2
        try:
            lines += file_info(args.database, args.check)
        except sqlite3.DatabaseError as exc:
            print(f"error: {args.database} is not a readable SQLite database: {exc}", file=sys.stderr)
            return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    code = main()
    sys.exit(code)
