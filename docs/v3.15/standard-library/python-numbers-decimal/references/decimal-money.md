# Money with Decimal: patterns and storage

Money needs exact decimal arithmetic, a fixed number of minor units per currency, a stated rounding rule, and storage that keeps every digit. This reference gives tested patterns for each, using `decimal.Decimal` from the standard library. Examples were run on Python 3.14 with pydantic 2.14.

## Two representations: Decimal or integer minor units

Money is held either as a `Decimal` amount quantized to the currency's minor unit, or as an `int` count of minor units (cents). Both are exact; pick one per system and convert only at the edges.

| Representation | Strengths | Weaknesses |
|---|---|---|
| `Decimal("19.99")` | readable, carries its exponent, arithmetic with rates and percentages is natural | slower than ints; every result needs a `quantize` |
| `1999` (int cents) | fast, trivially exact, any database or JSON stores it | every display and rate calculation must convert; the exponent must travel with the currency |

Never use `float` for either. A `float` cannot hold 0.10 exactly, and the error surfaces as a cent lost in a sum or a comparison that fails.

## Currency exponents

Currencies have different numbers of minor units: two for USD and EUR, zero for JPY and KRW, three for BHD and KWD. Keep the quantum per currency in one table (ISO 4217 lists them) and quantize through it.

```python
from decimal import ROUND_HALF_UP, Decimal

QUANTUM = {"USD": Decimal("0.01"), "EUR": Decimal("0.01"), "JPY": Decimal("1"), "BHD": Decimal("0.001")}


def to_currency(amount: Decimal, currency: str) -> Decimal:
    return amount.quantize(QUANTUM[currency], rounding=ROUND_HALF_UP)


def to_minor(amount: Decimal, currency: str) -> int:
    return int(to_currency(amount, currency) / QUANTUM[currency])


def from_minor(units: int, currency: str) -> Decimal:
    return units * QUANTUM[currency]


to_currency(Decimal("1234.5"), "JPY")     # Decimal('1235')
to_currency(Decimal("1.2345"), "BHD")     # Decimal('1.235')
to_minor(Decimal("19.99"), "USD")         # 1999
from_minor(1999, "USD")                   # Decimal('19.99')
```

A money type (a frozen dataclass with `amount: Decimal` and `currency: str`) that refuses arithmetic between different currencies prevents adding dollars to euros. The `python-object-model` skill shows the operator methods for it.

## Rounding per line or per total

Rounding each invoice line and then summing gives a different result from summing and then rounding once. Neither is wrong; the tax rules of the country and the accounting system decide which one is required, and every part of the system must use the same one.

```python
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
lines = [Decimal("0.35")] * 3
vat = Decimal("0.21")

per_line = sum(((p * vat).quantize(CENT, ROUND_HALF_UP) for p in lines), Decimal(0))   # Decimal('0.21')
per_total = (sum(lines, Decimal(0)) * vat).quantize(CENT, ROUND_HALF_UP)               # Decimal('0.22')
```

Write the choice into a named function (`invoice_tax`) so that the invoice PDF, the database and the payment provider all compute the same number.

## Splitting an amount without losing cents

Dividing 100.00 three ways gives 33.333..., and rounding each share loses or invents a cent. Allocate in whole minor units and hand the leftover units to the shares with the largest remainders, so the shares always add up to the total.

```python
from decimal import Decimal

def allocate(total: Decimal, weights: list[int], quantum: Decimal = Decimal("0.01")) -> list[Decimal]:
    units = int(total / quantum)
    if units * quantum != total:
        raise ValueError(f"{total} is not a whole number of {quantum}")
    weight_sum = sum(weights)
    shares = [units * w // weight_sum for w in weights]
    remainders = [units * w % weight_sum for w in weights]
    leftover = units - sum(shares)
    for i in sorted(range(len(weights)), key=lambda i: remainders[i], reverse=True)[:leftover]:
        shares[i] += 1
    return [s * quantum for s in shares]


allocate(Decimal("100.00"), [1, 1, 1])       # [33.34, 33.33, 33.33]
allocate(Decimal("10.00"), [50, 30, 20])     # [5.00, 3.00, 2.00]
allocate(Decimal("0.05"), [1, 1, 1, 1])      # [0.02, 0.01, 0.01, 0.01]
```

Ties in the remainders go to the earlier shares because `sorted` is stable. If fairness over time matters (monthly instalments), rotate who gets the extra unit.

## Currency conversion

Exchange rates are `Decimal` values with more digits than the currency (often 4 to 6). Multiply, then quantize to the target currency once, with the rounding mode the provider or regulation specifies.

```python
from decimal import ROUND_HALF_UP, Decimal

eur = Decimal("100.00")
usd_per_eur = Decimal("1.0842")
usd = (eur * usd_per_eur).quantize(QUANTUM["USD"], rounding=ROUND_HALF_UP)    # Decimal('108.42')
```

Store the rate used with the converted amount. Converting back with the inverse rate rarely returns the original amount exactly, so never convert back and forth.

## Contexts and traps for money code

The default context (28 significant digits, `ROUND_HALF_EVEN`) is enough for money; what matters is that the rounding of results to cents is explicit. Two traps catch common mistakes during development and tests.

```python
from decimal import FloatOperation, Inexact, localcontext

with localcontext() as ctx:
    ctx.traps[FloatOperation] = True     # Decimal(0.1) or Decimal(1) < 1.5 now raise
    run_billing()
```

- `FloatOperation` raises when a float is turned into a `Decimal` or compared with one, which finds leaks of floats into money code. Without the trap, `Decimal(1) < 1.5` silently works.
- `Inexact` raises when any operation rounds. Enable it around code that must be exact (adding amounts) to prove no rounding happens there.
- Set traps in tests or in a `localcontext`, never by changing `getcontext()` in library code: the context is per thread and shared by everything on that thread.

## JSON and APIs

JSON has one number type and most parsers read it as a binary float, so send money as a string (`"19.90"`) or as integer minor units. Read numbers with `parse_float=Decimal` when a provider sends them as JSON numbers.

```python
import json
from decimal import Decimal

json.dumps({"amount": Decimal("19.90")}, default=str)          # '{"amount": "19.90"}'
json.loads('{"amount": 19.90}', parse_float=Decimal)           # {'amount': Decimal('19.90')}
```

Pydantic validates `Decimal` fields with `max_digits` and `decimal_places` and serialises them as strings in JSON.

```python
from decimal import Decimal
from typing import Annotated

import pydantic


class Item(pydantic.BaseModel):
    price: Annotated[Decimal, pydantic.Field(max_digits=10, decimal_places=2, ge=0)]


Item(price="19.99").model_dump_json()     # '{"price":"19.99"}'
Item(price="19.999")                      # ValidationError: decimal_max_places
```

## Storing money in SQLite

SQLite has no exact decimal type. A column declared `NUMERIC` or `DECIMAL` converts `"19.90"` to the REAL (float) 19.9, so sums come back as floats with binary error. Store money as `INTEGER` minor units, or as `TEXT` and convert in Python.

```python
import sqlite3
from decimal import Decimal

sqlite3.register_adapter(Decimal, str)                         # write Decimal as text
con = sqlite3.connect("shop.db")
con.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, total_cents INTEGER NOT NULL)")
con.execute("INSERT INTO orders (total_cents) VALUES (?)", (to_minor(Decimal("19.90"), "USD"),))
```

With integer minor units, `SUM(total_cents)` in SQL is exact. With text, sum in Python after reading.

## Storing money in Postgres

PostgreSQL's `numeric(p, s)` is an exact decimal type; `numeric(12, 2)` holds up to 9,999,999,999.99. psycopg returns `numeric` values as `Decimal` and accepts `Decimal` parameters. Never use `real`, `double precision` or the `money` type (the latter depends on the server's locale setting). Store the currency code in its own column next to the amount.

## Decimal pitfalls

These behaviours of `Decimal` surprise people who know floats.

| Behaviour | Example | What to do |
|---|---|---|
| trailing zeros are kept | `Decimal("1.0") * Decimal("1.00")` is `Decimal('1.000')` | quantize results before display or storage |
| equal values with different exponents are one set member | `{Decimal("1.10"), Decimal("1.1")}` has one element | compare quantized values when the exponent matters |
| large values may print in scientific notation | `Decimal("1230").quantize(Decimal("1E+1"))` is `Decimal('1.23E+3')` | format with `f"{d:f}"` or `f"{d:,.2f}"` |
| division rounds to the context precision | `Decimal(1) / 3 * 3` is `Decimal('0.9999999999999999999999999999')` | divide last, or allocate in minor units |
| `round()` uses half-even | `round(Decimal("2.675"), 2)` is `Decimal('2.68')`, `round(Decimal("2.5"))` is `2` | use `quantize` with an explicit mode |
| locale formats are not parsed | `Decimal("19,90")` raises `InvalidOperation` | parse localised input with Babel (`babel.numbers.parse_decimal`) |
| `NaN` is not equal to itself | `Decimal("NaN") == Decimal("NaN")` is `False` | reject NaN at input; `d.is_nan()` |
| `%` and `//` truncate toward zero | `Decimal(-7) % 2` is `Decimal('-1')` | do not mix with int floor semantics |

`Decimal` accepts surrounding whitespace and underscores (`Decimal(" 1_000.5 ")`), which is more lenient than many input formats want; validate the text with a pattern first when the format is strict.
