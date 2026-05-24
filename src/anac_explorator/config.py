"""@notice Shared config model, persistence, merge logic, and validation."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from anac_explorator.ckan import (
    DEFAULT_ACCEPT_LANGUAGE,
    DEFAULT_CKAN_BASE_URL,
    DEFAULT_REFERER,
    DEFAULT_TRANSPORT,
    DEFAULT_USER_AGENT,
)
from anac_explorator.errors import CliCommandError
from anac_explorator.paths import (
    DEFAULT_CIG_COMPARISON_PATH,
    DEFAULT_CIG_SCHEMA_PATH,
    DEFAULT_DICTIONARIES_DIR,
    DEFAULT_RAW_DIR,
    DEFAULT_SCHEMAS_DIR,
    DEFAULT_VOCABULARY_DIR,
    DEFAULT_VOCABULARY_INDEX_PATH,
    DEFAULT_WAREHOUSE_DB_PATH,
    DEFAULT_WAREHOUSE_DIR,
)

_BOOLEAN_TRUE_LITERALS = {"1", "true", "yes", "on"}
_BOOLEAN_FALSE_LITERALS = {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class ConfigPaths:
    """@notice Persisted and effective storage-location settings."""

    raw_dir: str
    warehouse_dir: str
    warehouse_db_path: str
    schemas_dir: str
    vocabulary_dir: str
    vocabulary_index_path: str
    dictionaries_dir: str

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the path settings into a JSON-serializable mapping."""

        return {
            "raw_dir": self.raw_dir,
            "warehouse_dir": self.warehouse_dir,
            "warehouse_db_path": self.warehouse_db_path,
            "schemas_dir": self.schemas_dir,
            "vocabulary_dir": self.vocabulary_dir,
            "vocabulary_index_path": self.vocabulary_index_path,
            "dictionaries_dir": self.dictionaries_dir,
        }


@dataclass(frozen=True, slots=True)
class TransportConfig:
    """@notice Effective remote transport and request defaults."""

    default: str
    base_url: str
    timeout: int
    user_agent: str
    accept_language: str
    referer: str
    proxy_url: str | None

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the transport settings into a JSON-serializable mapping."""

        return {
            "default": self.default,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "user_agent": self.user_agent,
            "accept_language": self.accept_language,
            "referer": self.referer,
            "proxy_url": self.proxy_url,
        }


@dataclass(frozen=True, slots=True)
class DownloadConfig:
    """@notice Effective downloader defaults."""

    keep_materialized: bool
    skip_crosswalks: bool

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the download settings into a JSON-serializable mapping."""

        return {
            "keep_materialized": self.keep_materialized,
            "skip_crosswalks": self.skip_crosswalks,
        }


@dataclass(frozen=True, slots=True)
class QueryConfig:
    """@notice Effective local-query defaults."""

    row_limit: int
    timeout: int

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the query settings into a JSON-serializable mapping."""

        return {
            "row_limit": self.row_limit,
            "timeout": self.timeout,
        }


@dataclass(frozen=True, slots=True)
class OutputConfig:
    """@notice Effective output-format defaults."""

    format: str

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the output settings into a JSON-serializable mapping."""

        return {"format": self.format}


@dataclass(frozen=True, slots=True)
class EffectiveConfig:
    """@notice Full effective configuration after file, env, and default merge."""

    paths: ConfigPaths
    transport: TransportConfig
    download: DownloadConfig
    query: QueryConfig
    output: OutputConfig

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the effective config into a JSON-serializable mapping."""

        return {
            "paths": self.paths.to_dict(),
            "transport": self.transport.to_dict(),
            "download": self.download.to_dict(),
            "query": self.query.to_dict(),
            "output": self.output.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ConfigValidationIssue:
    """@notice Describe one config validation failure without stopping at the first issue."""

    key: str
    message: str

    def to_dict(self) -> dict[str, str]:
        """@notice Convert the validation issue into a JSON-friendly mapping."""

        return {
            "key": self.key,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ResolvedConfig:
    """@notice Capture effective config values, sources, and validation state."""

    config: EffectiveConfig
    sources: dict[str, object]
    config_path: str
    file_exists: bool
    validation_errors: list[ConfigValidationIssue]

    def to_show_payload(self) -> dict[str, object]:
        """@notice Build the `config show` payload from the resolved configuration."""

        return {
            "subcommand": "show",
            "config": {
                "effective": self.config.to_dict(),
                "sources": self.sources,
                "config_path": self.config_path,
                "file_exists": self.file_exists,
            },
            "key": None,
            "value": None,
            "source": None,
            "validation_errors": [issue.to_dict() for issue in self.validation_errors],
        }


@dataclass(frozen=True, slots=True)
class _ConfigFieldSpec:
    """@notice Schema metadata for one config key."""

    key: str
    default: object
    coercer: Callable[[object], object]
    env_primary: str | None = None
    env_compat: str | None = None


def _coerce_string(value: object) -> str:
    if value is None:
        raise ValueError("must not be null")
    text = str(value).strip()
    if not text:
        raise ValueError("must not be empty")
    return text


def _coerce_nullable_string(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _coerce_non_negative_int(value: object) -> int:
    integer = int(value)
    if integer < 0:
        raise ValueError("must be greater than or equal to 0")
    return integer


def _coerce_positive_int(value: object) -> int:
    integer = int(value)
    if integer <= 0:
        raise ValueError("must be greater than 0")
    return integer


def _coerce_boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in _BOOLEAN_TRUE_LITERALS:
        return True
    if text in _BOOLEAN_FALSE_LITERALS:
        return False
    raise ValueError("must be a boolean literal")


def _coerce_choice(*choices: str) -> Callable[[object], str]:
    allowed = set(choices)

    def _inner(value: object) -> str:
        text = _coerce_string(value)
        if text not in allowed:
            raise ValueError(f"must be one of: {', '.join(sorted(allowed))}")
        return text

    return _inner


CONFIG_FIELD_SPECS: dict[str, _ConfigFieldSpec] = {
    "paths.raw_dir": _ConfigFieldSpec("paths.raw_dir", str(DEFAULT_RAW_DIR), _coerce_string, "ANAC_RAW_DIR", "ANAC_EXPLORATOR_RAW_DIR"),
    "paths.warehouse_dir": _ConfigFieldSpec(
        "paths.warehouse_dir",
        str(DEFAULT_WAREHOUSE_DIR),
        _coerce_string,
        "ANAC_WAREHOUSE_DIR",
        "ANAC_EXPLORATOR_WAREHOUSE_DIR",
    ),
    "paths.warehouse_db_path": _ConfigFieldSpec(
        "paths.warehouse_db_path",
        str(DEFAULT_WAREHOUSE_DB_PATH),
        _coerce_string,
        "ANAC_DB_PATH",
        "ANAC_EXPLORATOR_DB_PATH",
    ),
    "paths.schemas_dir": _ConfigFieldSpec("paths.schemas_dir", str(DEFAULT_SCHEMAS_DIR), _coerce_string, "ANAC_SCHEMAS_DIR", "ANAC_EXPLORATOR_SCHEMAS_DIR"),
    "paths.vocabulary_dir": _ConfigFieldSpec(
        "paths.vocabulary_dir",
        str(DEFAULT_VOCABULARY_DIR),
        _coerce_string,
        "ANAC_VOCABULARY_DIR",
        "ANAC_EXPLORATOR_VOCABULARY_DIR",
    ),
    "paths.vocabulary_index_path": _ConfigFieldSpec(
        "paths.vocabulary_index_path",
        str(DEFAULT_VOCABULARY_INDEX_PATH),
        _coerce_string,
        "ANAC_VOCABULARY_INDEX_PATH",
        "ANAC_EXPLORATOR_VOCABULARY_INDEX_PATH",
    ),
    "paths.dictionaries_dir": _ConfigFieldSpec(
        "paths.dictionaries_dir",
        str(DEFAULT_DICTIONARIES_DIR),
        _coerce_string,
        "ANAC_DICTIONARIES_DIR",
        "ANAC_EXPLORATOR_DICTIONARIES_DIR",
    ),
    "transport.default": _ConfigFieldSpec(
        "transport.default",
        DEFAULT_TRANSPORT,
        _coerce_choice("auto", "http", "playwright"),
        "ANAC_TRANSPORT",
        "ANAC_EXPLORATOR_TRANSPORT",
    ),
    "transport.base_url": _ConfigFieldSpec("transport.base_url", DEFAULT_CKAN_BASE_URL, _coerce_string, "ANAC_BASE_URL"),
    "transport.timeout": _ConfigFieldSpec("transport.timeout", 30, _coerce_positive_int, "ANAC_TIMEOUT"),
    "transport.user_agent": _ConfigFieldSpec(
        "transport.user_agent",
        DEFAULT_USER_AGENT,
        _coerce_string,
        "ANAC_USER_AGENT",
        "ANAC_EXPLORATOR_USER_AGENT",
    ),
    "transport.accept_language": _ConfigFieldSpec(
        "transport.accept_language",
        DEFAULT_ACCEPT_LANGUAGE,
        _coerce_string,
        "ANAC_ACCEPT_LANGUAGE",
        "ANAC_EXPLORATOR_ACCEPT_LANGUAGE",
    ),
    "transport.referer": _ConfigFieldSpec(
        "transport.referer",
        DEFAULT_REFERER,
        _coerce_string,
        "ANAC_REFERER",
        "ANAC_EXPLORATOR_REFERER",
    ),
    "transport.proxy_url": _ConfigFieldSpec(
        "transport.proxy_url",
        None,
        _coerce_nullable_string,
        "ANAC_PROXY_URL",
        "ANAC_EXPLORATOR_PROXY_URL",
    ),
    "download.keep_materialized": _ConfigFieldSpec(
        "download.keep_materialized",
        False,
        _coerce_boolean,
        "ANAC_DOWNLOAD_KEEP_MATERIALIZED",
    ),
    "download.skip_crosswalks": _ConfigFieldSpec(
        "download.skip_crosswalks",
        False,
        _coerce_boolean,
        "ANAC_DOWNLOAD_SKIP_CROSSWALKS",
    ),
    "query.row_limit": _ConfigFieldSpec("query.row_limit", 1_000, _coerce_non_negative_int, "ANAC_QUERY_ROW_LIMIT"),
    "query.timeout": _ConfigFieldSpec("query.timeout", 30, _coerce_positive_int, "ANAC_QUERY_TIMEOUT", "ANAC_EXPLORATOR_QUERY_TIMEOUT"),
    "output.format": _ConfigFieldSpec("output.format", "json", _coerce_choice("json", "table"), "ANAC_OUTPUT_FORMAT"),
}

_CONFIG_COMMAND_OPTION_KEYS = {
    "base_url": ("transport.base_url", "--base-url"),
    "timeout": ("transport.timeout", "--timeout"),
    "user_agent": ("transport.user_agent", "--user-agent"),
    "accept_language": ("transport.accept_language", "--accept-language"),
    "referer": ("transport.referer", "--referer"),
    "proxy_url": ("transport.proxy_url", "--proxy-url"),
    "transport": ("transport.default", "--transport"),
    "row_limit": ("query.row_limit", "--row-limit"),
    "query_timeout": ("query.timeout", "--timeout"),
    "output_format": ("output.format", "--format"),
}
_CONFIG_COMMAND_BOOLEAN_KEYS = {
    "keep_materialized": ("download.keep_materialized", "--keep-materialized"),
    "skip_crosswalks": ("download.skip_crosswalks", "--skip-crosswalks"),
}
_CONFIG_DOMAIN_PREFIXES = {"paths", "transport", "download", "query", "output"}


def legacy_default_config_path(*, env: Mapping[str, str] | None = None) -> Path:
    """@notice Return the legacy persisted config-file path used before the CLI rename."""

    active_env = os.environ if env is None else env
    xdg_config_home = active_env.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "anac-explorator" / "config.json"
    return Path.home() / ".config" / "anac-explorator" / "config.json"


def default_config_path(*, env: Mapping[str, str] | None = None) -> Path:
    """@notice Return the default persisted config-file path."""

    active_env = os.environ if env is None else env
    xdg_config_home = active_env.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        default_path = Path(xdg_config_home) / "anacx" / "config.json"
    else:
        default_path = Path.home() / ".config" / "anacx" / "config.json"
    legacy_path = legacy_default_config_path(env=active_env)
    if not default_path.exists() and legacy_path.exists():
        return legacy_path
    return default_path


def load_persisted_config(config_path: str | Path) -> dict[str, object]:
    """@notice Load the raw persisted config JSON, or return an empty config when absent."""

    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliCommandError(
            "CONFIG_ERROR",
            f"Config file is not valid JSON: {path}",
            details={"config_path": str(path)},
            cause=exc,
        ) from exc
    if not isinstance(payload, dict):
        raise CliCommandError(
            "CONFIG_ERROR",
            f"Config file root must be a JSON object: {path}",
            details={"config_path": str(path)},
        )
    return payload


def save_persisted_config(config_path: str | Path, payload: dict[str, object]) -> None:
    """@notice Persist the user config to disk as formatted JSON."""

    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def delete_persisted_config(config_path: str | Path) -> None:
    """@notice Remove the persisted config file when it exists."""

    Path(config_path).unlink(missing_ok=True)


def resolve_effective_config(
    *,
    config_path: str | Path | None = None,
    no_config: bool = False,
    env: Mapping[str, str] | None = None,
) -> ResolvedConfig:
    """@notice Resolve effective config values from defaults, file, and environment."""

    active_env = os.environ if env is None else env
    path = default_config_path(env=active_env) if config_path is None else Path(config_path)
    persisted = {} if no_config else load_persisted_config(path)
    validation_errors = validate_persisted_config(persisted)

    values = {key: spec.default for key, spec in CONFIG_FIELD_SPECS.items()}
    sources = {key: "default" for key in CONFIG_FIELD_SPECS}

    for key, raw_value in _flatten_persisted_config(persisted).items():
        spec = CONFIG_FIELD_SPECS.get(key)
        if spec is None:
            continue
        try:
            values[key] = spec.coercer(raw_value)
            sources[key] = "config_file"
        except (TypeError, ValueError):
            continue

    for key, spec in CONFIG_FIELD_SPECS.items():
        env_value = _first_env_value(spec, active_env)
        if env_value is None:
            continue
        env_name, raw_value = env_value
        try:
            values[key] = spec.coercer(raw_value)
            sources[key] = f"env:{env_name}"
        except (TypeError, ValueError):
            validation_errors.append(
                ConfigValidationIssue(
                    key=key,
                    message=f"Environment variable {env_name} has an invalid value.",
                )
            )

    config = EffectiveConfig(
        paths=ConfigPaths(
            raw_dir=str(values["paths.raw_dir"]),
            warehouse_dir=str(values["paths.warehouse_dir"]),
            warehouse_db_path=str(values["paths.warehouse_db_path"]),
            schemas_dir=str(values["paths.schemas_dir"]),
            vocabulary_dir=str(values["paths.vocabulary_dir"]),
            vocabulary_index_path=str(values["paths.vocabulary_index_path"]),
            dictionaries_dir=str(values["paths.dictionaries_dir"]),
        ),
        transport=TransportConfig(
            default=str(values["transport.default"]),
            base_url=str(values["transport.base_url"]),
            timeout=int(values["transport.timeout"]),
            user_agent=str(values["transport.user_agent"]),
            accept_language=str(values["transport.accept_language"]),
            referer=str(values["transport.referer"]),
            proxy_url=None if values["transport.proxy_url"] is None else str(values["transport.proxy_url"]),
        ),
        download=DownloadConfig(
            keep_materialized=bool(values["download.keep_materialized"]),
            skip_crosswalks=bool(values["download.skip_crosswalks"]),
        ),
        query=QueryConfig(
            row_limit=int(values["query.row_limit"]),
            timeout=int(values["query.timeout"]),
        ),
        output=OutputConfig(format=str(values["output.format"])),
    )
    return ResolvedConfig(
        config=config,
        sources=_nested_dict_from_flat(sources),
        config_path=str(path),
        file_exists=path.exists(),
        validation_errors=validation_errors,
    )


def validate_persisted_config(payload: dict[str, object]) -> list[ConfigValidationIssue]:
    """@notice Validate all persisted config entries without stopping at the first issue."""

    errors: list[ConfigValidationIssue] = []
    if not isinstance(payload, dict):
        return [ConfigValidationIssue(key="config", message="Config payload must be a JSON object.")]

    _validate_config_domain(payload, prefix="", errors=errors)

    for key, value in _flatten_persisted_config(payload).items():
        spec = CONFIG_FIELD_SPECS.get(key)
        if spec is None:
            continue
        try:
            spec.coercer(value)
        except (TypeError, ValueError) as exc:
            errors.append(ConfigValidationIssue(key=key, message=str(exc)))
    return errors


def apply_effective_config(
    args: object,
    *,
    argv: list[str] | tuple[str, ...] | None = None,
    env: Mapping[str, str] | None = None,
) -> object:
    """@notice Attach resolved config and fill unset command options from it."""

    config_path = getattr(args, "config_path", None)
    no_config = bool(getattr(args, "no_config", False))
    resolved = resolve_effective_config(config_path=config_path, no_config=no_config, env=env)
    setattr(args, "resolved_config", resolved)
    setattr(args, "config_path", resolved.config_path)

    command = str(getattr(args, "command", ""))
    if command != "config" and resolved.validation_errors:
        raise CliCommandError(
            "CONFIG_ERROR",
            "Configuration is invalid.",
            details={"validation_errors": [issue.to_dict() for issue in resolved.validation_errors]},
        )

    provided_options = set(_extract_cli_options(argv))
    for attribute_name, (config_key, option_name) in _CONFIG_COMMAND_OPTION_KEYS.items():
        if not hasattr(args, attribute_name) or option_name in provided_options:
            continue
        resolved_value = get_config_value(resolved.config, config_key)
        setattr(args, attribute_name, resolved_value)

    for attribute_name, (config_key, option_name) in _CONFIG_COMMAND_BOOLEAN_KEYS.items():
        if not hasattr(args, attribute_name) or option_name in provided_options:
            continue
        current_value = bool(getattr(args, attribute_name))
        if current_value:
            continue
        setattr(args, attribute_name, bool(get_config_value(resolved.config, config_key)))

    return args


def get_config_value(config: EffectiveConfig, key: str) -> object:
    """@notice Read one dot-delimited key from the effective config model."""

    domain_name, field_name = _split_config_key(key)
    domain = getattr(config, domain_name)
    return getattr(domain, field_name)


def set_config_value(config_path: str | Path, key: str, raw_value: str) -> object:
    """@notice Persist one typed config value into the config file."""

    spec = CONFIG_FIELD_SPECS.get(key)
    if spec is None:
        raise CliCommandError(
            "CONFIG_KEY_NOT_FOUND",
            f"Unknown config key: {key}",
            details={"key": key},
        )
    try:
        parsed_value = spec.coercer(raw_value)
    except (TypeError, ValueError) as exc:
        raise CliCommandError(
            "CONFIG_ERROR",
            f"Invalid value for {key}: {exc}",
            details={"key": key, "value": raw_value},
            cause=exc,
        ) from exc

    payload = load_persisted_config(config_path)
    _set_nested_value(payload, key, parsed_value)
    validation_errors = validate_persisted_config(payload)
    if validation_errors:
        raise CliCommandError(
            "CONFIG_ERROR",
            "Configuration is invalid.",
            details={"validation_errors": [issue.to_dict() for issue in validation_errors]},
        )
    save_persisted_config(config_path, payload)
    return parsed_value


def unset_config_value(config_path: str | Path, key: str) -> bool:
    """@notice Remove one explicitly persisted config key from the config file."""

    if key not in CONFIG_FIELD_SPECS:
        raise CliCommandError(
            "CONFIG_KEY_NOT_FOUND",
            f"Unknown config key: {key}",
            details={"key": key},
        )
    payload = load_persisted_config(config_path)
    removed = _unset_nested_value(payload, key)
    if payload:
        save_persisted_config(config_path, payload)
    else:
        delete_persisted_config(config_path)
    return removed


def validate_current_config(*, config_path: str | Path | None, no_config: bool, env: Mapping[str, str] | None = None) -> ResolvedConfig:
    """@notice Resolve the current config and preserve all validation errors for reporting."""

    return resolve_effective_config(config_path=config_path, no_config=no_config, env=env)


def show_config(
    *,
    config_path: str | Path | None = None,
    no_config: bool = False,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """@notice Resolve the current config and return the backend payload for `show`."""

    resolved = resolve_effective_config(config_path=config_path, no_config=no_config, env=env)
    return resolved.to_show_payload()


def get_config(
    key: str,
    *,
    config_path: str | Path | None = None,
    no_config: bool = False,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """@notice Resolve one current config key and return the backend payload for `get`."""

    resolved = resolve_effective_config(config_path=config_path, no_config=no_config, env=env)
    return render_config_get_payload(resolved, key)


def set_config(config_path: str | Path, key: str, raw_value: str) -> dict[str, object]:
    """@notice Persist one config key and return the backend payload for `set`."""

    value = set_config_value(config_path, key, raw_value)
    return render_config_set_payload(str(config_path), key, value)


def unset_config(config_path: str | Path, key: str) -> dict[str, object]:
    """@notice Remove one persisted config key and return the backend payload for `unset`."""

    removed = unset_config_value(config_path, key)
    return render_config_unset_payload(str(config_path), key, removed)


def reset_config(config_path: str | Path) -> dict[str, object]:
    """@notice Clear the persisted config file and return the backend payload for `reset`."""

    delete_persisted_config(config_path)
    return render_config_reset_payload(str(config_path))


def validate_config(
    *,
    config_path: str | Path | None = None,
    no_config: bool = False,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """@notice Resolve and validate the current config, preserving all issues for `validate`."""

    resolved = validate_current_config(config_path=config_path, no_config=no_config, env=env)
    return render_config_validate_payload(resolved)


def _split_config_key(key: str) -> tuple[str, str]:
    """@notice Split a `domain.field` config key into its two components."""

    if "." not in key:
        raise CliCommandError(
            "CONFIG_KEY_NOT_FOUND",
            f"Unknown config key: {key}",
            details={"key": key},
        )
    domain_name, field_name = key.split(".", 1)
    if domain_name not in _CONFIG_DOMAIN_PREFIXES or key not in CONFIG_FIELD_SPECS:
        raise CliCommandError(
            "CONFIG_KEY_NOT_FOUND",
            f"Unknown config key: {key}",
            details={"key": key},
        )
    return domain_name, field_name


def _flatten_persisted_config(payload: dict[str, object], prefix: str = "") -> dict[str, object]:
    """@notice Flatten a nested persisted config object into dot-separated keys."""

    flattened: dict[str, object] = {}
    for key, value in payload.items():
        current_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flattened.update(_flatten_persisted_config(value, prefix=current_key))
            continue
        flattened[current_key] = value
    return flattened


def _nested_dict_from_flat(flat_mapping: Mapping[str, object]) -> dict[str, object]:
    """@notice Rebuild a nested dictionary from a flat dot-separated mapping."""

    nested: dict[str, object] = {}
    for key, value in flat_mapping.items():
        _set_nested_value(nested, key, value)
    return nested


def _set_nested_value(payload: dict[str, object], key: str, value: object) -> None:
    """@notice Assign one dot-separated key into a nested mapping."""

    parts = key.split(".")
    target = payload
    for part in parts[:-1]:
        existing = target.get(part)
        if not isinstance(existing, dict):
            existing = {}
            target[part] = existing
        target = existing
    target[parts[-1]] = value


def _unset_nested_value(payload: dict[str, object], key: str) -> bool:
    """@notice Remove one dot-separated key from a nested mapping."""

    parts = key.split(".")
    trail: list[tuple[dict[str, object], str]] = []
    target = payload
    for part in parts[:-1]:
        next_value = target.get(part)
        if not isinstance(next_value, dict):
            return False
        trail.append((target, part))
        target = next_value
    removed = target.pop(parts[-1], None) is not None
    if not removed:
        return False
    for parent, part in reversed(trail):
        child = parent.get(part)
        if isinstance(child, dict) and not child:
            parent.pop(part)
            continue
        break
    return True


def _extract_cli_options(argv: list[str] | tuple[str, ...] | None) -> list[str]:
    """@notice Extract the explicit long-option names that appeared in the CLI argv."""

    if argv is None:
        return []
    options: list[str] = []
    for token in argv:
        if token == "--":
            break
        if token.startswith("--"):
            options.append(token.split("=", 1)[0])
    return options


def _first_env_value(spec: _ConfigFieldSpec, env: Mapping[str, str]) -> tuple[str, str] | None:
    """@notice Return the primary env value when present, else the compatibility fallback."""

    if spec.env_primary and env.get(spec.env_primary) not in (None, ""):
        return spec.env_primary, str(env[spec.env_primary])
    if spec.env_compat and env.get(spec.env_compat) not in (None, ""):
        return spec.env_compat, str(env[spec.env_compat])
    return None


def _validate_config_domain(
    payload: dict[str, object],
    *,
    prefix: str,
    errors: list[ConfigValidationIssue],
) -> None:
    """@notice Validate nested config domains and unknown keys."""

    for key, value in payload.items():
        current_key = f"{prefix}.{key}" if prefix else key
        if prefix == "" and current_key not in _CONFIG_DOMAIN_PREFIXES:
            errors.append(ConfigValidationIssue(key=current_key, message="Unknown config domain."))
            continue
        if isinstance(value, dict):
            _validate_config_domain(value, prefix=current_key, errors=errors)
            continue
        if current_key not in CONFIG_FIELD_SPECS:
            errors.append(ConfigValidationIssue(key=current_key, message="Unknown config key."))


def render_config_get_payload(resolved: ResolvedConfig, key: str) -> dict[str, object]:
    """@notice Build the `config get` payload from one resolved config key."""

    value = get_config_value(resolved.config, key)
    source = _source_for_key(resolved.sources, key)
    return {
        "subcommand": "get",
        "config": None,
        "key": key,
        "value": value,
        "source": source,
        "validation_errors": None,
    }


def render_config_set_payload(config_path: str, key: str, value: object) -> dict[str, object]:
    """@notice Build the `config set` payload."""

    return {
        "subcommand": "set",
        "config": {"config_path": config_path},
        "key": key,
        "value": value,
        "source": "config_file",
        "validation_errors": None,
    }


def render_config_unset_payload(config_path: str, key: str, removed: bool) -> dict[str, object]:
    """@notice Build the `config unset` payload."""

    return {
        "subcommand": "unset",
        "config": {"config_path": config_path},
        "key": key,
        "value": removed,
        "source": "config_file",
        "validation_errors": None,
    }


def render_config_reset_payload(config_path: str) -> dict[str, object]:
    """@notice Build the `config reset` payload."""

    return {
        "subcommand": "reset",
        "config": {"config_path": config_path},
        "key": None,
        "value": True,
        "source": "config_file",
        "validation_errors": None,
    }


def render_config_validate_payload(resolved: ResolvedConfig) -> dict[str, object]:
    """@notice Build the `config validate` payload including all validation issues."""

    return {
        "subcommand": "validate",
        "config": {
            "config_path": resolved.config_path,
            "file_exists": resolved.file_exists,
        },
        "key": None,
        "value": len(resolved.validation_errors) == 0,
        "source": None,
        "validation_errors": [issue.to_dict() for issue in resolved.validation_errors],
    }


def _source_for_key(sources: dict[str, object], key: str) -> str | None:
    """@notice Resolve one nested source string from the show payload source tree."""

    target: object = sources
    for part in key.split("."):
        if not isinstance(target, dict):
            return None
        target = target.get(part)
    return None if target is None else str(target)
