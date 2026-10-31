---
title: Running Python services under systemd and launchd
author: MOZGII, Inc.
lang: en
---

# Running Python services under systemd and launchd

How to run a Python program as a supervised service on Linux with systemd and on macOS with launchd: unit files and property lists, readiness with `Type=notify` and a standard-library `sd_notify`, the watchdog, restart policies, stop timeouts, hardening, logs, and the commands to install, start, stop and inspect. Windows services are in the companion reference.

## Contents

This reference has 9 sections.

1. A systemd service unit for a Python program
2. Readiness and status with sd_notify
3. The systemd watchdog
4. Restart policy and stop behaviour
5. Hardening a systemd service
6. User services and logs
7. A launchd agent for a Python program
8. Installing and controlling launchd jobs
9. Gotchas

## 1. A systemd service unit for a Python program

A systemd service unit describes how to start the program, when it counts as started, how to stop it and when to restart it. Point `ExecStart` at the virtual environment's interpreter with an absolute path; systemd does not read shell profiles or activate environments.

```ini
# /etc/systemd/system/invoice-sync.service
[Unit]
Description=Invoice sync worker
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=notify
NotifyAccess=main
User=invoice-sync
WorkingDirectory=/opt/invoice-sync
ExecStart=/opt/invoice-sync/.venv/bin/python -m invoice_sync
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/etc/invoice-sync/env
Restart=on-failure
RestartSec=5
TimeoutStartSec=60
TimeoutStopSec=30
KillMode=mixed
WatchdogSec=30

[Install]
WantedBy=multi-user.target
```

- `Type=notify`: the service is "started" only after it sends `READY=1` (section 2), so units ordered after it wait for real readiness. Without notification use `Type=exec`, which counts the service as started once the program has been executed.
- `KillMode=mixed` sends `SIGTERM` to the main process only, then `SIGKILL` to every remaining process in the cgroup at the timeout, so a Python parent can stop its own children in order. The default (`control-group`) sends `SIGTERM` to all of them at once.
- `EnvironmentFile=-...` reads `KEY=value` lines; the `-` makes a missing file not an error. Keep secrets there with mode `0600`, or use `LoadCredential=` (systemd 247+), never in the unit itself.
- `StartLimitIntervalSec` and `StartLimitBurst` live in `[Unit]`: after 5 failed starts in 300 s systemd stops trying.

## 2. Readiness and status with sd_notify

`sd_notify` is a datagram sent to the Unix socket whose path systemd puts in `$NOTIFY_SOCKET`. The protocol is plain text lines, so the standard library is enough; no `systemd` Python package is needed.

```python
import os
import socket


def sd_notify(state: str) -> bool:
    """Send state lines to systemd (READY=1, STOPPING=1, WATCHDOG=1, STATUS=...). False when not under systemd."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address or not hasattr(socket, "AF_UNIX"):
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]                      # Linux abstract socket namespace
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | getattr(socket, "SOCK_CLOEXEC", 0)) as sock:
        sock.connect(address)
        sock.sendall(state.encode())
    return True


sd_notify("STATUS=loading models")
# ... warm-up ...
sd_notify("READY=1\nSTATUS=processing jobs")
# ... on SIGTERM, before draining:
sd_notify("STOPPING=1\nSTATUS=draining")
```

- The function returns `False` outside systemd (in a terminal, on macOS or Windows), so the same code runs everywhere.
- `STATUS=` text appears in `systemctl status invoice-sync`; update it with what the service is doing.
- With `NotifyAccess=main` only the main process may notify; use `all` if a child process sends `READY=1`.
- `Type=notify-reload` (systemd 253+) also handles reload: systemd sends `SIGHUP`, the service reloads its configuration and sends `RELOADING=1` with `MONOTONIC_USEC=<now in µs>`, then `READY=1`.

## 3. The systemd watchdog

The watchdog restarts a service that is alive but stuck. With `WatchdogSec=30`, systemd sets `WATCHDOG_USEC` in the environment and expects `WATCHDOG=1` more often than that; a missed deadline counts as a failure and triggers `Restart=`.

```python
import os
import time


def watchdog_interval() -> float | None:
    usec = os.environ.get("WATCHDOG_USEC")
    return int(usec) / 2_000_000 if usec else None           # ping at half the timeout


interval = watchdog_interval()
next_ping = time.monotonic()
for _ in range(3):                                           # the service's main loop
    # ... one unit of work ...
    if interval and time.monotonic() >= next_ping:
        sd_notify("WATCHDOG=1")
        next_ping = time.monotonic() + interval
```

Send the ping from the loop that does the work, never from a separate timer thread; a timer thread keeps pinging while the work loop is deadlocked, which defeats the purpose.

## 4. Restart policy and stop behaviour

The restart policy decides when systemd starts the service again; the stop settings decide how long a graceful stop may take.

| Setting | Meaning | Typical value |
|---|---|---|
| `Restart=on-failure` | restart after a non-zero exit, a signal kill, a timeout or a watchdog miss | most services |
| `Restart=always` | also restart after a clean exit 0 | programs that exit to reload |
| `RestartSec=` | wait before restarting | `5` |
| `RestartSteps=`, `RestartMaxDelaySec=` | grow the delay between restarts (systemd 254+) | `5`, `5min` |
| `TimeoutStopSec=` | time from `SIGTERM` to `SIGKILL` | drain budget plus a margin |
| `KillSignal=` | the polite signal | `SIGTERM` (default) |
| `SuccessExitStatus=` | extra exit codes counted as success | `143` if the program exits 128+15 on purpose |

Exit 0 only for a clean, requested stop. Exiting 0 after a fatal error makes `Restart=on-failure` leave the service down.

## 5. Hardening a systemd service

systemd can sandbox a service with a few lines: a separate user, a read-only system, private temporary files, and no privilege escalation. Start with these and loosen only what the program needs.

```ini
[Service]
# A throwaway user per start; or User= a dedicated account
DynamicUser=yes
# /var/lib/invoice-sync, owned by the service user
StateDirectory=invoice-sync
# /run/invoice-sync, removed at stop: sockets, heartbeat files
RuntimeDirectory=invoice-sync
CacheDirectory=invoice-sync
NoNewPrivileges=yes
# The whole file system read-only except the directories above
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
SystemCallArchitectures=native
```

A unit file has no trailing comments: `#` starts a comment only at the start of a line, so `ProtectSystem=strict  # note` is a bad value. `systemd-analyze verify` prints `Failed to parse ProtectSystem=strict  # note, ignoring` and exits 0: the setting is ignored and that protection is silently lost. `systemd-analyze security invoice-sync` scores the unit and lists what is still open. Python needs write access only where it writes; bytecode caches are not one of them when the virtual environment was installed with compiled bytecode.

## 6. User services and logs

A user service runs as a person's account without root: put the unit in `~/.config/systemd/user/` and control it with `systemctl --user`. It starts at login and stops at logout unless lingering is enabled.

```bash
systemctl --user daemon-reload
systemctl --user enable --now invoice-sync.service
loginctl enable-linger "$USER"                 # keep user services running without a login session
journalctl --user -u invoice-sync -f
```

- journald collects stdout and stderr; one line is one entry. A line starting with `<3>` (error) to `<7>` (debug) sets the entry's priority, so a log format of `<%(levelno)s>`-style prefixes mapped to syslog levels makes `journalctl -p err` work.
- `journalctl -u invoice-sync -b -o cat` shows this boot's lines without metadata; `-o json` gives structured fields.
- Keep logs on stdout; do not also write rotating files under systemd.

## 7. A launchd agent for a Python program

launchd starts jobs described by property-list files: agents (per user, in `~/Library/LaunchAgents/`) run in the user's session; daemons (system-wide, in `/Library/LaunchDaemons/`, owned by root with mode 644) run at boot without a login. Paths must be absolute: launchd expands neither `~` nor environment variables.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.acme.invoice-sync</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/me/Apps/invoice-sync/.venv/bin/python</string>
    <string>-m</string>
    <string>invoice_sync</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/me/Apps/invoice-sync</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>ExitTimeOut</key>
  <integer>30</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>/Users/me/Library/Logs/invoice-sync/out.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/me/Library/Logs/invoice-sync/err.log</string>
</dict>
</plist>
```

- `KeepAlive` with `SuccessfulExit` false restarts the job after a non-zero exit or a crash, and leaves it stopped after a clean exit 0.
- `ThrottleInterval` (10 s by default) is the minimum time between starts; a job that dies sooner is restarted only after it.
- `ExitTimeOut` is the time between `SIGTERM` and `SIGKILL` at stop. Its default is system-defined (launchd.plist(5)), so set it instead of counting on one; 0 means forever and can stall a system shutdown.
- `ProcessType` `Background` lets macOS lower the job's CPU and I/O priority; use `Standard` or `Interactive` for latency-sensitive work.
- The log directory must exist; launchd does not create it, and does not rotate the files.
- Check the file with `plutil -lint ~/Library/LaunchAgents/com.acme.invoice-sync.plist`.

## 8. Installing and controlling launchd jobs

`launchctl` with the `bootstrap`, `bootout`, `kickstart` and `print` subcommands replaces the older `load` and `unload`. A domain names whose jobs they are: `gui/<uid>` for a logged-in user's agents, `system` for daemons.

```bash
uid=$(id -u)
plist=~/Library/LaunchAgents/com.acme.invoice-sync.plist
mkdir -p ~/Library/Logs/invoice-sync
launchctl bootstrap "gui/$uid" "$plist"                     # load and start (RunAtLoad)
launchctl print "gui/$uid/com.acme.invoice-sync" | head -30 # state, pid, last exit code
launchctl kickstart -k "gui/$uid/com.acme.invoice-sync"     # restart now
launchctl bootout "gui/$uid/com.acme.invoice-sync"          # stop (SIGTERM, then SIGKILL after ExitTimeOut) and unload
# daemons: sudo launchctl bootstrap system /Library/LaunchDaemons/com.acme.invoice-sync.plist
```

- macOS 13 and later show a "Background Items Added" notification when an agent is installed, and list it in System Settings > General > Login Items, where the user can turn it off.
- An app bundle should register its helper with `SMAppService` (macOS 13+) instead of writing plists into the user's Library.
- An agent that touches protected folders (Documents, Desktop, Downloads) triggers privacy prompts for the interpreter binary; give the service its own data directory under `~/Library/Application Support/`.
- launchd has no readiness notification like `sd_notify`; a job is running once its process exists.

## 9. Gotchas

These are the supervisor problems seen most with Python services.

1. **`ModuleNotFoundError` only when started by systemd or launchd.** The unit ran the system `python3`, not the virtual environment's. Use the absolute path to `.venv/bin/python`.
2. **The service is restarted every 90 seconds at start.** `Type=notify` without ever sending `READY=1`, so `TimeoutStartSec` expired. Send it, or use `Type=exec`.
3. **`systemctl stop` takes 90 seconds.** The program ignores `SIGTERM` or drains longer than `TimeoutStopSec`. Handle the signal and size the timeout.
4. **launchd restarts the job in a tight loop, then gives up.** It exits at once (bad path, missing log directory). Read `launchctl print` for the last exit code and check `StandardErrorPath`.
5. **Log lines appear late or in bursts.** Block-buffered stdout. Set `PYTHONUNBUFFERED=1`.
6. **The service works as root but not as its own user.** It writes into the application directory. Write only to `StateDirectory`, `CacheDirectory` or `~/Library/Application Support/<app>`.
