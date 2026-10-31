---
name: python-docs
description: Documents Python code and publishes it - what to put in a docstring, Google versus NumPy versus reST (Sphinx) docstring styles, letting type hints carry the types, MkDocs with Material and mkdocstrings for an API reference, Sphinx 9 with autodoc, napoleon and intersphinx, MyST Markdown, the Material for MkDocs end of life in May 2027 and Zensical, keeping examples correct with doctest and pytest --doctest-modules including Markdown files, a README that renders on PyPI, strict docs builds in CI, and versioned docs with mike or Read the Docs. Use when writing docstrings, choosing a docstring style or a docs tool, setting up an API reference, making code examples in docs testable, fixing a README on PyPI, or building docs in CI.
metadata:
  tags:
  - python
  - documentation
  - docstrings
  - mkdocs
  - mkdocstrings
  - sphinx
  - doctest
  - readme
---

# Python documentation: docstrings, API reference and tested examples

Good Python documentation has three layers: docstrings that say what each public function promises, a generated API reference built from them, and prose pages (guides, a README) whose code examples run in the test suite so they cannot rot. This skill covers all three and the tools that build them. The public API itself (what to export, `__all__`) is the python-code-design skill; type hints are the python-typing skill.

## Step 0: read the installed versions

Docs tools changed status in 2026 (Material for MkDocs announced its end of life; Sphinx 9 needs Python 3.13+), so read what the project uses first.

```bash
uv run python -c "import sys; print(sys.version)"
uv pip list 2>/dev/null | grep -iE "mkdocs|mkdocstrings|griffe|sphinx|myst|furo|zensical|mike"
ls mkdocs.yml docs/conf.py docs/source/conf.py zensical.toml 2>/dev/null
grep -nE "docstring_style|napoleon|convention" mkdocs.yml docs/conf.py pyproject.toml 2>/dev/null
```

Current releases (October 2026): mkdocs 1.6.1 (no release since August 2024), mkdocs-material 9.8.0, mkdocstrings 1.0.8, mkdocstrings-python 2.0.10, griffe 2.4.1, Zensical 0.0.74 (alpha), Sphinx 9.2.0 (needs Python 3.13+), furo 2025.12.19, myst-parser 5.1.0, sphinx-autodoc-typehints 3.17.4, mike 2.2.0.

## What to write in a docstring

A docstring is the contract a caller reads in the editor's hover. It states what the function does and promises, not how it does it.

- First line: one sentence in the imperative mood ending with a period ("Return the great-circle distance between two points in kilometres."). Tools show it alone in summaries.
- Then what a caller cannot see from the signature: units, ranges, what `None` means, side effects (writes a file, opens a connection), thread safety, complexity when it matters.
- `Raises:` lists exceptions callers should handle, not every possible one.
- `Examples:` with a short doctest when usage is not obvious; it doubles as a test.
- Leave out implementation notes (they belong in comments), types already in the annotations, and history ("changed in 2.0" belongs in the changelog, or a `versionchanged` directive in Sphinx).
- Private helpers get a docstring only when the name does not say enough. Modules and public classes get one that says what they are for.

## Docstring styles: Google, NumPy and reST

Three styles exist; tools parse all three. Pick one per project and set it in the docs tool and the linter.

```python
def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the initial bearing from the first point to the second.

    Args:
        lat1: Latitude of the start, in degrees.
        lon1: Longitude of the start, in degrees.
        lat2: Latitude of the end, in degrees.
        lon2: Longitude of the end, in degrees.

    Returns:
        Degrees clockwise from true north, from 0 up to 360.
    """
```

The same in NumPy style uses underlined section headers and can group parameters:

```python
def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the initial bearing from the first point to the second.

    Parameters
    ----------
    lat1, lon1 : float
        Latitude and longitude of the start, in degrees.
    lat2, lon2 : float
        Latitude and longitude of the end, in degrees.

    Returns
    -------
    float
        Degrees clockwise from true north, from 0 up to 360.
    """
```

reST (Sphinx field list) style writes `:param lat1: Latitude of the start, in degrees.` and `:returns: ...` lines.

| Style | Reads well | Typical in |
|---|---|---|
| Google | compact, readable in the source | applications and most new libraries |
| NumPy | long parameter descriptions, grouped parameters | scientific Python (NumPy, SciPy, pandas) |
| reST | native to Sphinx, no extension needed | older Sphinx projects |

Ruff's pydocstyle rules (`D`) check docstrings; set the style with `[tool.ruff.lint.pydocstyle] convention = "google"` (or `"numpy"`, `"pep257"`).

## Type hints carry the types

When the signature has annotations, do not repeat the types in the docstring. Both mkdocstrings and Sphinx (with napoleon and `autodoc_typehints`) take parameter and return types from the annotations and show them in the rendered table.

- A Google-style `Args:` entry is `name: description`, without `(float)`. The rendered page still shows `float`, taken from the signature.
- Types in two places drift apart; the annotation is the one a type checker verifies.
- mkdocstrings reads the source statically with griffe, so names imported under `if TYPE_CHECKING:` and 3.14's lazy annotations work. Sphinx autodoc imports the module, so the docs build needs the package's dependencies installed and import-time side effects run.

## Choose a docs tool

Two ecosystems cover nearly every Python project.

| | MkDocs + Material + mkdocstrings | Sphinx + autodoc + napoleon |
|---|---|---|
| Source format | Markdown | reStructuredText, or Markdown with MyST |
| API reference | `::: package.module` in a page; static analysis with griffe | `.. automodule::`; imports the code |
| Cross-project links | inventories (`objects.inv`) | intersphinx, the origin of `objects.inv` |
| Output | HTML | HTML, PDF via LaTeX, ePub, man pages |
| Status in late 2026 | MkDocs 1.6.1 unchanged since 2024; Material reaches end of life on 5 May 2027 | actively released (9.2) |

Material for MkDocs prints at every build: "Material for MkDocs reaches end of life on May 5, 2027", pointing to Zensical, a successor by the same team. Zensical 0.0.74 is alpha, but it built an existing `mkdocs.yml` with mkdocstrings pages unchanged (`zensical build -f mkdocs.yml`). For a new project today: MkDocs + Material is still the shortest path for Markdown docs, with a migration to Zensical planned before May 2027; Sphinx is the long-term choice when you need PDF output, deep cross-referencing, or already use it. `NO_MKDOCS_2_WARNING=1` silences the build banner.

## MkDocs with Material and mkdocstrings

mkdocstrings renders API documentation from docstrings into MkDocs pages; its Python handler uses griffe to read the code without importing it. This `mkdocs.yml` builds a site with an API page.

```yaml
site_name: geo
repo_url: https://github.com/example/geo
theme:
  name: material
  features: [content.code.copy, navigation.sections]
nav:
  - Home: index.md
  - API reference: reference.md
plugins:
  - search
  - mkdocstrings:
      handlers:
        python:
          paths: [src]
          inventories:
            - https://docs.python.org/3/objects.inv
          options:
            docstring_style: google
            show_source: false
            show_signature_annotations: true
            separate_signature: true
            members_order: source
            merge_init_into_class: true
markdown_extensions:
  - admonition
  - pymdownx.highlight
  - pymdownx.superfences
```

The API page is one line per module:

```markdown
# API reference

::: geo.distance
```

```bash
uv add --group docs mkdocs-material mkdocstrings-python
uv run --group docs mkdocs serve           # live preview at http://127.0.0.1:8000
uv run --group docs mkdocs build --strict  # warnings are errors
```

- `paths: [src]` lets griffe find a src-layout package without installing it.
- `separate_signature` needs Ruff or Black installed to format long signatures; without them the build logs a notice and shows them unformatted.
- Every option, cross-references and per-page overrides: [mkdocs-mkdocstrings.md](references/mkdocs-mkdocstrings.md).

## Sphinx with autodoc and napoleon

Sphinx's autodoc extension imports modules and renders their docstrings; napoleon converts Google and NumPy sections to reST first. This `conf.py` builds API docs from Google- or NumPy-style docstrings and links standard-library types to docs.python.org.

```python
project = "geo"
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
]
napoleon_google_docstring = True
napoleon_numpy_docstring = True
autodoc_typehints = "description"         # types from annotations, shown in the parameter list
autodoc_member_order = "bysource"
autodoc_default_options = {"members": True, "show-inheritance": True}
intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}
nitpicky = True                           # warn on every cross-reference that does not resolve
html_theme = "furo"
```

```bash
uv run --group docs sphinx-build -W --keep-going -n -b html docs docs/_build/html
```

`-W` turns warnings into errors, `--keep-going` reports them all before failing, `-n` is nitpicky mode. MyST Markdown pages, autosummary, the doctest builder and link checking: [sphinx.md](references/sphinx.md).

## Keep examples tested with doctest

Examples in docstrings and Markdown pages are code; run them in the test suite so they fail when the API changes. pytest collects them with two options.

```toml
[tool.pytest]
addopts = ["--doctest-modules", "--doctest-glob=*.md"]
testpaths = ["src", "docs"]
doctest_optionflags = ["NORMALIZE_WHITESPACE", "ELLIPSIS"]
```

A docstring example is a `>>>` line followed by the expected output:

```text
    Examples:
        >>> round(haversine(51.5074, -0.1278, 48.8566, 2.3522))
        344
        >>> haversine(0, 0, 0, 0)
        0.0
```

- `--doctest-modules` imports every module under `testpaths` and runs its docstring examples; `--doctest-glob=*.md` runs `>>>` examples in Markdown files.
- In a Markdown file, leave a blank line between the last output line and the closing fence. Without it, doctest reads the fence as part of the expected output and fails with "Expected: 3936 ``` Got: 3936".
- Keep doctests short and deterministic: round floats, sort sets and dict output, use `ELLIPSIS` (`...`) for addresses and long reprs.
- `uv run python -m doctest -v src/geo/distance.py` runs one file without pytest.
- Longer examples belong in scripts under `docs/examples/` that a test imports and runs.

## A README that renders on PyPI

The README is the package's front page on PyPI and GitHub. `readme = "README.md"` in `[project]` makes it the long description with content type `text/markdown`.

- Use absolute URLs for images and links (`https://raw.githubusercontent.com/<owner>/<repo>/main/docs/logo.png`): relative paths work on GitHub and break on PyPI.
- Start with one paragraph on what the package does, then install and a minimal example; link to the full docs.
- Check the rendering before publishing: `uv build && uvx twine check --strict dist/*`. A missing `readme` field shows as "`long_description` missing" and fails `--strict`.
- The python-packaging skill covers the rest of the metadata.

## Build docs in CI

A docs build that only runs on release day breaks on release day. Build on every pull request, strictly.

```yaml
  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
      - run: uv sync --locked --group docs
      - run: uv run --group docs mkdocs build --strict    # or: sphinx-build -W --keep-going -n ...
      - run: uv run pytest --doctest-modules --doctest-glob="*.md" src docs
```

- `--strict` (MkDocs) and `-W` (Sphinx) make broken references, missing pages and bad docstrings fail the job.
- Run Sphinx's `linkcheck` builder on a schedule, not on every pull request; external sites fail at random.
- Sphinx 9.2 needs Python 3.13+ for the docs job, even when the package supports older versions.

## Versioned docs

Users of version 1.x need the 1.x docs after 2.0 ships. Two common setups:

- **mike with MkDocs on GitHub Pages**: `mike deploy --push --update-aliases 1.4 latest` builds the current checkout into a `1.4/` folder on the `gh-pages` branch and points the `latest` alias at it; `mike set-default --push latest` makes the site root redirect there. Material shows a version selector when `extra.version.provider: mike` is set.
- **Read the Docs**: builds every tag and branch you activate as its own version, with a version menu, for Sphinx and MkDocs projects; configure it with `.readthedocs.yaml`.

Build versions from tags in CI after the release, never by hand from a laptop. More detail on mike: [mkdocs-mkdocstrings.md](references/mkdocs-mkdocstrings.md).

## Verify

Prove the docs with a strict build, the doctests and the PyPI renderer, and plant one wrong example to see that the gate fails.

```bash
uv run --group docs mkdocs build --strict; echo "exit $?"       # or: sphinx-build -W --keep-going -n -b html docs docs/_build/html
uv run pytest --doctest-modules --doctest-glob="*.md" src docs  # expect every example to pass
uv run ruff check --select D .                                  # expect no finding
uv build && uvx twine check --strict dist/*                     # expect PASSED for the sdist and the wheel
printf 'def one():\n    """Return one.\n\n    >>> one()\n    2\n    """\n    return 1\n' > /tmp/doc_probe.py
uv run python -m doctest /tmp/doc_probe.py; echo "exit $?"      # expect "Failed example" and exit 1
rm /tmp/doc_probe.py
```

## Anti-patterns

| Instead of | Do |
|---|---|
| types repeated in the docstring | annotations only; the docstring gives meaning and units |
| a docstring that retells the code | what a caller cannot see from the signature |
| examples nobody runs | doctests in the test suite |
| relative image links in the README | absolute URLs |
| a docs build only on release day | a strict build on every pull request |
| new work on MkDocs with no exit plan | a dated move to Zensical, or Sphinx |

## Pre-merge checklist

A project's docs are in shape when every item holds.

- [ ] Every public module, class and function has a docstring whose first line is one imperative sentence.
- [ ] One docstring style, set in the docs tool and in Ruff's pydocstyle convention.
- [ ] Types live in annotations only; docstrings describe meaning, units and errors.
- [ ] The API reference is generated from docstrings; the build is strict in CI.
- [ ] Doctest examples in docstrings and Markdown run in the test suite.
- [ ] The README uses absolute URLs and passes `twine check --strict`.
- [ ] A plan exists for Material for MkDocs' end of life (Zensical, or Sphinx), if the project uses it.

## Gotchas

These are the docs problems seen most.

1. **A Markdown doctest fails with the closing ``` in "Expected".** No blank line before the closing fence. Add one.
2. **Sphinx autodoc shows an empty page or "failed to import module".** Sphinx imports the code; a dependency is missing in the docs environment, or the import has side effects that fail. Install the package and its dependencies in the docs group; mkdocstrings avoids the import entirely.
3. **The Sphinx doctest builder fails with `NameError` for a function the docstring documents.** `sphinx.ext.doctest` runs examples in an empty namespace. Set `doctest_global_setup = "from geo import haversine"`, or run docstring examples with pytest instead.
4. **Parameter tables show no types.** Annotations are missing, or `show_signature_annotations`/`autodoc_typehints` is off. Annotate the signature; do not add types to the docstring.
5. **Images in the README are broken on PyPI only.** Relative paths. Use absolute URLs.
6. **`mkdocs build --strict` prints "Formatting signatures requires either Black or Ruff to be installed".** That line is an INFO notice, not the failure. Read the WARNING lines; add Ruff to the docs group to format long signatures.
7. **Docs build on a contributor's machine but not in CI with Sphinx 9.** CI uses Python 3.12 or older. Run the docs job on 3.13+.

## References

The reference files hold the full tool configuration.

- [mkdocs-mkdocstrings.md](references/mkdocs-mkdocstrings.md): mkdocstrings Python handler options, cross-references and inventories, per-object options, docstring style per page, mike versioning, Zensical migration and Material's end of life
- [sphinx.md](references/sphinx.md): Sphinx 9 project layout, autodoc and napoleon settings, intersphinx and nitpicky mode, MyST Markdown, sphinx-autodoc-typehints, the doctest and linkcheck builders, themes and Read the Docs

## See also

- `python-code-design`: what the public API is and how it is exported
- `python-typing`: the annotations the docs tools read
- `python-packaging`: `readme`, metadata and publishing
- `python-library-maintenance`: changelogs and versioned releases
- `pytest-testing`: doctest options and test selection
