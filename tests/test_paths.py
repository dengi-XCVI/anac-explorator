"""@notice Tests for shared effective-path resolution across CLI commands."""

from __future__ import annotations

import unittest

from anac_explorator.cli import build_parser
from anac_explorator.paths import (
    apply_effective_paths,
    resolve_effective_paths,
    resolve_effective_paths_for_args,
)


class PathResolutionTests(unittest.TestCase):
    """@notice Verify shared path defaults and override precedence."""

    def test_resolve_effective_paths_uses_repository_defaults(self) -> None:
        """@notice Fall back to the documented repository layout without overrides."""

        paths = resolve_effective_paths(env={})

        self.assertEqual(str(paths.raw_dir), "data/raw")
        self.assertEqual(str(paths.warehouse_dir), "data/warehouse")
        self.assertEqual(str(paths.schemas_dir), "schemas")
        self.assertEqual(str(paths.vocabulary_dir), "vocabularies")
        self.assertEqual(str(paths.dictionaries_dir), "dictionaries")
        self.assertEqual(str(paths.warehouse_db_path), "data/warehouse/anac.duckdb")
        self.assertEqual(str(paths.vocabulary_index_path), "vocabularies/index.json")

    def test_resolve_effective_paths_applies_config_then_env_then_cli(self) -> None:
        """@notice Respect the intended precedence order for shared path roots."""

        paths = resolve_effective_paths(
            config_paths={
                "paths": {
                    "raw_dir": "config/raw",
                    "warehouse_dir": "config/warehouse",
                }
            },
            env={
                "ANAC_EXPLORATOR_RAW_DIR": "env/raw",
                "ANAC_EXPLORATOR_SCHEMAS_DIR": "env/schemas",
            },
            cli_paths={
                "raw_dir": "cli/raw",
                "vocabulary_dir": "cli/vocabularies",
            },
        )

        self.assertEqual(str(paths.raw_dir), "cli/raw")
        self.assertEqual(str(paths.warehouse_dir), "config/warehouse")
        self.assertEqual(str(paths.schemas_dir), "env/schemas")
        self.assertEqual(str(paths.vocabulary_dir), "cli/vocabularies")
        self.assertEqual(str(paths.vocabulary_index_path), "cli/vocabularies/index.json")

    def test_apply_effective_paths_derives_default_artifact_paths_from_root_overrides(self) -> None:
        """@notice Fill command-specific artifact paths from the resolved shared roots."""

        parser = build_parser()
        args = parser.parse_args(["build-data-dictionary"])
        apply_effective_paths(
            args,
            env={
                "ANAC_EXPLORATOR_SCHEMAS_DIR": "/tmp/shared-schemas",
                "ANAC_EXPLORATOR_VOCABULARY_DIR": "/tmp/shared-vocabularies",
                "ANAC_EXPLORATOR_DICTIONARIES_DIR": "/tmp/shared-dictionaries",
            },
        )

        self.assertEqual(args.schema_path, "/tmp/shared-schemas/cig_2025_01.schema.json")
        self.assertEqual(args.comparison_path, "/tmp/shared-schemas/cig_2007_01_vs_cig_2025_01.comparison.json")
        self.assertEqual(args.vocabulary_dir, "/tmp/shared-vocabularies")
        self.assertEqual(args.vocabulary_index_path, "/tmp/shared-vocabularies/index.json")
        self.assertEqual(args.output_dir, "/tmp/shared-dictionaries")

    def test_resolve_effective_paths_for_args_derives_roots_from_explicit_file_paths(self) -> None:
        """@notice Infer shared roots from specific file-path overrides when root flags are absent."""

        parser = build_parser()
        args = parser.parse_args(
            [
                "validate-local-data-integrity",
                "--db-path",
                "/tmp/custom-warehouse/custom.duckdb",
                "--schema-path",
                "/tmp/custom-schemas/current.schema.json",
                "--vocabulary-index-path",
                "/tmp/custom-vocabularies/index.json",
            ]
        )

        paths = resolve_effective_paths_for_args(args, env={})

        self.assertEqual(str(paths.warehouse_dir), "/tmp/custom-warehouse")
        self.assertEqual(str(paths.warehouse_db_path), "/tmp/custom-warehouse/custom.duckdb")
        self.assertEqual(str(paths.schemas_dir), "/tmp/custom-schemas")
        self.assertEqual(str(paths.vocabulary_dir), "/tmp/custom-vocabularies")
        self.assertEqual(str(paths.vocabulary_index_path), "/tmp/custom-vocabularies/index.json")


if __name__ == "__main__":
    unittest.main()
