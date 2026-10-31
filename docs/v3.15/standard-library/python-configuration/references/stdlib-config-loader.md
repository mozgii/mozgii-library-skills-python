---
title: A layered config loader with the standard library
author: MOZGII, Inc.
lang: en
---

# A layered config loader with the standard library

A complete configuration loader with no dependencies, for tools and libraries that should not pull in pydantic: frozen dataclasses as the schema, defaults merged with TOML files and environment variables, type conversion from the annotations, unknown-key errors, config file discovery, and a commented example file on first run.

## Contents

This reference has 6 sections.

1. The schema as frozen dataclasses
2. Environment variables into a nested dict
3. Converting values by annotation
4. Building the dataclasses with unknown-key checks
5. Finding the config files
6. Putting it together, with a first-run example file

## 1. The schema as frozen dataclasses

The settings schema is a tree of frozen dataclasses; every field has a default, so an empty configuration is valid, and the types in the annotations drive conversion.

```python
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Db:
    host: str = "localhost"
    port: int = 5432


@dataclass(frozen=True, slots=True)
class Settings:
    log_level: str = "info"
    debug: bool = False
    data_dir: Path = Path("data")
    tags: tuple[str, ...] = ()
    db: Db = field(default_factory=Db)


print(Settings())
```

Required settings without a sensible default are better checked after loading (`if not settings.api_url: fail(...)`) than given a placeholder default.

## 2. Environment variables into a nested dict

Environment variables become a nested dict in the same shape as the TOML file: `MY_APP_DB__PORT=6000` becomes `{"db": {"port": "6000"}}`, still a string until conversion.

```python
from collections.abc import Mapping
from typing import Any


def env_layer(env: Mapping[str, str], prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in env.items():
        if not name.upper().startswith(prefix):
            continue
        path = name[len(prefix):].lower().split("__")      # double underscore separates levels
        node = out
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = value
    return out


print(env_layer({"MY_APP_DB__PORT": "6000", "MY_APP_DEBUG": "yes", "HOME": "/home/me"}, "MY_APP_"))
```

## 3. Converting values by annotation

Values from TOML already have types; values from the environment are strings. One function converts either to the annotated type and names the setting in its error.

```python
import json
from pathlib import Path
from typing import Any, get_args, get_origin

TRUE, FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


def convert(value: Any, kind: Any, name: str) -> Any:
    origin = get_origin(kind)
    if origin is tuple or origin is list:
        if isinstance(value, str):
            value = json.loads(value) if value.strip().startswith("[") else [v.strip() for v in value.split(",") if v.strip()]
        item_type = get_args(kind)[0]
        items = [convert(v, item_type, f"{name}[{i}]") for i, v in enumerate(value)]
        return tuple(items) if origin is tuple else items
    if kind is bool:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in TRUE | FALSE:
            return text in TRUE
        raise ValueError(f"{name}: expected true or false, got {value!r}")
    if kind in (int, float):
        if isinstance(value, bool):
            raise ValueError(f"{name}: expected a number, got {value!r}")
        try:
            return kind(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name}: expected {kind.__name__}, got {value!r}") from None
    if kind is Path:
        return Path(str(value)).expanduser()
    if kind is str:
        return str(value)
    raise TypeError(f"{name}: unsupported type {kind!r}")


print(convert("yes", bool, "debug"), convert("a, b", tuple[str, ...], "tags"), convert("~/data", Path, "data_dir"))
```

## 4. Building the dataclasses with unknown-key checks

The merged dict is walked against the dataclass fields: known keys are converted, nested dataclasses recurse, and unknown keys are collected as errors, so a typo in a config file is reported instead of ignored.

```python
import dataclasses
import typing
from typing import Any


def build(cls: type, data: dict[str, Any], where: str, errors: list[str]) -> Any:
    hints = typing.get_type_hints(cls)
    names = {f.name for f in dataclasses.fields(cls)}
    for key in data.keys() - names:
        errors.append(f"{where}{key}: unknown setting")
    kwargs = {}
    for name in names & data.keys():
        kind, value = hints[name], data[name]
        try:
            if dataclasses.is_dataclass(kind):
                if not isinstance(value, dict):
                    raise ValueError(f"{where}{name}: expected a table")
                kwargs[name] = build(kind, value, f"{where}{name}.", errors)
            else:
                kwargs[name] = convert(value, kind, f"{where}{name}")
        except ValueError as exc:
            errors.append(str(exc))
    return cls(**kwargs)
```

This block needs `convert` from section 3 and the dataclasses from section 1; section 6 runs them together.

## 5. Finding the config files

A tool looks for a project file by walking up from the current folder (as git and Ruff do), and for a user file in the per-user config folder. Without platformdirs, the folder per OS is one small function with the platform injected, so it is testable on any machine.

```python
import os
import sys
from pathlib import Path


def user_config_dir(app: str, platform: str = sys.platform, env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ) if env is None else env
    home = Path(env.get("HOME") or env.get("USERPROFILE") or "~").expanduser()
    if platform == "win32":
        return Path(env.get("APPDATA") or home / "AppData" / "Roaming") / app
    if platform == "darwin":
        return home / "Library" / "Application Support" / app
    return Path(env.get("XDG_CONFIG_HOME") or home / ".config") / app


def find_project_file(name: str, start: Path) -> Path | None:
    for folder in (start, *start.parents):
        candidate = folder / name
        if candidate.is_file():
            return candidate
        if (folder / ".git").exists():                     # stop at the repository root
            return None
    return None


for plat in ("darwin", "linux", "win32"):                  # run on macOS, the win32 line joins with "/"
    print(plat, user_config_dir("my-app", plat, {"HOME": "/home/me", "APPDATA": r"C:\Users\me\AppData\Roaming"}))
print(find_project_file("my-app.toml", Path.cwd()))
```

This returns the roaming `%APPDATA%` folder on Windows for settings, as many Windows tools do; platformdirs returns `%LOCALAPPDATA%` unless asked for roaming. Pick one and document it.

## 6. Putting it together, with a first-run example file

The loader merges the layers in order (defaults, user file, project file, environment, explicit overrides), builds the dataclasses, and raises one error listing every problem. On first run it writes a commented example file so people can see what to set.

```python
import dataclasses
import json
import os
import sys
import tomllib
import typing
from pathlib import Path
from typing import Any, get_args, get_origin

# --- the pieces from sections 1 to 4, repeated so this block runs alone ---
TRUE, FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


@dataclasses.dataclass(frozen=True, slots=True)
class Db:
    host: str = "localhost"
    port: int = 5432


@dataclasses.dataclass(frozen=True, slots=True)
class Settings:
    log_level: str = "info"
    debug: bool = False
    tags: tuple[str, ...] = ()
    db: Db = dataclasses.field(default_factory=Db)


def convert(value: Any, kind: Any, name: str) -> Any:
    if get_origin(kind) is tuple:
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]
        return tuple(convert(v, get_args(kind)[0], name) for v in value)
    if kind is bool:
        text = str(value).strip().lower()
        if isinstance(value, bool) or text in TRUE | FALSE:
            return value if isinstance(value, bool) else text in TRUE
        raise ValueError(f"{name}: expected true or false, got {value!r}")
    try:
        return kind(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: expected {kind.__name__}, got {value!r}") from None


def build(cls: type, data: dict[str, Any], where: str, errors: list[str]) -> Any:
    hints, names = typing.get_type_hints(cls), {f.name for f in dataclasses.fields(cls)}
    errors.extend(f"{where}{k}: unknown setting" for k in data.keys() - names)
    kwargs = {}
    for name in names & data.keys():
        try:
            kind = hints[name]
            kwargs[name] = (build(kind, data[name], f"{where}{name}.", errors) if dataclasses.is_dataclass(kind)
                            else convert(data[name], kind, f"{where}{name}"))
        except ValueError as exc:
            errors.append(str(exc))
    return cls(**kwargs)


def env_layer(env: dict[str, str], prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in env.items():
        if key.upper().startswith(prefix):
            *parents, leaf = key[len(prefix):].lower().split("__")
            node = out
            for part in parents:
                node = node.setdefault(part, {})
            node[leaf] = value
    return out


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        merged[key] = deep_merge(merged[key], value) if isinstance(value, dict) and isinstance(merged.get(key), dict) else value
    return merged


# --- the loader ---
EXAMPLE = """# my-app settings; uncomment and change what you need
# log_level = "info"
# debug = false
# tags = ["team-a"]
#
# [db]
# host = "localhost"
# port = 5432
"""


class ConfigError(Exception):
    pass


def load_settings(user_file: Path, project_file: Path | None, env: dict[str, str], **overrides: Any) -> Settings:
    if not user_file.exists():
        user_file.parent.mkdir(parents=True, exist_ok=True)
        user_file.write_text(EXAMPLE, encoding="utf-8")     # first run: a file people can read
    merged: dict[str, Any] = {}
    for path in (user_file, project_file):
        if path and path.exists():
            try:
                merged = deep_merge(merged, tomllib.loads(path.read_text(encoding="utf-8")))
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"{path}: {exc}") from None
    merged = deep_merge(merged, env_layer(env, "MY_APP_"))
    merged = deep_merge(merged, {k: v for k, v in overrides.items() if v is not None})
    errors: list[str] = []
    settings = build(Settings, merged, "", errors)
    if errors:
        raise ConfigError("invalid configuration:\n  " + "\n  ".join(sorted(errors)))
    return settings


user = Path("cfg-home") / "config.toml"
project = Path("my-app.toml")
project.write_text('log_level = "debug"\n[db]\nport = 6000\n', encoding="utf-8")
print(load_settings(user, project, {"MY_APP_TAGS": "a, b", "MY_APP_DB__HOST": "db.local"}, debug=True))
try:
    project.write_text('[db]\nprot = 6000\nport = "x"\n', encoding="utf-8")
    load_settings(user, project, {})
except ConfigError as exc:
    print(exc)
```

In a CLI, catch `ConfigError` in `main()`, print it to stderr and exit with status 2; the person sees every problem at once with the file and key that caused it.
