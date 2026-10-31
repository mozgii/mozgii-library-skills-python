---
title: mypy configuration and adoption
author: MOZGII, Inc.
lang: en
---

# mypy configuration and adoption

mypy is the reference type checker for Python. This reference configures mypy 2.x in strict mode, explains what changed in 2.0, and shows how to adopt it in an untyped codebase one module at a time.

## Contents

This reference has 9 sections.

1. Strict configuration in pyproject.toml
2. What changed in mypy 2.0
3. Running mypy: CLI, daemon, parallel mode
4. Per-module overrides and missing imports
5. Plugins
6. Stubs: stubgen and stub packages
7. Ignore comments and error codes
8. Gradual adoption plan
9. Gotchas

## 1. Strict configuration in pyproject.toml

A strict mypy configuration turns on every optional check that catches real bugs, plus a few error codes that are off even under `--strict`.

```toml
[tool.mypy]
python_version = "3.12"                  # the lowest supported version: newer syntax is reported
files = ["src", "tests"]
strict = true                            # disallow untyped defs, Any generics, implicit re-exports, ...
warn_unreachable = true
enable_error_code = [
  "ignore-without-code",                 # every ignore names its code
  "redundant-expr",
  "truthy-bool",                         # `if obj:` where obj is never falsy
  "possibly-undefined",
  "exhaustive-match",                    # match statements that miss a case
  "deprecated",                          # calls to @deprecated functions
  "explicit-override",                   # require @override
  "unused-awaitable",
]
plugins = ["pydantic.mypy"]              # only when pydantic is used
```

- `python_version` is the minimum supported version, so `class Box[T = int]` is reported as "requires Python 3.13" when the minimum is 3.12.
- mypy reads `[tool.mypy]` in `pyproject.toml`, or `mypy.ini`, `.mypy.ini`, `setup.cfg` (first found wins).

## 2. What changed in mypy 2.0

mypy 2.0 (2026) changed several defaults; code that passed 1.x can fail after the upgrade.

| Change | Effect |
|---|---|
| `--local-partial-types` on by default | an empty container's type must be inferable in the same scope; `x = []` at module level filled in a function is an error |
| `--strict-bytes` on by default | `bytearray` and `memoryview` are no longer accepted where `bytes` is expected (PEP 688) |
| `--allow-redefinition` has the new semantics | a variable may change type between blocks, but two annotations for one name are an error; `--allow-redefinition-old` keeps the 1.x behaviour |
| targeting Python 3.9 removed | `python_version` must be 3.10 or newer |
| `--ignore-missing-imports` respected for every package | no special case for formerly bundled stubs |
| parallel checking (`--num-workers N`) | experimental; up to about 5x faster on large projects |

The current release is mypy 2.4.1 (October 2026).

## 3. Running mypy: CLI, daemon, parallel mode

Run mypy through the project environment so it sees installed packages and their stubs.

```bash
uv add --dev mypy
uv run mypy                               # uses `files` from the config
uv run mypy src/my_app/models.py          # one file
uv run mypy -n 8                          # 8 worker processes (experimental parallel mode)
uv run dmypy run -- src                   # daemon: fast re-checks while editing
uv run mypy --install-types --non-interactive   # install missing types-* stub packages (asks pip)
```

- The cache lives in `.mypy_cache/`; keep it in CI between runs for incremental speed, and add it to `.gitignore`.
- `reveal_type(expr)` prints the inferred type as a note; `reveal_locals()` prints every local.

## 4. Per-module overrides and missing imports

Overrides relax or tighten settings for some modules, which is how strict mode coexists with legacy code.

```toml
[[tool.mypy.overrides]]
module = ["my_app.legacy.*"]
ignore_errors = true                     # not checked yet; remove the entry module by module

[[tool.mypy.overrides]]
module = ["legacy_client", "legacy_client.*"]
ignore_missing_imports = true            # untyped third-party package without stubs

[[tool.mypy.overrides]]
module = ["tests.*"]
disallow_untyped_defs = false            # test functions without annotations
```

Never set `ignore_missing_imports = true` globally: a misspelled import then passes silently.

## 5. Plugins

Plugins teach mypy about libraries whose behaviour the type system cannot express.

| Library | Plugin | Adds |
|---|---|---|
| pydantic v2 | `pydantic.mypy` | `__init__` signatures from fields, `model_validate` return types; settings `[tool.pydantic-mypy] init_typed = true` |
| Django | `mypy_django_plugin.main` (django-stubs) | model fields, managers, settings |
| SQLAlchemy 2.0 | none needed | `Mapped[...]` annotations type models natively |
| attrs | built in | `@define` classes |

dataclasses, `NamedTuple`, `Enum` and `@dataclass_transform` libraries need no plugin.

## 6. Stubs: stubgen and stub packages

A stub (`.pyi`) gives types to a package that has none; mypy and Pyright read the same stubs.

```bash
uv add --dev types-requests types-PyYAML          # typeshed stubs from PyPI
uv run stubgen -p legacy_client -o typings         # draft stubs from the installed package
```

Then edit the drafts (stubgen writes `Any` where it cannot infer) and point mypy at them with `mypy_path = "typings"`. Publish stubs for your own library inline instead: ship `py.typed` and annotate the code.

## 7. Ignore comments and error codes

Every mypy error has a code in brackets; an ignore comment names that code.

```python
result = legacy_client.fetch()  # type: ignore[no-untyped-call]  # stub pending upstream
```

| Code | Meaning |
|---|---|
| `attr-defined` | attribute or module member does not exist (or is newer than `python_version`) |
| `arg-type`, `return-value`, `assignment` | value does not fit the declared type |
| `union-attr` | attribute used on a union where one member lacks it (often `None`) |
| `no-untyped-def`, `no-untyped-call` | missing annotations in strict mode |
| `untyped-decorator` | a decorator without types makes the function untyped |
| `import-untyped`, `import-not-found` | package without stubs, or not installed |
| `override` | incompatible method override |
| `narrowed-type-not-subtype` | a `TypeIs` return type outside the parameter's own type |

`warn_unused_ignores` (part of `strict`) reports ignores that no longer suppress anything.

## 8. Gradual adoption plan

Adopt mypy on an existing codebase in steps, so every step merges green.

1. Add mypy with the strict config and a global override that ignores errors for every existing package: `module = ["my_app.*"]`, `ignore_errors = true`.
2. Add a CI job that runs `uv run mypy`; new packages outside the override are strict from the start.
3. Pick a leaf module (one with few imports from the rest), remove it from the override with a narrower pattern, fix it, merge.
4. Repeat upwards; track progress with `uv run mypy --linecount-report report/` or by counting override entries.
5. Remove the override when it is empty.

## 9. Gotchas

These are the mypy behaviours that surprise people.

1. **`Module "typing" has no attribute "TypeIs"`.** The name is newer than `python_version`. Import it from `typing_extensions`.
2. **mypy and Pyright disagree.** They infer empty containers and overloads differently. Annotate the variable explicitly; choose one checker as the gate.
3. **`Need type annotation for "items"` after upgrading to 2.0.** `local_partial_types` is now the default. Annotate the empty container where it is created.
4. **`bytearray` rejected where `bytes` worked before.** `strict_bytes` is the default in 2.0. Accept `bytes | bytearray`, or `collections.abc.Buffer`.
5. **mypy does not reject an f-string passed as `LiteralString`.** mypy treats `LiteralString` as `str`; Pyright enforces it. Use Pyright for that guarantee, or a query builder.
6. **mypy is slow on a large codebase.** Keep `.mypy_cache` between runs, use `dmypy` locally, try `-n` workers in CI.
