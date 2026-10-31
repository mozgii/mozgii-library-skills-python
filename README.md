# Python skills

A library of Claude skills for general Python development: project setup and packaging with uv, linting with Ruff, typing with Pyright, mypy and ty, testing with pytest and Hypothesis, code structure and idioms, error handling and retries, dates and time zones, HTTP clients, asyncio, logging, command-line tools, performance, data models, subprocesses and workers, security, debugging and upgrading Python itself. Web frameworks are out of scope; they have their own libraries.

Every skill is written for current tools (checked on PyPI and python.org in October 2026: Python 3.15.2, uv 0.13.6, Ruff 0.17.3, Pyright 1.1.414, mypy 2.4, pytest 9.2, pydantic 2.14, httpx 0.28.1), with exact commands, examples that were run, a checklist and the problems people actually hit.

## Use these skills

Each skill is one self-contained folder, so you can take only the ones you need; none depends on another skill or on any particular app.

- **Claude Code, for all your projects**: copy a folder such as `docs/v3.15/standard-library/python-datetime/` to `~/.claude/skills/python-datetime/`.
- **Claude Code, for one repository**: copy it to `.claude/skills/<name>/` in that repository and commit it, so everyone working there gets it.
- **claude.ai and Claude Desktop**: zip the folder (the zip holds one folder named like the skill, with `SKILL.md` inside) and upload it in the Skills section of claude.ai's settings.

The front matter of every `SKILL.md` uses only portable fields (`name`, `description`, `metadata`), so the same folder works in Claude Code, claude.ai and the Skills API unchanged. Claude reads a skill's `description` to decide when to load it, and opens its reference files only when a task needs them.

## Layout

- `docs/v3.15/<group>/<skill>/SKILL.md`: one skill per folder; the folder name is the skill's `name`
- `docs/v3.15/<group>/<skill>/references/*.md`: detail the skill links to, read only when needed
- `docs/v3.15/<group>/<skill>/scripts/*.py`: runnable, standard-library-only helpers (each has `--help`)
- `library.json`: name `python`, title, version, self-test minimum
- `questions.json`: the self-test, at least one question per document, each with a phrase its answering file contains

## Skills

| Skill | What it covers |
|---|---|
| `uv-projects` | creating projects with uv, Python versions, dependencies and groups, lock and sync, workspaces, indexes, scripts, CI and Docker |
| `python-packaging` | `[project]` metadata, build backends, src layout, versions, building and checking wheels, trusted publishing to PyPI |
| `python-version-upgrade` | moving to a newer Python and dropping old ones: CI matrix, dependency wheels, what breaks in 3.12 to 3.15, Ruff `UP` rewrites, free-threading |
| `ruff` | Ruff configuration, the large default rule set since 0.16, fixes, suppression comments, the formatter, pre-commit and CI |
| `python-typing` | the modern type system (generics, Protocol, TypedDict, overload, ParamSpec, TypeIs) and mypy 2.x configuration |
| `ty` | Astral's type checker: when it fits, rule levels and configuration, suppressions, adopting it on old code, moving from mypy or Pyright, exit codes, CI, pre-commit and editors |
| `pyright-strict` | Pyright strict mode setup, error codes and fixes, Any versus Unknown, stubs, and fixing cascading errors from the root with a grouping script |
| `pytest-testing` | pytest 9 configuration, fixtures, parametrize, mocking, async tests, coverage, xdist, Hypothesis |
| `python-idioms` | idiomatic modern Python, the classic traps (mutable defaults, late binding, `is`), modern replacements by version, a review checklist |
| `python-code-design` | package layout by feature, a pure core with I/O at the edges, passing dependencies in, public APIs, import cycles, import-linter |
| `python-error-handling` | where to catch, exception hierarchies, `raise from`, notes, exception groups, cleanup, retries with backoff, timeouts, warnings |
| `python-datetime` | aware datetimes, zoneinfo, storing UTC, DST arithmetic, gaps and folds, ISO 8601, timestamps, monotonic clocks, testing time |
| `python-http-clients` | httpx and requests: one reused client, timeouts, status checks, retries, auth, pagination, streaming downloads, TLS, tests with mock transports |
| `python-asyncio` | TaskGroup, timeouts, cancellation, bounded concurrency, queues, blocking work, debugging, recipes |
| `python-logging` | library versus application logging, dictConfig, JSON output, request ids, QueueHandler, structlog |
| `python-cli` | argparse, Typer and Click, entry points, output and exit-code conventions, config precedence, testing |
| `python-performance` | measuring, CPU and memory profilers (including Python 3.15's sampling profiler), fixes that matter, using more cores, compiling hot code with mypyc |
| `python-data-models` | dataclasses, NamedTuple, TypedDict, enums, pydantic v2, msgspec, settings from the environment |
| `python-subprocess` | running commands safely and portably, streaming, timeouts that stop process trees, exit codes and signals, asyncio subprocesses |
| `python-stdio-worker` | a long-lived Python worker another program drives over stdio JSON-RPC: protocol, runtime, concurrency, Postgres, packaging, contract tests |
| `python-ml-packages` | detecting the machine and installing torch, MLX and other ML packages from the right CUDA, ROCm, MPS or CPU builds |
| `python-security` | injection, deserialization, paths and archives, secrets, passwords, encryption, TLS, SSRF, supply chain |
| `python-debugging` | tracebacks, pdb and post-mortem, attaching to running processes, faulthandler, debugpy, common causes |
| `python-object-model` | dunder methods, __repr__, __eq__ and __hash__, total_ordering, container abcs, __getattr__, properties, descriptors, __slots__, __init_subclass__, metaclasses, copy hooks |
| `python-decorators` | functools.wraps, decorator factories, paramspec typing, class decorators, methods, async-aware decorators, contextdecorator, stacking order, testing |
| `python-iterators-generators` | iterator protocol, generators, yield from, send/throw/close, pipelines, lazy vs eager, exhausted iterators, itertools, async generators, aclosing |
| `python-functools-caching` | cache, lru_cache, cached_property, cache keys, invalidation, thread safety, cachetools ttl, diskcache, partial, reduce, singledispatch |
| `python-pattern-matching-enums` | match statement, class and mapping patterns, capture trap, assert_never, enum, intenum, strenum, flag, auto, serialising enums |
| `python-data-structures` | list, dict, set, deque, counter, defaultdict, chainmap, heapq, bisect, array, memoryview, big-o, sorting, graphs and trees |
| `python-numbers-decimal` | float precision, isclose, fsum, decimal for money, rounding modes, fraction, int digit limit, negative modulo, math, statistics, number formatting |
| `python-files-pathlib` | pathlib paths, text and binary files with explicit encodings, large files in chunks, atomic writes with fsync and os.replace, tempfile, copying and deleting trees, Path.walk and glob, file locks, watchfiles, platformdirs |
| `python-text-unicode-regex` | str versus bytes, encodings and errors=, utf-8 mode, boms, nfc and nfkc, casefold, graphemes, the re module, catastrophic backtracking, the regex module, f-strings and t-strings |
| `python-import-system` | modules and packages, namespace packages, sys.path, python -m, circular imports, import-time cost, lazy imports in 3.15, importlib.resources, importlib.metadata, entry-point plugins, __all__ |
| `python-concurrency` | threads versus processes versus asyncio, threading primitives, thread and process pools, start methods, pickling and shared memory, free-threaded python, subinterpreters, thread safety, deadlocks |
| `python-serialization` | json with default and hooks, big ints and decimals, json lines, orjson and msgspec, toml, safe yaml and ruamel.yaml, csv for excel, pickle risks, struct, messagepack, schema evolution |
| `python-configuration` | config layers and precedence, typed frozen settings, validated environment variables, pydantic-settings, toml config files, config locations per os, twelve-factor services, secrets out of git and logs, testing settings |
| `python-cross-platform` | os detection, windows paths and reserved names, case sensitivity, line endings and encodings, open files on windows, signals, executables and pathext, chmod and symlinks, ci matrix, injected-platform tests |
| `python-sqlite` | the sqlite3 module: connections and pragmas, autocommit and transactions, begin immediate, parameters, row factories, executemany, wal and busy_timeout, date adapters, user_version migrations, online backups, json and fts5, threads and aiosqlite |
| `psycopg-postgres` | psycopg 3 sync and async: explicit conninfo, transactions and savepoints, parameters and t-strings, sql.Identifier, row factories, psycopg_pool, error classes, type adaptation, copy, server-side cursors, pipelines, listen/notify, testing against real postgres |
| `sqlalchemy-orm` | sqlalchemy 2.1: engines, typed models with mapped_column, session per unit of work, select() queries, relationships and loading strategies, n+1 detection, bulk insert and upsert, asyncsession, tests with rollback, typing |
| `alembic-migrations` | alembic setup and env.py, autogenerate and what it misses, upgrade and downgrade, naming conventions, data migrations, batch mode for sqlite, branches and merges, offline sql, migrating at start or deploy, migration tests |
| `numpy` | numpy 2.x arrays and dtypes, vectorisation, broadcasting, views and copies, boolean and fancy indexing, default_rng, nan handling, linear algebra, structured arrays, memory layout, numpy 2 migration, zero-copy interop |
| `pandas` | pandas 3.x: copy-on-write, str dtype, reading csv and parquet with dtypes, loc and iloc, groupby, merge pitfalls, time zones and resample, replacing apply, memory reduction, method chaining with pd.col, pandas.testing, upgrading from 2.x |
| `polars` | polars 2.x: lazy and eager, expressions, group_by and joins, lazy parquet and csv scans, the default streaming engine and row order, window functions, dates and time zones, null vs nan, pandas, numpy, arrow and duckdb interop, choosing polars, pandas or duckdb |
| `coverage-py` | coverage.py config, branch coverage, source_pkgs and omit, pytest-cov vs coverage run, subprocess patch, parallel and combine, ci matrix combining, fail_under, exclusions and pragmas, reports, contexts, sys.monitoring core |
| `pre-commit` | pre-commit and prek, .pre-commit-config.yaml, ruff hooks, type checkers as local hooks, uv-lock check, hygiene hooks, local and pygrep hooks, autoupdate with freeze and cooldown, ci, speed, skip, never --no-verify |
| `nox-sessions` | noxfile with uv backend, uv sync into sessions, tests lint typecheck docs sessions, python matrix from classifiers, parametrize, posargs, reuse, requires and tags, parallel sessions, ci, tox 4 with tox-uv |
| `python-docs` | docstrings, google numpy and rest styles, type hints in docs, mkdocs material and mkdocstrings, material end of life and zensical, sphinx autodoc napoleon intersphinx, myst, doctest, pypi readme, docs ci, versioned docs with mike |
| `python-library-maintenance` | public api, semver for python, warnings.deprecated, stacklevel and skip_file_prefixes, deprecation policy, python version range, compat shims, lowest-direct dependency tests, griffe check, changelog and towncrier, py.typed and stubtest, release checklist |
| `python-native-extensions` | when to go native, ctypes, cffi, cython, nanobind and pybind11, pyo3 and maturin, releasing the gil, abi3 and abi3t, free-threaded builds, scikit-build-core, cibuildwheel, macos deployment target, debugging crashes |
| `python-containers` | docker images with uv, multi-stage builds, base images (slim, alpine, distroless), non-root user, pid 1 and signals, healthchecks, image size, reproducible builds |
| `python-services-shutdown` | long-running services, sigterm and ctrl+c handling, graceful shutdown in threads and asyncio, atexit limits, systemd and sd_notify, launchd, windows services, single-instance locks |
| `python-observability-otel` | opentelemetry traces, metrics and logs, zero-code and manual setup, spans and attributes, context propagation, otlp exporters, sampling, prometheus_client, testing telemetry |
| `python-task-queues` | background jobs with celery, dramatiq, rq, arq, procrastinate and huey, idempotent tasks, late acks and visibility timeouts, retries, outbox, periodic tasks, testing, worker shutdown |
| `python-networking-websockets` | tcp sockets and asyncio streams, message framing, timeouts, tls, websockets server and client, heartbeats and reconnection, backpressure, unix sockets and named pipes |
| `python-html-xml-parsing` | html and xml parsing with lxml, beautifulsoup and selectolax, xpath and css selectors, namespaces, iterparse, defusedxml, encoding detection, polite scraping with robots.txt |
| `mcp-python-servers` | mcp servers with the official sdk 2.x, mcpserver vs low-level server, typed tools, output schemas, tool annotations, toolerror vs protocol errors, resources and prompts, lifespan, resolve and elicit, stdio and streamable http, clean stdout, claude code and desktop config, in-process tests |
| `anthropic-sdk-python` | anthropic sdk 2.x client and keys, models list, messages api, stop reasons, streaming, tool use loop and tool runner, structured output with parse, prompt caching, token counting, thinking and effort, errors and retries, async client, batches, files api, offline tests with httpx2 |
| `python-terminal-ui` | print vs rich vs textual, tty detection and no_color, rich console, tables, progress bars, richhandler logging, tracebacks, live displays, markup escaping, textual apps, css, reactive attributes, workers, pilot and snapshot tests, windows terminals |

## Sources

Every text in this library is written by MOZGII, Inc. Vendors' official skills and documentation were read as context: they showed what to cover and which facts are true, and each fact taken from them was checked against the current release. Nothing was copied from them, under any licence. `sources.json` records each official skill and changelog read, at the commit read, and `NOTICE` lists them. Three skills (`pyright-strict`, `python-ml-packages`, `python-stdio-worker`) were rewritten for general use from an earlier skill collection by the same author. This version merged `pylance-fixer` into `pyright-strict`, renamed `python-package-router` to `python-ml-packages` and `python-worker-process` to `python-stdio-worker`, and added `python-version-upgrade`, `python-idioms`, `python-code-design`, `python-error-handling`, `python-datetime` and `python-http-clients`.

### Official agent skills

We looked for vendors' own skills first. Licences are as each project published them on 2026-10-30.

| Source | Licence | What it informed |
|---|---|---|
| [Astral's plugin](https://github.com/astral-sh/claude-code-plugins) (`uv`, `ruff`, `ty`) | MIT OR Apache-2.0 | which workflow fits a folder, the tool migration tables, keeping Ruff fixes to the files being changed, the new `ty` skill |
| [Pydantic's skills](https://github.com/pydantic/skills) (`pydantic`, `logfire-instrumentation`) | MIT | field metadata, choosing validators, subclasses in fields, forward references, Logfire setup |
| [Typer's skill](https://github.com/fastapi/typer/tree/master/typer/.agents/skills/typer), shipped inside the package | MIT | explicit apps, `rich_markup_mode`, settings left over from Click |
| [mypy's skills](https://github.com/python/mypy/tree/master/skills) (`mypyc-migrate`, `mypyc-optimize`) | MIT | the `mypyc` reference |
| [Anthropic's skills](https://github.com/anthropics/skills) (`claude-api`, `mcp-builder`) | Apache-2.0 | refusals, compaction, the SDK upgrade reference, tool design and evaluating an MCP server; `mcp-builder` still teaches `FastMCP`, so our SDK 2.x examples come from the SDK itself |

Found and not used: FastAPI's skill (covered by the separate FastAPI library), SQLModel's and Asyncer's skills, Pydantic AI's skills and Microsoft's Azure SDK skills (topics this library does not teach), and the contributor-only skills in the `ruff`, `uv`, `pyright`, `cibuildwheel`, `pybind11`, `aiohttp` and `opentelemetry-*` repositories, which are about working on those projects rather than using them.

These publishers have no skills for users of their tools as of 2026-10-30, by a search of their GitHub organizations: the Python project for the language and standard library (`python/skills` does not exist, and `python/cpython` carries none; `python/mypy` is the exception above), PyPA, Pallets, encode, the Python Software Foundation, pytest-dev, NumPy, pandas, Polars, SQLAlchemy, psycopg, Textualize, nedbat (coverage.py), wntrblm (nox), pre-commit, Celery, PyO3 and hynek (structlog, stamina). `astral-sh/skills` does not exist either: Astral's skills are in the plugin above.

### Official documentation

| Source | Licence | Used for |
|---|---|---|
| [Python documentation](https://docs.python.org/3.15/) and [What's New](https://docs.python.org/3.15/whatsnew/index.html) from 3.12 to 3.15 | PSF-2.0 (code examples also 0BSD) | language and standard library facts, new features, removals, porting notes, `datetime` and `zoneinfo` behaviour |
| [PEPs](https://peps.python.org/) (594, 621, 632, 639, 649, 654, 661, 667, 678, 686, 695, 702, 706, 723, 728, 734, 735, 740, 747, 750, 751, 758, 765, 768, 779, 798, 799, 810, 814) | public domain / CC0-1.0 | standards behind packaging, typing, exceptions and runtime changes |
| [Python release data](https://www.python.org/downloads/) and [endoflife.date](https://endoflife.date/python) | PSF / MIT | release and end-of-life dates |
| [uv documentation](https://docs.astral.sh/uv/) and [changelog](https://github.com/astral-sh/uv/blob/main/CHANGELOG.md) | MIT OR Apache-2.0 | uv commands, build backend, breaking changes 0.9 to 0.13 |
| [Ruff documentation](https://docs.astral.sh/ruff/), [default rules](https://docs.astral.sh/ruff/default-rules/), [v0.16 post](https://astral.sh/blog/ruff-v0.16.0) | MIT | rule sets, suppression comments, formatter changes |
| [ty](https://docs.astral.sh/ty/) and its [repository](https://github.com/astral-sh/ty) | MIT | configuration, rules, suppressions, exit codes, migration from mypy and Pyright |
| [Pyright documentation](https://microsoft.github.io/pyright/) | MIT | configuration and rule names |
| [mypy documentation](https://mypy.readthedocs.io/) and [changelog](https://github.com/python/mypy/blob/master/CHANGELOG.md) | MIT | mypy 2.0 default changes, flags, error codes |
| [typing specification](https://typing.python.org/) | PSF-2.0 | type system semantics |
| [pytest documentation](https://docs.pytest.org/) and [changelog](https://docs.pytest.org/en/stable/changelog.html) | MIT | pytest 9 configuration, strict mode, subtests |
| [pytest-asyncio](https://pytest-asyncio.readthedocs.io/) | Apache-2.0 | modes, loop scopes, 1.x changes |
| [coverage.py](https://coverage.readthedocs.io/) | Apache-2.0 | configuration, subprocess measurement |
| [pytest-xdist](https://pytest-xdist.readthedocs.io/), [pytest-mock](https://pytest-mock.readthedocs.io/), [pytest-randomly](https://github.com/pytest-dev/pytest-randomly), [syrupy](https://github.com/syrupy-project/syrupy) | MIT | plugin options |
| [Hypothesis documentation](https://hypothesis.readthedocs.io/) | MPL-2.0 | strategies and settings (linked, not adapted) |
| [pydantic](https://docs.pydantic.dev/) and [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | MIT | v2 API, migration table |
| [Typer](https://typer.tiangolo.com/) and [release notes](https://typer.tiangolo.com/release-notes/) | MIT | Typer API and the 0.26 vendored-Click change |
| [Click](https://click.palletsprojects.com/) | BSD-3-Clause | Click API and testing |
| [structlog](https://www.structlog.org/) | MIT OR Apache-2.0 | processors, contextvars, stdlib integration |
| [Rich](https://rich.readthedocs.io/), [platformdirs](https://platformdirs.readthedocs.io/) | MIT | CLI output and per-user paths |
| [py-spy](https://github.com/benfred/py-spy), [pyperf](https://pyperf.readthedocs.io/), [debugpy](https://github.com/microsoft/debugpy) | MIT | profiling, benchmarking and remote debugging commands |
| [memray](https://bloomberg.github.io/memray/), [Scalene](https://github.com/plasma-umass/scalene) | Apache-2.0 | memory and CPU profiling commands |
| [pyinstrument](https://pyinstrument.readthedocs.io/) | BSD-3-Clause | call-tree profiling |
| [PyTorch install and wheel index](https://pytorch.org/get-started/locally/), [download.pytorch.org](https://download.pytorch.org/whl/torch/) | BSD-3-Clause | CUDA, ROCm and CPU builds for torch 2.14 |
| [MLX installation](https://ml-explore.github.io/mlx/build/html/install.html) | MIT | MLX platforms and extras |
| [cryptography](https://cryptography.io/), [argon2-cffi](https://argon2-cffi.readthedocs.io/), [defusedxml](https://github.com/tiran/defusedxml) | Apache-2.0 OR BSD-3-Clause, MIT, PSF-2.0 | encryption, password hashing, safe XML |
| [Bandit](https://bandit.readthedocs.io/), [pip-audit](https://github.com/pypa/pip-audit) | Apache-2.0 | security scanning commands |
| [Python Packaging User Guide](https://packaging.python.org/) | CC-BY-SA-3.0 | packaging practice (linked, not adapted) |
| [httpx](https://www.python-httpx.org/) | BSD-3-Clause | client API, timeouts, transports, 0.28 changes (checked by running it) |
| [requests](https://requests.readthedocs.io/) and [urllib3 Retry](https://urllib3.readthedocs.io/) | Apache-2.0, MIT | timeouts, sessions, adapter retries |
| [respx](https://lundberg.github.io/respx/), [pytest-httpx](https://github.com/Colin-b/pytest_httpx), [truststore](https://truststore.readthedocs.io/), [aiohttp](https://docs.aiohttp.org/) | BSD-3-Clause, MIT, MIT, Apache-2.0 | HTTP mocking, the OS trust store, async client choice |
| [stamina](https://stamina.hynek.me/) | MIT | retry API, predicates, `Retry-After` hooks, test mode |
| [tenacity](https://tenacity.readthedocs.io/) | Apache-2.0 | retry strategies; the 9.2 `multiplier` change (checked by running it) |
| [time-machine](https://time-machine.readthedocs.io/) | MIT | freezing time in tests, 3.x changes |
| [whenever](https://whenever.readthedocs.io/), [pendulum](https://pendulum.eustace.io/), [tzdata](https://github.com/python/tzdata), [Babel](https://babel.pocoo.org/) | MIT, MIT, Apache-2.0, BSD-3-Clause | datetime libraries and the time zone database |
| [import-linter](https://import-linter.readthedocs.io/), [pydeps](https://github.com/thebjorn/pydeps), [vermin](https://github.com/netromdk/vermin) | BSD-2-Clause, BSD-2-Clause, MIT | layer contracts (checked by running them), import graphs, minimum-version detection |
| [prek changelog](https://github.com/j178/prek/blob/master/CHANGELOG.md), [tox changelog](https://tox.wiki/en/latest/changelog.html), [virtualenv changelog](https://virtualenv.pypa.io/en/latest/changelog.html), [filelock changelog](https://py-filelock.readthedocs.io/en/latest/changelog.html), [pipx changelog](https://pipx.pypa.io/latest/changelog.html), [Zensical releases](https://github.com/zensical/zensical/releases) | MIT | what each patch update changed |
| [prek official skill](https://github.com/j178/prek/blob/8fe6b5b743e13e9986f76b930af4246bab9b072e/skills/prek/SKILL.md) | MIT | which keys and commands prek adds to pre-commit, each checked by running prek 0.5.9 |
| [PyPI JSON API](https://pypi.org/pypi/uv/json) | - | current package versions |

### Other skill collections reviewed

These were checked to see what Python developers expect from a skill library and what is missing; none of their text is included.

| Collection | Licence | What we looked at |
|---|---|---|
| [wshobson/agents](https://github.com/wshobson/agents) (python-development plugin) | MIT | its list of 16 Python skills (async, packaging, testing, performance, type safety, uv, error handling, resilience, resource management, project structure, anti-patterns), to check coverage |
| [trailofbits/skills](https://github.com/trailofbits/skills) (modern-python) | CC-BY-SA-4.0 | its outline (uv, Ruff, ty, prek, security tools); share-alike text not adapted |
| [wdm0006/python-skills](https://github.com/wdm0006/python-skills) | MIT | its skill list (project setup, packaging, testing, security audit, CLI, performance) |
| [honnibal/claude-skills](https://github.com/honnibal/claude-skills), [julianobarbosa/claude-code-skills](https://github.com/julianobarbosa/claude-code-skills), [s2005/uv-skill](https://github.com/s2005/uv-skill), [obra/superpowers](https://github.com/obra/superpowers) | MIT | found in search; licence and scope checked, not read in depth |
| [PatrickJS/awesome-cursorrules](https://github.com/PatrickJS/awesome-cursorrules) | CC0-1.0 | found in search; licence checked |
| [hesreallyhim/awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code), [travisvn/awesome-claude-skills](https://github.com/travisvn/awesome-claude-skills), [vinta/awesome-python](https://github.com/vinta/awesome-python) | see each repository | lists used to find the collections and tools above |

### Package versions

`versions.json` holds every package and GitHub Action the library names, with the version and date checked (2026-10-30, and 2026-10-31 for the last patch updates). Each skill's Step 0 reads the installed version instead of trusting a number written here.
