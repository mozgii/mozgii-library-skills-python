---
title: Changelogs, support policies and the release routine
author: MOZGII, Inc.
lang: en
---

# Changelogs, support policies and the release routine

How a library tells users what changed and when old things go away: a changelog in Keep a Changelog form, towncrier fragments so pull requests never conflict on it, a written support policy for Python and dependency versions, maintenance branches for fixes to old releases, yanking a bad release, and the release routine as commands. Versions in October 2026: towncrier 26.9.0, uv 0.13.6.

## Contents

This reference has 8 sections.

1. Keep a Changelog structure
2. towncrier: one fragment per change
3. Checking for fragments in CI
4. A support policy for Python and dependencies
5. Maintenance branches and backports
6. Yanking a bad release
7. The release routine, step by step
8. Gotchas

## 1. Keep a Changelog structure

Keep a Changelog is a convention for `CHANGELOG.md`: newest release first, an `Unreleased` section at the top while work is in progress, and entries grouped under six fixed headings.

```markdown
# Changelog

## [2.3.0] - 2026-10-28

### Deprecated

- `load()` is deprecated; use `read()`. It will be removed in 3.0. (#142)

### Added

- `read(encoding=...)`. (#142)

### Fixed

- Retries no longer double the timeout. (#150)
```

| Heading | For |
|---|---|
| Added | new features |
| Changed | changes in existing behaviour |
| Deprecated | features that will be removed, with the replacement |
| Removed | features removed in this release, with the replacement |
| Fixed | bug fixes |
| Security | vulnerabilities fixed; name the advisory |

- Write for users upgrading: what changed, what they must do. A commit log is not a changelog.
- Put the release date in ISO 8601 form, and link each version to its tag or diff.
- Link the changelog from `[project.urls]` (`Changelog = "https://.../CHANGELOG.md"`), so PyPI shows it.

## 2. towncrier: one fragment per change

Editing one `CHANGELOG.md` in every pull request causes merge conflicts. towncrier collects one small file per change (a fragment) and writes them into the changelog at release time.

```toml
[tool.towncrier]
directory = "changes"
filename = "CHANGELOG.md"
start_string = "<!-- towncrier release notes start -->\n"
title_format = "## [{version}] - {project_date}"
issue_format = "[#{issue}](https://github.com/example/oldlib/issues/{issue})"

[[tool.towncrier.type]]
directory = "deprecated"
name = "Deprecated"
showcontent = true

[[tool.towncrier.type]]
directory = "added"
name = "Added"
showcontent = true

[[tool.towncrier.type]]
directory = "fixed"
name = "Fixed"
showcontent = true
```

`CHANGELOG.md` must contain the start marker once, where new releases are inserted:

```markdown
# Changelog

<!-- towncrier release notes start -->
```

Fragments are named `<issue>.<type>.md`; one starting with `+` has no issue number.

```bash
uvx towncrier create --content 'Fixed rounding of negative distances.' 171.fixed.md
echo '`load()` is deprecated; use `read()`. It will be removed in 3.0.' > changes/142.deprecated.md
echo 'Typo in docs.' > 'changes/+docs1.fixed.md'
uvx towncrier build --draft --version 2.3.0          # print the result; change nothing
uvx towncrier build --version 2.3.0 --yes            # write CHANGELOG.md and git rm the fragments
```

- The sections appear in the order the types are declared; put Removed and Deprecated first so breaking news leads.
- `build --yes` stages the changelog and the fragment deletions with git; commit them as the release commit.
- Add Changed, Removed and Security types the same way as the three shown.

## 3. Checking for fragments in CI

A pull request that changes behaviour without a fragment leaves the change out of the changelog. `towncrier check` compares the branch with the main branch and fails when no fragment was added.

```bash
uvx towncrier check --compare-with origin/main
```

On a branch without a fragment it prints "No new newsfragments found on this branch." and exits 1.

- Check out with `fetch-depth: 0` in GitHub Actions, so `origin/main` exists.
- Let pull requests that change nothing user-visible (CI, tests, refactors) skip the check with a label, rather than adding empty fragments.

## 4. A support policy for Python and dependencies

Users plan upgrades around your support window; write it down in the README.

- **Python versions**: support every version that still receives security fixes (3.11 to 3.15 in late 2026), and drop a version in a minor release after its end of life. The python-version-upgrade skill has the calendar.
- **Scientific Python's SPEC 0** is a stricter, popular alternative: support each Python version for three years after its release and each core dependency version for two years. Libraries in the NumPy and pandas ecosystem follow it, so following it too keeps your ranges compatible with theirs.
- **Dependencies**: lower bounds as low as the tests prove (the lowest-direct CI job); no upper bounds unless a known release breaks you.
- **Your own releases**: say which release lines get fixes, for example "the latest minor release only" or "the last two majors receive security fixes for 12 months".

## 5. Maintenance branches and backports

When a major release ships while many users stay on the previous one, keep a branch for fixes to the old line.

```bash
git switch -c 2.x v2.3.0              # the branch starts at the last 2.x release
git cherry-pick -x <fix-commit>       # -x records the original commit in the message
uv version 2.3.1 && git commit -am "Release 2.3.1" && git tag v2.3.1
```

- Backport only fixes and security patches; new features go to the current line.
- Each maintenance release gets its own changelog section; build it with towncrier on that branch.
- PyPI orders versions numerically, so a 2.3.1 uploaded after 3.0.0 does not become "latest"; users on `>=2,<3` pick it up.

## 6. Yanking a bad release

A yanked release (PEP 592) stays on PyPI but installers skip it unless the version is pinned exactly (`==2.3.0`). Yank from the release's management page on pypi.org and give a reason; installers show the reason to anyone who installs it by pin.

- Yank when a release is broken or dangerous to install by accident: a wrong dependency, a crash on import, data corruption.
- Never delete a release: deleted file names can never be uploaded again, and lock files that pinned it fail with no explanation.
- Release a fixed version right after yanking, and say in the changelog what was wrong.

## 7. The release routine, step by step

The same steps every time, with the publishing itself done by CI from the tag (python-packaging covers trusted publishing).

```bash
git switch main && git pull --ff-only
uvx nox -s tests lint typecheck                       # or your CI's equivalent, all green
uvx griffe check mylib --search src --against "$(git describe --tags --abbrev=0)"
uv version --bump minor                               # 2.3.0 -> 2.4.0; prints the new version
uvx towncrier build --version "$(uv version --short)" --yes
git commit -am "Release $(uv version --short)"
git tag "v$(uv version --short)" && git push origin main --tags
```

After CI publishes:

```bash
uv run --isolated --no-project --with "mylib==$(uv version --short)" python -c "import mylib; print(mylib.__version__)"
```

- Bump major when `griffe check` or review found breaking changes, minor for features or deprecations, patch for fixes only.
- Create the GitHub release from the tag with the version's changelog section as its notes.
- Deploy the versioned docs from the tag (mike or Read the Docs; the python-docs skill).
- Announce deprecations and removals where users read: the release notes first, then any mailing list or discussion forum.

## 8. Gotchas

These are the release problems seen most.

1. **`towncrier build` inserts nothing, or at the top of the file.** The start marker is missing from `CHANGELOG.md` or differs from `start_string`, including the trailing newline. Copy it exactly.
2. **The tag and the package version disagree.** The version was bumped after tagging, or the tag was made on another commit. Tag the release commit, and have CI fail when `v$(uv version --short)` does not equal the tag.
3. **A fragment from a reverted change ends up in the changelog.** Reverting the code left the fragment. Revert or delete the fragment too.
4. **Users on the old major never get the security fix.** No maintenance branch, or the fix was not backported. Decide the policy before the major release, not after the advisory.
5. **A yanked version still installs in CI.** CI pins it exactly in a lock file. Update the lock to the fixed release.
