---
name: python-code-design
description: Structures Python applications and libraries so they stay easy to change - package layout by feature, a pure core with I/O at the edges, the direction imports may go, dependencies passed in as parameters instead of globals, Protocols at boundaries, a deliberate public API with __init__ re-exports and __all__, breaking import cycles, configuration loaded once at the entry point, splitting a large module without breaking callers, and enforcing layers in CI with import-linter. Use when starting a codebase, deciding where new code goes, untangling circular imports, making code testable without heavy mocking, splitting a god module, or reviewing a pull request for structure rather than style.
metadata:
  tags:
  - python
  - architecture
  - project-structure
  - modules
  - dependency-injection
  - circular-imports
  - import-linter
  - refactoring
---

# Structuring Python code

Good structure in Python means each module has one reason to change, imports point in one direction, and the code that decides things is separate from the code that talks to disks, networks and clocks. Then most code is tested with plain values, and a change touches one folder. This skill gives the layout, the rules for dependencies, and the refactoring steps; it does not prescribe a framework.

## Step 0: read the current shape

Structure decisions start from where the size and the tangles are now.

```bash
find src -name "*.py" -not -path "*/.venv/*" | xargs wc -l | sort -n | tail -10     # the biggest modules
uv run python -X importtime -c "import my_app" 2>&1 | sort -t'|' -k2 -n | tail -5   # heavy imports at start
uvx pydeps src/my_app --max-bacon 2 --cluster -o deps.svg                           # import graph (needs graphviz)
```

Modules over about 800 lines, folders named `utils` or `helpers` holding a third of the code, and many function-local imports (a sign of cycles) are the usual places to start.

## Lay out the package by feature

Group code by what it is about (orders, billing, users), not by what kind of code it is (models, services, utils); a change to one feature then stays in one folder.

```
src/shop/
  __init__.py            public API of the package (re-exports)
  __main__.py            python -m shop → cli.main()
  cli.py                 entry point: parses args, loads config, wires dependencies
  config.py              Settings, loaded once
  orders/
    __init__.py          from .service import place_order; __all__ = [...]
    model.py             Order, OrderLine: dataclasses, pure rules
    service.py           place_order(): the use case, calls ports
    repository.py        OrderRepository: SQL; the only module that imports the driver
  billing/
    ...
  shared/
    money.py             types several features use; small and stable
tests/
  orders/test_service.py
```

| Size | Layout |
|---|---|
| a script or tool under ~500 lines | one module, `main()` at the bottom |
| a small app or library | a flat package of 3 to 10 modules |
| an app with several areas | one subpackage per feature, as above |
| several deployable parts | a uv workspace with one package per part |

A `shared/` (or `core/`) folder is for types and rules used by several features; when it grows faster than the features, code is being dumped there instead of placed.

## A pure core, I/O at the edges

Code that makes decisions takes values and returns values; code that reads files, calls services or reads the clock sits at the edge and passes values in. The core is then tested without mocks.

```python
# orders/model.py: pure, no I/O, no clock
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Order:
    id: int
    total: Decimal
    placed_at: datetime


def can_refund(order: Order, now: datetime, window: timedelta = timedelta(days=30)) -> bool:
    return now - order.placed_at <= window and order.total > 0
```

```python
# orders/service.py: the edge, gathers values and acts on the decision
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from shop.orders.model import Order, can_refund

Clock = Callable[[], datetime]


class RefundRefused(Exception):
    pass


class PaymentGateway(Protocol):
    def refund(self, order_id: int, amount: Decimal) -> None: ...


class OrderRepository(Protocol):
    def get(self, order_id: int) -> Order: ...


def refund(order_id: int, repo: OrderRepository, payments: PaymentGateway, clock: Clock) -> None:
    order = repo.get(order_id)
    if not can_refund(order, clock()):
        raise RefundRefused(order_id)
    payments.refund(order.id, order.total)
```

`test_can_refund` needs two datetimes and no fixtures; only `refund` needs fakes.

## Imports point one way

Modules form layers, and a module may import only from its own layer or the layers below it; the entry point at the top wires everything together.

```
cli.py, api handlers         entry points: parse input, build objects, call services
    ↓
*/service.py                 use cases: orchestrate, no SQL or HTTP details
    ↓
*/model.py, shared/          rules and types: pure, import nothing above
    ↑ implemented by
*/repository.py, clients     adapters: SQL, HTTP, files; satisfy Protocols the services declare
```

- A lower module never imports a higher one; when it needs something from above, it receives it as a parameter or declares a `Protocol` for it.
- Features import each other through their package's public names (`from shop.billing import charge`), never through private modules.

## Pass dependencies in

A function or class receives the things it talks to (a repository, an HTTP client, a clock, settings) as parameters, instead of importing a global instance; tests then pass fakes, and nothing needs `mock.patch`. `refund` in `orders/service.py` above declares its collaborators as `Clock` and two `Protocol`s, and only the entry point names real classes.

```python
# cli.py: the one place that builds real objects (the "composition root")
import sys
from datetime import UTC, datetime

from shop.billing.stripe import StripeGateway
from shop.config import Settings
from shop.orders.repository import SqlOrderRepository
from shop.orders.service import refund


def main() -> int:
    settings = Settings()
    repo = SqlOrderRepository(settings.database_url)
    payments = StripeGateway(settings.payments_key.get_secret_value())
    refund(int(sys.argv[1]), repo, payments, clock=lambda: datetime.now(UTC))
    return 0
```

```python
# tests/orders/test_service.py: fakes are plain classes that satisfy the Protocol
from datetime import UTC, datetime
from decimal import Decimal

from shop.orders.model import Order
from shop.orders.service import refund

NOW = datetime(2026, 10, 31, tzinfo=UTC)


class FakeRepository:
    def get(self, order_id: int) -> Order:
        return Order(id=order_id, total=Decimal("9.50"), placed_at=NOW)


class FakePayments:
    def __init__(self) -> None:
        self.refunds: list[tuple[int, Decimal]] = []

    def refund(self, order_id: int, amount: Decimal) -> None:
        self.refunds.append((order_id, amount))


def test_refund_pays_back_the_total() -> None:
    payments = FakePayments()
    refund(7, FakeRepository(), payments, clock=lambda: NOW)
    assert payments.refunds == [(7, Decimal("9.50"))]
```

- Protocols live next to the code that uses them (the service), not next to the implementation.
- A dependency-injection framework is rarely needed in Python; one `main()` or app factory that builds the objects is enough for most applications.
- Module-level singletons (`db = Database()` at import) make every importer depend on configuration and network at import time. Build them in `main()`.

## A deliberate public API

A package's public API is what its `__init__.py` exports; everything else is an implementation detail that may move.

```python
# src/shop/orders/__init__.py
from shop.orders.model import Order, OrderLine
from shop.orders.service import place_order, refund

__all__ = ["Order", "OrderLine", "place_order", "refund"]
```

- Name private modules and helpers with a leading underscore (`_parsing.py`, `_round_cents`) in libraries; callers and type checkers (Pyright's `reportPrivateUsage`) then know not to rely on them.
- Keep `__init__.py` to imports and `__all__`; logic there runs on every import of any submodule.
- A library that re-exports heavy optional parts can defer them: `lazy from shop.reports import render_pdf` on 3.15, or a module `__getattr__` on older versions.
- Removing a public name is a breaking change; deprecate it first with a warning for one release.

## Break import cycles

An import cycle (`a` imports `b`, `b` imports `a`) fails with `ImportError: cannot import name 'X' from partially initialized module`, and is a sign that two modules share something that belongs in a third.

| Cause | Fix |
|---|---|
| two modules share a type | move the type down into a module both import (`model.py`, `shared/types.py`) |
| a lower module calls up into a higher one | pass the function or object in as a parameter, or declare a `Protocol` in the lower module |
| the import is only for annotations | `if TYPE_CHECKING: from shop.billing import Invoice`; on 3.14+ annotations are lazy, so no quotes are needed |
| a module-level `from x import name` | `import shop.x as x` and use `x.name` at call time; it tolerates the partial module (a stopgap) |
| last resort | an import inside the function; leave a comment saying which cycle it avoids |

Moving the shared piece down is the real fix; the others hide the cycle.

## Configuration at the entry point

Configuration is read once, at start-up, into a typed, immutable settings object that is passed to what needs it; library code never reads `os.environ`.

```python
# config.py
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHOP_", frozen=True)
    database_url: str
    payments_key: SecretStr
    refund_window_days: int = 30
```

Pass `settings.refund_window_days` (a value) rather than `settings` (everything) to code that needs one setting; the function's signature then says what it depends on. A stdlib-only alternative is a frozen dataclass filled from `os.environ` in `main()`.

## Split a large module without breaking callers

A large module is split by moving code into new modules while the old module keeps re-exporting the names, so no caller changes in the same step.

1. Make sure tests cover the module's public functions; add characterisation tests (assert the current output) where they are missing.
2. Create the new modules (`orders/pricing.py`, `orders/validation.py`) and move one group of related functions at a time. Use `git mv` for the biggest part, so history follows it.
3. In the old module, import the moved names back: `from shop.orders.pricing import price_for as price_for`. Run the tests after each move.
4. Update callers to the new locations in a later change; Ruff and the type checker find them.
5. When no caller uses the old location, delete the re-exports, or keep them with a `DeprecationWarning` for a library's users.

## Enforce the structure in CI

Rules about which module may import which are checked by import-linter, so a pull request that breaks the layers fails CI instead of relying on review.

```toml
# pyproject.toml
[tool.importlinter]
root_package = "shop"
include_external_packages = true           # lets contracts name third-party packages

[[tool.importlinter.contracts]]
name = "Layers: entry points on top, models at the bottom"
type = "layers"
layers = ["shop.cli", "shop.orders.service", "shop.orders.model"]

[[tool.importlinter.contracts]]
name = "Models do no I/O"
type = "forbidden"
source_modules = ["shop.orders.model", "shop.shared"]
forbidden_modules = ["httpx", "psycopg", "boto3"]

[[tool.importlinter.contracts]]
name = "Features are independent"
type = "independence"
modules = ["shop.orders", "shop.billing"]
```

```bash
uv add --dev import-linter                 # 2.15
uv run lint-imports                        # lists each broken contract with the import line that breaks it
```

## Verify

Prove the layers with a tool, and prove that importing the package does nothing but import.

```bash
uv run lint-imports                                 # "Contracts: N kept, 0 broken."
env -i PATH="$PATH" HOME="$HOME" uv run python -c "import shop; print('ok')"   # no environment needed at import
uv run pytest -q tests/orders/test_model.py         # the pure core: no fixtures, no mocks
grep -rnE "^\s+(from \S+ )?import " src | grep -v "TYPE_CHECKING"    # each hit is a local import that needs a reason
find src -name "*.py" | xargs wc -l | awk '$1 > 800 && $2 != "total"'   # expect nothing
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `models/`, `services/`, `utils/` at the top | one folder per feature |
| `db = Database()` at module level | build it in `main()` and pass it in |
| `datetime.now()` inside a rule | a `clock` parameter, or the value itself |
| `mock.patch` in every test | dependencies as parameters, fakes in tests |
| a function-local import to dodge a cycle | move the shared piece down |
| passing `settings` everywhere | the one value a function needs |
| logic in `__init__.py` | imports and `__all__` only |
| a dependency-injection framework for a small app | one `main()` that wires objects |

## Checklist

A codebase's structure is in good shape when every item holds.

- [ ] Code is grouped by feature; `utils`/`helpers` modules are small and specific.
- [ ] Decision logic is pure; I/O, the clock and randomness are passed in from the edge.
- [ ] Imports go downward only; the layers are written down as import-linter contracts that pass.
- [ ] Each package's `__init__.py` defines its public API with `__all__`; private names start with `_`.
- [ ] No import cycles, and no function-local imports without a comment.
- [ ] Configuration is loaded once in the entry point and passed as values.
- [ ] No module does network, database or file work at import time.

## Gotchas

These are the structural problems people ask about.

1. **"ImportError: cannot import name 'X' from partially initialized module (most likely due to a circular import)".** Two modules import each other at the top. Move the shared code into a third module both import.
2. **"Every test needs five `mock.patch` calls."** The code imports its dependencies as globals. Pass them in as parameters and give tests fakes.
3. **"Importing my package takes three seconds."** Module-level work: connecting, loading models, reading config, importing heavy libraries in `__init__.py`. Move it into functions called from `main()`.
4. **"The `utils.py` module has 2,000 lines."** Unrelated helpers collected over time. Move each group next to the feature that uses it; keep only truly shared, stable code in `shared/`.
5. **"Changing one feature breaks another."** Features import each other's private modules or share mutable state. Import only public names and add an `independence` contract.
6. **"Tests pass but the app fails at start-up with a configuration error."** Configuration is read deep inside the code, which tests never reach. Load it once at the entry point and fail fast there.

## See also

- `python-import-system`: how cycles and lazy imports work
- `python-configuration`: the settings object at the entry point
- `python-library-maintenance`: public APIs and deprecation
- `python-data-models`: the dataclasses in a pure core
- `pytest-testing`: fakes and fixtures
- `python-idioms`: style inside a module
