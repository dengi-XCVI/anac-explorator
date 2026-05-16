"""@notice Tests for centralized CLI error mapping and SQL policy helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import duckdb

from anac_explorator.errors import (
    QueryPolicyError,
    enforce_read_only_query,
    resolve_command_error,
)


class ErrorMappingTests(unittest.TestCase):
    """@notice Verify stable error-code translation away from raw exceptions."""

    def test_enforce_read_only_query_blocks_mutating_statements_after_comments(self) -> None:
        """@notice Detect writes even when SQL starts with leading comments."""

        with self.assertRaises(QueryPolicyError) as context:
            enforce_read_only_query("/* lead comment */\n-- second comment\nINSERT INTO demo VALUES (1)")

        self.assertEqual(context.exception.keyword, "INSERT")

    def test_resolve_command_error_adds_unknown_relation_recovery_hints(self) -> None:
        """@notice Include relation hints when DuckDB reports a missing table or view."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse.duckdb"
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute("CREATE TABLE cig (id INTEGER)")
                connection.execute("CREATE VIEW anac_datasets AS SELECT 'cig' AS dataset")
            finally:
                connection.close()

            cli_error = resolve_command_error(
                "query-local-data",
                duckdb.CatalogException('Catalog Error: Table with name missing_view does not exist!'),
                args=SimpleNamespace(db_path=str(db_path), sql_query="SELECT * FROM missing_view"),
            )

        self.assertEqual(cli_error.code, "UNKNOWN_RELATION")
        self.assertEqual(cli_error.exit_code, 52)
        self.assertEqual(cli_error.details["relation"], "missing_view")
        self.assertEqual(cli_error.details["available_metadata_views"], ["anac_datasets"])
        self.assertEqual(cli_error.details["available_dataset_views"], ["cig"])

    def test_resolve_command_error_maps_temporal_slice_failures(self) -> None:
        """@notice Translate remote period lookup failures into TEMPORAL_SLICE_NOT_FOUND."""

        cli_error = resolve_command_error(
            "sync-cig-periods",
            ValueError("The requested CIG periods were not found in the remote CKAN dataset: 2025_13"),
            args=SimpleNamespace(dataset_id="cig-2025", period=["2025_13"]),
        )

        self.assertEqual(cli_error.code, "TEMPORAL_SLICE_NOT_FOUND")
        self.assertEqual(cli_error.exit_code, 12)
        self.assertEqual(cli_error.details["requested_periods"], ["2025_13"])


if __name__ == "__main__":
    unittest.main()
