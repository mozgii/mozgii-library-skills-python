---
title: Running Python as a Windows service
author: MOZGII, Inc.
lang: en
---

# Running Python as a Windows service

How to run a Python program as a Windows service: a native service with pywin32's `ServiceFramework`, or an ordinary console program under a service wrapper (WinSW, NSSM); how each one stops the program, which account it runs as, recovery after failures, and catching console close, logoff and shutdown events with ctypes. pywin32 312 (October 2026) is the current release; WinSW 2.12 is its latest stable release, NSSM 2.24 has not had a stable release since 2014.

## Contents

This reference has 8 sections.

1. Choosing how to run a Python service on Windows
2. A native service with pywin32 ServiceFramework
3. Installing and debugging a pywin32 service
4. A console program under WinSW
5. A console program under NSSM
6. Service accounts and recovery options
7. Console close, logoff and shutdown events with ctypes
8. Gotchas

## 1. Choosing how to run a Python service on Windows

A Windows service is a program the Service Control Manager (SCM) starts and talks to through a control protocol; a plain `python.exe` does not speak that protocol, so `sc.exe create` pointing at it fails with error 1053 ("The service did not respond to the start or control request in a timely fashion").

| Option | How stop arrives in Python | Pick when |
|---|---|---|
| pywin32 `ServiceFramework` | `SvcStop()` method called by the SCM | you want native service controls (pause, shutdown notice, session events) |
| WinSW wrapper | Ctrl+C (`SIGINT` in Python), then `TerminateProcess` after `<stoptimeout>` (15 s default) | the same program also runs in a terminal; XML config in the repo |
| NSSM wrapper | Ctrl+C first (1500 ms default), then window and thread messages, then `TerminateProcess` | quick setup from the command line; accept an unmaintained tool |
| Task Scheduler "at startup" task | none: the process is ended | a simple background job that may be killed |

A wrapper keeps the program an ordinary console program, testable with `scripts/shutdown_probe.py`. Raise the wrapper's stop timeout to the drain budget: WinSW's 15 s and NSSM's 1.5 s are short for a worker.

## 2. A native service with pywin32 ServiceFramework

A pywin32 service is a class derived from `win32serviceutil.ServiceFramework` with `_svc_name_`, `_svc_display_name_`, a `SvcDoRun` method that runs the service until stopped, and a `SvcStop` method the SCM calls on stop. The SCM calls `SvcStop` on another thread, so it only signals the main loop.

```python
# invoice_service.py
import sys
import threading

import servicemanager
import win32event
import win32service
import win32serviceutil

from invoice_sync.worker import run_until       # your code: run_until(stop: threading.Event) -> None


class InvoiceSyncService(win32serviceutil.ServiceFramework):
    _svc_name_ = "InvoiceSync"
    _svc_display_name_ = "Invoice Sync"
    _svc_description_ = "Syncs invoices to the ERP."

    def __init__(self, args: list[str]) -> None:
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.stop_flag = threading.Event()

    def SvcStop(self) -> None:                                  # called by the SCM on stop
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=30_000)
        self.stop_flag.set()
        win32event.SetEvent(self.stop_event)

    def SvcShutdown(self) -> None:                              # system shutdown: same as stop, less time
        self.SvcStop()

    def SvcDoRun(self) -> None:
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE, servicemanager.PYS_SERVICE_STARTED,
                              (self._svc_name_, ""))
        worker = threading.Thread(target=run_until, args=(self.stop_flag,))
        worker.start()
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
        worker.join(timeout=25)                                 # drain inside the wait hint


if __name__ == "__main__":
    if len(sys.argv) == 1:                                      # started by the SCM (frozen exe)
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(InvoiceSyncService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(InvoiceSyncService)  # install, start, stop, remove, debug
```

- `ServiceFramework.SvcRun` reports `SERVICE_RUNNING` before calling `SvcDoRun`; slow start-up work belongs inside `SvcDoRun`, after which the service is already "running".
- Defining `SvcShutdown` makes the service accept the shutdown control; at system shutdown Windows gives services limited time, so drain fast.
- `waitHint` (milliseconds) tells the SCM how long the stop may take; report `SERVICE_STOP_PENDING` again with a new hint during a long drain.
- A service has no console: `print` output is lost. Log to a file under `%PROGRAMDATA%\<app>\logs` or to the Event Log with `servicemanager.LogInfoMsg` and `LogErrorMsg`.

## 3. Installing and debugging a pywin32 service

`win32serviceutil.HandleCommandLine` turns the module into an installer and controller; run it from an elevated prompt. For a script (not a frozen exe), the SCM starts `pythonservice.exe`, which pywin32 copies next to the interpreter at install time.

```powershell
python invoice_service.py --startup auto install    # register; --startup delayed for delayed auto-start
python invoice_service.py start
python invoice_service.py debug                     # run in the console; Ctrl+C stops it
python invoice_service.py stop
python invoice_service.py remove
```

- `pythonservice.exe` must find the Python DLL, `pywintypes3XX.dll` and your package. In a virtual environment these live in different folders, which is the usual reason a script service fails to start with error 1053. Install from a regular (non-venv) interpreter, freeze the service with PyInstaller and run the frozen exe (the `len(sys.argv) == 1` branch above), or use a wrapper.
- `debug` runs `SvcDoRun` in the console as the current user, which hides permission problems of the service account; test under the real account before shipping.

## 4. A console program under WinSW

WinSW is a single executable plus an XML file with the same base name: `invoice-sync.exe` (renamed `WinSW-x64.exe`) next to `invoice-sync.xml`. It starts the program, stops it with Ctrl+C, restarts it on failure and rotates its logs.

```xml
<service>
  <id>InvoiceSync</id>
  <name>Invoice Sync</name>
  <description>Syncs invoices to the ERP.</description>
  <executable>%BASE%\.venv\Scripts\python.exe</executable>
  <arguments>-m invoice_sync</arguments>
  <workingdirectory>%BASE%</workingdirectory>
  <env name="PYTHONUNBUFFERED" value="1"/>
  <stoptimeout>30 sec</stoptimeout>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="60 sec"/>
  <resetfailure>1 hour</resetfailure>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
</service>
```

```powershell
.\invoice-sync.exe install
.\invoice-sync.exe start
.\invoice-sync.exe status
.\invoice-sync.exe stop
.\invoice-sync.exe uninstall
```

- `%BASE%` is the folder of the WinSW executable, so the whole service (venv, code, wrapper, config) can live in one folder.
- WinSW installs services as LocalSystem unless `<serviceaccount>` says otherwise; prefer a lower-privilege account (section 6).
- The Ctrl+C arrives in Python as `SIGINT`; the handler from the skill's threaded example drains and exits.
- WinSW 2.x ships builds for .NET Framework and self-contained .NET builds (`WinSW-x64.exe`) for machines without it.

## 5. A console program under NSSM

NSSM (the Non-Sucking Service Manager) registers any program as a service from the command line and keeps the configuration in the registry.

```powershell
nssm install InvoiceSync C:\Apps\invoice-sync\.venv\Scripts\python.exe -m invoice_sync
nssm set InvoiceSync AppDirectory C:\Apps\invoice-sync
nssm set InvoiceSync AppEnvironmentExtra PYTHONUNBUFFERED=1
nssm set InvoiceSync AppStdout C:\ProgramData\invoice-sync\service.log
nssm set InvoiceSync AppStderr C:\ProgramData\invoice-sync\service.log
nssm set InvoiceSync AppStopMethodConsole 30000     # wait 30 s after Ctrl+C before the next method
nssm set InvoiceSync AppExit Default Restart
nssm start InvoiceSync
```

- NSSM 2.24 is from 2014; the project has prerelease builds since but no stable release. Some antivirus products flag it; WinSW is the maintained alternative.
- `AppThrottle` (1500 ms default) delays restarts of a program that exits quickly, so a crash loop does not spin.

## 6. Service accounts and recovery options

A service runs as an account chosen at install; the default for many tools is LocalSystem, which has full control of the machine. Run as the least privileged account that works.

| Account | Rights | Use for |
|---|---|---|
| `NT AUTHORITY\LocalService` | minimal local rights, anonymous on the network | most local workers |
| `NT AUTHORITY\NetworkService` | minimal local rights, the computer's identity on the network | services calling domain resources |
| `NT SERVICE\<ServiceName>` (virtual account) | its own identity, grantable on folders | per-service file permissions |
| a dedicated user | whatever you grant | access to network shares with credentials |
| `LocalSystem` | everything | avoid |

Recovery actions tell the SCM what to do when the service process ends without being asked to stop. Set them with `sc.exe` (the `.exe` matters in Windows PowerShell, where `sc` is an alias of `Set-Content`):

```powershell
sc.exe failure InvoiceSync reset= 86400 actions= restart/5000/restart/30000/restart/60000
sc.exe failureflag InvoiceSync 1        # also act when the service stops itself with a non-zero exit code
```

## 7. Console close, logoff and shutdown events with ctypes

A console program on Windows receives `CTRL_CLOSE_EVENT` when its console window is closed, and `CTRL_LOGOFF_EVENT` / `CTRL_SHUTDOWN_EVENT` at logoff and shutdown (in services and console programs that load no GUI libraries). Python maps only Ctrl+C (`SIGINT`) and Ctrl+Break (`SIGBREAK`); register a handler for the others with `SetConsoleCtrlHandler`.

```python
import sys
import threading

stop = threading.Event()
finished = threading.Event()                               # set by main after the drain

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT = 2, 5, 6
    _HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def _on_console_event(event: int) -> bool:
        if event in (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
            stop.set()
            finished.wait(4)                               # Windows ends the process soon after this returns
            return True
        return False                                       # Ctrl+C and Ctrl+Break: Python's own handling

    _handler = _HandlerRoutine(_on_console_event)          # keep a reference, or it is garbage collected
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler, True)
```

- Windows calls the handler on a new thread. For `CTRL_CLOSE_EVENT` the process is terminated a few seconds later whatever the handler does, so the drain must be short.
- Returning `False` passes the event to the next handler (Python's), so Ctrl+C still becomes `KeyboardInterrupt` or your `SIGINT` handler.
- GUI applications get logoff and shutdown as window messages (`WM_QUERYENDSESSION`, `WM_ENDSESSION`) instead; use the GUI toolkit's session-end hook.

## 8. Gotchas

These are the Windows service problems seen most with Python.

1. **Error 1053 at start.** The SCM started a program that does not speak the service protocol (plain `python.exe`), or `pythonservice.exe` could not load Python or pywintypes. Use a wrapper or a frozen exe.
2. **The service starts but cannot import the package.** The service account's environment has no `PYTHONPATH` and a different working directory. Use absolute paths and set the working directory in the wrapper.
3. **Stop always takes the full timeout, then kills.** The program ignores Ctrl+C (no `SIGINT` handler, or a blocking call without a timeout in the main thread). Handle `SIGINT` and `SIGBREAK` and wait with timeouts.
4. **Files written by the service are missing.** It runs as another account; relative paths and `%USERPROFILE%` point elsewhere. Write to `%PROGRAMDATA%\<app>`.
5. **`sc failure ...` in Windows PowerShell does nothing useful.** `sc` is an alias of `Set-Content` there. Type `sc.exe`.
6. **Network drives are not visible.** Mapped drive letters belong to a logon session. Use UNC paths and an account with access.
