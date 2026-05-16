"""@notice Tests for the DuckDB/Parquet loader helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from shutil import copyfile
from unittest.mock import Mock, patch
from zipfile import ZipFile

import duckdb

from anac_explorator.loader import (
    download_dataset_to_parquet,
    load_downloaded_resource,
    register_vocabulary_crosswalks,
    run_local_query,
    sync_cig_periods_to_parquet,
)
from anac_explorator.models import CkanPackage, CkanResource


class LoaderTests(unittest.TestCase):
    """@notice Verify manifest-backed warehouse loading and query registration."""

    def test_load_downloaded_resource_registers_partitioned_cig_view(self) -> None:
        """@notice Load two CIG monthly manifests into Parquet and query the combined DuckDB view."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            schema_path = temp_path / "schemas" / "cig.schema.json"
            schema_path.parent.mkdir(parents=True)
            schema_path.write_text(
                json.dumps(
                    {
                        "source_path": "demo.csv",
                        "delimiter": ";",
                        "encoding": "utf-8-sig",
                        "rows_sampled": 2,
                        "row_length_mismatches": 0,
                        "columns": [
                            {"name": "cig", "inferred_type": "text", "nullable": False, "non_empty_samples": ["0001"]},
                            {"name": "flag_prevalente", "inferred_type": "boolean", "nullable": False, "non_empty_samples": ["1"]},
                            {"name": "importo_lotto", "inferred_type": "decimal", "nullable": False, "non_empty_samples": ["10.50"]},
                            {
                                "name": "data_pubblicazione",
                                "inferred_type": "date",
                                "nullable": False,
                                "non_empty_samples": ["2025-01-24"],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            january_manifest = self._write_manifest_backed_csv(
                temp_path,
                dataset_id="cig-2025",
                resource_name="cig_csv_2025_01",
                body=(
                    "cig;flag_prevalente;importo_lotto;data_pubblicazione\n"
                    "0001;1;10.50;2025-01-24\n"
                    "0002;0;11.75;2025-01-25\n"
                ),
            )
            february_manifest = self._write_manifest_backed_csv(
                temp_path,
                dataset_id="cig-2025",
                resource_name="cig_csv_2025_02",
                body=(
                    "cig;flag_prevalente;importo_lotto;data_pubblicazione\n"
                    "0003;1;9.25;2025-02-05\n"
                ),
            )

            january_result = load_downloaded_resource(
                january_manifest,
                schema_path=schema_path,
                warehouse_dir=temp_path / "warehouse",
            )
            february_result = load_downloaded_resource(
                february_manifest,
                schema_path=schema_path,
                warehouse_dir=temp_path / "warehouse",
            )

            self.assertEqual(january_result.view_name, "cig")
            self.assertEqual(february_result.table_name, "cig")
            self.assertEqual(february_result.registered_parquet_files, 2)
            self.assertEqual(
                [(partition.key, partition.value) for partition in february_result.partition_values],
                [("year", "2025"), ("month", "02")],
            )
            self.assertTrue(Path(january_result.parquet_path).exists())
            self.assertTrue(Path(february_result.parquet_path).exists())

            query_result = run_local_query(
                january_result.duckdb_path,
                (
                    "SELECT COUNT(*) AS total_rows, "
                    "MIN(cig) AS first_cig, "
                    "MAX(CAST(month AS INTEGER)) AS latest_month "
                    "FROM cig"
                ),
            )

            self.assertEqual(query_result.row_count, 1)
            self.assertEqual(query_result.rows[0]["total_rows"], 3)
            self.assertEqual(query_result.rows[0]["first_cig"], "0001")
            self.assertEqual(query_result.rows[0]["latest_month"], 2)

    def test_load_downloaded_resource_fails_on_invalid_typed_projection(self) -> None:
        """@notice Reject a load when the SQL-native projection would coerce invalid typed values."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            manifest_path = self._write_manifest_backed_csv(
                temp_path,
                dataset_id="demo-dataset",
                resource_name="demo_csv",
                body="code;flag_prevalente\n1;maybe\n",
            )
            schema_path = temp_path / "schemas" / "demo.schema.json"
            schema_path.parent.mkdir(parents=True)
            schema_path.write_text(
                json.dumps(
                    {
                        "source_path": "demo.csv",
                        "delimiter": ";",
                        "encoding": "utf-8-sig",
                        "rows_sampled": 1,
                        "row_length_mismatches": 0,
                        "columns": [
                            {"name": "code", "inferred_type": "integer", "nullable": False, "non_empty_samples": ["1"]},
                            {
                                "name": "flag_prevalente",
                                "inferred_type": "boolean",
                                "nullable": False,
                                "non_empty_samples": ["1"],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "flag_prevalente=1"):
                load_downloaded_resource(
                    manifest_path,
                    schema_path=schema_path,
                    warehouse_dir=temp_path / "warehouse",
                )

    def test_load_downloaded_resource_reuses_existing_parquet_without_source_file(self) -> None:
        """@notice Reuse a prior Parquet load for the same manifest instead of requiring the source CSV again."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            manifest_path = self._write_manifest_backed_csv(
                temp_path,
                dataset_id="demo-dataset",
                resource_name="demo_csv",
                body="code;label\n1;ONE\n",
            )
            schema_path = temp_path / "schemas" / "demo.schema.json"
            schema_path.parent.mkdir(parents=True)
            schema_path.write_text(
                json.dumps(
                    {
                        "source_path": "demo.csv",
                        "delimiter": ";",
                        "encoding": "utf-8-sig",
                        "rows_sampled": 1,
                        "row_length_mismatches": 0,
                        "columns": [
                            {"name": "code", "inferred_type": "text", "nullable": False, "non_empty_samples": ["1"]},
                            {"name": "label", "inferred_type": "text", "nullable": False, "non_empty_samples": ["ONE"]},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            first_result = load_downloaded_resource(
                manifest_path,
                schema_path=schema_path,
                warehouse_dir=temp_path / "warehouse",
            )
            Path(json.loads(manifest_path.read_text(encoding="utf-8"))["materialized_path"]).unlink()

            second_result = load_downloaded_resource(
                manifest_path,
                schema_path=schema_path,
                warehouse_dir=temp_path / "warehouse",
            )

            self.assertEqual(first_result.row_count, 1)
            self.assertEqual(second_result.load_status, "cache_hit")
            self.assertTrue(Path(second_result.parquet_path).exists())

    def test_load_downloaded_resource_uses_archive_when_materialized_csv_is_missing(self) -> None:
        """@notice Extract the archive temporarily when a manifest-backed CSV was pruned after download."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            resource_dir = temp_path / "data" / "raw" / "demo-dataset" / "demo_csv"
            resource_dir.mkdir(parents=True)
            archive_path = resource_dir / "demo_csv.zip"
            with ZipFile(archive_path, "w") as archive:
                archive.writestr("inner/demo.csv", "code;label\n1;ONE\n")

            manifest_path = resource_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "dataset_id": "demo-dataset",
                        "resource_id": "demo_csv-id",
                        "resource_name": "demo_csv",
                        "resource_format": "CSV",
                        "resource_url": "https://example.invalid/demo.zip",
                        "transport": "http",
                        "archive_path": str(archive_path),
                        "materialized_path": str(resource_dir / "extracted" / "demo_csv.csv"),
                        "materialized_kind": "csv",
                        "cache_status": "fresh",
                        "resume_supported": True,
                        "source_size": None,
                        "source_last_modified": None,
                        "downloaded_at": "2026-05-06T12:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            schema_path = temp_path / "schemas" / "demo.schema.json"
            schema_path.parent.mkdir(parents=True)
            schema_path.write_text(
                json.dumps(
                    {
                        "source_path": "demo.csv",
                        "delimiter": ";",
                        "encoding": "utf-8-sig",
                        "rows_sampled": 1,
                        "row_length_mismatches": 0,
                        "columns": [
                            {"name": "code", "inferred_type": "text", "nullable": False, "non_empty_samples": ["1"]},
                            {"name": "label", "inferred_type": "text", "nullable": False, "non_empty_samples": ["ONE"]},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = load_downloaded_resource(
                manifest_path,
                schema_path=schema_path,
                warehouse_dir=temp_path / "warehouse",
            )

            self.assertEqual(result.row_count, 1)
            self.assertEqual(result.load_status, "fresh")
            self.assertTrue(Path(result.parquet_path).exists())

    def test_register_vocabulary_crosswalks_exposes_joinable_views(self) -> None:
        """@notice Register normalized vocabulary artifacts as DuckDB views for cross-reference joins."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            duckdb_path = temp_path / "warehouse" / "anac.duckdb"
            duckdb_path.parent.mkdir(parents=True)
            connection = duckdb.connect(str(duckdb_path))
            connection.close()

            vocab_dir = temp_path / "vocabularies"
            vocab_dir.mkdir()
            artifact_path = vocab_dir / "bandi-cig-tipo-scelta-contraente.json"
            artifact_path.write_text(
                json.dumps(
                    {
                        "tables": [
                            {
                                "name": "tipo_scelta_contraente",
                                "source_columns": [
                                    "tipo-scelta-contraente_codice",
                                    "tipo-scelta-contraente_denominazione",
                                ],
                                "extra_columns": [],
                                "entries": [
                                    {
                                        "code": "24",
                                        "label": "AFFIDAMENTO DIRETTO",
                                        "usage_count": 1,
                                        "attributes": {},
                                        "raw": {
                                            "tipo-scelta-contraente_codice": "24",
                                            "tipo-scelta-contraente_denominazione": "AFFIDAMENTO DIRETTO",
                                        },
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            index_path = vocab_dir / "index.json"
            index_path.write_text(
                json.dumps(
                    {
                        "datasets": [
                            {
                                "dataset_id": "bandi-cig-tipo-scelta-contraente",
                                "artifact_path": str(artifact_path),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            registration = register_vocabulary_crosswalks(
                duckdb_path,
                vocabulary_index_path=index_path,
            )
            query_result = run_local_query(
                duckdb_path,
                "SELECT code, label FROM tipo_scelta_contraente",
            )

            self.assertEqual(registration.status, "registered")
            self.assertEqual(registration.registered_views[0].view_name, "tipo_scelta_contraente")
            self.assertEqual(query_result.rows[0]["code"], "24")
            self.assertEqual(query_result.rows[0]["label"], "AFFIDAMENTO DIRETTO")

    def test_download_dataset_to_parquet_downloads_loads_and_prunes_materialized_csv(self) -> None:
        """@notice Orchestrate download, schema creation, Parquet loading, and source pruning in one workflow."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_zip = temp_path / "source.zip"
            with ZipFile(source_zip, "w") as archive:
                archive.writestr(
                    "inner/demo.csv",
                    "code;label\n1;ONE\n2;TWO\n",
                )

            client = Mock()
            client.transport = "http"
            client.package_show.return_value = CkanPackage(
                id="demo-dataset",
                name="demo-dataset",
                title="Demo dataset",
                notes="Demo notes",
                resources=[
                    CkanResource(
                        id="resource-1",
                        name="demo_csv",
                        format="CSV",
                        url="https://example.invalid/demo.zip",
                    )
                ],
            )

            def fake_download(_client, _url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                copyfile(source_zip, destination)
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                result = download_dataset_to_parquet(
                    client,
                    dataset_id="demo-dataset",
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                    keep_materialized=False,
                )

            query_result = run_local_query(
                result.load_result.duckdb_path,
                "SELECT COUNT(*) AS total_rows FROM demo_dataset",
            )

            self.assertEqual(result.download_cache_status, "fresh")
            self.assertTrue(result.schema_generated)
            self.assertTrue(result.removed_materialized_path)
            self.assertEqual(result.load_result.row_count, 2)
            self.assertEqual(query_result.rows[0]["total_rows"], 2)
            self.assertFalse(Path(result.load_result.source_path).exists())

    def test_sync_cig_periods_updates_forward_without_backfilling_older_gaps(self) -> None:
        """@notice Download only newer periods after the newest locally imported monthly CIG slice."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            client = Mock()
            client.transport = "http"
            client.package_show.return_value = self._build_cig_package(
                "cig-2025",
                {
                    "cig_csv_2025_01": ("https://example.invalid/cig_2025_01.zip", "2026-05-01T00:00:00"),
                    "cig_csv_2025_02": ("https://example.invalid/cig_2025_02.zip", "2026-05-02T00:00:00"),
                    "cig_csv_2025_03": ("https://example.invalid/cig_2025_03.zip", "2026-05-03T00:00:00"),
                    "cig_csv_2025_04": ("https://example.invalid/cig_2025_04.zip", "2026-05-04T00:00:00"),
                    "cig_csv_2025_05": ("https://example.invalid/cig_2025_05.zip", "2026-05-05T00:00:00"),
                },
            )
            zip_payloads = {
                "https://example.invalid/cig_2025_01.zip": "cig;flag_prevalente;importo_lotto;data_pubblicazione\n0100;1;1.00;2025-01-01\n",
                "https://example.invalid/cig_2025_02.zip": "cig;flag_prevalente;importo_lotto;data_pubblicazione\n0200;1;2.00;2025-02-01\n",
                "https://example.invalid/cig_2025_03.zip": "cig;flag_prevalente;importo_lotto;data_pubblicazione\n0300;1;3.00;2025-03-01\n",
                "https://example.invalid/cig_2025_04.zip": "cig;flag_prevalente;importo_lotto;data_pubblicazione\n0400;1;4.00;2025-04-01\n",
                "https://example.invalid/cig_2025_05.zip": "cig;flag_prevalente;importo_lotto;data_pubblicazione\n0500;1;5.00;2025-05-01\n",
            }

            def fake_download(_client, url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._write_zip_payload(destination, zip_payloads[url])
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                download_dataset_to_parquet(
                    client,
                    dataset_id="cig-2025",
                    preferred_resource_name="cig_csv_2025_03",
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                )
                result = sync_cig_periods_to_parquet(
                    client,
                    dataset_id="cig-2025",
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                )

            self.assertEqual(result.selection_mode, "forward")
            self.assertEqual(result.requested_periods, ["2025_04", "2025_05"])
            self.assertEqual([item.period for item in result.plan], ["2025_04", "2025_05"])
            self.assertEqual([load.resource_name for load in result.applied_loads], ["cig_csv_2025_04", "cig_csv_2025_05"])
            self.assertEqual([record.period for record in result.period_manifest], ["2025_03", "2025_04", "2025_05"])

            query_result = run_local_query(
                temp_path / "warehouse" / "anac.duckdb",
                "SELECT COUNT(*) AS total_rows, MIN(cig) AS first_cig, MAX(cig) AS last_cig FROM cig",
            )
            self.assertEqual(query_result.rows[0]["total_rows"], 3)
            self.assertEqual(query_result.rows[0]["first_cig"], "0300")
            self.assertEqual(query_result.rows[0]["last_cig"], "0500")

    def test_sync_cig_periods_is_idempotent_when_remote_period_is_unchanged(self) -> None:
        """@notice Skip already-loaded periods when their remote CKAN metadata did not change."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            client = Mock()
            client.transport = "http"
            client.package_show.return_value = self._build_cig_package(
                "cig-2025",
                {
                    "cig_csv_2025_01": ("https://example.invalid/cig_2025_01.zip", "2026-05-01T00:00:00"),
                },
            )

            def fake_download(_client, _url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._write_zip_payload(
                    destination,
                    "cig;flag_prevalente;importo_lotto;data_pubblicazione\n0001;1;1.00;2025-01-01\n",
                )
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download) as download_mock:
                sync_cig_periods_to_parquet(
                    client,
                    dataset_id="cig-2025",
                    periods=["2025_01"],
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                )
                result = sync_cig_periods_to_parquet(
                    client,
                    dataset_id="cig-2025",
                    periods=["2025_01"],
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                )

            self.assertEqual(download_mock.call_count, 1)
            self.assertEqual(result.plan[0].action, "skip")
            self.assertEqual(result.applied_loads, [])
            self.assertEqual(result.period_manifest[0].period, "2025_01")

    def test_sync_cig_periods_accepts_hyphenated_legacy_range_inputs(self) -> None:
        """@notice Normalize legacy period ranges through shared canonical slice parsing."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            client = Mock()
            client.transport = "http"
            client.package_show.return_value = self._build_cig_package(
                "cig-2025",
                {
                    "cig_csv_2025_01": ("https://example.invalid/cig_2025_01.zip", "2026-05-01T00:00:00"),
                    "cig_csv_2025_02": ("https://example.invalid/cig_2025_02.zip", "2026-05-02T00:00:00"),
                    "cig_csv_2025_03": ("https://example.invalid/cig_2025_03.zip", "2026-05-03T00:00:00"),
                },
            )
            zip_payloads = {
                "https://example.invalid/cig_2025_01.zip": "cig;flag_prevalente;importo_lotto;data_pubblicazione\n0100;1;1.00;2025-01-01\n",
                "https://example.invalid/cig_2025_02.zip": "cig;flag_prevalente;importo_lotto;data_pubblicazione\n0200;1;2.00;2025-02-01\n",
                "https://example.invalid/cig_2025_03.zip": "cig;flag_prevalente;importo_lotto;data_pubblicazione\n0300;1;3.00;2025-03-01\n",
            }

            def fake_download(_client, url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._write_zip_payload(destination, zip_payloads[url])
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                result = sync_cig_periods_to_parquet(
                    client,
                    dataset_id="cig-2025",
                    period_start="2025-02",
                    period_end="2025_03",
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                )

            self.assertEqual(result.selection_mode, "range")
            self.assertEqual(result.requested_periods, ["2025_02", "2025_03"])
            self.assertEqual([item.period for item in result.plan], ["2025_02", "2025_03"])

    def test_sync_cig_periods_refreshes_corrected_remote_periods(self) -> None:
        """@notice Re-download and replace a loaded period slice when CKAN metadata changes upstream."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            client = Mock()
            client.transport = "http"
            package_initial = self._build_cig_package(
                "cig-2025",
                {
                    "cig_csv_2025_01": ("https://example.invalid/cig_2025_01.zip", "2026-05-01T00:00:00"),
                },
            )
            package_corrected = self._build_cig_package(
                "cig-2025",
                {
                    "cig_csv_2025_01": ("https://example.invalid/cig_2025_01.zip", "2026-05-09T00:00:00"),
                },
            )
            client.package_show.side_effect = [package_initial, package_initial, package_corrected, package_corrected]
            zip_payloads = {
                "initial": "cig;flag_prevalente;importo_lotto;data_pubblicazione\n0001;1;1.00;2025-01-01\n",
                "corrected": (
                    "cig;flag_prevalente;importo_lotto;data_pubblicazione\n"
                    "0002;1;2.00;2025-01-02\n"
                    "0003;0;3.00;2025-01-03\n"
                ),
            }
            current_payload_key = {"value": "initial"}

            def fake_download(_client, _url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._write_zip_payload(destination, zip_payloads[current_payload_key["value"]])
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download) as download_mock:
                first_result = sync_cig_periods_to_parquet(
                    client,
                    dataset_id="cig-2025",
                    periods=["2025_01"],
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                )
                current_payload_key["value"] = "corrected"
                second_result = sync_cig_periods_to_parquet(
                    client,
                    dataset_id="cig-2025",
                    periods=["2025_01"],
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                )

            self.assertEqual(download_mock.call_count, 2)
            self.assertEqual(first_result.plan[0].action, "download")
            self.assertEqual(second_result.plan[0].action, "refresh")
            self.assertEqual(second_result.applied_loads[0].load_result.row_count, 2)

            query_result = run_local_query(
                temp_path / "warehouse" / "anac.duckdb",
                "SELECT COUNT(*) AS total_rows, MIN(cig) AS first_cig, MAX(cig) AS last_cig FROM cig WHERE month = '01'",
            )
            self.assertEqual(query_result.rows[0]["total_rows"], 2)
            self.assertEqual(query_result.rows[0]["first_cig"], "0002")
            self.assertEqual(query_result.rows[0]["last_cig"], "0003")
            self.assertEqual(second_result.period_manifest[0].remote_modified, "2026-05-09T00:00:00")

    def _write_manifest_backed_csv(
        self,
        temp_path: Path,
        *,
        dataset_id: str,
        resource_name: str,
        body: str,
    ) -> Path:
        """@notice Create a manifest-backed CSV resource in the repository's expected cache layout."""

        resource_dir = temp_path / "data" / "raw" / dataset_id / resource_name / "extracted"
        resource_dir.mkdir(parents=True, exist_ok=True)
        csv_path = resource_dir / f"{resource_name}.csv"
        csv_path.write_text(body, encoding="utf-8")

        manifest_path = resource_dir.parent / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "dataset_id": dataset_id,
                    "resource_id": f"{resource_name}-id",
                    "resource_name": resource_name,
                    "resource_format": "CSV",
                    "resource_url": f"https://example.invalid/{resource_name}.zip",
                    "transport": "http",
                    "archive_path": None,
                    "materialized_path": str(csv_path),
                    "materialized_kind": "csv",
                    "cache_status": "fresh",
                    "resume_supported": True,
                    "source_size": None,
                    "source_last_modified": None,
                    "downloaded_at": "2026-05-06T12:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        return manifest_path

    def _build_cig_package(
        self,
        dataset_id: str,
        resources: dict[str, tuple[str, str]],
    ) -> CkanPackage:
        """@notice Build a simple CKAN package payload for monthly CIG resources."""

        return CkanPackage(
            id=dataset_id,
            name=dataset_id,
            title=dataset_id,
            notes="Demo monthly CIG package",
            resources=[
                CkanResource(
                    id=f"{resource_name}-id",
                    name=resource_name,
                    format="CSV",
                    url=resource_url,
                    size=1024,
                    last_modified=last_modified,
                )
                for resource_name, (resource_url, last_modified) in resources.items()
            ],
        )

    def _write_zip_payload(self, zip_path: Path, csv_body: str) -> None:
        """@notice Write a zipped CSV payload used by mocked CKAN downloads."""

        with ZipFile(zip_path, "w") as archive:
            archive.writestr("inner/cig.csv", csv_body)
