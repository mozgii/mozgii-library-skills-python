---
title: MkDocs, Material and mkdocstrings for Python API docs
author: MOZGII, Inc.
lang: en
---

# MkDocs, Material and mkdocstrings for Python API docs

The configuration behind a Markdown documentation site with a generated Python API reference: the mkdocstrings Python handler and its options, cross-references between pages and to other projects, per-page and per-object settings, versioned sites with mike, and the path off Material for MkDocs before its end of life. Versions in October 2026: mkdocs 1.6.1, mkdocs-material 9.8.0, mkdocstrings 1.0.8, mkdocstrings-python 2.0.10, griffe 2.4.1, mike 2.2.0, Zensical 0.0.74.

## Contents

This reference has 8 sections.

1. How mkdocstrings reads your code
2. Handler options worth setting
3. Per-page and per-object options
4. Cross-references and inventories
5. One page per module
6. Versioned sites with mike
7. Material's end of life, Zensical and other forks
8. Gotchas

## 1. How mkdocstrings reads your code

mkdocstrings is a MkDocs plugin; its Python handler (`mkdocstrings-python`) loads your package with griffe, which parses source files statically. A `::: dotted.path` line in any Markdown page is replaced by that object's rendered documentation.

- Static analysis means the docs build does not import your code: no dependencies needed, no import side effects, and names under `if TYPE_CHECKING:` resolve.
- `paths: [src]` tells griffe where to find the package; with a flat layout use `paths: [.]`.
- Compiled extension modules have no source to parse; griffe inspects them by importing them. `allow_inspection` (default on) permits that; `force_inspection` imports even pure-Python modules, for code that builds its API dynamically.
- Stub files (`.pyi`) next to modules are merged, so a compiled module documented in stubs renders with full signatures.

## 2. Handler options worth setting

Options go under `plugins: mkdocstrings: handlers: python: options:` and apply to every `:::` block.

| Option | Typical value | Effect |
|---|---|---|
| `docstring_style` | `google`, `numpy`, `sphinx` or `auto` | the parser for docstring sections |
| `show_source` | `false` | hide the "Source code" fold under each object |
| `show_signature_annotations` | `true` | types in the signature |
| `separate_signature` | `true` | signature in its own code block under the heading; formatted with Ruff or Black if installed |
| `signature_crossrefs` | `true` | link types in signatures to their docs |
| `members_order` | `source` | keep the file's order instead of alphabetical |
| `merge_init_into_class` | `true` | show `__init__` parameters on the class |
| `show_root_heading` | `true` | a heading for the object named in `:::` itself |
| `heading_level` | `2` | the level of that heading |
| `filters` | `["!^_"]` | regexes on member names; `!` excludes; the default hides private members |
| `members` | `[haversine, EARTH_RADIUS_KM]` | document only these members |
| `inherited_members` | `true` | include members from base classes |
| `show_if_no_docstring` | `false` | skip undocumented members |
| `docstring_section_style` | `table`, `list` or `spacy` | how parameters and returns are laid out |
| `show_symbol_type_heading`, `show_symbol_type_toc` | `true` | small badges (function, class, attribute) |
| `extensions` | griffe extensions | e.g. reading pydantic fields or dataclass details |

Parameter tables take their Type column from the annotations, even when the docstring gives no types.

## 3. Per-page and per-object options

Indented `options:` under a `:::` line override the global ones for that object only. This page documents one module with a heading and two chosen members:

```markdown
# API reference

::: geo.distance
    options:
      show_root_heading: true
      heading_level: 2
      filters: ["!^_"]
      members: [haversine, EARTH_RADIUS_KM]
```

A module written in a different docstring style can be rendered with its own parser:

```markdown
::: geo.legacy
    options:
      docstring_style: sphinx
```

## 4. Cross-references and inventories

mkdocstrings registers every rendered object as a link target (through the mkdocs-autorefs plugin it installs). Link to one from any page with Markdown reference-link syntax.

```markdown
The main function is [haversine][geo.distance.haversine]; see also
[`EARTH_RADIUS_KM`][geo.distance.EARTH_RADIUS_KM] and Python's [math.asin][].
```

- `[title][dotted.path]` links with your own text; `[dotted.path][]` uses the path as the text.
- Links to other projects come from their Sphinx-format inventories, listed under the handler: `inventories: [https://docs.python.org/3/objects.inv]`. Then `[math.asin][]` links to docs.python.org.
- Your site publishes its own `objects.inv`, so Sphinx projects can link to you with intersphinx, and MkDocs projects with `inventories`.
- In strict mode an unknown target fails the build: "mkdocs_autorefs: index.md: Could not find cross-reference target 'geo.nope'".
- Inside docstrings, the same syntax works: `See [geo.distance.haversine][] for the formula.`

## 5. One page per module

A small package fits on one API page. For a larger one, write one Markdown page per public module and list them in `nav`:

```yaml
nav:
  - Home: index.md
  - Guide: guide.md
  - API:
      - geo.distance: api/distance.md
      - geo.bearing: api/bearing.md
```

Each page holds a title and one `::: geo.distance` block. Document what the package exports (its `__all__`), not every internal module; readers and search find the public names faster. Plugins that generate these pages automatically exist, but a handful of hand-written pages keeps the navigation deliberate.

## 6. Versioned sites with mike

mike builds each version of the site into its own folder on a git branch (`gh-pages` by default) and keeps a `versions.json` that Material's version selector reads.

```yaml
# mkdocs.yml
extra:
  version:
    provider: mike
```

```bash
mike deploy --update-aliases 0.1 latest      # build the checkout into 0.1/ and move the "latest" alias to it
mike set-default latest                      # the site root redirects to latest/
mike list                                    # "0.1 [latest]"
mike deploy --push --update-aliases 0.2 latest   # in CI: build and push to origin/gh-pages
mike delete 0.0.9 --push                     # remove an old version
```

After `mike deploy` the `gh-pages` branch holds `.nojekyll`, `0.1/`, `latest/`, `index.html` and `versions.json`.

- Deploy from a release workflow after the tag, with `--push`, so the published docs always match a released version.
- Deploy docs for unreleased work under a `dev` version, not under `latest`.
- `--alias-type` chooses how aliases are written: `symlink` (the default), `copy` or `redirect`. Use `copy` or `redirect` for hosts that do not follow symlinks.

## 7. Material's end of life, Zensical and other forks

Material for MkDocs announced its end of life for 5 May 2027; until then it receives critical fixes and security updates. MkDocs itself has had no release since 1.6.1 in August 2024. Every build with Material 9.8 prints the notice; `NO_MKDOCS_2_WARNING=1` hides it.

The options in late 2026:

| Option | Status |
|---|---|
| Stay on MkDocs + Material | works and is supported until May 2027 |
| Zensical | successor by the Material team, MIT licensed, alpha (0.0.74); reads `mkdocs.yml` and a native `zensical.toml`; a `classic` theme variant keeps Material's look |
| ProperDocs | a fork of MkDocs (1.6.7 on PyPI); cibuildwheel builds its docs with it |
| Sphinx with MyST | Markdown pages on Sphinx, actively maintained |

Zensical 0.0.74 built a test project's `mkdocs.yml`, including mkdocstrings API pages, with `zensical build -f mkdocs.yml`. Evaluate it on a branch: build, compare pages, navigation, search and every plugin you use, and read its migration guide before switching. Being alpha, its configuration may still change between releases; pin its version.

## 8. Gotchas

These are the MkDocs and mkdocstrings problems seen most.

1. **"mkdocstrings: geo.distance could not be found" and "Could not collect 'geo.distance'".** The dotted path has a typo, or griffe cannot find the package. Check the path, and set `paths: [src]` (src layout) or install the package in the docs environment.
2. **Private helpers appear in the API reference.** A `filters` override replaced the default. Keep `"!^_"` in the list.
3. **Parameters show twice, once from the docstring and once from `__init__`.** Class docstring and `__init__` docstring both document them. Set `merge_init_into_class: true` and document parameters in one place.
4. **The NumPy-style page renders sections as plain text.** The global `docstring_style` is `google`. Set `numpy` globally or for that `:::` block.
5. **Links to Python's documentation do not resolve.** No inventory was listed. Add `https://docs.python.org/3/objects.inv` under `inventories`.
6. **mike's version selector is missing.** `extra.version.provider: mike` is not set, or the site was built with `mkdocs build` instead of `mike deploy`.
