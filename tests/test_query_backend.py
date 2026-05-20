"""@notice Tests for the Phase 3 query backend execution helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from anac_explorator.errors import CliCommandError
from anac_explorator.loader import run_local_query


class QueryBackendTests(unittest.TestCase):
    """@notice Verify metadata bootstrapping, read-only policy enforcement, and query error translation."""

    def test_run_local_query_supports_read_only_selects_against_metadata_views(self) -> None:
        """@notice Bootstrap metadata views before execution so discoverability queries succeed."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse" / "anac.duckdb"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            duckdb.connect(str(db_path)).close()

            result = run_local_query(
                db_path,
                "SELECT dataset, update_supported FROM anac_datasets ORDER BY dataset",
                row_limit=3,
            )

        self.assertEqual(result.row_count, 3)
        self.assertEqual(
            [row["dataset"] for row in result.rows],
            ["aggiudicatari", "bandi-cig-modalita-realizzazione", "bandi-cig-tipo-scelta-contraente"],
        )
        self.assertFalse(result.rows[0]["update_supported"])

    def test_run_local_query_blocks_mutating_sql_without_opt_in(self) -> None:
        """@notice Reject writes by default even when SQL starts with leading comments."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse.duckdb"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute("CREATE TABLE demo (id INTEGER, label VARCHAR)")
            finally:
                connection.close()

            with self.assertRaises(CliCommandError) as context:
                run_local_query(
                    db_path,
                    "/* preface */\n-- second preface\nINSERT INTO demo VALUES (1, 'one')",
                )

        self.assertEqual(context.exception.code, "WRITE_QUERY_BLOCKED")
        self.assertEqual(context.exception.exit_code, 50)
        self.assertEqual(context.exception.details["blocked_keyword"], "INSERT")
        self.assertIn("INSERT", context.exception.details["sql_query"])

    def test_run_local_query_allows_mutating_sql_with_opt_in(self) -> None:
        """@notice Permit writes when the backend explicitly opts into mutating statements."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse.duckdb"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute("CREATE TABLE demo (id INTEGER, label VARCHAR)")
            finally:
                connection.close()

            run_local_query(
                db_path,
                "INSERT INTO demo VALUES (1, 'one')",
                row_limit=0,
                allow_write=True,
            )
            result = run_local_query(db_path, "SELECT id, label FROM demo ORDER BY id", row_limit=10)

        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.rows[0]["id"], 1)
        self.assertEqual(result.rows[0]["label"], "one")

    def test_run_local_query_explain_returns_structured_plan_payload(self) -> None:
        """@notice Prefix the query with EXPLAIN and return the plan rows in a JSON-friendly field."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse" / "anac.duckdb"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute("CREATE TABLE demo (id INTEGER, label VARCHAR)")
                connection.execute("INSERT INTO demo VALUES (1, 'one'), (2, 'two')")
            finally:
                connection.close()

            result = run_local_query(
                db_path,
                "SELECT id, label FROM demo ORDER BY id",
                explain=True,
                output_format="json",
            )

        self.assertEqual(result.sql_query, "SELECT id, label FROM demo ORDER BY id")
        self.assertEqual(result.rows, [])
        self.assertGreater(result.row_count, 0)
        self.assertEqual(result.column_names, ["explain_key", "explain_value"])
        self.assertIsNotNone(result.plan)
        self.assertTrue(any(row["explain_key"] == "physical_plan" for row in result.plan))
        self.assertIsNone(result.output_path)

    def test_run_local_query_exports_csv_output_to_file(self) -> None:
        """@notice Stream query results directly to CSV output when the backend is asked for file routing."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "warehouse" / "anac.duckdb"
            csv_path = temp_path / "exports" / "demo.csv"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute("CREATE TABLE demo (id INTEGER, label VARCHAR)")
                connection.execute("INSERT INTO demo VALUES (1, 'one'), (2, 'two')")
            finally:
                connection.close()

            result = run_local_query(
                db_path,
                "SELECT id, label FROM demo ORDER BY id",
                output_format="csv",
                output_path=csv_path,
                row_limit=10,
            )

            rendered = csv_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertTrue(csv_path.exists())
            self.assertEqual(rendered, ["id,label", "1,one", "2,two"])
            self.assertEqual(result.row_count, 2)
            self.assertEqual(result.column_names, ["id", "label"])
            self.assertEqual(result.rows, [])
            self.assertEqual(result.output_path, str(csv_path))
            self.assertIsNone(result.plan)

    def test_run_local_query_translates_missing_relations_into_recovery_payload(self) -> None:
        """@notice Surface unknown DuckDB relations as the shared UNKNOWN_RELATION command error."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse.duckdb"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute("CREATE TABLE demo (id INTEGER, label VARCHAR)")
            finally:
                connection.close()

            with self.assertRaises(CliCommandError) as context:
                run_local_query(db_path, "SELECT * FROM missing_relation")

        self.assertEqual(context.exception.code, "UNKNOWN_RELATION")
        self.assertEqual(context.exception.exit_code, 52)
        self.assertEqual(context.exception.details["relation"], "missing_relation")
        self.assertIn("demo", context.exception.details["available_dataset_views"])
        self.assertIn("anac_datasets", context.exception.details["available_metadata_views"])
        self.assertEqual(context.exception.details["db_path"], str(db_path))
        self.assertEqual(context.exception.details["sql_query"], "SELECT * FROM missing_relation")

    def test_run_local_query_translates_timeout_into_query_error(self) -> None:
        """@notice Surface timed-out queries as the shared QUERY_ERROR with timeout context."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse" / "anac.duckdb"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute("CREATE TABLE demo (id INTEGER)")
            finally:
                connection.close()

            with patch(
                "anac_explorator.loader._execute_query_operation_with_timeout",
                side_effect=TimeoutError("Query exceeded timeout of 1 seconds."),
            ):
                with self.assertRaises(CliCommandError) as context:
                    run_local_query(db_path, "SELECT * FROM demo", timeout_seconds=1)

        self.assertEqual(context.exception.code, "QUERY_ERROR")
        self.assertEqual(context.exception.details["timeout_seconds"], 1)
        self.assertIn("timeout", str(context.exception).casefold())


if __name__ == "__main__":
    unittest.main()
