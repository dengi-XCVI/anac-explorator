"""@notice Shared path defaults and effective-path resolution for CLI commands."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_RAW_DIR = Path("data/raw")
DEFAULT_WAREHOUSE_DIR = Path("data/warehouse")
DEFAULT_SCHEMAS_DIR = Path("schemas")
DEFAULT_VOCABULARY_DIR = Path("vocabularies")
DEFAULT_DICTIONARIES_DIR = Path("dictionaries")
DEFAULT_WAREHOUSE_DB_PATH = DEFAULT_WAREHOUSE_DIR / "anac.duckdb"
DEFAULT_VOCABULARY_INDEX_PATH = DEFAULT_VOCABULARY_DIR / "index.json"
DEFAULT_CIG_SCHEMA_PATH = DEFAULT_SCHEMAS_DIR / "cig_2025_01.schema.json"
DEFAULT_CIG_COMPARISON_PATH = DEFAULT_SCHEMAS_DIR / "cig_2007_01_vs_cig_2025_01.comparison.json"

ENV_PATH_KEYS = {
    "raw_dir": ("ANAC_RAW_DIR", "ANAC_EXPLORATOR_RAW_DIR"),
    "warehouse_dir": ("ANAC_WAREHOUSE_DIR", "ANAC_EXPLORATOR_WAREHOUSE_DIR"),
    "schemas_dir": ("ANAC_SCHEMAS_DIR", "ANAC_EXPLORATOR_SCHEMAS_DIR"),
    "vocabulary_dir": ("ANAC_VOCABULARY_DIR", "ANAC_EXPLORATOR_VOCABULARY_DIR"),
    "dictionaries_dir": ("ANAC_DICTIONARIES_DIR", "ANAC_EXPLORATOR_DICTIONARIES_DIR"),
    "warehouse_db_path": ("ANAC_DB_PATH", "ANAC_EXPLORATOR_DB_PATH"),
    "vocabulary_index_path": ("ANAC_VOCABULARY_INDEX_PATH", "ANAC_EXPLORATOR_VOCABULARY_INDEX_PATH"),
}


@dataclass(frozen=True, slots=True)
class EffectivePaths:
    """@notice Capture the canonical storage paths resolved for one command run."""

    raw_dir: Path
    warehouse_dir: Path
    schemas_dir: Path
    vocabulary_dir: Path
    dictionaries_dir: Path
    warehouse_db_path: Path
    vocabulary_index_path: Path

    def to_meta_paths(self) -> dict[str, str]:
        """@notice Convert the effective paths into the JSON-ready `meta.paths` block."""

        return {
            "raw_dir": str(self.raw_dir),
            "warehouse_dir": str(self.warehouse_dir),
            "schemas_dir": str(self.schemas_dir),
            "vocabulary_dir": str(self.vocabulary_dir),
            "dictionaries_dir": str(self.dictionaries_dir),
            "warehouse_db_path": str(self.warehouse_db_path),
            "vocabulary_index_path": str(self.vocabulary_index_path),
        }


def resolve_effective_paths(
    *,
    cli_paths: Mapping[str, str | Path | None] | None = None,
    config_paths: Mapping[str, object] | None = None,
    env: Mapping[str, str] | None = None,
) -> EffectivePaths:
    """@notice Resolve canonical CLI paths from defaults, config, env, and CLI overrides."""

    active_cli_paths = dict(cli_paths or {})
    active_config_paths = _extract_config_paths(config_paths)
    active_env = os.environ if env is None else env

    raw_dir = _resolve_path_value(
        key="raw_dir",
        default=DEFAULT_RAW_DIR,
        cli_paths=active_cli_paths,
        config_paths=active_config_paths,
        env=active_env,
    )
    warehouse_dir = _resolve_path_value(
        key="warehouse_dir",
        default=DEFAULT_WAREHOUSE_DIR,
        cli_paths=active_cli_paths,
        config_paths=active_config_paths,
        env=active_env,
    )
    schemas_dir = _resolve_path_value(
        key="schemas_dir",
        default=DEFAULT_SCHEMAS_DIR,
        cli_paths=active_cli_paths,
        config_paths=active_config_paths,
        env=active_env,
    )
    vocabulary_dir = _resolve_path_value(
        key="vocabulary_dir",
        default=DEFAULT_VOCABULARY_DIR,
        cli_paths=active_cli_paths,
        config_paths=active_config_paths,
        env=active_env,
    )
    dictionaries_dir = _resolve_path_value(
        key="dictionaries_dir",
        default=DEFAULT_DICTIONARIES_DIR,
        cli_paths=active_cli_paths,
        config_paths=active_config_paths,
        env=active_env,
    )
    warehouse_db_path = _resolve_path_value(
        key="warehouse_db_path",
        default=warehouse_dir / DEFAULT_WAREHOUSE_DB_PATH.name,
        cli_paths=active_cli_paths,
        config_paths=active_config_paths,
        env=active_env,
    )
    vocabulary_index_path = _resolve_path_value(
        key="vocabulary_index_path",
        default=vocabulary_dir / DEFAULT_VOCABULARY_INDEX_PATH.name,
        cli_paths=active_cli_paths,
        config_paths=active_config_paths,
        env=active_env,
    )
    return EffectivePaths(
        raw_dir=raw_dir,
        warehouse_dir=warehouse_dir,
        schemas_dir=schemas_dir,
        vocabulary_dir=vocabulary_dir,
        dictionaries_dir=dictionaries_dir,
        warehouse_db_path=warehouse_db_path,
        vocabulary_index_path=vocabulary_index_path,
    )


def apply_effective_paths(
    args: object,
    *,
    config_paths: Mapping[str, object] | None = None,
    env: Mapping[str, str] | None = None,
) -> object:
    """@notice Resolve and bind effective paths onto a parsed CLI namespace."""

    paths = resolve_effective_paths_for_args(args, config_paths=config_paths, env=env)
    setattr(args, "effective_paths", paths)

    command = str(getattr(args, "command", ""))
    if hasattr(args, "output_dir") and command in {
        "download-dataset-csv",
        "download-dataset-resource",
        "download-dataset-to-parquet",
        "sync-cig-periods",
        "download-cig-sample",
    }:
        setattr(args, "output_dir", str(paths.raw_dir))
    if hasattr(args, "data_dir"):
        setattr(args, "data_dir", str(paths.raw_dir))
    if hasattr(args, "schemas_dir"):
        setattr(args, "schemas_dir", str(paths.schemas_dir))
    if hasattr(args, "warehouse_dir"):
        setattr(args, "warehouse_dir", str(paths.warehouse_dir))
    if hasattr(args, "vocabulary_dir"):
        setattr(args, "vocabulary_dir", str(paths.vocabulary_dir))
    if hasattr(args, "db_path"):
        setattr(args, "db_path", str(paths.warehouse_db_path))
    if hasattr(args, "vocabulary_index_path"):
        setattr(args, "vocabulary_index_path", str(paths.vocabulary_index_path))

    if command == "build-vocabulary-crosswalks" and hasattr(args, "output_dir"):
        setattr(args, "output_dir", str(paths.vocabulary_dir))
    if command == "build-data-dictionary":
        setattr(args, "schema_path", str(_resolve_optional_file_path(args, "schema_path", default=paths.schemas_dir / DEFAULT_CIG_SCHEMA_PATH.name)))
        setattr(
            args,
            "comparison_path",
            str(_resolve_optional_file_path(args, "comparison_path", default=paths.schemas_dir / DEFAULT_CIG_COMPARISON_PATH.name)),
        )
        setattr(args, "output_dir", str(paths.dictionaries_dir))
    if command == "validate-local-data-integrity":
        setattr(args, "schema_path", str(_resolve_optional_file_path(args, "schema_path", default=paths.schemas_dir / DEFAULT_CIG_SCHEMA_PATH.name)))

    return args


def resolve_effective_paths_for_args(
    args: object,
    *,
    config_paths: Mapping[str, object] | None = None,
    env: Mapping[str, str] | None = None,
) -> EffectivePaths:
    """@notice Resolve shared canonical paths from one parsed CLI namespace."""

    cli_paths: dict[str, str | Path | None] = {}
    command = str(getattr(args, "command", ""))

    if command in {
        "download-dataset-csv",
        "download-dataset-resource",
        "download-dataset-to-parquet",
        "sync-cig-periods",
        "download-cig-sample",
    }:
        cli_paths["raw_dir"] = getattr(args, "output_dir", None)
    if command == "build-vocabulary-crosswalks":
        cli_paths["raw_dir"] = getattr(args, "data_dir", None)
        cli_paths["vocabulary_dir"] = getattr(args, "output_dir", None)
    if command == "build-data-dictionary":
        cli_paths["dictionaries_dir"] = getattr(args, "output_dir", None)
        cli_paths["vocabulary_dir"] = getattr(args, "vocabulary_dir", None)

    if hasattr(args, "schemas_dir"):
        cli_paths["schemas_dir"] = getattr(args, "schemas_dir", None)
    if hasattr(args, "warehouse_dir"):
        cli_paths["warehouse_dir"] = getattr(args, "warehouse_dir", None)
    if hasattr(args, "db_path"):
        cli_paths["warehouse_db_path"] = getattr(args, "db_path", None)
    if hasattr(args, "vocabulary_index_path"):
        cli_paths["vocabulary_index_path"] = getattr(args, "vocabulary_index_path", None)

    if cli_paths.get("warehouse_dir") is None and getattr(args, "db_path", None) is not None:
        cli_paths["warehouse_dir"] = Path(str(getattr(args, "db_path"))).parent
    if cli_paths.get("schemas_dir") is None and getattr(args, "schema_path", None) is not None:
        cli_paths["schemas_dir"] = Path(str(getattr(args, "schema_path"))).parent
    if cli_paths.get("schemas_dir") is None and getattr(args, "comparison_path", None) is not None:
        cli_paths["schemas_dir"] = Path(str(getattr(args, "comparison_path"))).parent
    if cli_paths.get("vocabulary_dir") is None and getattr(args, "vocabulary_index_path", None) is not None:
        cli_paths["vocabulary_dir"] = Path(str(getattr(args, "vocabulary_index_path"))).parent

    return resolve_effective_paths(cli_paths=cli_paths, config_paths=config_paths, env=env)


def _resolve_path_value(
    *,
    key: str,
    default: Path,
    cli_paths: Mapping[str, str | Path | None],
    config_paths: Mapping[str, object],
    env: Mapping[str, str],
) -> Path:
    """@notice Resolve one path using config, env, and CLI precedence over a default."""

    cli_value = cli_paths.get(key)
    if cli_value not in (None, ""):
        return Path(str(cli_value))

    for env_name in ENV_PATH_KEYS[key]:
        env_value = env.get(env_name)
        if env_value not in (None, ""):
            return Path(env_value)

    config_value = config_paths.get(key)
    if config_value not in (None, ""):
        return Path(str(config_value))

    return default


def _extract_config_paths(config_paths: Mapping[str, object] | None) -> dict[str, object]:
    """@notice Normalize flat or nested config-path mappings into one flat dictionary."""

    if config_paths is None:
        return {}
    if "paths" in config_paths and isinstance(config_paths["paths"], Mapping):
        return dict(config_paths["paths"])
    return dict(config_paths)


def _resolve_optional_file_path(args: object, attribute_name: str, *, default: Path) -> Path:
    """@notice Resolve one optional file path or return the supplied default."""

    value = getattr(args, attribute_name, None)
    if value in (None, ""):
        return default
    return Path(str(value))
