"""@notice Tests for the command-line facade."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import duckdb

from anac_explorator.ckan import CkanClientError
from anac_explorator.cli import main
from anac_explorator.models import (
    CkanPackage,
    CkanResource,
    DictionaryRefreshResult,
    DownloadManifest,
    DropPlan,
    DropPlanTarget,
    SchemaColumn,
    SchemaMapping,
    UpdateCommandResult,
    VocabularyRefreshResult,
)
from anac_explorator.paths import apply_effective_paths


class CliTests(unittest.TestCase):
    """@notice Verify that the CLI emits machine-readable payloads."""

    def test_build_parser_uses_anacx_prog(self) -> None:
        """@notice Surface the renamed top-level executable in argparse help output."""

        parser = main.__globals__["build_parser"]()

        self.assertEqual(parser.prog, "anacx")

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
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "inspect-csv-schema")
        self.assertEqual(payload["data"]["rows_sampled"], 2)
        self.assertEqual(payload["data"]["columns"][1]["inferred_type"], "decimal")
        self.assertTrue(payload["data"]["columns"][1]["nullable"])
        self.assertEqual(payload["warnings"], [])
        self.assertIn("generated_at", payload["meta"])
        self.assertIn("elapsed_ms", payload["meta"])
        self.assertEqual(payload["meta"]["paths"]["raw_dir"], "data/raw")
        self.assertEqual(payload["meta"]["paths"]["warehouse_dir"], "data/warehouse")

    def test_download_dataset_csv_parser_accepts_dataset_slug(self) -> None:
        """@notice Parse the generic dataset-download subcommand without execution."""

        parser = main.__globals__["build_parser"]()
        args = parser.parse_args(["download-dataset-csv", "bandi-cig-tipo-scelta-contraente"])

        self.assertEqual(args.dataset_id, "bandi-cig-tipo-scelta-contraente")

    def test_datasets_parser_accepts_filters_and_uses_default_db_path(self) -> None:
        """@notice Parse the new datasets surface with its discovery filters and shared DB path."""

        parser = main.__globals__["build_parser"]()
        args = apply_effective_paths(
            parser.parse_args(
                [
                    "datasets",
                    "cig",
                    "--search",
                    "pnrr",
                    "--year",
                    "2025",
                    "--downloaded",
                    "--source-format",
                    "csv",
                    "--format",
                    "table",
                ]
            )
        )

        self.assertEqual(args.dataset, "cig")
        self.assertEqual(args.search, "pnrr")
        self.assertEqual(args.year, 2025)
        self.assertTrue(args.downloaded)
        self.assertEqual(args.source_format, "csv")
        self.assertEqual(args.output_format, "table")
        self.assertEqual(args.db_path, "data/warehouse/anac.duckdb")

    def test_download_parser_accepts_temporal_flags_and_uses_shared_paths(self) -> None:
        """@notice Parse the Phase 3 download command with its temporal and storage options."""

        parser = main.__globals__["build_parser"]()
        args = apply_effective_paths(
            parser.parse_args(
                [
                    "download",
                    "cig",
                    "--year",
                    "2025",
                    "--month",
                    "1",
                    "--source-format",
                    "csv",
                    "--output-format",
                    "both",
                    "--format",
                    "table",
                ]
            )
        )

        self.assertEqual(args.dataset, "cig")
        self.assertEqual(args.year, "2025")
        self.assertEqual(args.month, "1")
        self.assertEqual(args.source_format, "csv")
        self.assertEqual(args.download_output_format, "both")
        self.assertEqual(args.output_format, "table")
        self.assertEqual(args.output_dir, "data/raw")
        self.assertEqual(args.schemas_dir, "schemas")
        self.assertEqual(args.warehouse_dir, "data/warehouse")

    def test_schema_parser_accepts_temporal_flags_and_uses_shared_paths(self) -> None:
        """@notice Parse the Phase 3 schema command with target-selection and metadata-path options."""

        parser = main.__globals__["build_parser"]()
        args = apply_effective_paths(
            parser.parse_args(
                [
                    "schema",
                    "cig",
                    "--year",
                    "2025",
                    "--month",
                    "1",
                    "--describe",
                    "--format",
                    "table",
                ]
            )
        )

        self.assertEqual(args.dataset, "cig")
        self.assertEqual(args.year, "2025")
        self.assertEqual(args.month, "1")
        self.assertTrue(args.describe)
        self.assertEqual(args.output_format, "table")
        self.assertEqual(args.db_path, "data/warehouse/anac.duckdb")
        self.assertEqual(args.schemas_dir, "schemas")
        self.assertEqual(args.dictionaries_dir, "dictionaries")

    def test_schema_parser_accepts_diff_operands(self) -> None:
        """@notice Parse the schema diff mode with its two explicit target operands."""

        parser = main.__globals__["build_parser"]()
        args = parser.parse_args(["schema", "cig", "--diff", "2007-01", "2025-01"])

        self.assertEqual(args.dataset, "cig")
        self.assertEqual(args.diff, ["2007-01", "2025-01"])
        self.assertFalse(args.ddl)
        self.assertFalse(args.describe)

    def test_schema_parser_rejects_conflicting_modes(self) -> None:
        """@notice Keep schema inspection modes mutually exclusive at parse time."""

        parser = main.__globals__["build_parser"]()

        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as describe_vs_ddl:
                parser.parse_args(["schema", "cig", "--describe", "--ddl"])
            with self.assertRaises(SystemExit) as describe_vs_diff:
                parser.parse_args(["schema", "cig", "--describe", "--diff", "2007-01", "2025-01"])

        self.assertEqual(describe_vs_ddl.exception.code, 2)
        self.assertEqual(describe_vs_diff.exception.code, 2)

    def test_query_parser_accepts_output_and_safety_flags(self) -> None:
        """@notice Parse the Phase 3 query command with its routing and write-safety options."""

        parser = main.__globals__["build_parser"]()
        args = apply_effective_paths(
            parser.parse_args(
                [
                    "query",
                    "SELECT * FROM anac_datasets",
                    "--timeout",
                    "15",
                    "--format",
                    "csv",
                    "--output",
                    "out.csv",
                    "--allow-write",
                ]
            )
        )

        self.assertEqual(args.sql_query, "SELECT * FROM anac_datasets")
        self.assertEqual(args.query_timeout, 15)
        self.assertEqual(args.output_format, "csv")
        self.assertEqual(args.output, "out.csv")
        self.assertTrue(args.allow_write)
        self.assertEqual(args.db_path, "data/warehouse/anac.duckdb")

    def test_stats_parser_accepts_dataset_scope_flags_and_uses_default_db_path(self) -> None:
        """@notice Parse the Phase 3 stats command with optional dataset inspection flags."""

        parser = main.__globals__["build_parser"]()
        args = apply_effective_paths(
            parser.parse_args(
                [
                    "stats",
                    "cig",
                    "--year",
                    "2025",
                    "--month",
                    "1",
                    "--profile",
                    "--partitions",
                    "--format",
                    "table",
                ]
            )
        )

        self.assertEqual(args.dataset, "cig")
        self.assertEqual(args.year, "2025")
        self.assertEqual(args.month, "1")
        self.assertTrue(args.profile)
        self.assertTrue(args.partitions)
        self.assertEqual(args.output_format, "table")
        self.assertEqual(args.db_path, "data/warehouse/anac.duckdb")

    def test_update_parser_accepts_optional_dataset_flags_and_uses_shared_paths(self) -> None:
        """@notice Parse the Phase 3 update command with dataset scope and execution flags."""

        parser = main.__globals__["build_parser"]()
        args = apply_effective_paths(
            parser.parse_args(
                [
                    "update",
                    "cig",
                    "--year",
                    "2025",
                    "--month",
                    "1",
                    "--dry-run",
                    "--refresh-changed",
                    "--force-full",
                    "--validate",
                    "--format",
                    "table",
                ]
            )
        )

        self.assertEqual(args.dataset, "cig")
        self.assertEqual(args.year, "2025")
        self.assertEqual(args.month, "1")
        self.assertTrue(args.dry_run)
        self.assertTrue(args.refresh_changed)
        self.assertTrue(args.force_full)
        self.assertTrue(args.validate)
        self.assertEqual(args.output_format, "table")
        self.assertEqual(args.output_dir, "data/raw")
        self.assertEqual(args.schemas_dir, "schemas")
        self.assertEqual(args.dictionaries_dir, "dictionaries")
        self.assertEqual(args.warehouse_dir, "data/warehouse")

    def test_update_parser_accepts_global_mode_without_dataset(self) -> None:
        """@notice Parse the Phase 3 update command in global mode when no dataset is provided."""

        parser = main.__globals__["build_parser"]()
        args = apply_effective_paths(parser.parse_args(["update", "--dry-run"]))

        self.assertIsNone(args.dataset)
        self.assertTrue(args.dry_run)
        self.assertFalse(args.refresh_changed)
        self.assertFalse(args.validate)
        self.assertEqual(args.output_dir, "data/raw")
        self.assertEqual(args.schemas_dir, "schemas")
        self.assertEqual(args.dictionaries_dir, "dictionaries")
        self.assertEqual(args.warehouse_dir, "data/warehouse")

    def test_drop_parser_accepts_temporal_layer_and_dry_run_flags(self) -> None:
        """@notice Parse the Phase 3 drop command with shared temporal scope and layer filtering."""

        parser = main.__globals__["build_parser"]()
        args = parser.parse_args(
            [
                "drop",
                "cig",
                "--year",
                "2025",
                "--month",
                "1",
                "--layer",
                "raw",
                "--resource-id",
                "csv-01,cig_csv_2025_01",
                "--dry-run",
                "--yes",
                "--format",
                "table",
            ]
        )

        self.assertEqual(args.command, "drop")
        self.assertEqual(args.dataset, "cig")
        self.assertEqual(args.year, "2025")
        self.assertEqual(args.month, "1")
        self.assertEqual(args.layer, "raw")
        self.assertEqual(args.resource_ids, ["csv-01,cig_csv_2025_01"])
        self.assertTrue(args.dry_run)
        self.assertTrue(args.yes)
        self.assertEqual(args.output_format, "table")

    def test_config_parser_accepts_show_subcommand_and_output_format(self) -> None:
        """@notice Parse the Phase 3 config show surface with its dedicated output options."""

        parser = main.__globals__["build_parser"]()
        args = parser.parse_args(["config", "show", "--format", "yaml"])

        self.assertEqual(args.command, "config")
        self.assertEqual(args.config_subcommand, "show")
        self.assertEqual(args.output_format, "yaml")

    def test_build_data_dictionary_parser_uses_default_artifacts(self) -> None:
        """@notice Parse the data-dictionary subcommand with its default artifact paths."""

        parser = main.__globals__["build_parser"]()
        args = apply_effective_paths(parser.parse_args(["build-data-dictionary"]))

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
        args = apply_effective_paths(parser.parse_args(["load-downloaded-resource", "data/raw/demo/manifest.json"]))

        self.assertEqual(args.manifest_path, "data/raw/demo/manifest.json")
        self.assertEqual(args.warehouse_dir, "data/warehouse")

    def test_download_dataset_to_parquet_parser_uses_defaults(self) -> None:
        """@notice Parse the direct-to-Parquet download command with its default warehouse settings."""

        parser = main.__globals__["build_parser"]()
        args = apply_effective_paths(parser.parse_args(["download-dataset-to-parquet", "demo-dataset"]))

        self.assertEqual(args.dataset_id, "demo-dataset")
        self.assertEqual(args.output_dir, "data/raw")
        self.assertEqual(args.schemas_dir, "schemas")
        self.assertEqual(args.warehouse_dir, "data/warehouse")
        self.assertFalse(args.keep_materialized)
        self.assertFalse(args.skip_crosswalks)

    def test_sync_cig_periods_parser_uses_incremental_defaults(self) -> None:
        """@notice Parse the incremental CIG sync command with its default update behavior."""

        parser = main.__globals__["build_parser"]()
        args = apply_effective_paths(parser.parse_args(["sync-cig-periods", "cig-2025"]))

        self.assertEqual(args.dataset_id, "cig-2025")
        self.assertEqual(args.output_dir, "data/raw")
        self.assertEqual(args.schemas_dir, "schemas")
        self.assertEqual(args.warehouse_dir, "data/warehouse")
        self.assertEqual(args.period, [])
        self.assertFalse(args.refresh_changed)
        self.assertFalse(args.keep_materialized)
        self.assertFalse(args.skip_crosswalks)

    def test_download_cig_sample_routes_through_shared_temporal_parser(self) -> None:
        """@notice Normalize the year/month request via the shared slice parser before download."""

        sample = Mock()
        sample.to_dict.return_value = {"dataset_id": "cig-2025", "resource_name": "cig_csv_2025_02"}

        output = io.StringIO()
        with patch("anac_explorator.cli.download_cig_monthly_sample", return_value=sample) as download_mock:
            with redirect_stdout(output):
                exit_code = main(["download-cig-sample", "--year", "2025", "--month", "2"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        download_mock.assert_called_once()
        self.assertEqual(download_mock.call_args.kwargs["year"], 2025)
        self.assertEqual(download_mock.call_args.kwargs["month"], 2)

    def test_download_dataset_to_parquet_uses_family_registry_when_dataset_is_registered(self) -> None:
        """@notice Route known registered datasets through the shared family-adapter dispatch."""

        family = main.__globals__["DATASET_FAMILY_REGISTRY"].get_family("cig")
        load_result = Mock()
        load_result.to_dict.return_value = {"dataset_id": "cig-2025", "view_name": "cig"}

        output = io.StringIO()
        with patch.object(
            main.__globals__["DATASET_FAMILY_REGISTRY"],
            "resolve_family_for_dataset_id",
            return_value=family,
        ):
            with patch.object(
                main.__globals__["DATASET_FAMILY_REGISTRY"],
                "download_to_parquet",
                return_value=load_result,
            ) as download_mock:
                with redirect_stdout(output):
                    exit_code = main(["download-dataset-to-parquet", "cig-2025"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        download_mock.assert_called_once()
        self.assertEqual(download_mock.call_args.args[0], "cig")
        self.assertEqual(download_mock.call_args.kwargs["dataset_id"], "cig-2025")

    def test_sync_cig_periods_uses_family_registry_update_dispatch(self) -> None:
        """@notice Route the legacy CIG sync shim through the shared family-update adapter."""

        update_result = Mock()
        update_result.to_dict.return_value = {
            "dataset_type": "cig",
            "dataset_id": "cig-2025",
            "selection_mode": "forward",
            "requested_periods": [],
            "plan": [],
            "applied_loads": [],
            "period_manifest": [],
            "duckdb_path": "data/warehouse/anac.duckdb",
        }

        output = io.StringIO()
        with patch.object(
            main.__globals__["DATASET_FAMILY_REGISTRY"],
            "update",
            return_value=update_result,
        ) as update_mock:
            with redirect_stdout(output):
                exit_code = main(["sync-cig-periods", "cig-2025"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        update_mock.assert_called_once()
        self.assertEqual(update_mock.call_args.args[0], "cig")
        self.assertEqual(update_mock.call_args.kwargs["dataset_id"], "cig-2025")

    def test_datasets_detail_prints_json_envelope_and_remote_warning(self) -> None:
        """@notice Emit detail-mode JSON with the shared warning envelope when live CKAN refresh fails."""

        with tempfile.TemporaryDirectory() as temp_dir:
            self._write_manifest(
                Path(temp_dir),
                DownloadManifest(
                    dataset_id="cig-2025",
                    resource_id="demo-id",
                    resource_name="cig_csv_2025_01",
                    resource_format="csv",
                    resource_url="https://example.invalid/cig_csv_2025_01.zip",
                    transport="playwright",
                    archive_path="data/raw/cig-2025/cig_csv_2025_01/cig_csv_2025_01.zip",
                    materialized_path="data/raw/cig-2025/cig_csv_2025_01/extracted/cig_csv_2025_01.csv",
                    materialized_kind="csv",
                    cache_status="fresh",
                    resume_supported=False,
                    downloaded_at="2026-05-19T12:00:00",
                ),
            )
            mock_client = Mock()
            mock_client.package_show.side_effect = CkanClientError("blocked or filtered by WAF")

            output = io.StringIO()
            previous_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                with patch("anac_explorator.cli.CkanClient", return_value=mock_client):
                    with redirect_stdout(output):
                        exit_code = main(["datasets", "cig"])
            finally:
                os.chdir(previous_cwd)

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "datasets")
        self.assertEqual(payload["data"]["dataset"], "cig")
        self.assertEqual(payload["data"]["local_status"], "raw")
        self.assertEqual(payload["warnings"][0]["code"], "REMOTE_METADATA_UNAVAILABLE")
        self.assertEqual(payload["meta"]["paths"]["warehouse_db_path"], "data/warehouse/anac.duckdb")

    def test_datasets_list_table_output_renders_filtered_catalog(self) -> None:
        """@notice Render list mode through the shared table output path."""

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["datasets", "--search", "smart", "--format", "table"])

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("smartcig", rendered)
        self.assertIn("summary", rendered)
        self.assertIn("items", rendered)

    def test_datasets_unknown_family_emits_stable_error(self) -> None:
        """@notice Return DATASET_NOT_FOUND when detail mode targets an unknown family."""

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["datasets", "unknown-family"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 10)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "DATASET_NOT_FOUND")

    def test_download_dry_run_returns_monthly_cig_plan_without_execution(self) -> None:
        """@notice Emit the normalized download plan for one monthly CIG dry-run."""

        mock_client = Mock()
        mock_client.package_show.return_value = self._build_cig_package(
            resources=[
                CkanResource(
                    id="cig-csv-01",
                    name="cig_csv_2025_01",
                    format="CSV",
                    url="https://example.invalid/cig_2025_01.csv",
                )
            ]
        )

        output = io.StringIO()
        with patch("anac_explorator.cli.CkanClient", return_value=mock_client):
            with patch("anac_explorator.cli.execute_download_plan") as execute_mock:
                with redirect_stdout(output):
                    exit_code = main(["download", "cig", "--year", "2025", "--month", "1", "--dry-run"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "download")
        self.assertEqual(payload["data"]["requested_selection"]["dataset"], "cig")
        self.assertEqual(payload["data"]["requested_selection"]["output_format"], "parquet")
        self.assertTrue(payload["data"]["requested_selection"]["dry_run"])
        self.assertEqual(payload["data"]["requested_selection"]["selection_mode"], "range")
        self.assertEqual(payload["data"]["requested_selection"]["requested_slices"], ["2025-01"])
        self.assertEqual(payload["data"]["resolved_plan"]["resolved_dataset_ids"], ["cig-2025"])
        self.assertEqual(payload["data"]["resolved_plan"]["resolved_resource_names"], ["cig_csv_2025_01"])
        self.assertEqual(payload["data"]["resolved_plan"]["plan"][0]["action"], "download_and_load")
        self.assertEqual(payload["data"]["applied_actions"], [])
        self.assertIsNone(payload["data"]["validation_result"])
        execute_mock.assert_not_called()

    def test_download_source_format_unavailable_emits_stable_error(self) -> None:
        """@notice Return DATASET_NOT_SUPPORTED when the requested source format is unavailable."""

        mock_client = Mock()
        mock_client.package_show.return_value = self._build_cig_package(
            resources=[
                CkanResource(
                    id="cig-csv-01",
                    name="cig_csv_2025_01",
                    format="CSV",
                    url="https://example.invalid/cig_2025_01.csv",
                )
            ]
        )

        output = io.StringIO()
        with patch("anac_explorator.cli.CkanClient", return_value=mock_client):
            with redirect_stdout(output):
                exit_code = main(["download", "cig", "--year", "2025", "--month", "1", "--source-format", "json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 11)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "download")
        self.assertEqual(payload["error"]["code"], "DATASET_NOT_SUPPORTED")
        self.assertEqual(payload["error"]["details"]["dataset"], "cig")
        self.assertEqual(payload["error"]["details"]["source_format"], "json")

    def test_schema_canonical_output_prints_shared_json_envelope(self) -> None:
        """@notice Emit the canonical artifact-driven schema result through the shared Phase 3 envelope."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self._write_schema_artifact(
                temp_path / "schemas" / "cig_2025_01.schema.json",
                SchemaMapping(
                    source_path="data/raw/cig-2025/cig_csv_2025_01/extracted/cig.csv",
                    delimiter=";",
                    encoding="utf-8-sig",
                    rows_sampled=2,
                    row_length_mismatches=0,
                    columns=[
                        SchemaColumn(name="cig", inferred_type="text", nullable=False, non_empty_samples=["0001"]),
                        SchemaColumn(name="importo", inferred_type="decimal", nullable=True, non_empty_samples=["10.50"]),
                    ],
                ),
            )

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["schema", "cig", "--schemas-dir", str(temp_path / "schemas")])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "schema")
        self.assertEqual(payload["data"]["dataset"], "cig")
        self.assertEqual(payload["data"]["mode"], "canonical")
        self.assertIsNone(payload["data"]["target"])
        self.assertEqual([column["name"] for column in payload["data"]["columns"]], ["cig", "importo"])
        self.assertEqual(payload["data"]["columns"][0]["duckdb_type"], "VARCHAR")
        self.assertTrue(payload["data"]["columns"][1]["nullable"])
        self.assertIsNone(payload["data"]["diff"])
        self.assertIsNone(payload["data"]["ddl"])
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["meta"]["paths"]["schemas_dir"], str(temp_path / "schemas"))
        self.assertEqual(payload["meta"]["paths"]["dictionaries_dir"], "dictionaries")

    def test_query_write_requires_allow_write_and_yes(self) -> None:
        """@notice Permit write SQL only when both the opt-in and confirmation flags are present."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse.duckdb"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute("CREATE TABLE demo (id INTEGER, label VARCHAR)")
            finally:
                connection.close()

            blocked_without_allow = io.StringIO()
            with redirect_stdout(blocked_without_allow):
                exit_code_without_allow = main(
                    ["query", "INSERT INTO demo VALUES (1, 'one')", "--db-path", str(db_path)]
                )

            blocked_without_yes = io.StringIO()
            with redirect_stdout(blocked_without_yes):
                exit_code_without_yes = main(
                    [
                        "query",
                        "INSERT INTO demo VALUES (1, 'one')",
                        "--db-path",
                        str(db_path),
                        "--allow-write",
                    ]
                )

            allowed_output = io.StringIO()
            with redirect_stdout(allowed_output):
                exit_code_allowed = main(
                    [
                        "query",
                        "INSERT INTO demo VALUES (1, 'one')",
                        "--db-path",
                        str(db_path),
                        "--allow-write",
                        "--yes",
                    ]
                )

            verify_output = io.StringIO()
            with redirect_stdout(verify_output):
                verify_exit_code = main(
                    [
                        "query",
                        "SELECT id, label FROM demo ORDER BY id",
                        "--db-path",
                        str(db_path),
                    ]
                )

        blocked_without_allow_payload = json.loads(blocked_without_allow.getvalue())
        blocked_without_yes_payload = json.loads(blocked_without_yes.getvalue())
        allowed_payload = json.loads(allowed_output.getvalue())
        verify_payload = json.loads(verify_output.getvalue())

        self.assertEqual(exit_code_without_allow, 50)
        self.assertFalse(blocked_without_allow_payload["ok"])
        self.assertEqual(blocked_without_allow_payload["command"], "query")
        self.assertEqual(blocked_without_allow_payload["error"]["code"], "WRITE_QUERY_BLOCKED")

        self.assertEqual(exit_code_without_yes, 50)
        self.assertFalse(blocked_without_yes_payload["ok"])
        self.assertEqual(blocked_without_yes_payload["error"]["code"], "WRITE_QUERY_BLOCKED")
        self.assertTrue(blocked_without_yes_payload["error"]["details"]["confirmation_required"])

        self.assertEqual(exit_code_allowed, 0)
        self.assertTrue(allowed_payload["ok"])
        self.assertEqual(allowed_payload["command"], "query")

        self.assertEqual(verify_exit_code, 0)
        self.assertTrue(verify_payload["ok"])
        self.assertEqual(verify_payload["data"]["row_count"], 1)
        self.assertEqual(verify_payload["data"]["rows"][0]["label"], "one")

    def test_query_metadata_view_query_succeeds_via_cli(self) -> None:
        """@notice Route a metadata-view select through the new Phase 3 query surface."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse.duckdb"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            duckdb.connect(str(db_path)).close()

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "query",
                        "SELECT dataset, update_supported FROM anac_datasets ORDER BY dataset",
                        "--db-path",
                        str(db_path),
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "query")
        self.assertGreaterEqual(payload["data"]["row_count"], 3)
        self.assertEqual(payload["data"]["rows"][0]["dataset"], "aggiudicatari")
        self.assertFalse(payload["data"]["rows"][0]["update_supported"])
        self.assertEqual(payload["meta"]["paths"]["warehouse_db_path"], str(db_path))

    def test_update_dry_run_prints_shared_json_envelope(self) -> None:
        """@notice Emit the normalized update dry-run payload through the shared result envelope."""

        output = io.StringIO()
        with patch("anac_explorator.cli.CkanClient", return_value=Mock()):
            with patch(
                "anac_explorator.cli.run_dataset_update",
                return_value=UpdateCommandResult(
                    scope={
                        "dataset": "cig",
                        "selection_mode": "forward",
                        "refresh_changed": False,
                        "validate": False,
                    },
                    latest_local_state={"latest_slice": "2025-01"},
                    plan=[],
                ),
            ) as update_mock:
                with redirect_stdout(output):
                    exit_code = main(["update", "cig", "--dry-run"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "update")
        self.assertEqual(payload["data"]["scope"]["dataset"], "cig")
        self.assertEqual(payload["data"]["scope"]["selection_mode"], "forward")
        self.assertEqual(payload["data"]["latest_local_state"]["latest_slice"], "2025-01")
        self.assertEqual(payload["data"]["plan"], [])
        self.assertEqual(payload["data"]["applied"], [])
        self.assertIsNone(payload["data"]["validation"])
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["meta"]["paths"]["raw_dir"], "data/raw")
        update_mock.assert_called_once()
        self.assertEqual(update_mock.call_args.args[0], "cig")
        self.assertTrue(update_mock.call_args.kwargs["dry_run"])

    def test_update_dataset_execution_surfaces_vocabulary_artifacts_and_uses_effective_dictionary_path(self) -> None:
        """@notice Preserve the shared result envelope for vocabulary updates and forward the resolved dictionary path."""

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            dictionaries_dir = Path(temp_dir) / "custom-dictionaries"
            with patch("anac_explorator.cli.CkanClient", return_value=Mock()):
                with patch(
                    "anac_explorator.cli.run_dataset_update",
                    return_value=UpdateCommandResult(
                        scope={"dataset": "bandi-cig-tipo-scelta-contraente", "selection_mode": "all"},
                        latest_local_state={"artifact_present": True},
                        plan=[],
                        applied=[
                            VocabularyRefreshResult(
                                dataset="bandi-cig-tipo-scelta-contraente",
                                dataset_id="bandi-cig-tipo-scelta-contraente",
                                resource_name="bandi-cig-tipo-scelta-contraente_csv",
                                manifest_path="data/raw/bandi-cig-tipo-scelta-contraente/bandi-cig-tipo-scelta-contraente_csv/manifest.json",
                                download_cache_status="fresh",
                                schema_path="schemas/bandi-cig-tipo-scelta-contraente.schema.json",
                                artifact_path="vocabularies/bandi-cig-tipo-scelta-contraente.json",
                                vocabulary_index_path="vocabularies/index.json",
                                table_count=1,
                            ),
                            DictionaryRefreshResult(
                                dataset="cig",
                                dataset_id="cig-2025",
                                dictionary_name="cig_2025_01",
                                json_path=str(dictionaries_dir / "cig_2025_01.dictionary.json"),
                                markdown_path=str(dictionaries_dir / "cig_2025_01.dictionary.md"),
                                entry_count=3,
                                section_count=2,
                                reason="source_vocabulary_changed",
                                source_dataset="bandi-cig-tipo-scelta-contraente",
                            ),
                        ],
                    ),
                ) as update_mock:
                    with redirect_stdout(output):
                        exit_code = main(
                            [
                                "update",
                                "bandi-cig-tipo-scelta-contraente",
                                "--dictionaries-dir",
                                str(dictionaries_dir),
                            ]
                        )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "update")
        self.assertEqual(payload["data"]["applied"][0]["artifact_type"], "vocabulary_refresh")
        self.assertEqual(payload["data"]["applied"][1]["artifact_type"], "dictionary_refresh")
        self.assertEqual(payload["data"]["applied"][1]["source_dataset"], "bandi-cig-tipo-scelta-contraente")
        self.assertEqual(update_mock.call_args.kwargs["dictionaries_dir"], dictionaries_dir)

    def test_update_temporal_force_full_routes_selection_to_dataset_runner(self) -> None:
        """@notice Normalize shared temporal flags for update and forward force-full to the dataset runner."""

        output = io.StringIO()
        with patch("anac_explorator.cli.CkanClient", return_value=Mock()):
            with patch(
                "anac_explorator.cli.run_dataset_update",
                return_value=UpdateCommandResult(
                    scope={"dataset": "cig", "selection_mode": "range", "force_full": True},
                    latest_local_state={"latest_slice": "2025-01"},
                    plan=[],
                ),
            ) as update_mock:
                with redirect_stdout(output):
                    exit_code = main(["update", "cig", "--year", "2025", "--month", "1", "--force-full", "--dry-run"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "update")
        self.assertTrue(update_mock.call_args.kwargs["force_full"])
        self.assertTrue(update_mock.call_args.kwargs["dry_run"])
        self.assertEqual(update_mock.call_args.kwargs["selection"].mode, "range")
        self.assertEqual(update_mock.call_args.kwargs["selection"].slices, ["2025-01"])

    def test_update_global_temporal_scope_routes_selection_to_orchestrator(self) -> None:
        """@notice Forward shared temporal selection to the global update orchestrator."""

        output = io.StringIO()
        with patch("anac_explorator.cli.CkanClient", return_value=Mock()):
            with patch(
                "anac_explorator.cli.run_global_update",
                return_value=UpdateCommandResult(
                    scope={"mode": "global", "datasets": ["cig"], "selection_mode": "latest"},
                    latest_local_state={"cig": {"latest_slice": "2025-02"}},
                    plan=[],
                ),
            ) as update_mock:
                with redirect_stdout(output):
                    exit_code = main(["update", "--latest", "--dry-run"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "update")
        self.assertEqual(update_mock.call_args.kwargs["selection"].mode, "latest")
        self.assertTrue(update_mock.call_args.kwargs["dry_run"])

    def test_update_without_dataset_routes_to_global_orchestrator(self) -> None:
        """@notice Dispatch the update CLI to global orchestration when no dataset family is provided."""

        output = io.StringIO()
        with patch("anac_explorator.cli.CkanClient", return_value=Mock()):
            with patch(
                "anac_explorator.cli.run_global_update",
                return_value=UpdateCommandResult(
                    scope={"mode": "global", "datasets": ["cig"], "dry_run": True},
                    latest_local_state={"cig": {"latest_slice": "2025-01"}},
                    plan=[],
                ),
            ) as update_mock:
                with redirect_stdout(output):
                    exit_code = main(["update", "--dry-run"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "update")
        self.assertEqual(payload["data"]["scope"]["mode"], "global")
        self.assertEqual(payload["data"]["scope"]["datasets"], ["cig"])
        self.assertEqual(payload["data"]["latest_local_state"]["cig"]["latest_slice"], "2025-01")
        self.assertEqual(payload["data"]["plan"], [])
        update_mock.assert_called_once()
        self.assertTrue(update_mock.call_args.kwargs["dry_run"])

    def test_drop_dry_run_prints_shared_json_envelope(self) -> None:
        """@notice Emit the normalized drop dry-run payload through the shared result envelope."""

        plan = DropPlan(
            dataset="cig",
            scope={"selection_mode": "range", "selected_slices": ["2025-01"]},
            layer="raw",
            targets=[
                DropPlanTarget(
                    path="data/raw/cig-2025/cig_csv_2025_01/manifest.json",
                    layer="raw",
                    size_bytes=128,
                    dataset="cig",
                    dataset_id="cig-2025",
                    slice="2025-01",
                    resource_id="csv-01",
                    resource_name="cig_csv_2025_01",
                    target_kind="manifest",
                )
            ],
        )

        output = io.StringIO()
        with patch.object(
            main.__globals__["DATASET_FAMILY_REGISTRY"],
            "build_drop_plan",
            return_value=plan,
        ) as build_drop_mock, patch.object(
            main.__globals__["DATASET_FAMILY_REGISTRY"],
            "apply_drop_plan",
        ) as apply_drop_mock:
            with redirect_stdout(output):
                exit_code = main(["drop", "cig", "--year", "2025", "--month", "1", "--layer", "raw", "--dry-run"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "drop")
        self.assertEqual(payload["data"]["dataset"], "cig")
        self.assertEqual(payload["data"]["layer"], "raw")
        self.assertEqual(payload["data"]["scope"]["selection_mode"], "range")
        self.assertEqual(payload["data"]["scope"]["selected_slices"], ["2025-01"])
        self.assertEqual(payload["data"]["totals"]["target_count"], 1)
        self.assertEqual(payload["data"]["totals"]["size_bytes"], 128)
        self.assertEqual(payload["data"]["applied"], [])
        self.assertTrue(payload["data"]["dry_run"])
        self.assertEqual(build_drop_mock.call_args.kwargs["scope"].slices, ["2025-01"])
        self.assertEqual(build_drop_mock.call_args.kwargs["layers"], "raw")
        apply_drop_mock.assert_not_called()

    def test_drop_requires_yes_when_not_dry_run(self) -> None:
        """@notice Abort destructive drop execution unless the command is explicitly confirmed."""

        plan = DropPlan(
            dataset="cig",
            scope={"selection_mode": "range", "selected_slices": ["2025-01"]},
            layer="all",
            targets=[],
        )

        output = io.StringIO()
        with patch.object(
            main.__globals__["DATASET_FAMILY_REGISTRY"],
            "build_drop_plan",
            return_value=plan,
        ) as build_drop_mock, patch.object(
            main.__globals__["DATASET_FAMILY_REGISTRY"],
            "apply_drop_plan",
        ) as apply_drop_mock:
            with redirect_stdout(output):
                exit_code = main(["drop", "cig", "--year", "2025"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 42)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "drop")
        self.assertEqual(payload["error"]["code"], "VALIDATION_FAILED")
        self.assertTrue(payload["error"]["details"]["confirmation_required"])
        build_drop_mock.assert_called_once()
        apply_drop_mock.assert_not_called()

    def test_query_uses_configured_timeout_when_flag_is_absent(self) -> None:
        """@notice Fill the query timeout from config when the CLI flag is not provided."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({"query": {"timeout": 9}}), encoding="utf-8")

            mocked_result = Mock()
            mocked_result.to_dict.return_value = {
                "sql": "SELECT 1",
                "row_limit": 1000,
                "row_count": 1,
                "column_names": ["1"],
                "rows": [{"1": 1}],
                "plan": None,
                "output_path": None,
                "db_path": "data/warehouse/anac.duckdb",
                "sql_query": "SELECT 1",
            }

            output = io.StringIO()
            with patch("anac_explorator.cli.run_local_query", return_value=mocked_result) as query_mock:
                with redirect_stdout(output):
                    exit_code = main(["--config", str(config_path), "query", "SELECT 1"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(query_mock.call_args.kwargs["timeout_seconds"], 9)

    def test_stats_partitions_rejects_snapshot_dataset_families(self) -> None:
        """@notice Reject --partitions for snapshot dataset families with the stable shared error envelope."""

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["stats", "aggiudicatari", "--partitions"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 11)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "stats")
        self.assertEqual(payload["error"]["code"], "DATASET_NOT_SUPPORTED")
        self.assertEqual(payload["error"]["details"]["dataset"], "aggiudicatari")
        self.assertEqual(payload["error"]["details"]["coverage_kind"], "snapshot")

    def test_stats_temporal_flags_reject_snapshot_dataset_families(self) -> None:
        """@notice Reject scoped stats on snapshot families through the shared adapter validation."""

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["stats", "aggiudicatari", "--year", "2025"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 11)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "stats")
        self.assertEqual(payload["error"]["code"], "DATASET_NOT_SUPPORTED")
        self.assertEqual(payload["error"]["details"]["dataset"], "aggiudicatari")
        self.assertEqual(payload["error"]["details"]["coverage_kind"], "snapshot")

    def test_validate_local_data_integrity_parser_uses_defaults(self) -> None:
        """@notice Parse the integrity-validation subcommand with its default warehouse artifacts."""

        parser = main.__globals__["build_parser"]()
        args = apply_effective_paths(parser.parse_args(["validate-local-data-integrity"]))

        self.assertEqual(args.db_path, "data/warehouse/anac.duckdb")
        self.assertEqual(args.dataset_type, "cig")
        self.assertEqual(args.schema_path, "schemas/cig_2025_01.schema.json")
        self.assertEqual(args.vocabulary_index_path, "vocabularies/index.json")

    def test_parse_resource_prints_structured_csv_payload(self) -> None:
        """@notice Emit a parsed CSV payload from the new Phase 2 parser surface."""

        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".csv") as handle:
            handle.write("cig;importo\n0001;100.50\n0002;200.00\n")
            handle.flush()

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["parse-resource", handle.name, "--format", "csv", "--record-limit", "1"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["row_count"], 2)
        self.assertEqual(len(payload["data"]["rows"]), 1)
        self.assertEqual(payload["data"]["rows"][0]["values"]["cig"], "0001")
        self.assertTrue(payload["meta"]["truncated"])

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
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["cleaned_records"][0]["cleaned_values"]["flag_prevalente"])
        self.assertEqual(payload["data"]["cleaned_records"][0]["cleaned_values"]["importo"], "100.50")

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
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["row_count"], 1)
        self.assertEqual(payload["data"]["rows"][0]["values"]["cig"], "0001")

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
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["row_count"], 2)
        self.assertEqual(payload["data"]["rows"][0]["label"], "one")
        self.assertEqual(payload["meta"]["paths"]["warehouse_db_path"], str(db_path))

    def test_runtime_failure_prints_error_envelope(self) -> None:
        """@notice Emit the shared JSON error envelope for command-runtime failures."""

        output = io.StringIO()
        missing_db_path = "/tmp/anac-explorator-missing.duckdb"
        with redirect_stdout(output):
            exit_code = main(
                [
                    "query-local-data",
                    "SELECT 1",
                    "--db-path",
                    missing_db_path,
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 30)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "query-local-data")
        self.assertEqual(payload["error"]["code"], "LOCAL_DATASET_NOT_AVAILABLE")
        self.assertEqual(payload["error"]["details"]["path"], missing_db_path)
        self.assertIn(missing_db_path, payload["error"]["message"])
        self.assertEqual(payload["meta"]["paths"]["warehouse_db_path"], missing_db_path)

    def test_parser_failure_remains_usage_error(self) -> None:
        """@notice Keep argparse failures as usage errors instead of JSON runtime envelopes."""

        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer), self.assertRaises(SystemExit) as context:
            main(["query-local-data"])

        self.assertEqual(context.exception.code, 2)
        self.assertEqual(stdout_buffer.getvalue(), "")
        self.assertIn("usage:", stderr_buffer.getvalue())

    def test_query_write_sql_is_blocked_with_stable_error_code(self) -> None:
        """@notice Reject write SQL before DuckDB execution and emit WRITE_QUERY_BLOCKED."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse.duckdb"
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute("CREATE TABLE demo (id INTEGER, label VARCHAR)")
            finally:
                connection.close()

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "query-local-data",
                        "-- this should be blocked\nINSERT INTO demo VALUES (1, 'one')",
                        "--db-path",
                        str(db_path),
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 50)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "WRITE_QUERY_BLOCKED")
        self.assertEqual(payload["error"]["details"]["blocked_keyword"], "INSERT")

    def test_query_unknown_relation_maps_to_structured_error(self) -> None:
        """@notice Surface missing DuckDB relations as UNKNOWN_RELATION with recovery hints."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse.duckdb"
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute("CREATE TABLE demo (id INTEGER, label VARCHAR)")
            finally:
                connection.close()

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "query-local-data",
                        "SELECT * FROM missing_relation",
                        "--db-path",
                        str(db_path),
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 52)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "UNKNOWN_RELATION")
        self.assertEqual(payload["error"]["details"]["relation"], "missing_relation")
        self.assertIn("demo", payload["error"]["details"]["available_dataset_views"])

    def test_query_generic_duckdb_failure_maps_to_query_error(self) -> None:
        """@notice Emit QUERY_ERROR for DuckDB failures that are not missing relations."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse.duckdb"
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute("CREATE TABLE demo (id INTEGER, label VARCHAR)")
            finally:
                connection.close()

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "query-local-data",
                        "SELECT FROM demo",
                        "--db-path",
                        str(db_path),
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 51)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "QUERY_ERROR")
        self.assertEqual(payload["error"]["details"]["sql_query"], "SELECT FROM demo")

    def test_transport_blocked_maps_to_transport_blocked_error(self) -> None:
        """@notice Distinguish blocked transport failures from generic remote errors."""

        output = io.StringIO()
        with patch(
            "anac_explorator.cli._handle_package_show",
            side_effect=CkanClientError(
                "CKAN endpoint returned HTML instead of JSON; remote access appears to be blocked or filtered."
            ),
        ), redirect_stdout(output):
            exit_code = main(["package-show", "demo-dataset"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 21)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "TRANSPORT_BLOCKED")

    def test_network_failure_maps_to_network_error(self) -> None:
        """@notice Distinguish generic network failures from blocked transport errors."""

        output = io.StringIO()
        with patch(
            "anac_explorator.cli._handle_package_show",
            side_effect=CkanClientError("Failed to reach CKAN endpoint: [Errno 111] Connection refused"),
        ), redirect_stdout(output):
            exit_code = main(["package-show", "demo-dataset"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 20)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "NETWORK_ERROR")

    def test_playwright_unavailable_maps_to_specific_error(self) -> None:
        """@notice Preserve a dedicated error code when Playwright support is unavailable."""

        output = io.StringIO()
        with patch(
            "anac_explorator.cli._handle_package_show",
            side_effect=CkanClientError("Playwright is not installed. Install the package and browser runtime first."),
        ), redirect_stdout(output):
            exit_code = main(["package-show", "demo-dataset"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 22)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "PLAYWRIGHT_UNAVAILABLE")

    def test_debug_mode_prints_traceback_to_stderr(self) -> None:
        """@notice Keep raw traceback detail on stderr in debug mode only."""

        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        with patch(
            "anac_explorator.cli._handle_package_show",
            side_effect=CkanClientError("Failed to reach CKAN endpoint: timeout"),
        ), redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            exit_code = main(["--debug", "package-show", "demo-dataset"])

        payload = json.loads(stdout_buffer.getvalue())
        self.assertEqual(exit_code, 20)
        self.assertEqual(payload["error"]["code"], "NETWORK_ERROR")
        self.assertIn("CkanClientError", stderr_buffer.getvalue())

    def test_config_show_reports_effective_values_and_sources(self) -> None:
        """@notice Merge config file plus env values and report their sources."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "transport": {"default": "http"},
                        "paths": {"raw_dir": "config/raw"},
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "ANAC_TRANSPORT": "playwright",
                    "ANAC_EXPLORATOR_TRANSPORT": "http",
                },
                clear=False,
            ), redirect_stdout(output):
                exit_code = main(["--config", str(config_path), "config", "show"])

        payload = json.loads(output.getvalue())
        effective = payload["data"]["config"]["effective"]
        sources = payload["data"]["config"]["sources"]
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "config")
        self.assertEqual(payload["data"]["subcommand"], "show")
        self.assertEqual(effective["transport"]["default"], "playwright")
        self.assertEqual(sources["transport"]["default"], "env:ANAC_TRANSPORT")
        self.assertEqual(effective["paths"]["raw_dir"], "config/raw")
        self.assertEqual(sources["paths"]["raw_dir"], "config_file")

    def test_config_get_returns_key_value_and_source(self) -> None:
        """@notice Return one resolved config value together with its source."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({"query": {"row_limit": 25}}), encoding="utf-8")

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["--config", str(config_path), "config", "get", "query.row_limit"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["data"]["key"], "query.row_limit")
        self.assertEqual(payload["data"]["value"], 25)
        self.assertEqual(payload["data"]["source"], "config_file")

    def test_config_set_and_unset_only_change_config_file(self) -> None:
        """@notice Persist and remove config values through the dedicated config subcommands."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"

            set_output = io.StringIO()
            with redirect_stdout(set_output):
                set_exit_code = main(
                    ["--config", str(config_path), "config", "set", "transport.default", "playwright"]
                )

            unset_output = io.StringIO()
            with redirect_stdout(unset_output):
                unset_exit_code = main(
                    ["--config", str(config_path), "config", "unset", "transport.default"]
                )

            persisted_exists = config_path.exists()

        set_payload = json.loads(set_output.getvalue())
        unset_payload = json.loads(unset_output.getvalue())
        self.assertEqual(set_exit_code, 0)
        self.assertEqual(set_payload["data"]["value"], "playwright")
        self.assertEqual(unset_exit_code, 0)
        self.assertTrue(unset_payload["data"]["value"])
        self.assertFalse(persisted_exists)

    def test_config_reset_requires_yes(self) -> None:
        """@notice Protect config reset behind the shared confirmation flag."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({"query": {"row_limit": 25}}), encoding="utf-8")

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["--config", str(config_path), "config", "reset"])

            exists_after_attempt = config_path.exists()

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 60)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "CONFIG_ERROR")
        self.assertTrue(exists_after_attempt)

    def test_config_reset_with_yes_removes_file(self) -> None:
        """@notice Remove the persisted config file when reset is confirmed."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({"query": {"row_limit": 25}}), encoding="utf-8")

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["--config", str(config_path), "--yes", "config", "reset"])

            exists_after_reset = config_path.exists()

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["data"]["subcommand"], "reset")
        self.assertFalse(exists_after_reset)

    def test_config_validate_returns_all_detected_issues(self) -> None:
        """@notice Report every config validation issue in one validate response."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "transport": {"default": "invalid", "timeout": 0},
                        "paths": {"unknown": "value"},
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["--config", str(config_path), "config", "validate"])

        payload = json.loads(output.getvalue())
        error_keys = {issue["key"] for issue in payload["data"]["validation_errors"]}
        self.assertEqual(exit_code, 0)
        self.assertIn("transport.default", error_keys)
        self.assertIn("transport.timeout", error_keys)
        self.assertIn("paths.unknown", error_keys)

    def test_config_show_yaml_renders_without_json_envelope(self) -> None:
        """@notice Support YAML output for the effective config view without extra dependencies."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["--config", str(config_path), "config", "show", "--format", "yaml"])

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("subcommand: show", rendered)
        self.assertIn("transport:", rendered)

    def test_query_uses_row_limit_from_config_when_cli_flag_is_absent(self) -> None:
        """@notice Apply shared query defaults from config without command-specific merge logic."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({"query": {"row_limit": 1}}), encoding="utf-8")
            db_path = Path(temp_dir) / "warehouse.duckdb"
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute("CREATE TABLE demo (id INTEGER)")
                connection.execute("INSERT INTO demo VALUES (1), (2)")
            finally:
                connection.close()

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--config",
                        str(config_path),
                        "query-local-data",
                        "SELECT id FROM demo ORDER BY id",
                        "--db-path",
                        str(db_path),
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["data"]["row_count"], 1)

    def _write_manifest(self, project_root: Path, manifest: DownloadManifest) -> None:
        """@notice Persist one manifest under the default raw-data layout for datasets tests."""

        manifest_path = project_root / "data" / "raw" / manifest.dataset_id / manifest.resource_name / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

    @staticmethod
    def _write_schema_artifact(path: Path, mapping: SchemaMapping) -> None:
        """@notice Persist one serialized schema artifact for schema CLI integration tests."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(mapping.to_dict()), encoding="utf-8")

    @staticmethod
    def _build_cig_package(*, resources: list[CkanResource]) -> CkanPackage:
        """@notice Build one fake monthly CIG CKAN package for CLI integration tests."""

        return CkanPackage(
            id="cig-2025",
            name="cig-2025",
            title="CIG 2025",
            notes="Monthly CIG package",
            resources=resources,
        )
