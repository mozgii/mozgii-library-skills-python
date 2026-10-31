---
name: python-numbers-decimal
description: Gets numbers right in Python - why 0.1 + 0.2 != 0.3 and how floats are stored, comparing floats with math.isclose and its abs_tol trap, exact sums with math.fsum, Decimal for money with string construction, contexts, quantize and rounding modes, round() and banker's rounding, Fraction for exact ratios, arbitrary-precision int and the 4300-digit int-to-str limit, integer division and modulo with negative numbers, NaN and infinity, the math and statistics modules, and formatting numbers with thousands separators, fixed decimals, percentages and signs. Use when handling money, prices, tax or currency, comparing or summing floats, rounding for display or invoices, when round(2.5) gives 2, a total is off by a cent, a modulo of a negative number surprises, a huge int fails to print, or numbers need formatting.
metadata:
  tags:
  - python
  - numbers
  - decimal
  - float
  - rounding
  - money
  - fractions
  - formatting
---

# Numbers in Python: floats, Decimal, Fraction and int

Python has three exact number types and one approximate one. `int` is exact and unbounded, `Fraction` is an exact ratio, `Decimal` is exact decimal arithmetic with chosen precision and rounding, and `float` is a 64-bit binary approximation that is fast and almost always slightly off. Most number bugs come from using `float` where the problem is decimal (money), from rounding rules nobody chose on purpose, or from negative numbers in `//` and `%`. This skill gives the rules and the code; [decimal-money.md](references/decimal-money.md) has the money patterns and [math-statistics.md](references/math-statistics.md) the function reference.

## Step 0: read the Python version

Number APIs changed recently, so read the oldest supported Python first.

```bash
uv run python -c "import sys; print(sys.version_info[:2], sys.int_info.default_max_str_digits)"
grep -n "requires-python" pyproject.toml
```

| Version | Change that matters |
|---|---|
| 3.11 | converting an int of more than 4300 digits to or from `str` raises `ValueError` |
| 3.12 | `math.sumprod`; `Fraction` supports format specs (`f"{Fraction(1, 3):.4f}"`); `statistics.correlation(method="ranked")` |
| 3.13 | `math.fma`; `statistics.kde` and `kde_random` |
| 3.14 | `float.from_number`, `Decimal.from_number`, `Fraction.from_number`; `decimal.IEEEContext`; `_` and `,` grouping in the fractional part of a format spec |
| 3.15 | `math.integer` submodule for integer functions; `math.fmax`, `fmin`, `signbit`, `isnormal`, `issubnormal` |

## Choose a number type

Pick the type from what the value means, then keep it the same type all the way through.

| Value | Use | Why the others lose here |
|---|---|---|
| counts, ids, money in minor units | `int` | a `float` loses integers above 2**53 |
| money, tax, prices, anything audited in decimals | `Decimal` from strings | `float` cannot hold most decimals |
| measurements, statistics, graphics, ML | `float` | `Decimal` is about six times slower and the inputs are approximate anyway |
| exact ratios, probabilities, unit conversions | `Fraction` | `float` rounds and `Decimal` cannot hold 1/3 |
| arrays of numbers | NumPy | a Python loop over `math` is far slower |

## How floats are stored

A `float` is an IEEE 754 double: 53 bits of binary fraction and an exponent. Most decimal fractions, including 0.1, have no exact binary form, so the stored value is the nearest double. `repr` prints the shortest decimal that rounds back to the same double, which hides the error until arithmetic exposes it.

```python
0.1 + 0.2                 # 0.30000000000000004
0.1 + 0.2 == 0.3          # False
f"{0.1:.20f}"             # '0.10000000000000000555'
(0.1).as_integer_ratio()  # (3602879701896397, 36028797018963968): the exact stored value
float(2**53 + 1)          # 9007199254740992.0: integers above 2**53 lose precision
```

- Floats have about 15 to 17 significant digits; ids and counts beyond 2**53 must stay `int`.
- Floats are right for measurements, statistics, graphics and ML, where inputs are approximate, and wrong for money.
- JSON numbers are parsed as `float` by default; `json.loads(text, parse_float=Decimal)` keeps decimals exact.

## Comparing floats with math.isclose

Never compare computed floats with `==`. `math.isclose(a, b)` checks a relative tolerance (default `rel_tol=1e-09`), which scales with the size of the values.

```python
import math

math.isclose(0.1 + 0.2, 0.3)                 # True
math.isclose(1e-12, 0.0)                     # False: a relative tolerance of zero is zero
math.isclose(1e-12, 0.0, abs_tol=1e-9)       # True: comparisons with zero need abs_tol
```

- Choose `abs_tol` from the problem's units (a millimetre, a cent) whenever values can be near zero.
- `pytest.approx(expected)` does the same in tests.
- Never use floats as dict keys or set members after arithmetic; round to a fixed number of digits, or use integers (cents, micrometres).

## Exact sums with math.fsum

Adding many floats accumulates rounding error. Since 3.12, `sum()` of floats uses compensated summation, so `sum([0.1] * 10)` is `1.0` (it was `0.9999999999999999` before). `math.fsum` goes further: it tracks every lost low-order bit and returns the correctly rounded sum of the exact inputs.

```python
import math

values = [0.1] * 7 + [1e16, 1.0, -1e16]
sum(values)              # 1.7000000000000002
math.fsum(values)        # 1.7
```

Use `fsum` when values of very different sizes cancel out. Neither makes the inputs themselves exact.

## round() and banker's rounding

`round(x)` and `round(x, n)` use round-half-to-even ("banker's rounding"): a value exactly halfway goes to the nearest even digit. That avoids bias over many values but surprises people who expect half-up.

```python
round(2.5), round(3.5), round(-2.5)    # (2, 4, -2)
round(2.675, 2)                        # 2.67: the stored double is 2.67499999...
round(1234, -2)                        # 1200
```

`round(2.675, 2)` is not a banker's rounding case: the float is slightly below 2.675, so it rounds down under any rule. For decimal rounding with a stated rule (half-up on an invoice), use `Decimal.quantize`.

## Decimal for money

`decimal.Decimal` does exact decimal arithmetic: `Decimal("0.1") + Decimal("0.2") == Decimal("0.3")`. Build values from strings or integers, never from floats, and round explicitly with `quantize` and a rounding mode.

```python
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def line_total(unit_price: Decimal, qty: int, vat_rate: Decimal) -> Decimal:
    gross = unit_price * qty * (1 + vat_rate)
    return gross.quantize(CENT, rounding=ROUND_HALF_UP)


line_total(Decimal("19.99"), 3, Decimal("0.20"))     # Decimal('71.96')

Decimal(0.1)        # Decimal('0.1000000000000000055511151231257827021181583404541015625')
Decimal("0.1")      # Decimal('0.1')
```

- `Decimal + float` raises `TypeError`; `Decimal + int` works. Convert floats at the boundary with `Decimal(str(x))` only if the float was itself parsed from decimal text.
- The default context has 28 significant digits and rounds `ROUND_HALF_EVEN`; it limits division and multiplication results, not stored values.
- `Decimal("1.10")` keeps its trailing zero (`str` gives `"1.10"`) and equals `Decimal("1.1")`. Quantize before display or storage so every amount has the currency's exponent.
- Decimal arithmetic measured about six times slower than float on 3.14. For money that does not matter; for numeric loops it does.

Splitting amounts without losing cents, per-currency exponents, rounding per line versus per total, and storing money in databases and JSON are in [decimal-money.md](references/decimal-money.md).

## Decimal contexts and rounding modes

A context sets precision, rounding and which conditions raise. Change it locally with `localcontext()`, never globally in library code.

```python
from decimal import Decimal, FloatOperation, Inexact, localcontext

with localcontext(prec=50):
    Decimal(1) / Decimal(7)                   # 50 significant digits inside the block only

with localcontext() as ctx:
    ctx.traps[FloatOperation] = True          # Decimal(0.1) or comparing with a float now raises
    ctx.traps[Inexact] = True                 # any rounded result raises: proves exactness
```

The rounding modes, applied to quantizing to whole numbers:

| Mode | 2.5 | -2.5 | 2.4 | -2.6 | Typical use |
|---|---|---|---|---|---|
| `ROUND_HALF_EVEN` (default) | 2 | -2 | 2 | -3 | statistics, banking |
| `ROUND_HALF_UP` | 3 | -3 | 2 | -3 | invoices and tax in most countries |
| `ROUND_HALF_DOWN` | 2 | -2 | 2 | -3 | rare |
| `ROUND_UP` (away from zero) | 3 | -3 | 3 | -3 | charging for partial units |
| `ROUND_DOWN` (toward zero) | 2 | -2 | 2 | -2 | truncation |
| `ROUND_CEILING` | 3 | -2 | 3 | -2 | toward positive infinity |
| `ROUND_FLOOR` | 2 | -3 | 2 | -3 | toward negative infinity |

The legal rounding rule for tax or currency conversion is a business requirement: look it up for each country and write it into the code by name.

## Fraction for exact ratios

`fractions.Fraction` represents a ratio of two ints exactly; arithmetic never rounds. It suits ratios, probabilities and unit conversions that must stay exact, at the cost of numerators and denominators that can grow large.

```python
from fractions import Fraction

Fraction(1, 3) + Fraction(1, 6)           # Fraction(1, 2)
Fraction("3/7"), Fraction("0.1")          # exact from text
Fraction(0.1)                             # 3602879701896397/36028797018963968: the float's exact value
Fraction(0.1).limit_denominator(1000)     # Fraction(1, 10): nearest simple fraction
f"{Fraction(1, 3):.4f}"                   # '0.3333' (3.12+)
```

## int: arbitrary precision and the digit limit

Python's `int` has no fixed size: `2**100` is exact, and arithmetic on large values is correct, just slower. Converting a very large int to or from decimal text is quadratic, so since 3.11 it raises `ValueError` past 4300 digits to block denial-of-service through huge numeric strings.

```python
str(10**5000)        # ValueError: Exceeds the limit (4300 digits) for integer string conversion
hex(10**5000)        # fine: power-of-two bases are not limited

import sys
sys.set_int_max_str_digits(10_000)    # raise the limit for this process, deliberately
```

- The limit applies to `int(text)`, `str(n)`, `repr`, f-strings and `json` for ints; set it with `-X int_max_str_digits=N` or `PYTHONINTMAXSTRDIGITS`, or `0` for no limit.
- Raise it only in code that handles trusted big numbers (cryptography, combinatorics); keep the limit for parsing outside input.
- `n.bit_length()`, `n.bit_count()`, `n.to_bytes(length, "big")` and `int.from_bytes(data, "big")` handle binary formats.

## Integer division and modulo with negative numbers

`//` rounds toward negative infinity (floor division), and `%` takes the sign of the divisor, so `a == (a // b) * b + a % b` always holds. C, Java, JavaScript and `math.fmod` truncate toward zero instead.

```python
import math
from decimal import Decimal

-7 // 2, -7 % 2          # (-4, 1)
7 // -2, 7 % -2          # (-4, -1)
divmod(-7, 2)            # (-4, 1)
math.fmod(-7, 2)         # -1.0: C semantics, sign of the dividend
int(-3.7), math.floor(-3.7), math.trunc(-3.7)   # (-3, -4, -3)

Decimal(-7) % Decimal(2)        # Decimal('-1'): Decimal follows truncation, not floor
Decimal(-7) // Decimal(2)       # Decimal('-3')
```

Python's `%` suits wrapping indexes and clock arithmetic: `(hour - 5) % 24` is never negative. When porting a C or JavaScript formula that relies on truncation, use `math.fmod`. `int` and `Decimal` code paths give different answers for negative operands.

## NaN and infinity

`float("nan")` and `float("inf")` come from overflow, invalid operations in NumPy or parsed input. NaN is not equal to anything, including itself, and it poisons comparisons and sorting.

```python
import json

nan = float("nan")
nan == nan                  # False: test with math.isnan(x)
sorted([3, nan, 1])         # [3, nan, 1]: sorting with NaN gives no useful order
max([nan, 1]), max([1, nan])    # (nan, 1): the result depends on position
json.dumps(nan)             # 'NaN': not valid JSON; pass allow_nan=False to refuse it
int(float("inf"))           # OverflowError
```

Reject or filter NaN at the boundary (`math.isfinite(x)` is false for NaN and both infinities). Plain Python float division by zero raises `ZeroDivisionError`; NumPy returns `inf` or `nan` with a warning instead.

## The math and statistics modules

`math` provides float and integer functions implemented in C; `statistics` provides descriptive statistics that are exact with `Fraction` and `Decimal` inputs. The most used ones:

| Need | Function |
|---|---|
| integer square root, combinations, gcd | `math.isqrt`, `math.comb`, `math.gcd(*ints)` |
| product, dot product, distance | `math.prod`, `math.sumprod` (3.12), `math.dist(p, q)` |
| average, median | `statistics.fmean` (fast, float), `statistics.mean` (exact types), `statistics.median` |
| spread | `statistics.stdev` (sample), `statistics.pstdev` (population) |
| percentiles | `statistics.quantiles(data, n=100)` |

Signatures, exactness and edge cases (empty data, NaN, sample versus population) are in [math-statistics.md](references/math-statistics.md). For arrays of numbers, NumPy is faster than looping over `math` functions.

## Formatting numbers

Format specs after a colon control digits, separators, sign and alignment; the same spec works in f-strings, `format()` and `str.format`.

| Spec | Value | Output |
|---|---|---|
| `,.2f` | `1234567.891` | `1,234,567.89` |
| `_.2f` | `1234567.891` | `1_234_567.89` |
| `.3e` | `1234567.891` | `1.235e+06` |
| `.1%` | `0.256` | `25.6%` |
| `g` | `1234567.891` | `1.23457e+06` |
| `08.3f` | `3.14159` | `0003.142` |
| `+` | `42` | `+42` |
| `#06x`, `b` | `255` | `0x00ff`, `11111111` |
| `>15,.1f` | `1234567.891` | `    1,234,567.9` |
| `z.1f` | `-0.001` | `0.0` (no negative zero) |
| `,.4_f` (3.14) | `1234.5678` | `1,234.567_8` |

- Format specs round the exact stored value: `f"{2.675:.2f}"` is `'2.67'`. Format a quantized `Decimal` when the digits matter; `Decimal` supports the same specs.
- `,` and `_` ignore the locale. For locale-aware text use the `n` type after `locale.setlocale`, or Babel (`babel.numbers.format_currency`) for several languages.
- `f"{n:f}"` on a huge float prints every digit; use `g` or `e` when magnitude varies.

## Verify

Prove the money path with Ruff and exact expected values, and prove the input limits with attack cases: a huge numeric string and a NaN must not get through.

```bash
uv run ruff check --select RUF032 .                # expect no Decimal built from a float literal
grep -rnE "Decimal\(float|float\(.*(price|amount|total)" src   # expect nothing
uv run python - <<'PY'
import json
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
assert Decimal("0.125").quantize(CENT, rounding=ROUND_HALF_UP) == Decimal("0.13")
assert (Decimal("19.99") * 3 * Decimal("1.20")).quantize(CENT, rounding=ROUND_HALF_UP) == Decimal("71.96")
assert (-7 // 2, -7 % 2) == (-4, 1)

for attack in ("9" * 5000,):
    try:
        int(attack)
    except ValueError as exc:
        print(exc)                                 # expect the 4300-digit limit
    else:
        raise SystemExit("huge int accepted")
try:
    json.dumps(float("nan"), allow_nan=False)
except ValueError:
    print("ok")
PY
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `float` for money | `Decimal` or integer minor units |
| `Decimal(0.1)` | `Decimal("0.1")` |
| `round(x, 2)` on an invoice | `quantize` with a named rounding mode |
| `a == b` on computed floats | `math.isclose`, with `abs_tol` near zero |
| `sum` of values that cancel out | `math.fsum` |
| `sys.set_int_max_str_digits(0)` on outside input | keep the limit for parsing |
| stored numbers formatted for display | format at the edge and store the raw value |

## Pre-merge checklist

Number handling is correct when every item holds.

- [ ] Money and other audited decimal values are `Decimal` (or integer minor units), built from strings, never floats.
- [ ] Every rounding of money uses `quantize` with a named rounding mode chosen from the business rule.
- [ ] Float comparisons use `math.isclose` with an `abs_tol` where values can be near zero; tests use `pytest.approx`.
- [ ] `//` and `%` on negative numbers are intended, and ported formulas use `math.fmod` where they need truncation.
- [ ] NaN and infinity are rejected or handled at input; JSON output uses `allow_nan=False`.
- [ ] Huge ints from outside input stay under the digit limit; the limit is raised only for trusted data.
- [ ] Display formatting is separate from stored values; locale-aware text uses Babel or the `n` format.

## Gotchas

These are the number bugs seen most often.

1. **A total is off by one cent.** Floats were used for money, or rounding happened per line in one place and per total in another. Use `Decimal`, quantize at one agreed point, and allocate remainders explicitly.
2. **`round(2.5)` returns `2`.** `round` uses half-to-even. Use `Decimal.quantize(..., rounding=ROUND_HALF_UP)` for half-up.
3. **`Decimal(0.1)` has 55 digits.** It was built from a float, which carries the binary error. Build from a string.
4. **`TypeError: unsupported operand type(s) for +: 'decimal.Decimal' and 'float'`.** A float leaked into money code. Convert at the boundary and keep floats out.
5. **`math.isclose(x, 0)` is always `False`.** The default tolerance is relative. Pass `abs_tol`.
6. **`-7 % 3` is `2`, not `-1`.** Python's modulo follows floor division. Use `math.fmod` for C semantics.
7. **`ValueError: Exceeds the limit (4300 digits)`.** A very large int was converted to text. Raise the limit with `sys.set_int_max_str_digits` for trusted values, or write it in hex.
8. **A sorted list or `max()` gives nonsense.** It contains NaN. Filter with `math.isnan` first.

## References

The reference files hold the detail.

- [decimal-money.md](references/decimal-money.md): money as `Decimal` or integer minor units, currency exponents, quantize and rounding per line or per total, splitting amounts without losing cents, currency conversion, contexts and traps, storing money in SQLite, Postgres and JSON, and `Decimal` pitfalls
- [math-statistics.md](references/math-statistics.md): the `math` module function by function (integer functions, `fsum`, `fma`, `isclose`, the 3.15 additions), int binary operations, and the `statistics` module (means, medians, spread, quantiles, regression, `NormalDist`, `kde`) with their edge cases

## See also

- `numpy`: arrays and vectorised arithmetic
- `pandas`: nullable numeric columns and rounding
- `psycopg-postgres`: `numeric` columns and Decimal round trips
- `python-serialization`: Decimal and NaN in JSON
- `python-data-models`: validated money fields
