---
name: python-datetime
description: Handles dates, times and time zones in Python correctly - aware versus naive datetimes, datetime.now(UTC) instead of utcnow, zoneinfo and tzdata, storing UTC and converting at the edges, future local events stored with their zone name, adding hours versus adding wall-clock time across a DST change, gaps and folds, parsing and formatting ISO 8601, Unix timestamps, measuring durations with time.monotonic, month arithmetic, and testing code that reads the clock with an injected clock or time-machine. Use when code creates, stores, compares, parses or formats datetimes, schedules things in local time, converts time zones, or when a test fails at midnight, at month end or on a daylight saving day.
metadata:
  tags:
  - python
  - datetime
  - timezone
  - zoneinfo
  - utc
  - dst
  - iso-8601
  - time-machine
---

# Dates, times and time zones in Python

Most datetime bugs come from one mix-up: a value that names a moment (an instant) treated as a value that names a wall-clock reading in some place, or the reverse. Python's `datetime` can hold either, and it does not stop you from mixing them. This skill gives the rules and the standard-library code that keeps them apart.

## Step 0: read the Python version

The datetime API changed in recent releases, so read the minimum Python the code supports first.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
grep -n "requires-python" pyproject.toml
uv run python -c "import zoneinfo; print(len(zoneinfo.available_timezones()))"   # 0 on Windows without tzdata
```

| Version | Change that matters |
|---|---|
| 3.9 | `zoneinfo` in the standard library |
| 3.11 | `datetime.UTC` alias; `fromisoformat` accepts most ISO 8601, including a trailing `Z` |
| 3.12 | `datetime.utcnow()` and `utcfromtimestamp()` deprecated |
| 3.15 | `strptime` with `%d` but no year directive raises `ValueError` (it was ambiguous around 29 February) |

## Aware and naive datetimes

A datetime is aware when it carries a `tzinfo` and so names one exact instant; it is naive when it has none and is only a wall-clock reading. The default rule: every datetime that crosses a function boundary is aware.

```python
from datetime import UTC, datetime

created = datetime.now(UTC)          # aware: 2026-10-28 09:15:00.123456+00:00
legacy = datetime(2026, 10, 28, 9)   # naive: 09:00 where? Nobody knows
created.tzinfo is None               # False
```

- Comparing or subtracting a naive and an aware datetime raises `TypeError: can't compare offset-naive and offset-aware datetimes`. Fix the producer of the naive value, not the comparison.
- Naive values are fine only for things that really have no place: a birthday, a shop's opening hours, a form field before you know the zone.
- Ruff's `DTZ` rules flag naive constructors (`datetime.now()`, `utcnow()`, `strptime` without `%z`).

## Get the current time

The current instant is `datetime.now(UTC)`; the current local reading in a known place is `datetime.now(ZoneInfo(name))`.

```python
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

now = datetime.now(UTC)                                  # for storage, logs, comparisons
paris_now = datetime.now(ZoneInfo("Europe/Paris"))       # for showing or scheduling in Paris
machine_local = datetime.now().astimezone()              # the server's own zone, attached
```

Never use `datetime.utcnow()`: it returns a naive value that most code later treats as local time, which shifts it by the UTC offset. It is deprecated since 3.12.

## Time zones with zoneinfo

`zoneinfo.ZoneInfo` loads IANA time zone rules (`"America/New_York"`, `"Asia/Tokyo"`) from the operating system, or from the `tzdata` package when the OS has none.

```python
from datetime import datetime
from zoneinfo import ZoneInfo

meeting = datetime(2026, 11, 3, 9, 0, tzinfo=ZoneInfo("America/New_York"))
meeting.astimezone(ZoneInfo("Asia/Tokyo"))               # same instant, Tokyo wall clock: 23:00
```

- On Windows and in slim containers, add `tzdata` as a dependency (`uv add tzdata`); without it `ZoneInfo("Europe/Paris")` raises `ZoneInfoNotFoundError`.
- `astimezone(zone)` converts an aware value to another zone; the instant stays the same.
- `replace(tzinfo=zone)` attaches a zone to a naive value without converting; use it only when you know the naive value was a reading in that zone.
- Use zone names, never fixed offsets, for places: `+01:00` is Paris only in winter.
- `pytz` is obsolete: its zones need `localize()` and give wrong answers with `replace` or the constructor. Replace it with `zoneinfo`.

## Store UTC, convert at the edges

Past and present instants are stored and computed in UTC and converted to a person's zone only when shown. Future local events are different: store the wall-clock time and the zone name, because zone rules change before the event happens.

| Value | Store as | Why |
|---|---|---|
| when something happened (`created_at`, log time) | aware UTC; Postgres `timestamptz` | one instant, unambiguous, sortable |
| a future event in a place ("9:00 every Monday in Paris") | local date and time plus `"Europe/Paris"` | a government may move DST; the meeting stays at 9:00 |
| a date with no time (birthday, invoice date) | `date` | no zone applies |
| a duration | seconds or `timedelta` | not tied to any clock |

```python
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

def show(instant: datetime, user_zone: str) -> str:
    return instant.astimezone(ZoneInfo(user_zone)).strftime("%d %b %Y, %H:%M %Z")

show(datetime(2026, 10, 28, 9, 15, tzinfo=UTC), "Europe/Paris")   # '28 Oct 2026, 10:15 CET'
```

## Arithmetic across a DST change

Adding a `timedelta` to an aware datetime in a `ZoneInfo` zone adds wall-clock time, not elapsed time: on a daylight saving day "+24 hours" is the same time tomorrow, which may be 23 or 25 real hours. To add elapsed time, do the arithmetic in UTC.

```python
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

paris = ZoneInfo("Europe/Paris")
bedtime = datetime(2026, 3, 28, 22, 0, tzinfo=paris)       # clocks go forward that night

bedtime + timedelta(hours=8)                               # 2026-03-29 06:00+02:00: only 7 real hours
(bedtime.astimezone(UTC) + timedelta(hours=8)).astimezone(paris)   # 07:00+02:00: 8 real hours
```

Decide which one the business rule means: "same time tomorrow" (a daily reminder) is wall-clock arithmetic; "expires in 8 hours" (a token, a timeout) is elapsed time in UTC. Subtracting two aware datetimes in different zones gives the real elapsed time; subtracting two in the same `ZoneInfo` object compares wall clocks.

## Gaps and folds

A gap is a wall-clock time that never happens (02:30 when clocks jump from 02:00 to 03:00); a fold is one that happens twice (02:30 when clocks fall back). The `fold` attribute picks which of the two readings is meant.

```python
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

paris = ZoneInfo("Europe/Paris")
first = datetime(2026, 10, 25, 2, 30, tzinfo=paris)        # fold=0: the first 02:30, +02:00
second = first.replace(fold=1)                              # the second 02:30, +01:00

ghost = datetime(2026, 3, 29, 2, 30, tzinfo=paris)          # inside the gap: Python accepts it
ghost.astimezone(UTC).astimezone(paris)                     # 03:30+02:00: the round trip moves it


def exists(dt: datetime) -> bool:
    """False for a wall-clock reading inside a DST gap: the UTC round trip changes it."""
    return dt.astimezone(UTC).astimezone(dt.tzinfo).replace(tzinfo=None) == dt.replace(tzinfo=None)


exists(ghost), exists(first)                                # (False, True)
```

When people pick a local time in a form, check it with a round trip like `exists` and ask again, or move it forward to the first valid minute, rather than storing a time that never occurs.

## Parsing and formatting

ISO 8601 with an offset is the default text format: it sorts, round-trips and every system reads it. `fromisoformat` and `isoformat` handle it without a format string.

```python
from datetime import UTC, datetime

datetime.fromisoformat("2026-10-28T09:15:00Z")              # aware, UTC (3.11+)
datetime.fromisoformat("2026-10-28T10:15:00+01:00")         # aware, +01:00
datetime.now(UTC).isoformat(timespec="seconds")             # '2026-10-28T09:15:00+00:00'
datetime.strptime("28/10/2026 09:15 +0100", "%d/%m/%Y %H:%M %z")   # a known non-ISO format
```

- Parse with an explicit format (`strptime`) for every non-ISO input you know; reject what does not match instead of guessing.
- A parsed value without an offset is naive: attach the zone the source promised (`.replace(tzinfo=...)`) right after parsing, or reject it.
- `email.utils.parsedate_to_datetime` reads HTTP and email dates (RFC 2822); `format_datetime(dt, usegmt=True)` writes them.
- `%Z` in `strptime` does not attach a zone; it only matches a few names. Use `%z` for offsets.
- For messy human input ("next Tuesday 3pm"), `dateutil.parser.parse` exists; check its result and its current release notes, and never use it on data that should have a fixed format.
- `strftime` month and day names follow the process locale; for user-facing text in several languages, use Babel (`babel.dates.format_datetime`).

## Unix timestamps

A Unix timestamp counts seconds since 1970-01-01 UTC and is always an instant; convert it with an explicit UTC zone.

```python
from datetime import UTC, datetime

datetime.fromtimestamp(1_793_178_000, UTC)                 # 2026-10-28 09:00:00+00:00
datetime.now(UTC).timestamp()                               # float seconds
int(datetime.now(UTC).timestamp() * 1000)                   # milliseconds, as JavaScript uses
```

`.timestamp()` on a naive datetime assumes it is the machine's local time, so the same code gives different numbers on a laptop and on a UTC server. Make the value aware first.

## Measuring durations

Elapsed time inside a program is measured with a monotonic clock, which never jumps when the system clock is corrected or DST changes.

```python
import time

start = time.monotonic()
run_job()
elapsed = time.monotonic() - start                          # seconds, float

deadline = time.monotonic() + 30                            # a 30 s timeout that survives clock changes
while time.monotonic() < deadline:
    if poll():
        break
    time.sleep(0.5)
```

`time.perf_counter()` is the higher-resolution choice for benchmarks. Never compute timeouts from `datetime.now()` differences; an NTP correction can make them negative.

## Dates without times and months

Calendar dates use `date`, and "one month later" needs a rule because months differ in length; `timedelta` has no months.

```python
import calendar
from datetime import date


def add_months(d: date, months: int) -> date:
    """Same day number in the target month, clamped to its last day (31 Jan + 1 month = 28 or 29 Feb)."""
    index = d.month - 1 + months
    year, month = d.year + index // 12, index % 12 + 1
    return d.replace(year=year, month=month, day=min(d.day, calendar.monthrange(year, month)[1]))


add_months(date(2026, 1, 31), 1)                            # date(2026, 2, 28)
```

- `date.today()` is the machine's local date. On a server in UTC it is "tomorrow" for a user in Auckland in the morning; compute a user's today as `datetime.now(ZoneInfo(user_zone)).date()`.
- `dateutil.relativedelta(months=+1)` implements the same clamping rule if the project already depends on dateutil.
- Week numbers: `d.isocalendar().week`; the ISO year can differ from `d.year` in late December and early January.

## Testing code that reads the clock

Tests that call the real clock fail at midnight, at month end and on DST days. The default is to pass the clock in; time-machine is the alternative when the clock is read deep inside code you cannot change.

```python
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def is_expired(issued: datetime, *, ttl: timedelta, now: Clock = utc_now) -> bool:
    return now() - issued >= ttl


def test_token_expires_after_ttl() -> None:
    issued = datetime(2026, 3, 28, 22, 0, tzinfo=UTC)
    assert is_expired(issued, ttl=timedelta(hours=8), now=lambda: issued + timedelta(hours=8))
```

```python
from datetime import UTC, datetime, timedelta

import time_machine                                        # uv add --dev time-machine (3.6)

from my_app.reports import build_daily_report, current_year


@time_machine.travel(datetime(2026, 10, 25, 1, 0, tzinfo=UTC), tick=False)
def test_report_on_fall_back_day() -> None:
    assert build_daily_report().hours == 25


def test_moves_forward(time_machine: time_machine.TimeMachineFixture) -> None:   # the pytest fixture
    time_machine.move_to(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
    time_machine.shift(timedelta(minutes=2))
    assert current_year() == 2027
```

- Since time-machine 3.0 `time.monotonic()` is not faked; code that measures durations takes the clock as a parameter, or the test patches the imported `monotonic` name.
- Always give `travel` an aware datetime; naive ones are read as UTC by default, which hides zone bugs.
- Add tests for the days that break code: 29 February, 31 January plus a month, both DST changes in each zone you support, and 23:59 on 31 December.

## Libraries beyond the standard library

The standard library is enough for most programs; a library helps when time zone arithmetic is the core of the application.

| Library | Pick when |
|---|---|
| `zoneinfo` + `datetime` (stdlib) | the default |
| whenever (0.11, Rust core) | scheduling or calendar logic with many zone conversions; separate types (`Instant`, `ZonedDateTime`, `PlainDateTime`) make mixing them a type error; still before 1.0 |
| pendulum (3.3) | an existing pendulum codebase |
| Babel | localised month and day names and formats for people |

## Verify

Prove the rules with Ruff's datetime checks and one calculation across a real DST change, then run the tests that freeze the clock on the risky days.

```bash
uv run ruff check --select DTZ .                     # expect no finding
grep -rnE "utcnow|utcfromtimestamp|pytz" src tests   # expect nothing
uv run python - <<'PY'
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

paris = ZoneInfo("Europe/Paris")
start = datetime(2026, 3, 28, 22, 0, tzinfo=paris)
wall = start + timedelta(hours=8)
real = (start.astimezone(UTC) + timedelta(hours=8)).astimezone(paris)
print(wall.hour, real.hour, (wall.astimezone(UTC) - start.astimezone(UTC)).total_seconds() / 3600)   # expect 6 7 7.0
PY
uv run pytest -k "dst or leap or month_end"          # tests named for the days that break code
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `datetime.utcnow()`, `utcfromtimestamp()` | `datetime.now(UTC)`, `datetime.fromtimestamp(ts, UTC)` |
| `datetime.now()` stored or compared | an aware value from the start |
| a fixed offset such as `+01:00` for a place | a zone name with `ZoneInfo` |
| a future meeting stored in UTC | local time plus the zone name |
| timeouts from `datetime.now()` differences | `time.monotonic()` |
| `replace(tzinfo=zone)` to convert | `astimezone(zone)` |
| `pytz` in new code | `zoneinfo` |

## Pre-merge checklist

Datetime code is correct when every item holds.

- [ ] Every datetime crossing a function boundary is aware; Ruff `DTZ` rules pass.
- [ ] No `utcnow`, `utcfromtimestamp` or `pytz`; zones come from `zoneinfo`, and `tzdata` is a dependency where the OS has no database.
- [ ] Instants are stored in UTC (`timestamptz`); future local events store local time plus the zone name.
- [ ] Elapsed-time arithmetic happens in UTC; durations and timeouts use `time.monotonic()`.
- [ ] Text uses ISO 8601 with an offset; other formats are parsed with explicit formats.
- [ ] Code takes a clock parameter or tests use time-machine; tests cover DST days, leap days and month ends.

## Gotchas

These are the datetime bugs people report most often.

1. **"My times are off by one hour" (or by the UTC offset).** A naive value from `utcnow()` or `strptime` was treated as local time, or the reverse. Make it aware at the source.
2. **`TypeError: can't compare offset-naive and offset-aware datetimes`.** One side came from a naive constructor or a database column without a zone. Fix that producer.
3. **`ZoneInfoNotFoundError: 'No time zone found with key Europe/Paris'`.** Windows or a slim container without a zone database. Add `tzdata`.
4. **A daily job ran twice, or not at all, on a DST night.** It was scheduled at a time inside the fold or the gap (02:30). Schedule outside 01:00 to 03:00, or in UTC.
5. **`timestamp()` differs between laptop and server.** The value was naive, so the machine's zone was assumed. Attach a zone first.
6. **`replace(tzinfo=...)` moved the wrong way.** `replace` attaches; `astimezone` converts. Converting an aware value needs `astimezone`.
7. **A test fails only around midnight or on the 31st.** It reads the real clock or uses `date.today()`. Inject the clock or freeze it.
8. **`strptime("29/02", "%d/%m")` raises `ValueError` on 3.15.** A day without a year is ambiguous. Include the year in the format, or parse into a date with an explicit year.

## See also

- `pytest-testing`: fixtures and plugins for frozen-clock tests
- `psycopg-postgres`: `timestamptz` columns and how they come back to Python
- `python-serialization`: dates in JSON and other formats
- `python-logging`: timestamps in log records
