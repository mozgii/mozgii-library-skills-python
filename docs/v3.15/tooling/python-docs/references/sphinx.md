---
title: Sphinx 9 for Python projects
author: MOZGII, Inc.
lang: en
---

# Sphinx 9 for Python projects

Sphinx builds documentation from reStructuredText or MyST Markdown and generates API reference pages by importing your code. This reference covers a project layout, the autodoc, napoleon and autosummary settings, type hints, intersphinx and nitpicky mode, Markdown pages with MyST, the doctest and linkcheck builders, themes, and Read the Docs. Versions in October 2026: Sphinx 9.2.0 (needs Python 3.13+), myst-parser 5.1.0, sphinx-autodoc-typehints 3.17.4, furo 2025.12.19, sphinx-rtd-theme 3.1.0.

## Contents

This reference has 9 sections.

1. Project layout and the build command
2. autodoc: documenting modules from their docstrings
3. napoleon for Google and NumPy docstrings
4. autosummary: one page per module
5. Type hints in Sphinx output
6. intersphinx and nitpicky mode
7. Markdown pages with MyST
8. The doctest and linkcheck builders
9. Themes, Read the Docs and gotchas

## 1. Project layout and the build command

A Sphinx project is a folder with `conf.py` and a root document (`index.rst` or `index.md`). Keep it in `docs/` and its dependencies in a `docs` dependency group.

```text
docs/
├── conf.py
├── index.rst
├── guide.md          # MyST Markdown page
└── api.rst           # autosummary table
```

```bash
uv add --group docs sphinx furo myst-parser sphinx-autodoc-typehints
uv run --group docs sphinx-build -W --keep-going -n -b html docs docs/_build/html
```

- `-W` turns warnings into errors, `--keep-going` lists every warning before failing, `-n` enables nitpicky mode.
- Sphinx 9.2 requires Python 3.13 or newer to run. The docs environment can use a newer Python than the oldest one your package supports; autodoc only needs to import the package there.
- Add `docs/_build/` and generated stub folders to `.gitignore`.

## 2. autodoc: documenting modules from their docstrings

`sphinx.ext.autodoc` imports a module and inserts the docstrings of its members. Directives choose what to document.

```rst
.. automodule:: geo.distance

.. autoclass:: geo.Route
   :members:
   :special-members: __call__

.. autofunction:: geo.distance.haversine
```

Defaults for every directive go in `conf.py`:

```python
autodoc_default_options = {"members": True, "show-inheritance": True}
autodoc_member_order = "bysource"          # the file's order, not alphabetical
autodoc_mock_imports = ["torch"]           # heavy optional dependencies the docs build should not install
```

- autodoc imports the code, so every import the module runs at import time must succeed in the docs environment. `autodoc_mock_imports` replaces listed packages with mocks when installing them is impractical.
- Objects documented twice (an `automodule` on one page and the same module through autosummary on another) produce "duplicate object description of geo.distance ... use :no-index: for one of them". Document each object once.

## 3. napoleon for Google and NumPy docstrings

`sphinx.ext.napoleon` converts Google-style (`Args:`, `Returns:`) and NumPy-style (`Parameters` with underlines) sections into reST before autodoc renders them. Without it, those sections render as plain text.

```python
extensions = ["sphinx.ext.autodoc", "sphinx.ext.napoleon"]
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_use_rtype = True                  # a separate "Return type" line
napoleon_attr_annotations = True           # take attribute types from class annotations
```

Docstrings already in reST field-list style (`:param x:`) need no extension. A project should still use one style throughout; napoleon accepting both is not a reason to mix them.

## 4. autosummary: one page per module

`sphinx.ext.autosummary` writes a summary table and, with `autosummary_generate = True`, one stub page per listed module, each holding an `automodule`.

```python
extensions += ["sphinx.ext.autosummary"]
autosummary_generate = True
```

```rst
API
===

.. autosummary::
   :toctree: generated

   geo.distance
   geo.styles
```

The build writes `docs/generated/geo.distance.rst` and `geo.styles.rst` and includes them in the navigation. Custom templates in `docs/_templates/autosummary/` change what each stub contains, for example a `:members:` list or a class table.

## 5. Type hints in Sphinx output

autodoc puts annotations in the signature by default. Two ways move them into the parameter list:

| Setting | Effect |
|---|---|
| `autodoc_typehints = "description"` | built in: types appear beside each parameter, not in the signature |
| `sphinx_autodoc_typehints` extension | adds types to the parameter list, with more control over defaults, unions and `Optional` rendering |

```python
extensions += ["sphinx_autodoc_typehints"]
```

- Use one of the two, not both.
- Names imported under `if TYPE_CHECKING:` may not resolve when Sphinx evaluates annotations at build time; unresolved names show as plain text and, in nitpicky mode, as warnings. Import them normally if they are cheap, or list them in `nitpick_ignore`.
- Leave types out of the docstring text; napoleon would otherwise show them twice.

## 6. intersphinx and nitpicky mode

intersphinx links names in your docs to other projects' documentation through their `objects.inv` inventories.

```python
extensions += ["sphinx.ext.intersphinx"]
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "httpx": ("https://www.python-httpx.org", None),
}
nitpicky = True
nitpick_ignore = [("py:class", "geo._internal.Token")]
```

- With intersphinx, `float` in a signature and `` :py:func:`math.asin` `` in text link to docs.python.org.
- Nitpicky mode warns for every reference that resolves nowhere, which catches typos and types from libraries you forgot to map. With `-W` the build fails on them.
- MkDocs sites built with mkdocstrings publish an `objects.inv` too, so they can be intersphinx targets.

## 7. Markdown pages with MyST

myst-parser lets Sphinx read Markdown pages with Sphinx roles and directives. Add it to `extensions` and write pages in `.md`.

```python
extensions += ["myst_parser"]
```

```markdown
# Guide

Call {py:func}`geo.distance.haversine` with two points.

:::{note}
Distances use a spherical Earth.
:::
```

- Roles are written `` {role}`target` ``; directives are fenced blocks with a `{directive}` info string. The `:::` colon fence shown needs `myst_enable_extensions = ["colon_fence"]`; the backtick form works without it.
- autodoc directives inside Markdown need an `{eval-rst}` block; most projects keep `api.rst` in reST and write guides in Markdown.

## 8. The doctest and linkcheck builders

`sphinx.ext.doctest` runs `>>>` examples found in the documentation pages, including docstrings that autodoc pulled in.

```python
extensions += ["sphinx.ext.doctest"]
doctest_global_setup = "from geo import haversine"
```

```bash
uv run --group docs sphinx-build -W -b doctest docs docs/_build/doctest
```

- Examples run in a namespace that does not contain the documented module's globals. Without `doctest_global_setup`, a docstring example calling `haversine` fails with `NameError: name 'haversine' is not defined`.
- Docstring examples are simpler to run with `pytest --doctest-modules`; keep the Sphinx builder for `.. doctest::` blocks in prose pages.
- The `linkcheck` builder (`-b linkcheck`) requests every external URL and reports broken and redirected links. Run it on a schedule; external sites fail at random and should not block pull requests.

## 9. Themes, Read the Docs and gotchas

Themes are packages; set `html_theme` in `conf.py`.

| Theme | Character |
|---|---|
| `furo` | clean, light and dark modes, good search; popular for libraries |
| `sphinx_rtd_theme` | the classic Read the Docs look |
| `alabaster` | Sphinx's built-in default |

Read the Docs builds Sphinx and MkDocs projects from a `.readthedocs.yaml` file in the repository and hosts one version per activated tag or branch, with a version menu and pull-request previews. Pin the Python version and install from the same `docs` group the CI job uses, so both build the same thing.

These are the Sphinx problems seen most.

1. **"autosummary: stub file not found 'geo.distance'".** Stubs were not generated: `autosummary_generate` is off, or the build ran with stale state. Set it to `True` and rebuild from a clean `_build`.
2. **"duplicate object description of geo.distance".** The module is documented on two pages. Keep one, or add `:no-index:` to the other.
3. **`NameError` in the doctest builder for a documented function.** The examples run without the module's globals. Set `doctest_global_setup`.
4. **"failed to import module" or an empty API page.** autodoc could not import the code. Install the package and its runtime dependencies in the docs environment, or mock heavy ones with `autodoc_mock_imports`.
5. **Hundreds of nitpicky warnings from third-party types.** Their projects are not in `intersphinx_mapping`. Add their inventories, and `nitpick_ignore` the few that publish none.
6. **The build fails on Python 3.12 after upgrading to Sphinx 9.** Sphinx 9.2 needs Python 3.13+. Run the docs job on a newer Python.
