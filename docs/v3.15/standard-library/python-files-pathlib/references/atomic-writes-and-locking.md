---
title: Atomic writes and file locking in Python
author: MOZGII, Inc.
lang: en
---

# Atomic writes and file locking in Python

How to save files so a crash, a power cut or a second process never leaves them broken: a complete atomic writer, what `fsync` really promises on each OS, append-only logs, and locks across processes with `fcntl`, `msvcrt` and the `filelock` package.

## Contents

This reference has 7 sections.

1. What can go wrong when a file is saved
2. A complete atomic writer
3. Durability per OS: fsync, the folder and F_FULLFSYNC
4. Append-only files
5. Standard-library locks with fcntl and msvcrt
6. filelock options that matter
7. One running instance of an app

## 1. What can go wrong when a file is saved

Writing a file in place (`open(path, "w")` then `write`) first truncates it, so for a moment the file is empty, and a crash at that moment loses both the old and the new content. Three separate problems hide here.

| Problem | Cause | Fix |
|---|---|---|
| torn file: half old, half new, or empty | in-place write interrupted | write a temp file, then `os.replace` |
| new file lost after power loss although the rename happened | data still in the OS page cache | `os.fsync` the file before the rename |
| rename lost after power loss | the folder entry not yet on disk | `fsync` the folder after the rename (POSIX) |
| two writers interleave or lose updates | no mutual exclusion | a lock both processes take |

Atomic replacement fixes readers seeing partial data; fsync fixes durability; a lock fixes lost updates. Most apps need the first always, the second for user data, and the third only when several processes write.

## 2. A complete atomic writer

This writer keeps the old file's permission bits, flushes data to disk, renames over the target, flushes the folder on POSIX, and retries on Windows while an antivirus scanner or indexer holds the target open.

```python
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


def _fsync_dir(folder: Path) -> None:
    if sys.platform == "win32":
        return                                            # Windows cannot open a folder for fsync
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace_with_retry(src: str, dst: Path, attempts: int = 10) -> None:
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform != "win32" or i == attempts - 1:
                raise
            time.sleep(0.05 * (i + 1))                    # another process has dst open; wait for it


def write_atomic(path: Path, data: bytes, *, durable: bool = True) -> None:
    path = path.resolve()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            if durable:
                f.flush()
                os.fsync(f.fileno())
        if path.exists():
            shutil.copymode(path, tmp)                    # mkstemp made it 0o600; keep the old mode
        _replace_with_retry(tmp, path)
        if durable:
            _fsync_dir(path.parent)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_text_atomic(path: Path, text: str) -> None:
    write_atomic(path, text.encode("utf-8"))


target = Path("settings.json")
target.write_text("{}", encoding="utf-8")
target.chmod(0o644)
write_text_atomic(target, '{"theme": "dark"}\n')
print(target.read_text(encoding="utf-8").strip(), oct(target.stat().st_mode & 0o777))
```

- `os.replace` keeps the old file's inode out of the picture: any process that already had the old file open keeps reading the old content, which is usually what you want.
- Hard links and symlinks: replacing `path` replaces the link itself, not the file it pointed to. Resolve the path first (as above) if the link must survive.
- On Windows, a target opened by another program without `FILE_SHARE_DELETE` blocks the replace for as long as it stays open; the retry covers short holds by scanners, not an editor that keeps the file open.

## 3. Durability per OS: fsync, the folder and F_FULLFSYNC

`os.fsync(fd)` asks the OS to write the file's data to the storage device, but what the device does next differs per OS.

| OS | `os.fsync` does | Extra step for real durability |
|---|---|---|
| Linux | `fsync(2)`: data and metadata reach the device, which flushes its cache on most file systems | `fsync` the folder after a rename or create |
| macOS | `fsync(2)`: data reaches the drive, which may still hold it in its own cache | `fcntl.fcntl(fd, fcntl.F_FULLFSYNC)` flushes the drive cache; folder fsync as on Linux |
| Windows | `_commit` (FlushFileBuffers): data and metadata flushed | none; folders cannot be opened for fsync |

This helper uses the strongest flush each OS offers.

```python
import os
import sys


def full_fsync(fd: int) -> None:
    if sys.platform == "darwin":
        import fcntl
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)                # flushes the drive's write cache too
    else:
        os.fsync(fd)


with open("journal.bin", "wb") as f:
    f.write(b"entry\n")
    f.flush()                                             # Python's buffer to the OS first
    full_fsync(f.fileno())
print(os.path.getsize("journal.bin"))
```

- `F_FULLFSYNC` is slow (milliseconds per call on SSDs); use it for data a person would miss, such as a document save, not for every log line or cache file.
- `f.flush()` empties Python's own buffer into the OS; without it `fsync` flushes nothing new.
- SQLite already does all of this; when a "file" is really a growing set of records, SQLite with WAL mode is usually the better store.

## 4. Append-only files

Opening a file with mode `"a"` sets `O_APPEND`, so each `write` lands at the current end of the file even when several processes append; small writes on a local disk do not interleave.

```python
import json
import os
from pathlib import Path

log = Path("events.jsonl")
with log.open("a", encoding="utf-8") as f:
    line = json.dumps({"event": "saved", "id": 7}, ensure_ascii=False) + "\n"
    f.write(line)                                         # one write call per record
    f.flush()
    os.fsync(f.fileno())
print(log.read_text(encoding="utf-8").splitlines()[-1])
```

- Build the whole record first and write it in one call; Python's buffer may still split a record larger than the buffer into several OS writes, so keep records small or take a lock.
- Readers must tolerate a last line without its newline after a crash: skip a final line that does not parse.
- On network file systems `O_APPEND` is not atomic; use a lock or one writer process.

## 5. Standard-library locks with fcntl and msvcrt

Without the filelock package, a cross-process lock uses `fcntl.flock` on macOS and Linux and `msvcrt.locking` on Windows. `flock` is advisory, so only processes that also take it are held back; `msvcrt.locking` is mandatory on the bytes it covers. The OS releases both when the process exits.

```python
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_lock(lock_path: Path, *, blocking: bool = True) -> Iterator[None]:
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            # LK_LOCK retries for about 10 s then raises; LK_NBLCK raises at once
            msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))   # BlockingIOError if busy
        try:
            yield
        finally:
            if sys.platform == "win32":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


with exclusive_lock(Path("job.lock")):
    print("only one process runs this block at a time")
```

- `fcntl.flock` locks the whole file and belongs to the open file description, so it survives `fork` in the child and is released when the last copy of the descriptor closes.
- `fcntl.lockf` uses POSIX record locks instead, which have a trap: closing any descriptor of that file in the same process releases every lock the process holds on it. Prefer `flock`.
- `msvcrt.locking` locks a byte range starting at the current file position, so seek to 0 first; it is a mandatory lock on Windows, so a reader that does not take it gets `PermissionError` on those bytes. Lock one byte of a separate lock file, never the data.
- Never delete the lock file while others may wait on it: a process that opened the old file and one that created a new file would both "hold" the lock.

## 6. filelock options that matter

`filelock.FileLock` picks `UnixFileLock` (`fcntl.flock`) or `WindowsFileLock` (`msvcrt.locking`) for the current OS and adds timeouts, reentrancy and an asyncio version.

| Option | Effect |
|---|---|
| `timeout=10` | wait up to 10 s, then raise `filelock.Timeout`; `-1` (the default) waits forever; `0` tries once |
| `blocking=False` | try once and raise `Timeout` at once if the lock is held |
| `thread_local=True` (default) | the lock's state is per thread; pass `False` to share one lock object between threads of one process |
| `is_singleton=True` | one lock object per lock file path in the process, so nested code can take it again without passing it around |
| `mode=0o600` | permissions of a newly created lock file |
| `AsyncFileLock` | `async with` version for asyncio code; it does not block the event loop while waiting |

This example tries a lock without waiting and reports who should retry.

```python
from filelock import FileLock, Timeout

lock = FileLock("export.lock", blocking=False)
try:
    with lock:
        with lock:                                        # reentrant: the same object can nest
            print("held", lock.lock_counter, "times")
except Timeout:
    print("export already running")
```

- The same `FileLock` object is reentrant; a second object for the same path in the same process is a different lock on POSIX and blocks against the first.
- On Unix the lock file stays after release; on Windows filelock deletes it. Do not treat its existence as "locked".
- `SoftFileLock` creates the file with `O_EXCL` and treats its existence as the lock, and writes the holder's PID and host into it. A crash leaves the file, but on the same host a contender finds that PID gone and takes the lock over: filelock 4.2.5 did so at once after `os._exit`. A marker from another host cannot be judged this way. Use it only where `flock` is unavailable.

## 7. One running instance of an app

A desktop tool that must run once per user takes a non-blocking lock in its runtime folder at start-up and keeps it until exit; the OS drops the lock if the process crashes.

```python
import sys
from pathlib import Path

from filelock import FileLock, Timeout
from platformdirs import user_runtime_path

lock_file = user_runtime_path("notes-sync", ensure_exists=True) / "instance.lock"
instance = FileLock(lock_file, blocking=False)
try:
    instance.acquire()
except Timeout:
    sys.exit("notes-sync is already running")
print("running as the only instance")
instance.release()                                        # a real app keeps it until exit
```

- The runtime folder is per user (`$XDG_RUNTIME_DIR` on Linux, `~/Library/Caches/TemporaryItems` on macOS, `%LOCALAPPDATA%\Temp` on Windows), so two users on one machine each get their own instance.
- To hand a file or URL to the instance already running, pair the lock with a local socket or a named pipe; the lock only answers "is one running".
