"""@notice Tests for the command-line facade."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import duckdb

from anac_explorator.cli import main


class CliTests(unittest.TestCase):
    """@notice Verify that the CLI emits machine-readable payloads."""

    def test_inspect_csv_schema_prints_json(self) -> None:
        """@notice Emit a schema mapping for a semicolon-delimited CSV file."""

        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".csv") as handle:
            handle.write("cig;importo;data_pubblicazione\n")
            handle.write("0001;100.50;2026-01-31\n")
            handle.write("0002;;2026-02-28\n")
            handle.flush()

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["inspect-csv-schema", handle.name])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["rows_sampled"], 2)
        self.assertEqual(payload["columns"][1]["inferred_type"], "decimal")
        self.assertTrue(payload["columns"][1]["nullable"])

    def test_download_dataset_csv_parser_accepts_dataset_slug(self) -> None:
        """@notice Parse the generic dataset-download subcommand without execution."""

        parser = main.__globals__["build_parser"]()
        args = parser.parse_args(["download-dataset-csv", "bandi-cig-tipo-scelta-contraente"])

        self.assertEqual(args.dataset_id, "bandi-cig-tipo-scelta-contraente")

    def test_build_data_dictionary_parser_uses_default_artifacts(self) -> None:
        """@notice Parse the data-dictionary subcommand with its default artifact paths."""

        parser = main.__globals__["build_parser"]()
        args = parser.parse_args(["build-data-dictionary"])

        self.assertEqual(args.schema_path, "schemas/cig_2025_01.schema.json")
        self.assertEqual(args.vocabulary_index_path, "vocabularies/index.json")

    def test_download_dataset_resource_parser_accepts_json_format(self) -> None:
        """@notice Parse the generic resource-download subcommand with explicit JSON format."""

        parser = main.__globals__["build_parser"]()
        args = parser.parse_args(["download-dataset-resource", "demo", "--resource-format", "json"])

        self.assertEqual(args.dataset_id, "demo")
        self.assertEqual(args.resource_format, "json")

    def test_load_downloaded_resource_parser_uses_default_warehouse_dir(self) -> None:
        """@notice Parse the warehouse-loader subcommand with its default storage location."""

        parser = main.__globals__["build_parser"]()
        args = parser.parse_args(["load-downloaded-resource", "data/raw/demo/manifest.json"])

        self.assertEqual(args.manifest_path, "data/raw/demo/manifest.json")
        self.assertEqual(args.warehouse_dir, "data/warehouse")

    def test_download_dataset_to_parquet_parser_uses_defaults(self) -> None:
        """@notice Parse the direct-to-Parquet download command with its default warehouse settings."""

        parser = main.__globals__["build_parser"]()
        args = parser.parse_args(["download-dataset-to-parquet", "demo-dataset"])

        self.assertEqual(args.dataset_id, "demo-dataset")
        self.assertEqual(args.output_dir, "data/raw")
        self.assertEqual(args.schemas_dir, "schemas")
        self.assertEqual(args.warehouse_dir, "data/warehouse")
        self.assertFalse(args.keep_materialized)
        self.assertFalse(args.skip_crosswalks)

    def test_sync_cig_periods_parser_uses_incremental_defaults(self) -> None:
        """@notice Parse the incremental CIG sync command with its default update behavior."""

        parser = main.__globals__["build_parser"]()
        args = parser.parse_args(["sync-cig-periods", "cig-2025"])

        self.assertEqual(args.dataset_id, "cig-2025")
        self.assertEqual(args.output_dir, "data/raw")
        self.assertEqual(args.schemas_dir, "schemas")
        self.assertEqual(args.warehouse_dir, "data/warehouse")
        self.assertEqual(args.period, [])
        self.assertFalse(args.refresh_changed)
        self.assertFalse(args.keep_materialized)
        self.assertFalse(args.skip_crosswalks)

    def test_parse_resource_prints_structured_csv_payload(self) -> None:
        """@notice Emit a parsed CSV payload from the new Phase 2 parser surface."""

        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".csv") as handle:
            handle.write("cig;importo\n0001;100.50\n")
            handle.flush()

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["parse-resource", handle.name, "--format", "csv", "--record-limit", "1"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["rows"][0]["values"]["cig"], "0001")

    def test_clean_resource_applies_schema_coercion(self) -> None:
        """@notice Emit cleaned CSV payloads using a schema artifact for type hints."""

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = f"{temp_dir}/demo.csv"
            schema_path = f"{temp_dir}/demo.schema.json"
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write("flag_prevalente;importo\n1;100.50\n")
            with open(schema_path, "w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "source_path": csv_path,
                            "delimiter": ";",
                            "encoding": "utf-8-sig",
                            "rows_sampled": 1,
                            "row_length_mismatches": 0,
                            "columns": [
                                {"name": "flag_prevalente", "inferred_type": "boolean", "nullable": False, "non_empty_samples": ["1"]},
                                {"name": "importo", "inferred_type": "decimal", "nullable": False, "non_empty_samples": ["100.50"]},
                            ],
                        }
                    )
                )

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["clean-resource", csv_path, "--format", "csv", "--schema-path", schema_path])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["cleaned_records"][0]["cleaned_values"]["flag_prevalente"])
        self.assertEqual(payload["cleaned_records"][0]["cleaned_values"]["importo"], "100.50")

    def test_module_invocation_executes_main(self) -> None:
        """@notice Support `python -m anac_explorator.cli` as a direct CLI entrypoint."""

        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".csv") as handle:
            handle.write("cig;importo\n0001;100.50\n")
            handle.flush()

            env = os.environ.copy()
            existing_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = str(repo_root / "src") if not existing_pythonpath else f"{repo_root / 'src'}:{existing_pythonpath}"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "anac_explorator.cli",
                    "parse-resource",
                    handle.name,
                    "--format",
                    "csv",
                    "--record-limit",
                    "1",
                ],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["rows"][0]["values"]["cig"], "0001")

    def test_query_local_data_prints_json_rows(self) -> None:
        """@notice Emit JSON-friendly rows from the local DuckDB warehouse query facade."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse.duckdb"
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute("CREATE TABLE demo (id INTEGER, label VARCHAR)")
                connection.execute("INSERT INTO demo VALUES (1, 'one'), (2, 'two')")
            finally:
                connection.close()

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "query-local-data",
                        "SELECT id, label FROM demo ORDER BY id",
                        "--db-path",
                        str(db_path),
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["row_count"], 2)
        self.assertEqual(payload["rows"][0]["label"], "one")
