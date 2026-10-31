---
title: Fixing cascading Pyright errors
author: MOZGII, Inc.
lang: en
---

# Fixing cascading Pyright errors

A cascading type error is reported far from its cause: one untyped return value deep in a helper module turns into dozens of `reportUnknownMemberType` errors in every file that touches it. Fixing the symptoms adds casts everywhere; fixing the root once clears them all. This file finds the root and fixes from the bottom of the chain upwards.

## Contents

This reference has 9 sections.

1. When an error is a cascade
2. Rules that never bend
3. Step 1: collect and group the errors
4. Step 2: map the chain top-down
5. Step 3: fix bottom-up
6. Common roots and their fixes
7. Notes that survive the session
8. Checklist
9. Gotchas

## 1. When an error is a cascade

A Pyright error is a cascade, not a local mistake, when the file it names looks correct and the type comes from somewhere else.

- More than about 20 errors of the `reportUnknown*` family after a change or an upgrade.
- An error that survives two fix attempts in the file where it is reported.
- The reported type is `Unknown` or "partially unknown" and the file itself is fully annotated.

For one or two local errors, the fix table in the skill's main page is faster.

## 2. Rules that never bend

These limits keep a cascade fix honest; breaking one hides the error instead of fixing it.

- Never lower `typeCheckingMode`, switch a file to `# pyright: basic`, or turn a rule off in config.
- Never add `Any` or a bare `# type: ignore` to make an error disappear. Use `TypedDict`, `Protocol`, generics, `@overload` or a stub.
- `cast()` is allowed once, at a documented boundary (an untyped library, a runtime check Pyright cannot follow), never at the symptom.
- Never guess a library's types: read its source, its docs or its stub; when none exist, write a stub from what the code really returns.
- Read large files by line range or symbol, not whole.

## 3. Step 1: collect and group the errors

Grouping Pyright's output by rule and file shows where the errors concentrate before anything is edited. The script `scripts/pyright_summary.py` (standard library only) reads `--outputjson`.

```bash
uv run pyright --outputjson > pyright.json
python3 scripts/pyright_summary.py pyright.json --limit 5          # counts by rule, then by file
python3 scripts/pyright_summary.py pyright.json --rule reportUnknownMemberType
uv run pyright --outputjson | python3 scripts/pyright_summary.py -  # from a pipe
```

When the file is not on disk (a pack install), read this script from the library that served this page and save it first.

It prints the total, a count per rule, and per file the first messages with line numbers. Many files with the same rule and the same symbol name usually share one root.

Without the script, `jq` gives the same counts: `jq -r '.generalDiagnostics[].rule' pyright.json | sort | uniq -c | sort -rn`.

## 4. Step 2: map the chain top-down

Mapping the chain means following the unknown type back through its definitions until it reaches one with a missing or wrong annotation.

1. Pick one error from the largest group and read the message: `Type of "rows" is partially unknown  Type of "rows" is "list[Unknown]"`.
2. Find where that value comes from (go to definition, or search for the function). Hover in Pylance, or add `reveal_type(rows)` and run Pyright on that file.
3. Repeat on the definition: its parameters, its return, the attribute it reads. Stop at the first symbol whose own annotation is missing, is `Unknown`, or comes from an untyped package.
4. Write the chain down before editing:

```text
reports/daily.py:41  rows: list[Unknown]          (symptom, 23 similar errors)
  ← services/sales.py:18  fetch_sales() -> list[Unknown]
    ← db/queries.py:7  run_query(sql) has no return annotation     (root)
      ← legacy_db.execute(): untyped package                        (leaf: needs a stub or a cast)
```

## 5. Step 3: fix bottom-up

Fixing bottom-up means the leaf gets its real type first, Pyright runs again, and the types flow upward before anything else is touched.

1. At the leaf, add the real type: a return annotation, a `TypedDict` for the row shape, a `Protocol` for a duck-typed argument, a type parameter for a function that returns what it receives, or a stub in `typings/<package>/__init__.pyi` for an untyped package.
2. Run `uv run pyright` again. Most symptoms above the leaf disappear because inference now has a type to carry.
3. Fix what remains one level up, re-run, and repeat. Do not edit the symptom files until the levels below them are clean.

```python
# db/queries.py: the root fix, written once
from typing import LiteralString, TypedDict, cast

import legacy_db


class SaleRow(TypedDict):
    sku: str
    qty: int
    total_cents: int


def run_query(sql: LiteralString, params: tuple[object, ...] = ()) -> list[SaleRow]:
    rows = legacy_db.execute(sql, params)          # untyped package
    return cast("list[SaleRow]", rows)             # one documented boundary cast; a stub removes it later
```

## 6. Common roots and their fixes

Each Pyright error family below usually has the same root, and the fix belongs at that root.

| Error family | Usual root | Fix at the root |
|---|---|---|
| `reportUnknownMemberType` across many files | an untyped package or a missing return annotation | stub in `typings/`, or annotate the wrapper function |
| `reportUnknownVariableType` on `list[Unknown]` | an empty container created without a type | `rows: list[SaleRow] = []` where it is created |
| `reportUnknownArgumentType` in callers | a callback or decorator that loses the signature | `ParamSpec` in the decorator: `def deco[**P, R](fn: Callable[P, R]) -> Callable[P, R]` |
| `reportAttributeAccessIssue` on a union | a function returns `A \| B` but callers assume `A` | `@overload` the function on its inputs, or narrow with `isinstance` |
| `reportArgumentType` with `None` | an optional value passed on without a check | guard once where the value is produced, or make the producer return a non-optional |
| `reportMissingModuleSource` | a stub package exists but the runtime package is missing in this environment | install the package in the environment Pyright uses, or keep the stub and accept the warning |
| `reportIncompatibleMethodOverride` | a base class signature changed | fix the base or every override together; add `@override` |

## 7. Notes that survive the session

A short notes file in the repository keeps the next session, or the next person, from rediscovering the same root.

```markdown
<!-- docs/typing-notes.md -->
### 2026-10-28 reportUnknownMemberType in reports/
- Root: legacy_db.execute() is untyped.
- Fix: typings/legacy_db/__init__.pyi with execute() -> list[dict[str, object]]; SaleRow TypedDict in db/queries.py.
```

Read it at the start of a typing session; add an entry when a fix was not obvious. Record stubs, shared `TypedDict`s, `Protocol`s and type parameter bounds.

## 8. Checklist

A cascade fix is done when every item holds.

- [ ] The chain from symptom to root was written down before editing.
- [ ] The first edit was at the root; Pyright was re-run after each level.
- [ ] `uv run pyright` reports 0 errors in every changed file, and no new errors elsewhere.
- [ ] No config strictness lowered; no new `Any`, bare `# type: ignore` or `# pyright: basic`.
- [ ] At most one `cast()` per boundary, with a comment saying why.
- [ ] A notes entry was added when the root was hard to find.

## 9. Gotchas

These are the ways a cascade fix goes wrong.

1. **Fixing symptoms first.** Casts in every caller make the error count drop while the root stays `Unknown`, and the next new caller fails again. Stop and map the chain.
2. **The error count rises after the root fix.** Expected: a typed root exposes real mismatches that `Unknown` hid. Those are bugs to fix, not regressions.
3. **`reveal_type` left in the code.** At runtime `typing.reveal_type` (3.11+) prints the runtime type to stderr, and Pyright reports it as information; remove it before committing.
4. **A stub that lies.** A `.pyi` that claims `-> str` for a function that returns `bytes` makes Pyright happy and the program wrong. Write stubs from the real return values (run the function once, or read its source).
5. **Two checkers disagree.** mypy and Pyright infer differently in places (empty containers, overloads). Treat the one CI enforces as the reference.
