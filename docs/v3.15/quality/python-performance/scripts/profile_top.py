# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright 2026 MOZGII, Inc. (https://mozgii.com/)
"""Profile a Python script or module with the deterministic profiler and print the top functions.

Usage:
    python3 profile_top.py my_script.py arg1 arg2
    python3 profile_top.py -m my_app.cli --limit 30 --sort tottime -- --input data.csv
    python3 profile_top.py --save out.prof my_script.py      # also keep the raw stats for snakeviz or pstats

Standard library only. Uses profiling.tracing on Python 3.15+ and cProfile before that (the same profiler).
Sort keys: cumulative (time in the function and everything it calls), tottime (time in the function itself),
calls (number of calls).
"""

from __future__ import annotations

import argparse
import io
import pstats
import runpy
import sys
from pathlib import Path

try:  # Python 3.15 moved the deterministic profiler to profiling.tracing (PEP 799); cProfile stays as an alias
    from profiling.tracing import Profile  # type: ignore[import-not-found, unused-ignore]
except ImportError:
    from cProfile import Profile


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.formatter_class = argparse.RawDescriptionHelpFormatter
    cli.add_argument("-m", dest="module", help="run this module as with python -m")
    cli.add_argument("--sort", choices=["cumulative", "tottime", "calls"], default="cumulative")
    cli.add_argument("--limit", type=int, default=20, help="rows to print (default 20)")
    cli.add_argument("--save", type=Path, help="write raw pstats data to this file")
    cli.add_argument("target", nargs=argparse.REMAINDER, help="script path and its arguments (or module arguments)")
    args = cli.parse_args()

    target: list[str] = [a for a in args.target if a != "--"] if args.target[:1] == ["--"] else list(args.target)
    if not args.module and not target:
        cli.error("give a script path, or -m module")

    profiler = Profile()
    exit_code = 0
    old_argv = sys.argv
    try:
        if args.module:
            sys.argv = [args.module, *target]
            profiler.enable()
            runpy.run_module(args.module, run_name="__main__", alter_sys=True)
        else:
            script = Path(target[0]).resolve()
            sys.argv = [str(script), *target[1:]]
            sys.path.insert(0, str(script.parent))
            profiler.enable()
            runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        profiler.disable()
        sys.argv = old_argv

    if args.save:
        profiler.dump_stats(args.save)
    out = io.StringIO()
    stats = pstats.Stats(profiler, stream=out).strip_dirs().sort_stats(args.sort)
    stats.print_stats(args.limit)
    print(out.getvalue(), file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
