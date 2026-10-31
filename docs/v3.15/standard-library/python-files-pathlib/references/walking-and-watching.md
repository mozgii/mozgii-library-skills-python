---
title: Walking, searching and watching folders in Python
author: MOZGII, Inc.
lang: en
---

# Walking, searching and watching folders in Python

Recipes for large folder trees: a fast scan with `os.scandir`, the exact glob pattern rules, disk usage that counts hard links once, finding duplicate files, and watching folders with watchfiles (filters, asyncio, restart on change) or a dependency-free polling loop.

## Contents

This reference has 7 sections.

1. A fast scan with os.scandir
2. Glob pattern rules
3. Disk usage of a folder
4. Finding duplicate files
5. watchfiles filters
6. watchfiles with asyncio and run_process
7. A polling watcher without dependencies

## 1. A fast scan with os.scandir

`os.scandir` returns `DirEntry` objects whose `is_dir()` and `is_file()` answers come from the directory listing itself on most file systems, so a scan of a million files does not cost a million `stat` calls.

```python
import os
from collections.abc import Iterator
from pathlib import Path

SKIP = frozenset({".git", "node_modules", "__pycache__", ".venv"})


def iter_files(root: Path) -> Iterator[os.DirEntry[str]]:
    stack = [os.fspath(root)]
    while stack:                                          # iterative: no recursion limit on deep trees
        folder = stack.pop()
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in SKIP:
                            stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        yield entry
        except PermissionError:
            continue                                      # unreadable folder: skip it, keep going


base = Path("tree")
for d in ("a/b", ".git/x"):
    (base / d).mkdir(parents=True, exist_ok=True)
(base / "a/b/f.txt").write_text("x", encoding="utf-8")
(base / ".git/x/obj").write_text("y", encoding="utf-8")
print([e.name for e in iter_files(base)])                 # ['f.txt']
```

- `entry.stat()` is cached on the entry; on Windows the listing already contains size and times, so it costs nothing there.
- On 3.14+, only the `Path` objects from `iterdir` carry a `path.info` filled by the scan, so `p.info.is_dir()` avoids a `stat` without dropping to `os.scandir`. `glob` yields paths with no cached info, and `walk` yields plain name strings.
- Always pass `follow_symlinks=False` when recursing yourself, or a link to a parent folder loops forever.

## 2. Glob pattern rules

Glob patterns are matched per path segment by `Path.glob`, `Path.rglob`, `Path.full_match` and the `glob` module.

| Pattern | Matches |
|---|---|
| `*` | any characters within one segment, including none |
| `?` | exactly one character |
| `[abc]`, `[a-z]`, `[!a-z]` | one character from the set, or not in it |
| `**` | zero or more whole segments (folders); must be a segment of its own |
| `**/*.py` | `.py` files at any depth, including the top folder |
| `*.py` with `rglob` | the same as `glob("**/*.py")` |

| Behaviour | `Path.glob` / `rglob` | `glob.glob` |
|---|---|---|
| hidden names (leading `.`) | matched by `*` | skipped unless `include_hidden=True` |
| recursion | `**` always recursive | only with `recursive=True` |
| case | sensitive on POSIX, insensitive on Windows; `case_sensitive=` overrides | follows the OS |
| symlinked folders under `**` | not followed unless `recurse_symlinks=True` (3.13) | followed |
| `**` alone (3.13+) | yields folders and files | yields folders and files with `recursive=True` |

`glob.translate` (3.13) turns a pattern into a regular expression, which is the fast way to test many paths against one pattern.

```python
import glob
import re

rx = re.compile(glob.translate("**/*.md", recursive=True, include_hidden=True))
paths = ["README.md", "docs/guide/intro.md", "docs/img/logo.png", ".github/notes.md"]
print([p for p in paths if rx.match(p)])                  # every .md, hidden folders included
```

- `fnmatch.fnmatch(name, "*.py")` matches one name, not a path; it is case-insensitive on Windows and sensitive elsewhere, while `fnmatchcase` is always case-sensitive.
- Escape user text before it goes into a pattern: `glob.escape("report[1].txt")` gives `report[[]1].txt`.

## 3. Disk usage of a folder

Disk usage sums file sizes once per real file: hard links share one inode, so count each `(st_dev, st_ino)` pair only once, and use allocated blocks for the space really taken on disk.

```python
import os
from pathlib import Path


def disk_usage(root: Path) -> tuple[int, int]:
    seen: set[tuple[int, int]] = set()
    apparent = allocated = 0
    for folder, _dirs, files in root.walk():
        for name in files:
            st = (folder / name).lstat()                  # lstat: a symlink counts as itself
            key = (st.st_dev, st.st_ino)
            if key in seen:
                continue                                  # a hard link to a file already counted
            seen.add(key)
            apparent += st.st_size
            allocated += getattr(st, "st_blocks", 0) * 512 or st.st_size   # st_blocks: POSIX only
    return apparent, allocated


root = Path("usage")
root.mkdir(exist_ok=True)
(root / "a.bin").write_bytes(b"x" * 10_000)
os.link(root / "a.bin", root / "a-link.bin")
print(disk_usage(root))                                   # the hard link is not counted twice
```

- `st_blocks` counts 512-byte units on Linux and macOS; Windows has no `st_blocks`, so the function falls back to the size.
- Sparse files and APFS clones make apparent size larger than allocated space; show both when a person is deciding what to delete.

## 4. Finding duplicate files

Duplicates are found cheaply by grouping on size first, then hashing only the files that share a size; most files have a unique size and are never read.

```python
import hashlib
from collections import defaultdict
from pathlib import Path


def find_duplicates(root: Path) -> list[list[Path]]:
    by_size: dict[int, list[Path]] = defaultdict(list)
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            by_size[p.stat().st_size].append(p)
    by_hash: dict[str, list[Path]] = defaultdict(list)
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        for p in same_size:
            with p.open("rb") as f:
                by_hash[hashlib.file_digest(f, "blake2b").hexdigest()].append(p)
    return [sorted(group) for group in by_hash.values() if len(group) > 1]


root = Path("photos")
root.mkdir(exist_ok=True)
for name, data in [("a.jpg", b"1" * 500), ("b.jpg", b"1" * 500), ("c.jpg", b"2" * 500), ("d.jpg", b"3")]:
    (root / name).write_bytes(data)
print([[p.name for p in g] for g in find_duplicates(root)])   # [['a.jpg', 'b.jpg']]
```

For very large sets, hash the first 64 KiB as a second filter before hashing whole files.

## 5. watchfiles filters

A watchfiles filter is a callable `(change, path) -> bool` that decides which events reach your code; the default filter already drops `.git`, `__pycache__`, `node_modules`, `.venv` and editor swap files.

```python
from pathlib import Path

from watchfiles import Change, DefaultFilter


class MarkdownOnly(DefaultFilter):
    def __call__(self, change: Change, path: str) -> bool:
        return super().__call__(change, path) and path.endswith(".md")


only_md = MarkdownOnly(ignore_dirs=(".git", "drafts"))
print(only_md(Change.modified, str(Path("docs/intro.md").resolve())))     # True
print(only_md(Change.modified, str(Path("docs/drafts/x.md").resolve())))  # False: an ignored folder
print(only_md(Change.added, str(Path("docs/logo.png").resolve())))        # False
```

- `PythonFilter(extra_extensions=(".toml",))` passes only Python source files plus the extensions you add, for reload loops.
- Passing `ignore_dirs` replaces the default list; include `.git` and the others again when you override it.
- `debounce` (milliseconds, default 1600) is the longest a batch waits to collect more events; `step` (default 50) is how long the stream must be quiet before the batch is yielded.

## 6. watchfiles with asyncio and run_process

`awatch` is the async generator form of `watch` for asyncio programs; it takes an `asyncio.Event` as `stop_event` so a shutdown ends the loop cleanly.

```python
import asyncio
from pathlib import Path

from watchfiles import awatch


async def main() -> None:
    folder = Path("watched")
    folder.mkdir(exist_ok=True)
    stop = asyncio.Event()

    async def produce() -> None:
        await asyncio.sleep(0.5)
        (folder / "a.txt").write_text("1", encoding="utf-8")
        await asyncio.sleep(2)
        stop.set()

    task = asyncio.create_task(produce())
    async for changes in awatch(folder, stop_event=stop, debounce=300):
        print(sorted((c.name, Path(p).name) for c, p in changes))
    await task


asyncio.run(main())
```

`run_process` restarts a function or command whenever files change: the dev-server reload loop in one call. The target runs in a new process (spawned), so it must be importable.

```python
# dev_loop.py: restart the server whenever anything under src/ changes
from watchfiles import run_process


def serve() -> None:
    import my_app.server
    my_app.server.main()


if __name__ == "__main__":
    run_process("src", target=serve)                      # or target="python -m my_app.server"
```

- The command-line form is `watchfiles "python -m my_app.server" src`.
- `run_process` stops the old process with SIGINT, then SIGKILL if it is still alive after `sigint_timeout` (5 s by default).

## 7. A polling watcher without dependencies

When adding a package is not an option, a polling loop compares snapshots of `(mtime_ns, size)` per file; it works on every file system, including network drives, at the cost of a scan per interval.

```python
import time
from pathlib import Path

Snapshot = dict[Path, tuple[int, int]]


def snapshot(root: Path) -> Snapshot:
    snap: Snapshot = {}
    for p in root.rglob("*"):
        try:
            st = p.stat()
        except FileNotFoundError:
            continue                                      # deleted between listing and stat
        if p.is_file():
            snap[p] = (st.st_mtime_ns, st.st_size)
    return snap


def diff(old: Snapshot, new: Snapshot) -> tuple[set[Path], set[Path], set[Path]]:
    added = new.keys() - old.keys()
    deleted = old.keys() - new.keys()
    modified = {p for p in new.keys() & old.keys() if new[p] != old[p]}
    return set(added), set(deleted), modified


root = Path("polled")
root.mkdir(exist_ok=True)
before = snapshot(root)
(root / "x.txt").write_text("hi", encoding="utf-8")
time.sleep(0.01)
print(diff(before, snapshot(root)))
```

- Compare `st_mtime_ns`, not float `st_mtime`: some file systems store nanoseconds, and floats lose them.
- FAT and exFAT drives store modification times in 2-second steps, so a quick rewrite may not change the time; include the size, or hash small files.
