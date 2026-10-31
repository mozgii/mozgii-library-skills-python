---
name: python-packaging
description: Packages and publishes Python libraries and applications - pyproject.toml [project] metadata with SPDX license expressions, choosing a build backend (uv_build, hatchling, setuptools, maturin, scikit-build-core), src layout, entry points, package data and py.typed, versioning and __version__, dependency bounds for libraries, building and checking wheels and sdists, testing the built wheel, and publishing to TestPyPI and PyPI with trusted publishing and attestations. Use when making a package installable, preparing a release, uploading to PyPI, or fixing missing files in a wheel.
metadata:
  tags:
  - python
  - packaging
  - pyproject
  - wheel
  - sdist
  - pypi
  - build-backend
  - uv
  - hatchling
  - trusted-publishing
---

# Python packaging and publishing

A Python package is a wheel (a zip that installs by copying files) and a source distribution (an archive a build backend turns into a wheel), both described by the `[project]` table in `pyproject.toml`. This skill writes that metadata, picks a backend, builds and checks the artifacts, and publishes them. Project setup and dependency locking are in the `uv-projects` skill.

## Step 0: read the installed versions

Packaging standards moved recently (PEP 639 licences, PEP 735 groups, PEP 751 lockfiles), so read the tool versions first.

```bash
uv --version; uv run python -c "import sys; print(sys.version)"
grep -nA3 "build-system" pyproject.toml
```

The current releases (October 2026): uv and `uv_build` 0.13.6, hatchling 1.33, setuptools 84, build 1.6, twine 7.0, maturin 1.16 for Rust extensions.

## The [project] table

The `[project]` table is the standard metadata every backend reads (PEP 621). Fill it completely before the first release; PyPI shows it.

```toml
[project]
name = "my-lib"                          # distribution name; import name is my_lib
version = "1.2.0"
description = "One line saying what it does."
readme = "README.md"
license = "MIT"                          # SPDX expression (PEP 639); "Apache-2.0 OR MIT" works too
license-files = ["LICENSE*"]
authors = [{ name = "Ada Example", email = "ada@example.com" }]
requires-python = ">=3.11"
dependencies = ["httpx>=0.28", "pydantic>=2.10"]
keywords = ["http", "client"]
classifiers = [
  "Programming Language :: Python :: 3",
  "Programming Language :: Python :: 3.14",
  "Typing :: Typed",
]

[project.optional-dependencies]
cli = ["typer>=0.27"]

[project.scripts]
my-lib = "my_lib.cli:app"                # console command -> module:callable

[project.urls]
Homepage = "https://example.com/my-lib"
Source = "https://github.com/example/my-lib"
Changelog = "https://github.com/example/my-lib/blob/main/CHANGELOG.md"

[build-system]
requires = ["uv_build>=0.13.6,<0.14"]
build-backend = "uv_build"
```

- `license` is an SPDX string, not a table; the old `license = { text = "MIT" }` form and `License ::` classifiers are deprecated by PEP 639.
- `name` is normalized on PyPI (`My_Lib`, `my-lib` and `my.lib` are the same project).
- `[dependency-groups]` (dev, test, docs) is never published; `optional-dependencies` (extras) is.
- `[project.gui-scripts]` makes a windowed command on Windows; `[project.entry-points."group"]` registers plugins.

## Choose a build backend

The build backend turns the source tree into a wheel; pick the simplest one that handles your code.

| Code | Backend | `[build-system]` |
|---|---|---|
| pure Python, standard layout | `uv_build` | `requires = ["uv_build>=0.13.6,<0.14"]`, `build-backend = "uv_build"` |
| pure Python, build hooks or unusual layout | hatchling | `requires = ["hatchling"]`, `build-backend = "hatchling.build"` |
| existing setuptools project, C extensions via `setup.py` | setuptools | `requires = ["setuptools>=77"]`, `build-backend = "setuptools.build_meta"` |
| Rust extension (PyO3) | maturin | `requires = ["maturin>=1.16,<2"]`, `build-backend = "maturin"` |
| C or C++ with CMake | scikit-build-core | `requires = ["scikit-build-core"]`, `build-backend = "scikit_build_core.build"` |
| C or C++ with Meson | meson-python | `requires = ["meson-python"]`, `build-backend = "mesonpy"` |

`uv_build` supports pure Python only; keep its upper bound and raise it when you upgrade uv. setuptools 77 or newer is needed for SPDX `license` strings.

## Layout and what goes into the wheel

The `src/` layout keeps tests honest: they import the installed package, not the folder next to them.

```
my-lib/
  pyproject.toml  README.md  LICENSE
  src/my_lib/
    __init__.py
    py.typed               # marks the package as typed (PEP 561); empty file
    data/schema.json       # package data: lives inside the package
    cli.py
  tests/
```

- `uv_build` takes the module from `src/<normalized name>/`. Override with `[tool.uv.build-backend] module-name = "my_lib"` and `module-root = ""` for a flat layout.
- Everything under the module directory goes into the wheel, minus `__pycache__` and `*.pyc`. Exclude more with `wheel-exclude` and `source-exclude`; add files to the sdist only with `source-include`.
- Read package data with `importlib.resources`, never with paths relative to `__file__`, so it works from zips and installed wheels:

```python
from importlib.resources import files

schema_text = files("my_lib").joinpath("data/schema.json").read_text(encoding="utf-8")
```

## Versions and __version__

The version lives in one place, `pyproject.toml`, and the code reads it from the installed metadata.

```python
# src/my_lib/__init__.py
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("my-lib")
except PackageNotFoundError:          # running from a source tree that is not installed
    __version__ = "0+unknown"
```

```bash
uv version --bump patch               # 1.2.0 => 1.2.1
uv version --bump minor --dry-run
uv version 2.0.0rc1
```

- Follow semantic versioning for libraries: a removed or renamed public name is a major bump.
- To derive the version from git tags, use hatchling with `hatch-vcs` or setuptools with `setuptools-scm` and `dynamic = ["version"]`; `uv_build` reads a static version only.

## Dependency bounds for libraries

A library's dependency ranges are a promise to every application that installs it, so keep them wide.

- Give lower bounds you have tested (`httpx>=0.28`). Run one CI job with `uv sync --resolution lowest-direct` to prove them.
- Avoid upper caps (`<2`) unless a known release breaks you: caps make your library impossible to install next to others that need the newer version.
- Applications pin exactly through their lockfile, not through `dependencies`.
- Mark optional heavy features as extras (`my-lib[pandas]`) and import them lazily.

## Build and check

Build both artifacts, then look inside them before anything is uploaded.

```bash
uv build                                   # dist/my_lib-1.2.0.tar.gz and dist/my_lib-1.2.0-py3-none-any.whl
uv build --sdist --wheel --out-dir dist
unzip -l dist/*.whl                        # module files, py.typed, data, *.dist-info/licenses/
tar tzf dist/*.tar.gz | head -50           # sdist: pyproject, src, README, LICENSE, no secrets or .env
uvx twine check --strict dist/*            # metadata and README render on PyPI
uv run --isolated --no-project --with dist/my_lib-1.2.0-py3-none-any.whl python -c "import my_lib; print(my_lib.__version__)"
```

- The last command installs the built wheel into a throwaway environment, which catches missing package data and wrong module names that tests against the source tree never see.
- Run the test suite against the installed wheel in CI: `uv run --isolated --no-project --with dist/*.whl --with pytest pytest tests/`.
- A wheel tagged `py3-none-any` is pure Python. Extension modules produce one wheel per platform and Python; build those with `cibuildwheel` in CI.

## Publish with trusted publishing

Trusted publishing lets a CI job upload to PyPI with a short-lived OpenID Connect token instead of a stored API token. Configure the publisher once on PyPI (project settings, "Publishing": owner, repository, workflow file, environment), then:

```yaml
# .github/workflows/release.yml
name: release
on:
  push:
    tags: ["v*"]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.3
        with: { version: "0.13.6" }
      - run: uv build
      - uses: actions/upload-artifact@v7
        with: { name: dist, path: dist/ }
  publish:
    needs: build
    runs-on: ubuntu-latest
    environment: pypi                  # a protected environment: require a reviewer
    permissions:
      id-token: write                  # needed for trusted publishing
    steps:
      - uses: actions/download-artifact@v8
        with: { name: dist, path: dist/ }
      - uses: pypa/gh-action-pypi-publish@release/v1
```

- `pypa/gh-action-pypi-publish` also creates PEP 740 attestations (signed provenance) automatically under trusted publishing.
- Build and publish in separate jobs, so the build step never holds the publishing token.
- From a laptop or another CI: `uv publish` (reads `UV_PUBLISH_TOKEN`), or `uv publish --trusted-publishing always` inside a supported CI.
- Rehearse on TestPyPI first: `uv publish --publish-url https://test.pypi.org/legacy/`, then install with `uv pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ my-lib`.
- PyPI never accepts the same file name twice: a broken release gets a new version (`1.2.1`), and the bad one is yanked, not deleted.

## Verify

Prove the artifacts rather than the source tree: a planted missing data file must fail these commands, and a test run from the source folder never would.

```bash
uv build --no-sources                                            # as an outside installer would build it
unzip -l dist/*.whl | grep -E 'py\.typed|dist-info/licenses/'     # expect both
tar tzf dist/*.tar.gz | grep -E '(^|/)\.env|\.pem$|\.key$'        # expect nothing
uvx twine check --strict dist/*                                  # expect PASSED for each file
uv run --isolated --no-project --with dist/*.whl python -c \
  "import my_lib; from importlib.resources import files; print(my_lib.__version__, files('my_lib').joinpath('py.typed').is_file())"
```

The last line prints the new version and `True`. A release dry run ends on TestPyPI: install the package from there in a clean environment before the tag reaches PyPI.

## Anti-patterns

| Instead of | Do |
|---|---|
| a version in `pyproject.toml` and a copy in `__init__.py` | read it with `importlib.metadata.version` |
| a long-lived PyPI token in CI secrets | trusted publishing from a protected environment |
| `__file__`-relative paths to package data | `importlib.resources.files` |
| upper caps such as `pydantic<3` with no known break | lower bounds, a `lowest-direct` CI job |
| dev tools in `optional-dependencies` | `[dependency-groups]`, which is never published |
| deleting a broken release | yank it and publish a new version |

## Checklist

A release is ready when every item holds.

- [ ] `[project]` has name, version, description, readme, SPDX `license`, `license-files`, `requires-python`, urls and classifiers.
- [ ] The wheel contains the package, `py.typed`, its data files and the licence; the sdist contains no secrets.
- [ ] `twine check --strict` passes; the built wheel imports in an isolated environment.
- [ ] Tests ran against the installed wheel, on the lowest and highest supported Python.
- [ ] The version was bumped and tagged; the changelog names breaking changes.
- [ ] Publishing uses trusted publishing from a protected environment, not a long-lived token.

## Gotchas

These are the packaging mistakes that reach users.

1. **`ModuleNotFoundError` after installing the wheel, but tests pass.** Tests imported the source folder. Use the `src/` layout and test the installed wheel.
2. **Data files missing from the wheel.** They live outside the package directory. Move them inside `src/my_lib/` and read them with `importlib.resources`.
3. **PyPI rejects the upload with "File already exists".** The version was uploaded before, perhaps to a failed release. Bump the version.
4. **`license` table rejected by a new backend.** PEP 639 replaced it with an SPDX string; drop `License ::` classifiers too.
5. **Users cannot install your library next to another one.** An upper bound such as `pydantic<2.11` conflicts with their newer pin. Remove caps you have no evidence for.
6. **The README shows as raw text on PyPI.** `readme` is missing or points to a file with an unknown extension. Use `README.md` and run `twine check --strict`.

## See also

- `uv-projects`: creating the project, groups and the lockfile
- `python-library-maintenance`: public API, deprecations and changelogs
- `python-native-extensions`: wheels with compiled code
- `python-security`: lockfiles, audits and supply-chain checks
