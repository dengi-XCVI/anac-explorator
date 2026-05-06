"""@notice Tests for the DuckDB/Parquet loader helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anac_explorator.loader import load_downloaded_resource, run_local_query


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
