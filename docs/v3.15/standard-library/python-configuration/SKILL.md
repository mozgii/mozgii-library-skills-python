---
name: python-configuration
description: Designs and loads application configuration in Python - layering defaults, config files, environment variables and flags in a fixed order, validating environment variables at start-up, pydantic-settings (BaseSettings, env_prefix, env_nested_delimiter, SecretStr, .env files, secrets_dir, TOML sources, hide_input_in_errors), TOML files with tomllib and deep merging, config file locations per OS with platformdirs, twelve-factor services, secrets kept out of git and logs, and one typed, frozen settings object passed in. Use when adding settings to an app or service, reading environment variables, adding a config file, supporting .env files, deciding which source wins, keeping API keys safe, or testing code with different settings.
metadata:
  tags:
  - python
  - configuration
  - settings
  - environment-variables
  - pydantic-settings
  - toml
  - dotenv
  - secrets
---

# Configuration in Python applications

Configuration is every value that changes between runs or machines without a code change: URLs, ports, paths, switches, credentials. It is read once at start-up from known sources in a fixed order, validated into one typed object, and passed to the code that needs it. Command-line flags are in the python-cli skill; where the settings object sits in a codebase is in the python-code-design skill.

## Step 0: find how configuration is read today

Before changing anything, read the Python version, the settings library in use, and every place that reads the environment.

```bash
uv run python -c "import sys; print(sys.version_info[:2])"
uv pip list 2>/dev/null | grep -iE "^(pydantic-settings|pydantic|python-dotenv|platformdirs) "
grep -rnE "os\.environ|os\.getenv|load_dotenv|BaseSettings" --include=*.py src/ | head -30
```

October 2026 releases: pydantic-settings 2.16, pydantic 2.14, python-dotenv 1.2, platformdirs 4.14. `os.environ` reads scattered through the code are the first thing to consolidate.

## Choose how to load settings

The right loader depends on what the project already carries and on who imports the code.

| Project | Choose | Why the others lose here |
|---|---|---|
| a service or app that already uses pydantic | pydantic-settings | a hand-written loader repeats its parsing, validation and masking |
| a tool that must stay free of dependencies | a frozen dataclass and a stdlib loader | pydantic adds a compiled dependency |
| a one-file script with two or three values | explicit parsing in `main()` | a settings class is more than the script needs |
| a library imported by other programs | no settings: parameters on its functions | a library that reads the environment surprises its host |

## Layers and precedence

Configuration comes from several layers; each later layer overrides the earlier ones, key by key, so a person sets only what differs from the default.

| Order (lowest first) | Source | Who sets it |
|---|---|---|
| 1 | defaults in code | the developer |
| 2 | system config file (`/etc/my-app/config.toml`) | an administrator |
| 3 | user config file (platformdirs user config folder) | the person |
| 4 | project config file (`./my-app.toml`, `[tool.my-app]` in `pyproject.toml`) | the project |
| 5 | `.env` file (development only) | the developer on their machine |
| 6 | environment variables (`MY_APP_*`) | the deployment |
| 7 | command-line flags | the person, for this run |

- Merge nested tables key by key: a file that sets only `db.port` must not erase the default `db.host`.
- Print the effective configuration, with secrets masked, behind a `--show-config` flag or in the debug log; most configuration bugs are "a different layer won".
- Desktop apps rarely need layers 2, 4 and 5; services usually need 1, 6 and a secrets source only.

## One typed, frozen settings object

All configuration goes into one typed object built at start-up and passed to the code that needs it. Nothing deeper in the program reads `os.environ` or opens the config file, so tests and other entry points can supply any settings they want.

```python
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DbSettings:
    host: str = "localhost"
    port: int = 5432


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    log_level: str = "info"
    db: DbSettings = field(default_factory=DbSettings)


def make_report(settings: Settings) -> str:             # receives settings; never reads the environment
    return f"{settings.db.host}:{settings.db.port} -> {settings.data_dir}"


print(make_report(Settings(data_dir=Path("/tmp/reports"))))
```

Pass the smallest piece a function needs (`settings.db`, not `settings`), so its signature says what it depends on. A dependency-free loader that fills this dataclass from files and the environment is in [stdlib-config-loader.md](references/stdlib-config-loader.md).

## Environment variables with validation

Environment variables are always strings, and the obvious conversions are wrong: `bool("false")` is `True`, and `int("")` crashes far from the cause. Parse each variable with an explicit function and report every problem at start-up in one message.

```python
import os
from collections.abc import Mapping

TRUE, FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off", ""}


def env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in TRUE:
        return True
    if value in FALSE:
        return False
    raise ValueError(f"{name} must be true or false, got {raw!r}")


def env_int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {raw!r}") from None
    if not lo <= value <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}, got {value}")
    return value


env = {"APP_DEBUG": "false", "APP_PORT": "70000"}       # in real code: os.environ
errors, values = [], {}
for name, parse in [("debug", lambda: env_bool(env, "APP_DEBUG", False)),
                    ("port", lambda: env_int(env, "APP_PORT", 8000, 1, 65535))]:
    try:
        values[name] = parse()
    except ValueError as exc:
        errors.append(str(exc))
print(values, errors)                                    # report all errors together, then exit
```

- Prefix every variable with the app name (`MY_APP_`) so it cannot collide with another program's.
- Lists: accept JSON (`["a", "b"]`) or a comma-separated string, and say which in the docs.
- Environment variable names are case-insensitive on Windows and case-sensitive on macOS and Linux; use upper case everywhere.

## pydantic-settings

pydantic-settings fills a pydantic model from environment variables, `.env` files, secret files and config files, with validation, nested models and masked secrets. It is the default choice when the project already uses pydantic.

```python
import os

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict   # uv add pydantic-settings


class Db(BaseModel):
    host: str = "localhost"
    port: int = 5432


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MY_APP_",
        env_nested_delimiter="__",                       # MY_APP_DB__PORT sets db.port
        env_file=".env",                                 # optional; a missing file is ignored
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,                       # keeps inputs out of str(exc) only
    )
    log_level: str = "info"
    db: Db = Db()
    api_key: SecretStr


os.environ.update({"MY_APP_API_KEY": "sk-test-123", "MY_APP_DB__PORT": "6543"})
settings = Settings()
print(settings.db, settings.api_key)                    # host='localhost' port=6543 **********
print(settings.api_key.get_secret_value()[:3])           # the real value only where it is used
```

- Field names map to `MY_APP_<NAME>`, case-insensitively by default; a nested field is `MY_APP_DB__HOST`.
- Complex types (`list[str]`, `dict`) are read from the environment as JSON; for comma-separated lists, see [pydantic-settings-in-depth.md](references/pydantic-settings-in-depth.md).
- Without `hide_input_in_errors=True`, `str(exc)` prints the input it received, secrets included. The flag covers `str(exc)` only: `exc.errors()` and `exc.json()` still carry each value under `input`, so log `exc.errors(include_input=False)`.
- `frozen=True` stops code from changing settings after start-up.

## Sources and their priority in pydantic-settings

By default pydantic-settings reads, highest priority first: keyword arguments, environment variables, the `.env` file, then the secrets folder. `settings_customise_sources` reorders them and adds others, such as a TOML file.

```python
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict, TomlConfigSettingsSource


class Db(BaseModel):
    host: str = "localhost"
    port: int = 5432


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MY_APP_", env_nested_delimiter="__", toml_file="my-app.toml")
    log_level: str = "info"
    db: Db = Db()

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        # first wins: arguments, environment, .env, secret files, then the TOML file
        return (init_settings, env_settings, dotenv_settings, file_secret_settings, TomlConfigSettingsSource(settings_cls))


Path("my-app.toml").write_text('log_level = "debug"\n[db]\nport = 6000\n', encoding="utf-8")
print(Settings())                                        # log_level and db.port from TOML, db.host default
print(Settings(log_level="warning").log_level)           # keyword arguments beat everything
```

Nested models are merged across sources field by field, so the TOML file and an environment variable can each set one field of `db`.

## TOML config files with tomllib

A TOML file is the natural format for configuration a person edits. Read it with `tomllib`, merge it over the defaults key by key, then validate the result into the typed settings.

```python
import tomllib
from pathlib import Path
from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"{path}: {exc}") from None     # a broken file stops start-up with its name


DEFAULTS = {"log_level": "info", "db": {"host": "localhost", "port": 5432}}
Path("user.toml").write_text("[db]\nport = 6000\n", encoding="utf-8")
print(deep_merge(DEFAULTS, load_toml(Path("user.toml"))))
```

Report unknown keys instead of ignoring them: a typo such as `prot = 6000` otherwise does nothing, silently.

## Where config files live on each OS

A desktop app or CLI keeps its user configuration in the OS's per-user config folder, found with `platformdirs`; never next to the code or in the current folder.

| OS | `platformdirs.user_config_path("my-app")` |
|---|---|
| macOS | `~/Library/Application Support/my-app` |
| Linux | `~/.config/my-app` (or `$XDG_CONFIG_HOME/my-app`) |
| Windows | `%LOCALAPPDATA%\my-app\my-app`: the app name doubles as the author; pass `appauthor=False` for `%LOCALAPPDATA%\my-app` |

- Allow an override (`MY_APP_CONFIG=/path/to/config.toml`) for tests, portable installs and containers.
- On first run, write a commented example file rather than an empty one, so people see what can be set.

## Twelve-factor configuration for services

A service deployed in several environments takes its configuration from the environment (the twelve-factor rule): the same build runs in staging and production, and only the variables differ.

- No environment names in code (`if ENV == "production"`); make each behaviour its own setting (`MY_APP_SEND_EMAILS=true`).
- The container or platform sets the variables; the image contains no config files with real values.
- Secrets come from the platform's secret store, mounted as files (`secrets_dir` in pydantic-settings) or injected as variables.
- Fail at start-up when a required setting is missing, not on the first request that needs it.

## Keeping secrets out of git and logs

Secrets (API keys, passwords, tokens) are configuration with stricter rules: never in the repository, never in logs, never in command-line arguments.

- Commit a `.env.example` with names and fake values; put `.env` in `.gitignore`. Add a secret scanner (gitleaks, trufflehog or GitHub secret scanning) to CI.
- Hold secrets in `SecretStr` so `repr`, `print` and logs show `**********`; call `get_secret_value()` only where the value is sent.
- Desktop apps store a person's secrets in the OS keychain (the `keyring` package: Keychain, Windows Credential Manager, Secret Service), not in a config file.
- A secret that reached git is compromised even after deletion: rotate it first, then clean the history.

## Testing code that needs configuration

Code that receives settings as a parameter is tested by building the settings in the test; no environment patching is needed.

```python
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MY_APP_", env_file=".env")
    api_key: SecretStr
    retries: int = 3


def test_retries_from_settings() -> None:
    settings = Settings(_env_file=None, api_key="test", retries=0)   # ignore the developer's .env
    assert settings.retries == 0


def test_env_parsing(monkeypatch) -> None:                        # pytest fixture
    monkeypatch.setenv("MY_APP_API_KEY", "k")
    monkeypatch.setenv("MY_APP_RETRIES", "5")
    assert Settings(_env_file=None).retries == 5


test_retries_from_settings()
print("ok")
```

Pass `_env_file=None` in tests so a developer's local `.env` cannot change the result.

## Verify

These attack cases prove that a missing value stops the program, a bad value names itself, and a secret never prints. The module path and variable names are this skill's examples, so use your own.

```bash
env -u MY_APP_API_KEY uv run python -c "from my_app.config import Settings; Settings(_env_file=None)"; echo "exit $?"   # expect 1, naming api_key
export MY_APP_API_KEY=sk-canary-123
MY_APP_DB__PORT=notaport uv run python -c "from my_app.config import Settings; Settings(_env_file=None)" 2>&1 | grep -E "db\.port|canary"   # names db.port, never the canary
uv run python -c "from my_app.config import Settings; print(Settings(_env_file=None))" | grep -c canary   # expect 0
unset MY_APP_API_KEY
grep -rnE "os\.environ|os\.getenv" src | grep -v "config.py"   # expect nothing
git ls-files | grep -E "(^|/)\.env$"                  # expect nothing: .env is not committed
```

## Anti-patterns

| Instead of | Do |
|---|---|
| `os.environ` reads deep in the code | one settings object built at start-up |
| `bool(os.environ["DEBUG"])` | parse from an explicit list of words |
| `if ENV == "production":` | one setting per behaviour |
| a default for a required secret | no default, so start-up fails |
| a key on the command line | the environment or a secret store |
| a mutable settings object | `frozen=True` |
| `~/.config` hard-coded | `platformdirs` |
| one error reported at a time | gather every problem, report them together |

## Checklist

Configuration is sound when every item holds.

- [ ] Sources and their order are written down; nested values merge key by key.
- [ ] Configuration is read once at start-up into one typed, frozen object and passed in.
- [ ] Every variable is prefixed, parsed explicitly and validated; all errors are reported together at start-up.
- [ ] User config files live in the platformdirs config folder, with an override variable.
- [ ] Secrets are `SecretStr`, absent from git, logs, argv and error messages (`hide_input_in_errors=True`, and `exc.errors(include_input=False)` wherever errors are logged or returned).
- [ ] Tests build settings directly and ignore the local `.env`.

## Gotchas

These are the configuration bugs seen most often.

1. **`DEBUG=false` turned debugging on.** `bool("false")` is `True`. Parse booleans from an explicit list of words.
2. **A setting in the config file has no effect.** A higher layer (an old environment variable, a `.env` file) wins, or the key has a typo. Print the effective config and reject unknown keys.
3. **A validation error printed the API key.** pydantic includes inputs in errors. Set `hide_input_in_errors=True` for `str(exc)`, and call `exc.errors(include_input=False)` for structured errors, which that flag leaves untouched.
4. **`.env` values leak into tests or production.** The `.env` file was read everywhere. Use it for local development only; pass `_env_file=None` in tests.
5. **A nested environment variable is ignored.** No `env_nested_delimiter`, or a single `_` that is ambiguous with field names. Use `__`.
6. **Setting a list from the environment fails with a JSON error.** Complex types are parsed as JSON. Pass JSON, or mark the field `NoDecode` and split it in a validator.
7. **The app reads a different config file on Windows.** A hard-coded `~/.config` path. Use platformdirs.

## References

The reference files hold deeper material.

- [pydantic-settings-in-depth.md](references/pydantic-settings-in-depth.md): every source and its priority, secrets folders, aliases for unprefixed variables, comma-separated lists, custom sources, nested merging, CLI parsing and readable start-up errors
- [stdlib-config-loader.md](references/stdlib-config-loader.md): a dependency-free layered loader into frozen dataclasses with type coercion from annotations, unknown-key checks, config file discovery and a first-run example file

## See also

- `python-cli`: flags on top of these layers
- `python-code-design`: where the settings object sits
- `python-data-models`: pydantic models and dataclasses
- `python-security`: secrets in depth
- `python-cross-platform`: per-user folders on each OS
- `python-containers`: variables in images and at run time
