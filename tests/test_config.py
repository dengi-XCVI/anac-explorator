"""@notice Tests for config persistence, merge precedence, and validation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anac_explorator.config import (
    default_config_path,
    get_config,
    load_persisted_config,
    resolve_effective_config,
    save_persisted_config,
    set_config,
    set_config_value,
    show_config,
    unset_config_value,
    validate_config,
    validate_persisted_config,
)
from anac_explorator.errors import CliCommandError


class ConfigTests(unittest.TestCase):
    """@notice Verify the shared config system without going through the CLI wrapper."""

    def test_default_config_path_uses_xdg_location_when_available(self) -> None:
        """@notice Prefer XDG_CONFIG_HOME over the home-directory fallback."""

        path = default_config_path(env={"XDG_CONFIG_HOME": "/tmp/xdg-config"})

        self.assertEqual(str(path), "/tmp/xdg-config/anacx/config.json")

    def test_default_config_path_falls_back_to_legacy_directory_when_present(self) -> None:
        """@notice Keep using an existing legacy config path until it is migrated."""

        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = Path(temp_dir) / "anac-explorator" / "config.json"
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.write_text("{}", encoding="utf-8")

            path = default_config_path(env={"XDG_CONFIG_HOME": temp_dir})

        self.assertEqual(path, legacy_path)

    def test_resolve_effective_config_prefers_anac_env_over_compat_and_file(self) -> None:
        """@notice Merge defaults, config file, compatibility env, and ANAC env deterministically."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            save_persisted_config(
                config_path,
                {
                    "transport": {"default": "http"},
                    "paths": {"raw_dir": "config/raw"},
                },
            )

            resolved = resolve_effective_config(
                config_path=config_path,
                env={
                    "ANAC_TRANSPORT": "playwright",
                    "ANAC_EXPLORATOR_TRANSPORT": "http",
                    "ANAC_RAW_DIR": "env/raw",
                },
            )

        self.assertEqual(resolved.config.transport.default, "playwright")
        self.assertEqual(resolved.sources["transport"]["default"], "env:ANAC_TRANSPORT")
        self.assertEqual(resolved.config.paths.raw_dir, "env/raw")
        self.assertEqual(resolved.sources["paths"]["raw_dir"], "env:ANAC_RAW_DIR")

    def test_set_and_unset_config_value_round_trip_through_json_file(self) -> None:
        """@notice Persist one value and remove it again without losing valid JSON structure."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"

            stored = set_config_value(config_path, "query.row_limit", "250")
            timeout_value = set_config_value(config_path, "query.timeout", "45")
            persisted_after_set = load_persisted_config(config_path)
            removed_timeout = unset_config_value(config_path, "query.timeout")
            removed = unset_config_value(config_path, "query.row_limit")
            persisted_after_unset = load_persisted_config(config_path)

        self.assertEqual(stored, 250)
        self.assertEqual(timeout_value, 45)
        self.assertEqual(persisted_after_set["query"]["row_limit"], 250)
        self.assertEqual(persisted_after_set["query"]["timeout"], 45)
        self.assertTrue(removed_timeout)
        self.assertTrue(removed)
        self.assertEqual(persisted_after_unset, {})

    def test_show_config_returns_effective_values_and_sources(self) -> None:
        """@notice Resolve the current config into the backend show payload with source metadata."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            save_persisted_config(config_path, {"query": {"timeout": 18}})

            payload = show_config(
                config_path=config_path,
                env={"ANAC_OUTPUT_FORMAT": "table"},
            )

        self.assertEqual(payload["subcommand"], "show")
        self.assertEqual(payload["config"]["effective"]["query"]["timeout"], 18)
        self.assertEqual(payload["config"]["sources"]["query"]["timeout"], "config_file")
        self.assertEqual(payload["config"]["effective"]["output"]["format"], "table")
        self.assertEqual(payload["config"]["sources"]["output"]["format"], "env:ANAC_OUTPUT_FORMAT")

    def test_get_config_raises_for_missing_key(self) -> None:
        """@notice Reject unknown config keys through the backend get helper."""

        with self.assertRaises(CliCommandError) as context:
            get_config("query.unknown")

        self.assertEqual(context.exception.code, "CONFIG_KEY_NOT_FOUND")

    def test_set_config_persists_value_through_backend_helper(self) -> None:
        """@notice Persist one config value through the backend set helper and write it to disk."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"

            payload = set_config(config_path, "query.row_limit", "250")
            persisted = load_persisted_config(config_path)

        self.assertEqual(payload["subcommand"], "set")
        self.assertEqual(payload["key"], "query.row_limit")
        self.assertEqual(payload["value"], 250)
        self.assertEqual(payload["source"], "config_file")
        self.assertEqual(persisted["query"]["row_limit"], 250)

    def test_validate_persisted_config_reports_all_detected_errors(self) -> None:
        """@notice Return all invalid keys and values instead of stopping at the first issue."""

        errors = validate_persisted_config(
            {
                "transport": {"default": "invalid", "timeout": 0},
                "paths": {"unknown": "value"},
                "mystery": {"enabled": True},
            }
        )

        error_keys = {error.key for error in errors}
        self.assertIn("transport.default", error_keys)
        self.assertIn("transport.timeout", error_keys)
        self.assertIn("paths.unknown", error_keys)
        self.assertIn("mystery", error_keys)

    def test_validate_config_returns_multiple_errors(self) -> None:
        """@notice Return all current config validation issues through the backend validate helper."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            save_persisted_config(
                config_path,
                {
                    "transport": {"default": "invalid", "timeout": 0},
                    "paths": {"unknown": "value"},
                    "mystery": {"enabled": True},
                },
            )

            payload = validate_config(config_path=config_path)

        self.assertEqual(payload["subcommand"], "validate")
        self.assertFalse(payload["value"])
        error_keys = {error["key"] for error in payload["validation_errors"]}
        self.assertIn("transport.default", error_keys)
        self.assertIn("transport.timeout", error_keys)
        self.assertIn("paths.unknown", error_keys)
        self.assertIn("mystery", error_keys)

    def test_set_config_value_writes_typed_json_scalars(self) -> None:
        """@notice Store booleans and integers in JSON with their typed representation."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            set_config_value(config_path, "download.keep_materialized", "true")
            set_config_value(config_path, "transport.timeout", "45")
            payload = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertTrue(payload["download"]["keep_materialized"])
        self.assertEqual(payload["transport"]["timeout"], 45)

    def test_resolve_effective_config_includes_query_timeout(self) -> None:
        """@notice Expose the query-timeout setting through the effective query config domain."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            save_persisted_config(config_path, {"query": {"timeout": 18}})

            resolved = resolve_effective_config(config_path=config_path)

        self.assertEqual(resolved.config.query.timeout, 18)
        self.assertEqual(resolved.sources["query"]["timeout"], "config_file")


if __name__ == "__main__":
    unittest.main()
