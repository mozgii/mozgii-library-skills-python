---
title: Property-based testing with Hypothesis
author: MOZGII, Inc.
lang: en
---

# Property-based testing with Hypothesis

Hypothesis generates many inputs from a strategy, checks that a property holds for each, and when one fails it shrinks the input to the smallest failing example and remembers it. It finds the empty string, the leap day and the 2**63 that hand-written examples forget.

## Contents

This reference has 7 sections.

1. A first property test
2. Strategies for common data
3. Properties that work: round trip, oracle, invariant
4. Building domain objects with builds and composite
5. Settings, profiles and CI
6. Stateful testing
7. Gotchas

## 1. A first property test

A property test states something true for every valid input, and `@given` supplies the inputs.

```python
from decimal import Decimal

from hypothesis import given, strategies as st

from my_app.prices import parse_price


@given(st.decimals(min_value=0, max_value=10**6, places=2, allow_nan=False, allow_infinity=False))
def test_parse_price_round_trips(value: Decimal) -> None:
    assert parse_price(str(value)) == value
```

Run it with plain `uv run pytest`; Hypothesis runs 100 examples by default and stores failing ones in `.hypothesis/` (add it to `.gitignore`), so the next run tries them first.

## 2. Strategies for common data

A strategy describes a set of values; combine them like the data they model.

| Data | Strategy |
|---|---|
| integers in a range | `st.integers(min_value=0, max_value=100)` |
| floats without NaN | `st.floats(allow_nan=False, allow_infinity=False)` |
| text | `st.text(min_size=1, max_size=50)`; `st.text(alphabet=st.characters(categories=["L", "Nd"]))` |
| one of fixed values | `st.sampled_from(["queued", "running", "done"])` |
| optional | `st.none() \| st.integers()` |
| lists, dicts | `st.lists(st.integers(), max_size=20, unique=True)`, `st.dictionaries(st.text(), st.integers())` |
| dates and times | `st.datetimes(timezones=st.timezones())`, `st.dates()` |
| a type | `st.from_type(MyDataclass)`; works for dataclasses, `TypedDict`, enums and annotated classes |
| a regex | `st.from_regex(r"[A-Z]{2}\d{4}", fullmatch=True)` |
| JSON-like values | `st.recursive(st.none() \| st.booleans() \| st.integers() \| st.text(), lambda c: st.lists(c) \| st.dictionaries(st.text(), c))` |

Use `.map()` to transform values and `.filter()` sparingly: a filter that rejects most values makes Hypothesis give up with `FailedHealthCheck`.

## 3. Properties that work: round trip, oracle, invariant

Most useful properties fall into a few shapes; pick the one that fits the function.

```python
import json

from hypothesis import given, strategies as st

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(),
    lambda children: st.lists(children) | st.dictionaries(st.text(), children),
)


@given(json_values)
def test_json_round_trip(value: object) -> None:          # round trip: decode(encode(x)) == x
    assert json.loads(json.dumps(value)) == value


@given(st.lists(st.integers()))
def test_fast_sort_matches_builtin(xs: list[int]) -> None:  # oracle: compare with a slow, obvious version
    assert fast_sort(xs) == sorted(xs)


@given(st.lists(st.integers()))
def test_dedupe_invariants(xs: list[int]) -> None:      # invariants: facts about the output
    out = dedupe(xs)
    assert len(out) == len(set(xs))
    assert set(out) == set(xs)
```

Other shapes: idempotence (`f(f(x)) == f(x)` for normalisers), commutativity, and "never crashes" for parsers (`parse(text)` raises only `ParseError`).

## 4. Building domain objects with builds and composite

`st.builds` calls a constructor with generated arguments; `@st.composite` writes a strategy as a function when fields depend on each other.

```python
from dataclasses import dataclass
from datetime import date

from hypothesis import strategies as st


@dataclass(frozen=True)
class Booking:
    start: date
    end: date
    guests: int


@st.composite
def bookings(draw: st.DrawFn) -> Booking:
    start = draw(st.dates(min_value=date(2026, 1, 1), max_value=date(2030, 12, 31)))
    nights = draw(st.integers(min_value=1, max_value=30))
    return Booking(start, date.fromordinal(start.toordinal() + nights), draw(st.integers(1, 8)))


users = st.builds(User, name=st.text(min_size=1, max_size=20), admin=st.booleans())
```

## 5. Settings, profiles and CI

Settings control how many examples run and how long each may take; profiles switch them per environment.

```python
# tests/conftest.py
import os

from hypothesis import HealthCheck, settings

settings.register_profile("dev", max_examples=50)
settings.register_profile("ci", max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))
```

- `deadline` (200 ms by default) fails examples that are slow; CI machines are noisy, so `deadline=None` there avoids flaky failures.
- `@example(Decimal("0"))` pins a specific case that must always run.
- `@settings(derandomize=True)` makes a test deterministic when reproducibility matters more than exploration.
- `@reproduce_failure(...)` is printed only when `print_blob=True`, which Hypothesis turns on when it detects CI; paste it onto the test to replay that exact case. Locally, the failing example is saved under `.hypothesis/` and replayed on the next run.

## 6. Stateful testing

A rule-based state machine generates sequences of operations and checks invariants after each step, which finds bugs that only appear after a particular order of calls.

```python
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule


class CacheMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.cache = LRUCache(capacity=3)
        self.model: dict[str, int] = {}

    @rule(key=st.sampled_from("abcde"), value=st.integers())
    def put(self, key: str, value: int) -> None:
        self.cache.put(key, value)
        self.model[key] = value

    @invariant()
    def never_over_capacity(self) -> None:
        assert len(self.cache) <= 3


TestCache = CacheMachine.TestCase
```

## 7. Gotchas

These are the problems people hit when starting with Hypothesis.

1. **`FailedHealthCheck: filter_too_much`.** `.filter()` rejects most values. Generate valid values directly (bounds, `sampled_from`, `builds`).
2. **Flaky `DeadlineExceeded`.** The first example includes import or JIT warm-up, or CI is slow. Set `deadline=None` in the CI profile.
3. **A fixture value is shared across examples.** Function-scoped fixtures are created once per test, not per example, so Hypothesis fails the test with `FailedHealthCheck` (`function_scoped_fixture`). Build per-example state inside the test, or pass `suppress_health_check=[HealthCheck.function_scoped_fixture]` when sharing it is safe.
4. **The property restates the implementation.** Then it can never fail. Use a round trip, an oracle or an invariant instead.
5. **Floats break equality.** NaN is not equal to itself. Exclude it (`allow_nan=False`) or compare with `math.isclose` / `pytest.approx`.
