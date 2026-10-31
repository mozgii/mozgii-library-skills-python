---
title: pydantic-settings in depth
author: MOZGII, Inc.
lang: en
---

# pydantic-settings in depth

The parts of pydantic-settings (2.16, October 2026) that real applications need beyond the basics: every source and its priority, several `.env` files, secret files, variables without the app prefix, comma-separated lists, a custom source, command-line parsing, and start-up errors a person can read.

## Contents

This reference has 7 sections.

1. Sources and their priority
2. Several .env files
3. Secret files from a secrets folder
4. Variables without the app prefix
5. Comma-separated lists from the environment
6. A custom settings source
7. Readable start-up errors and command-line parsing

## 1. Sources and their priority

A `BaseSettings` class collects values from its sources and validates the merged result once. The default sources, highest priority first:

| Priority | Source | Configured by |
|---|---|---|
| 1 | keyword arguments to `Settings(...)` | the caller |
| 2 | environment variables | `env_prefix`, `env_nested_delimiter`, `case_sensitive` |
| 3 | `.env` files | `env_file`, `env_file_encoding` |
| 4 | secret files | `secrets_dir` |
| 5 | field defaults | the class |

Extra sources ship with the package and are added in `settings_customise_sources`: `TomlConfigSettingsSource` (`toml_file`), `PyprojectTomlConfigSettingsSource` (`[tool.<name>]` in `pyproject.toml`), `JsonConfigSettingsSource`, `YamlConfigSettingsSource`, and cloud secret stores (AWS, Azure, Google). Return them in priority order, highest first; leave a source out to disable it.

## 2. Several .env files

`env_file` accepts a tuple; later files override earlier ones, which gives a shared `.env` with a personal `.env.local` on top. Real environment variables still beat both.

```python
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

Path(".env").write_text("APP_REGION=eu\nAPP_WORKERS=2\n", encoding="utf-8")
Path(".env.local").write_text("APP_WORKERS=8\n", encoding="utf-8")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=(".env", ".env.local"), env_file_encoding="utf-8")
    region: str = "us"
    workers: int = 1


print(Settings())                                        # region='eu' workers=8
```

- Missing `.env` files are skipped silently; a typo in the file name looks like "the setting is ignored".
- `.env` files are for development machines. Deployed services get real environment variables or secret files.

## 3. Secret files from a secrets folder

`secrets_dir` reads one file per field, named after the field with the prefix (`app_db_password`), which is how Docker and Kubernetes mount secrets (`/run/secrets`). The file content, stripped of the trailing newline, is the value.

```python
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

secrets = Path("run-secrets")
secrets.mkdir(exist_ok=True)
(secrets / "app_db_password").write_text("s3cr3t\n", encoding="utf-8")
(secrets / "app_db_password").chmod(0o600)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", secrets_dir=secrets, hide_input_in_errors=True)
    db_password: SecretStr


print(Settings().db_password, len(Settings().db_password.get_secret_value()))   # ********** 6
```

- A missing secrets folder only warns (`directory "..." does not exist`); set `secrets_dir_missing="error"` where the folder must exist.
- `secrets_dir` also accepts a list of folders; later folders win.
- On Windows there is no `/run/secrets`; point `secrets_dir` at a folder only the user can read, or use the OS keychain.

## 4. Variables without the app prefix

Some variables have names fixed by convention (`DATABASE_URL`, `PORT`, `SENTRY_DSN`). A `validation_alias` reads them by their own name, ignoring `env_prefix`; `AliasChoices` accepts the prefixed and the plain name.

```python
import os

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

os.environ["DATABASE_URL"] = "postgresql://db.internal/app"
os.environ["PORT"] = "8080"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_")
    database_url: str = Field(validation_alias=AliasChoices("APP_DATABASE_URL", "DATABASE_URL"))
    port: int = Field(8000, validation_alias="PORT")


print(Settings())
```

The first name in `AliasChoices` that is set wins, so put the app-specific name first.

## 5. Comma-separated lists from the environment

pydantic-settings parses complex fields (`list`, `dict`, nested models) from the environment as JSON. To accept `a.com, b.com`, mark the field `NoDecode` and split it in a validator; JSON input then needs its own branch if you want both.

```python
import json
import os
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

os.environ["APP_ALLOWED_HOSTS"] = "a.example, b.example"
os.environ["APP_PORTS"] = "[80, 443]"                    # plain JSON works without NoDecode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_")
    allowed_hosts: Annotated[list[str], NoDecode] = []
    ports: list[int] = []

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def split_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            return json.loads(text) if text.startswith("[") else [h.strip() for h in text.split(",") if h.strip()]
        return value


print(Settings())
```

## 6. A custom settings source

A source is a `PydanticBaseSettingsSource` subclass whose `__call__` returns a dict of field values. This one reads a JSON file and ignores keys the model does not define; a keyring or remote config source follows the same shape.

```python
import json
from pathlib import Path
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class JsonFileSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings], path: Path) -> None:
        super().__init__(settings_cls)
        self.data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self.data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self.data.items() if k in self.settings_cls.model_fields}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_")
    port: int = 8000
    name: str = "app"

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return (init_settings, env_settings, JsonFileSource(settings_cls, Path("settings.json")))


Path("settings.json").write_text('{"port": 9000, "unknown": true}', encoding="utf-8")
print(Settings())                                        # port=9000 name='app'
```

Warn about keys the model does not know instead of dropping them silently, once the format is stable.

## 7. Readable start-up errors and command-line parsing

A `ValidationError` lists every problem at once; turn it into a short message naming the environment variable or file key, and exit with a non-zero status before the app does any work.

```python
import os
import sys

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

os.environ["APP_API_KEY"] = "sk-live-123"
os.environ["APP_WORKERS"] = "many"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", hide_input_in_errors=True)
    api_key: SecretStr
    workers: int = 1
    region: str


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        lines = [f"  APP_{'__'.join(map(str, e['loc'])).upper()}: {e['msg']}" for e in exc.errors()]
        print("Configuration errors:\n" + "\n".join(lines), file=sys.stderr)
        raise SystemExit(2) from None


try:
    load_settings()
except SystemExit as stop:
    print("exit status", stop.code)
```

`CliApp.run(Settings)` adds command-line flags for every field (`--workers 4`), highest priority, which suits small tools whose flags mirror their settings. For a real CLI with subcommands and help text, parse flags with argparse, Typer or Click (python-cli skill) and pass them in as keyword arguments, the top-priority source.
