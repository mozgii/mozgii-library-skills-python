# math and statistics module reference

The `math` module gives C-speed functions for floats and exact integer functions; the `statistics` module gives descriptive statistics that respect exact input types. This reference lists the functions worth knowing, with results run on Python 3.14 (and 3.15 where marked), and the edge cases that cause wrong answers. For arrays of numbers, NumPy and Polars are faster than looping over these functions.

## Integer functions in math

These functions work on Python ints of any size and return exact ints.

| Function | Example | Result |
|---|---|---|
| `math.isqrt(n)` | `math.isqrt(99)` | `9`: floor of the exact square root, no float rounding |
| `math.comb(n, k)` | `math.comb(52, 5)` | `2598960`: ways to choose k of n |
| `math.perm(n, k)` | `math.perm(5, 2)` | `20`: ordered choices |
| `math.factorial(n)` | `math.factorial(20)` | `2432902008176640000` |
| `math.gcd(*ints)` | `math.gcd(12, 18, 27)` | `3`; `math.gcd()` is `0` |
| `math.lcm(*ints)` | `math.lcm(4, 6)` | `12`; `math.lcm()` is `1` |
| `math.prod(iterable, start=1)` | `math.prod(range(1, 6))` | `120`; works for floats too |

`math.isqrt(n)` is the right way to test for perfect squares and to bound loops; `int(math.sqrt(n))` goes wrong for large `n` because the float square root rounds: `int(math.sqrt(10**16 - 1))` is `100000000`, `math.isqrt(10**16 - 1)` is `99999999`.

On 3.15 these functions also live in the `math.integer` submodule (`math.integer.isqrt`, `comb`, `factorial`, `gcd`, `lcm`, `perm`); the names in `math` keep working.

## Operations on int values

Python ints carry methods and built-ins for binary and modular work.

```python
(-5).bit_length()                               # 3: bits needed for the absolute value
(10).bit_count()                                # 2: number of one bits (3.10)
(255).to_bytes(2, "big")                        # b'\x00\xff'
(-1).to_bytes(2, "big", signed=True)            # b'\xff\xff'
int.from_bytes(b"\xff\xff", "big", signed=True) # -1
pow(2, 100, 1000)                               # 376: modular exponentiation without the huge power
pow(3, -1, 7)                                   # 5: modular inverse
```

`(256).to_bytes(1, "big")` raises `OverflowError: int too big to convert`; compute the length as `(n.bit_length() + 7) // 8` for unsigned values. `int(text, 0)` reads `0x`, `0o` and `0b` prefixes, and `int("1_000")` accepts underscores.

## Floating-point functions in math

These take and return floats and follow IEEE 754 rules.

| Function | Example | Result / note |
|---|---|---|
| `math.fsum(iterable)` | `math.fsum([0.1] * 7 + [1e16, 1.0, -1e16])` | `1.7`: correctly rounded sum |
| `math.isclose(a, b, *, rel_tol=1e-09, abs_tol=0.0)` | `math.isclose(1e-12, 0, abs_tol=1e-9)` | `True`; without `abs_tol`, `False` |
| `math.sumprod(p, q)` (3.12) | `math.sumprod([1, 2], [3, 4])` | `11`: dot product with extended precision |
| `math.fma(x, y, z)` (3.13) | `math.fma(2.0, 3.0, 1.0)` | `7.0`: `x * y + z` with one rounding |
| `math.dist(p, q)` | `math.dist((0, 0), (3, 4))` | `5.0` |
| `math.hypot(*coords)` | `math.hypot(3, 4)` | `5.0`, any number of dimensions |
| `math.cbrt(x)`, `math.exp2(x)` (3.11) | `math.cbrt(27)`, `math.exp2(10)` | `3.0`, `1024.0` |
| `math.log(x, base)`, `math.log2`, `math.log10` | `math.log2(1024)` | `10.0`; prefer `log2`/`log10` for those bases |
| `math.log1p(x)`, `math.expm1(x)` | `math.log1p(1e-10)` | accurate for tiny `x`, where `log(1 + x)` loses digits |
| `math.ulp(x)`, `math.nextafter(x, y, steps=1)` | `math.ulp(1.0)` | `2.220446049250313e-16`: spacing of floats at `x` |
| `math.copysign(x, y)` | `math.copysign(1, -0.0)` | `-1.0`: detects negative zero |
| `math.remainder(x, y)` | `math.remainder(7, 2)` | `-1.0`: IEEE remainder, quotient rounded half-even |
| `math.fmod(x, y)` | `math.fmod(-7, 2)` | `-1.0`: C remainder, sign of `x` |
| `math.modf(x)` | `math.modf(-3.25)` | `(-0.25, -3.0)` |
| `math.frexp(x)`, `math.ldexp(m, e)` | `math.frexp(8.0)` | `(0.5, 4)` |

Python 3.15 adds `math.fmax(x, y)` and `math.fmin(x, y)`, which ignore a NaN argument (`math.fmax(nan, 1.0)` is `1.0`), `math.signbit(x)` (true for `-0.0`), and `math.isnormal` / `math.issubnormal` for classifying tiny values.

## Rounding functions compared

Python has several ways to turn a float into an integer, each with a different rule.

| Call | 2.5 | -2.5 | 2.7 | -2.7 |
|---|---|---|---|---|
| `round(x)` | 2 | -2 | 3 | -3 |
| `int(x)`, `math.trunc(x)` | 2 | -2 | 2 | -2 |
| `math.floor(x)` | 2 | -3 | 2 | -3 |
| `math.ceil(x)` | 3 | -2 | 3 | -2 |

All of them return `int` for float input. `round(x, n)` with `n` given returns a float. For decimal rounding rules on exact values, use `Decimal.quantize` with a named rounding mode.

## NaN and infinity in math

`math.inf`, `-math.inf` and `math.nan` are floats. `math.isnan`, `math.isinf` and `math.isfinite` classify them; `math.isfinite` is the single check that rejects all three.

- Most `math` functions raise `ValueError` for invalid input (`math.sqrt(-1)`, `math.log(0)`) instead of returning NaN; since 3.14 the message names the problem (`expected a nonnegative input, got -1.0`). NumPy returns NaN with a warning.
- Overflow raises `OverflowError` (`math.exp(1000)`), while plain float arithmetic returns `inf` (`1e308 * 10`).
- Comparisons involving NaN are always false, so `min`, `max`, `sorted` and `statistics.median` give position-dependent results on data with NaN. Filter first.

## Averages in statistics

The `statistics` module offers several averages; they differ in speed, exactness and sensitivity to outliers.

| Function | Example input | Result | Notes |
|---|---|---|---|
| `fmean(data, weights=None)` | `[1, 2, 3]` with weights `[3, 1, 1]` | `1.6` | fast, always float |
| `mean(data)` | `[Decimal("1.1"), Decimal("2.2")]` | `Decimal('1.65')` | keeps `Decimal` and `Fraction` exact |
| `median(data)` | `[1, 2, 3, 4]` | `2.5` | resists outliers |
| `median_low`, `median_high` | `[1, 2, 3, 4]` | `2`, `3` | always a member of the data |
| `mode(data)` | `["a", "b", "b"]` | `"b"` | first mode on ties |
| `multimode(data)` | `"aabbc"` | `['a', 'b']` | all modes |
| `geometric_mean(data)` | `[1, 10, 100]` | `10.000000000000002` | growth rates, ratios |
| `harmonic_mean(data)` | `[40, 60]` | `48.0` | average speeds, rates |

Every function in this table except `multimode` raises `statistics.StatisticsError` on empty data (`mean requires at least one data point`); `multimode([])` returns `[]`. `stdev` and `variance` need at least two points. Check the length first, or catch the error, in code that summarises user-selected data.

## Spread: sample versus population

`stdev` and `variance` treat the data as a sample from a larger population (dividing by n - 1); `pstdev` and `pvariance` treat it as the whole population (dividing by n). Use the sample versions for measurements and surveys, the population versions when you have every value.

```python
import statistics

data = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
statistics.pstdev(data)        # 2.0
statistics.stdev(data)         # 2.138...
statistics.variance([1, 2, 3, 4]), statistics.pvariance([1, 2, 3, 4])   # (1.666..., 1.25)
```

## Quantiles and percentiles

`statistics.quantiles(data, n=4, method="exclusive")` returns the `n - 1` cut points that split the data into `n` groups: quartiles with `n=4`, percentiles with `n=100`. The default `"exclusive"` method suits samples; `"inclusive"` suits a complete population and matches what spreadsheets call `PERCENTILE.INC`.

```python
import statistics

statistics.quantiles(range(1, 11), n=4)                       # [2.75, 5.5, 8.25]
statistics.quantiles(range(1, 11), n=4, method="inclusive")   # [3.25, 5.5, 7.75]
latencies_ms = [12.0, 15.5, 20.1, 31.0, 99.0]
p95 = statistics.quantiles(latencies_ms, n=100, method="inclusive")[94]   # 85.4
```

On a small sample the default `"exclusive"` method extrapolates past the ends: the same call without `method=` returns 146.6 from data whose maximum is 99.0.

For latency percentiles over large streams, a histogram or a t-digest library avoids keeping every value.

## Correlation and regression

`statistics.correlation(x, y)` gives Pearson's r; `method="ranked"` (3.12) gives Spearman's rank correlation, which handles monotonic but non-linear relations. `statistics.linear_regression(x, y)` fits a line by least squares; `proportional=True` (3.11) forces the line through the origin.

```python
import statistics

x, y = [1, 2, 3, 4, 5], [2, 4, 5, 4, 5]
statistics.correlation(x, y)                         # 0.7746
statistics.correlation(x, y, method="ranked")        # 0.7378...
statistics.linear_regression(x, y)                   # LinearRegression(slope=0.6, intercept=2.2)
statistics.linear_regression(x, y, proportional=True)   # slope=1.2, intercept=0.0
statistics.covariance([1, 2, 3], [1, 2, 4])          # 1.5
```

## NormalDist

`statistics.NormalDist(mu, sigma)` models a normal distribution: probabilities, quantiles, sampling and comparison of two distributions.

```python
from statistics import NormalDist

iq = NormalDist(mu=100, sigma=15)
iq.cdf(130)                     # 0.9772: share below 130
iq.inv_cdf(0.95)                # 124.67: the 95th percentile
fitted = NormalDist.from_samples([1, 2, 3, 4, 5])     # mean 3.0, stdev 1.581
fitted.overlap(NormalDist(3, 2))                       # 0.887: overlapping area of two distributions
```

## Kernel density estimation

`statistics.kde(data, h, kernel="normal")` (3.13) returns a smooth density function estimated from samples; `statistics.kde_random(data, h, seed=...)` returns a function that draws new samples from that estimate. The bandwidth `h` controls smoothing and must be chosen for the data's scale.

```python
import statistics

density = statistics.kde([1, 2, 2, 3], h=0.5)
density(2)                                          # 0.4529
draw = statistics.kde_random([1, 2, 3], h=0.5, seed=1)
draw()                                              # a new sample near the data
```

## Exact types through statistics

`mean`, `median` and the variance functions keep `Fraction` and `Decimal` inputs exact (`statistics.mean([Fraction(1, 3), Fraction(1, 6)])` is `Fraction(1, 4)`), while `fmean` always converts to float. Mixing `Decimal` and `float` in one data set raises `TypeError`. For money summaries, pass `Decimal` values to `mean` and quantize the result.
