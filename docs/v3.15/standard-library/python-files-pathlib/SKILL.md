---
name: python-files-pathlib
description: Works with files and folders in Python - pathlib paths, text with an explicit encoding and newline, binary and large files in chunks, atomic writes with a temp file, fsync and os.replace, tempfile, copying, moving and deleting trees with shutil, Path.copy and Path.move (3.14) and rmtree onexc, Path.walk and glob, locking files across processes with filelock, fcntl or msvcrt, watching folders with watchfiles, and app folders with platformdirs. Use when reading or writing files, saving data so a crash never leaves half a file, walking or searching a folder, copying or deleting directories, two processes writing the same file, reacting to file changes, or deciding where an app keeps its data.
metadata:
  tags:
  - python
  - pathlib
  - files
  - filesystem
  - atomic-write
  - file-locking
  - watchfiles
  - platformdirs
---

# Files and folders in Python with pathlib

File code fails quietly: the wrong encoding, half a JSON file after a crash, a delete that stops at the first read-only file on Windows, two processes overwriting each other. This skill gives the standard-library patterns that avoid those failures, plus `filelock`, `watchfiles` and `platformdirs` where the standard library falls short. Path traversal and archive extraction are in the python-security skill.

## Step 0: read the versions

Several pathlib methods are new, so read the minimum Python and the installed helper packages first.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
grep -n "requires-python" pyproject.toml
uv pip list 2>/dev/null | grep -iE "^(filelock|watchfiles|platformdirs) "
```

| Version | Change that matters |
|---|---|
| 3.12 | `Path.walk`; `shutil.rmtree(onexc=...)` replaces `onerror`; `NamedTemporaryFile(delete_on_close=False)` |
| 3.13 | `Path.full_match`; `glob("**")` yields files as well as folders |
| 3.14 | `Path.copy`, `copy_into`, `move`, `move_into`; `Path.info` caches file type from a directory scan |
| 3.15 | UTF-8 is the default text encoding everywhere (PEP 686), Windows included |

October 2026 releases: filelock 4.2, watchfiles 1.3, platformdirs 4.14.

## Choose how to read and write

The size of the file and who else touches it decide the call.

| Job | Use | Why the others lose here |
|---|---|---|
| small text file, read whole | `read_text`, `write_text` with `encoding=` | chunks add code for no gain |
| file of unknown size | iterate the file, or read chunks | `read_text` holds the whole file in memory |
| a save that must survive a crash, or a file others read | temp file, `fsync`, `os.replace` | writing in place truncates the file first |
| a log that only grows | open with mode `"a"` | rewriting the file loses lines to a crash |
| two processes writing one file | `filelock` on a separate `.lock` file | `threading.Lock` works inside one process |
| many records with queries | SQLite | a JSON file rewritten on every change is slow and racy |

## Build paths with pathlib

A `pathlib.Path` joins, splits and inspects paths with the right separator on every OS; string concatenation and `os.path` calls are the old way.

```python
from pathlib import Path

root = Path.home() / "Projects" / "notes"                 # "/" joins with the OS separator
report = root / "2026" / "report.final.md"
print(report.name, report.stem, report.suffix, report.suffixes)   # report.final.md report.final .md ['.final', '.md']
print(report.with_suffix(".html").name)                    # report.final.html
print(report.relative_to(root), report.is_relative_to(root))   # 2026/report.final.md True
```

- `resolve()` makes a path absolute and follows symlinks and `..`; `absolute()` only prepends the working directory.
- `Path("a") / "/etc"` gives `/etc`: an absolute right-hand side replaces the left. Check user input before joining.
- `path.as_posix()` gives forward slashes for URLs and archive member names.

## Read and write text with an explicit encoding

Text files are read and written with `encoding="utf-8"` stated every time. Python 3.15 uses UTF-8 by default, but code that also runs on 3.11 to 3.14 got the locale encoding (often cp1252 on Windows) and read the same file differently.

```python
from pathlib import Path

path = Path("notes.txt")
path.write_text("Grüße\nline two\n", encoding="utf-8")
text = path.read_text(encoding="utf-8")                    # the whole file
with path.open(encoding="utf-8", errors="replace") as f:   # bad bytes become U+FFFD instead of raising
    for line in f:                                         # one line at a time, any file size
        print(line.rstrip("\n"))
```

- Text mode translates line endings: on read every `\r\n` and `\r` becomes `\n`; on write `\n` becomes `os.linesep` (`\r\n` on Windows). Pass `newline=""` to keep bytes exactly, and `newline="\n"` to write Unix endings on Windows.
- `encoding="utf-8-sig"` strips a byte order mark that Excel or Notepad may have written.
- `read_text()` loads everything into memory; iterate the file object for files of unknown size.
- Mode `"x"` creates the file and fails with `FileExistsError` if it exists: no check-then-create race.

## Binary files and large files in chunks

Binary data (images, archives, model weights) is opened with `"rb"` or `"wb"` and read in fixed-size chunks so memory stays flat whatever the file size.

```python
import hashlib
from pathlib import Path

src, dst = Path("blob.bin"), Path("blob.copy")
src.write_bytes(bytes(range(256)) * 10_000)
done = 0
with src.open("rb") as fin, dst.open("wb") as fout:
    while block := fin.read(1 << 20):                      # 1 MiB at a time
        fout.write(block)
        done += len(block)                                 # report progress here
with dst.open("rb") as f:
    digest = hashlib.file_digest(f, "sha256").hexdigest()  # 3.11+, reads in chunks itself
print(done, digest[:12])
```

- To copy a whole file without progress, `shutil.copyfile` uses the kernel's fast path (`fcopyfile` on macOS, `copy_file_range` or `sendfile` on Linux), and `shutil.copy2` calls `CopyFile2` on Windows.

## Atomic writes with os.replace

An atomic write puts the new content in a temporary file in the same folder, flushes it to disk, then renames it over the target with `os.replace`, so a reader or a crash sees the old file or the new one, never half of each.

```python
import os
import tempfile
from pathlib import Path


def write_atomic(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())                           # the bytes are on disk before the rename
        os.replace(tmp, path)                              # atomic on one file system, POSIX and Windows
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


write_atomic(Path("state.json"), b'{"version": 2}\n')
print(Path("state.json").read_text(encoding="utf-8"))
```

- The temporary file must be in the same folder: `os.replace` across file systems fails with `OSError: Invalid cross-device link`.
- `os.rename` refuses to overwrite on Windows; `os.replace` overwrites everywhere, but fails with `PermissionError` while another process has the target open there.
- `mkstemp` creates the file with mode `0o600`. The writer that keeps the old mode, retries and flushes the folder is in [atomic-writes-and-locking.md](references/atomic-writes-and-locking.md).

## Temporary files and folders with tempfile

The `tempfile` module creates files and folders with unique names, removed when the `with` block ends; never use `tempfile.mktemp`, which only returns a name.

```python
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="import-") as tmp:
    (Path(tmp) / "part1.csv").write_text("a,b\n1,2\n", encoding="utf-8")
# the folder and everything in it are gone here

with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete_on_close=False) as f:
    f.write("hello")
    f.close()                                              # closed but kept until the with block ends
    print(Path(f.name).read_text(encoding="utf-8"))        # another open works on Windows too
```

- On Windows, an open `NamedTemporaryFile` cannot be opened again by name; `delete_on_close=False` (3.12) lets you close it and still deletes it at the end of the block.

## Copy, move and delete trees

`shutil` copies, moves and deletes whole trees; Python 3.14 adds the same operations as `Path` methods.

```python
import os
import shutil
import stat
from pathlib import Path

src = Path("site")
(src / "css").mkdir(parents=True, exist_ok=True)

shutil.copytree(src, "site-backup", dirs_exist_ok=True)   # copy2 per file: data plus timestamps
copied = src.copy(Path("site-copy"), preserve_metadata=True)   # 3.14+: returns the target Path
moved = copied.move(Path("site-moved"))                   # rename when possible, else copy then delete


def make_writable_and_retry(func, path, exc: BaseException) -> None:
    if isinstance(exc, PermissionError):                  # Windows: read-only files refuse deletion
        os.chmod(path, stat.S_IWRITE)
        func(path)
    else:
        raise exc


shutil.rmtree("site-backup", onexc=make_writable_and_retry)   # onexc: 3.12+
```

- `shutil.copy2` keeps timestamps; `shutil.copy` keeps permission bits only. `Path.copy` keeps neither unless you pass `preserve_metadata=True`: without it the mtime was lost and mode 0o640 became 0o644.
- `copytree(..., ignore=shutil.ignore_patterns("*.pyc", ".git"))` skips matching names.
- Git checkouts leave read-only files in `.git`; on Windows that is the usual reason `rmtree` fails, and the `onexc` handler fixes it.

## Walk a folder tree with Path.walk

`Path.walk` (3.12) yields each folder with its subfolder and file names, like `os.walk`; edit the subfolder list in place to prune.

```python
from pathlib import Path

root = Path("project")
(root / ".git").mkdir(parents=True, exist_ok=True)
SKIP = {".git", "node_modules", "__pycache__", ".venv"}
total = 0
for folder, dirnames, filenames in root.walk():
    dirnames[:] = [d for d in dirnames if d not in SKIP]   # prune: never descend into these
    for name in filenames:
        total += (folder / name).stat().st_size
print(total)
```

- `Path.walk` does not follow symlinks to folders by default, so link loops cannot trap it; unreadable folders are skipped silently unless you pass `on_error=`.
- For a million files, the faster `os.scandir` scan is in [walking-and-watching.md](references/walking-and-watching.md).

## Find files with glob

`Path.glob` and `Path.rglob` match names with shell-style patterns; `**` matches any number of folders.

```python
from pathlib import Path

docs = Path("docs")
(docs / "guide").mkdir(parents=True, exist_ok=True)
for name in ("guide/intro.md", "guide/IMAGE.PNG", ".draft.md"):
    (docs / name).touch()
print(sorted(p.as_posix() for p in docs.rglob("*.md")))                      # includes .draft.md
print(sorted(p.name for p in docs.rglob("*.png", case_sensitive=False)))    # ['IMAGE.PNG']
print(docs.joinpath("guide/intro.md").full_match("docs/**/*.md"))           # True, 3.13+
```

- Results come in file-system order, which differs between OSes: sort them when order matters. The full pattern rules are in [walking-and-watching.md](references/walking-and-watching.md).
- `Path.glob` matches hidden files; `glob.glob` skips names starting with `.` unless `include_hidden=True`.
- Matching is case-sensitive on macOS and Linux and case-insensitive on Windows by default, even though macOS file systems are usually case-insensitive; pass `case_sensitive=` to make it the same everywhere.

## Lock a file across processes

Two processes that write the same file need a lock both respect; a `threading.Lock` only works inside one process. `filelock` gives one API over `fcntl` on macOS and Linux and `msvcrt` on Windows.

```python
from pathlib import Path

from filelock import FileLock, Timeout                    # uv add filelock

data = Path("counter.txt")
lock = FileLock(data.with_name(data.name + ".lock"), timeout=10)
try:
    with lock:                                            # blocks up to 10 s, then raises Timeout
        n = int(data.read_text(encoding="utf-8")) if data.exists() else 0
        data.write_text(str(n + 1), encoding="utf-8")
except Timeout:
    print("another process holds the lock; try again later")
print(data.read_text(encoding="utf-8"))
```

- The lock is advisory: it stops only processes that also take it. Lock a separate `.lock` file, never the data file you replace. The OS releases it if the process dies.
- Network drives and cloud-synced folders (iCloud, Dropbox, OneDrive) lock unreliably; keep lock files on a local disk. The standard-library version with `fcntl.flock` and `msvcrt.locking` is in [atomic-writes-and-locking.md](references/atomic-writes-and-locking.md).

## Watch a folder for changes with watchfiles

`watchfiles` reports file changes through the OS notification API (FSEvents, inotify, ReadDirectoryChangesW) and groups bursts of events into batches.

```python
import threading

from watchfiles import Change, watch                     # uv add watchfiles

stop = threading.Event()
threading.Timer(2.0, stop.set).start()                   # a real program sets this on shutdown
for changes in watch(".", stop_event=stop):              # blocks; yields a set per batch
    for change, path in changes:
        if change in (Change.added, Change.modified):
            print("process", path)
```

- An editor's save often shows up as several events, so handle a path idempotently. Filters, `awatch` for asyncio and `run_process` are in [walking-and-watching.md](references/walking-and-watching.md).
- On network drives and some container bind mounts, events do not arrive; pass `force_polling=True`.

## App folders with platformdirs

`platformdirs` returns the per-user folders an app should use on each OS, so config, data, cache and logs never land in the current directory or the install folder.

```python
from platformdirs import PlatformDirs                     # uv add platformdirs

dirs = PlatformDirs("Notes", "Acme", ensure_exists=True)
config_file = dirs.user_config_path / "config.toml"       # *_path properties return Path
print(dirs.user_data_path, dirs.user_cache_path, dirs.user_log_path, sep="\n")
```

| Folder | macOS | Linux | Windows |
|---|---|---|---|
| config | `~/Library/Application Support/Notes` | `~/.config/Notes` | `%LOCALAPPDATA%\Acme\Notes` |
| data | the same as config | `~/.local/share/Notes` | the same as config |
| cache | `~/Library/Caches/Notes` | `~/.cache/Notes` | `%LOCALAPPDATA%\Acme\Notes\Cache` |
| logs | `~/Library/Logs/Notes` | `~/.local/state/Notes/log` | `%LOCALAPPDATA%\Acme\Notes\Logs` |

- `appauthor` is used only on Windows; `roaming=True` picks `%APPDATA%` there. Cache may be deleted at any time, so keep what you cannot rebuild in data.

## Verify

Prove the file handling with Ruff, the hostile paths from outside input, and a failed save that must leave the old file whole.

```bash
uv run ruff check --select PTH,S108,S306,SIM115 --ignore PTH105 .   # PTH105 would flag the os.replace above
uv run python - <<'PY'
import tempfile
from pathlib import Path
from unittest import mock

from my_app.storage import write_atomic                 # your writer

root = Path(tempfile.mkdtemp()).resolve()


def inside(name: str) -> bool:
    return (root / name).resolve().is_relative_to(root)


for hostile in ("../secret", "/etc/passwd", "a/../../b"):
    assert not inside(hostile), hostile                 # attack cases: all must be refused
assert inside("notes/a.txt")

target = root / "state.json"
target.write_bytes(b"old")
with mock.patch("os.fsync", side_effect=OSError("disk full")):
    try:
        write_atomic(target, b"new")
    except OSError:
        pass
assert target.read_bytes() == b"old"                    # the failed save changed nothing
assert [p.name for p in root.iterdir()] == ["state.json"]   # and left no temp file
print("ok")
PY
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `open(path)` with no encoding | `encoding="utf-8"`, stated every time |
| `root + "/" + name` | `root / name` |
| `path.exists()`, then `open` | open and catch `FileNotFoundError` |
| rewriting a config file in place | temp file, `fsync`, `os.replace` |
| a temp file in `/tmp` for an atomic save | a temp file in the target's folder |
| `tempfile.mktemp` | `mkstemp`, `NamedTemporaryFile` or `TemporaryDirectory` |
| joining a name from a user without a check | resolve it and test `is_relative_to(root)` |

## Pre-merge checklist

File code is correct when every item holds.

- [ ] Paths are `Path` objects; no string concatenation with `/` or `\\`.
- [ ] Every text open states `encoding=`, and `newline=` where line endings matter.
- [ ] Large files are read in chunks or line by line; saved files are replaced atomically (temp file, `fsync`, `os.replace`).
- [ ] Trees are deleted with `shutil.rmtree` and an `onexc` handler for read-only files.
- [ ] Glob and walk results are sorted where order matters and pruned of `.git`, `node_modules` and `.venv`.
- [ ] Concurrent writers lock a separate file on a local disk; app files live in `platformdirs` folders.

## Gotchas

These are the file bugs seen most often.

1. **`UnicodeDecodeError: 'charmap' codec can't decode byte` on Windows.** No `encoding=` on Python before 3.15, so cp1252 was used. Pass `encoding="utf-8"`.
2. **A JSON or config file is empty or cut off after a crash or power loss.** It was written in place. Write to a temp file, `fsync`, then `os.replace`.
3. **`PermissionError: [WinError 5] Access is denied` from `rmtree`.** A read-only file, usually inside `.git`. Use the `onexc` handler that clears the read-only bit.
4. **`PermissionError: [WinError 32] ... being used by another process`.** Windows refuses to delete or replace an open file. Close every handle first; retry for antivirus and indexers.
5. **Tests pass on macOS and fail on Linux CI.** An unsorted glob result, or a name that differs only in case. Sort, and use the real case.
6. **Two processes still corrupt the file despite a lock.** A `threading.Lock`, or a lock on the data file that `os.replace` swapped out. Lock a separate file with `filelock`.

## References

The reference files hold the longer recipes.

- [atomic-writes-and-locking.md](references/atomic-writes-and-locking.md): a complete atomic writer with folder fsync and Windows retries, durability per OS, standard-library locks with `fcntl` and `msvcrt`, and filelock options
- [walking-and-watching.md](references/walking-and-watching.md): fast scans with `os.scandir`, glob pattern rules, disk usage, duplicate finding, watchfiles filters, `awatch` and `run_process`

## See also

- `python-security`: path traversal and archive extraction
- `python-cross-platform`: Windows paths, locks and file-sharing rules
- `python-sqlite`: when a file should become a database
- `python-configuration`: where settings files live and how they load
