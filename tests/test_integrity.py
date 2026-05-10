"""@notice Tests for local warehouse integrity validation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from shutil import copyfile
from unittest.mock import Mock, patch
from zipfile import ZipFile

import duckdb

from anac_explorator.integrity import validate_local_data_integrity
from anac_explorator.loader import download_dataset_to_parquet, register_vocabulary_crosswalks
from anac_explorator.models import CkanPackage, CkanResource


class IntegrityValidationTests(unittest.TestCase):
    """@notice Verify read-only integrity validation over the local warehouse."""

    def test_validate_local_data_integrity_passes_for_consistent_cig_warehouse(self) -> None:
        """@notice Report a passing validation result for a coherent monthly CIG warehouse."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            vocabulary_index_path = self._write_vocabulary_artifacts(temp_path)
            client = self._build_client(
                dataset_id="cig-2025",
                resource_name="cig_csv_2025_01",
                resource_url="https://example.invalid/cig_2025_01.zip",
            )
            source_zip = self._write_source_zip(
                temp_path / "source.zip",
                (
                    "cig;cod_tipo_scelta_contraente;tipo_scelta_contraente;cod_modalita_realizzazione;"
                    "modalita_realizzazione;flag_prevalente;importo_lotto;data_pubblicazione\n"
                    "A001;24;AFFIDAMENTO DIRETTO;1;CONTRATTO D'APPALTO;1;10.50;2025-01-24\n"
                    "A002;24;AFFIDAMENTO DIRETTO;1;CONTRATTO D'APPALTO;0;11.75;2025-01-25\n"
                ),
            )

            def fake_download(_client, _url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                copyfile(source_zip, destination)
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                result = download_dataset_to_parquet(
                    client,
                    dataset_id="cig-2025",
                    preferred_resource_name="cig_csv_2025_01",
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                    keep_materialized=True,
                )

            register_vocabulary_crosswalks(
                result.load_result.duckdb_path,
                vocabulary_index_path=vocabulary_index_path,
            )
            report = validate_local_data_integrity(
                result.load_result.duckdb_path,
                schema_path=result.schema_path,
                vocabulary_index_path=vocabulary_index_path,
            )

            self.assertEqual(report.to_dict()["overall_status"], "passed")
            self.assertEqual(report.to_dict()["failed_checks"], 0)

    def test_validate_local_data_integrity_fails_on_exact_duplicate_rows_and_unmatched_code(self) -> None:
        """@notice Treat exact duplicate rows and unmatched external codes as hard validation failures."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            vocabulary_index_path = self._write_vocabulary_artifacts(temp_path)
            client = self._build_client(
                dataset_id="cig-2025",
                resource_name="cig_csv_2025_01",
                resource_url="https://example.invalid/cig_2025_01.zip",
            )
            source_zip = self._write_source_zip(
                temp_path / "source.zip",
                (
                    "cig;cod_tipo_scelta_contraente;tipo_scelta_contraente;cod_modalita_realizzazione;"
                    "modalita_realizzazione;flag_prevalente;importo_lotto;data_pubblicazione\n"
                    "B001;24;AFFIDAMENTO DIRETTO;1;CONTRATTO D'APPALTO;1;10.50;2025-01-24\n"
                    "B001;24;AFFIDAMENTO DIRETTO;1;CONTRATTO D'APPALTO;1;10.50;2025-01-24\n"
                    "B002;24;AFFIDAMENTO DIRETTO;99;SCONOSCIUTA;0;11.75;2025-01-25\n"
                ),
            )

            def fake_download(_client, _url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                copyfile(source_zip, destination)
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                result = download_dataset_to_parquet(
                    client,
                    dataset_id="cig-2025",
                    preferred_resource_name="cig_csv_2025_01",
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                    keep_materialized=True,
                )

            register_vocabulary_crosswalks(
                result.load_result.duckdb_path,
                vocabulary_index_path=vocabulary_index_path,
            )
            payload = validate_local_data_integrity(
                result.load_result.duckdb_path,
                schema_path=result.schema_path,
                vocabulary_index_path=vocabulary_index_path,
            ).to_dict()
            issue_codes = {
                issue["code"]
                for check in payload["checks"]
                for issue in check["issues"]
                if issue["severity"] == "error"
            }

            self.assertEqual(payload["overall_status"], "failed")
            self.assertIn("exact_duplicate_rows", issue_codes)
            self.assertIn("unmatched_external_code", issue_codes)

    def test_validate_local_data_integrity_detects_catalog_and_incremental_mismatches(self) -> None:
        """@notice Detect tampered row counts and period-catalog path/checksum inconsistencies."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            vocabulary_index_path = self._write_vocabulary_artifacts(temp_path)
            client = self._build_client(
                dataset_id="cig-2025",
                resource_name="cig_csv_2025_01",
                resource_url="https://example.invalid/cig_2025_01.zip",
            )
            source_zip = self._write_source_zip(
                temp_path / "source.zip",
                (
                    "cig;cod_tipo_scelta_contraente;tipo_scelta_contraente;cod_modalita_realizzazione;"
                    "modalita_realizzazione;flag_prevalente;importo_lotto;data_pubblicazione\n"
                    "C001;24;AFFIDAMENTO DIRETTO;1;CONTRATTO D'APPALTO;1;10.50;2025-01-24\n"
                ),
            )

            def fake_download(_client, _url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                copyfile(source_zip, destination)
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                result = download_dataset_to_parquet(
                    client,
                    dataset_id="cig-2025",
                    preferred_resource_name="cig_csv_2025_01",
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                    keep_materialized=True,
                )

            register_vocabulary_crosswalks(
                result.load_result.duckdb_path,
                vocabulary_index_path=vocabulary_index_path,
            )
            connection = duckdb.connect(result.load_result.duckdb_path)
            try:
                connection.execute("UPDATE loaded_resources SET row_count = 9 WHERE table_name = 'cig'")
                connection.execute(
                    """
                    UPDATE dataset_period_manifest
                    SET parquet_path = 'broken/year=2025/month=99/cig_csv_2025_01.parquet',
                        content_checksum = NULL
                    WHERE dataset_type = 'cig' AND period = '2025_01'
                    """
                )
            finally:
                connection.close()

            payload = validate_local_data_integrity(
                result.load_result.duckdb_path,
                schema_path=result.schema_path,
                vocabulary_index_path=vocabulary_index_path,
            ).to_dict()
            issue_codes = {issue["code"] for check in payload["checks"] for issue in check["issues"]}

            self.assertEqual(payload["overall_status"], "failed")
            self.assertIn("loaded_resource_row_count_mismatch", issue_codes)
            self.assertIn("missing_period_parquet", issue_codes)
            self.assertIn("period_partition_path_mismatch", issue_codes)
            self.assertIn("missing_period_checksum", issue_codes)

    def test_validate_local_data_integrity_detects_schema_drift_against_requested_artifact(self) -> None:
        """@notice Fail when the requested schema artifact expects columns missing from the loaded warehouse."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            vocabulary_index_path = self._write_vocabulary_artifacts(temp_path)
            client = self._build_client(
                dataset_id="cig-2025",
                resource_name="cig_csv_2025_01",
                resource_url="https://example.invalid/cig_2025_01.zip",
            )
            source_zip = self._write_source_zip(
                temp_path / "source.zip",
                (
                    "cig;cod_tipo_scelta_contraente;tipo_scelta_contraente;cod_modalita_realizzazione;"
                    "modalita_realizzazione;flag_prevalente;importo_lotto;data_pubblicazione\n"
                    "D001;24;AFFIDAMENTO DIRETTO;1;CONTRATTO D'APPALTO;1;10.50;2025-01-24\n"
                ),
            )

            def fake_download(_client, _url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                copyfile(source_zip, destination)
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                result = download_dataset_to_parquet(
                    client,
                    dataset_id="cig-2025",
                    preferred_resource_name="cig_csv_2025_01",
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                    keep_materialized=True,
                )

            register_vocabulary_crosswalks(
                result.load_result.duckdb_path,
                vocabulary_index_path=vocabulary_index_path,
            )
            original_schema = json.loads(Path(result.schema_path).read_text(encoding="utf-8"))
            original_schema["columns"].append(
                {
                    "name": "missing_field",
                    "inferred_type": "text",
                    "nullable": True,
                    "non_empty_samples": [],
                }
            )
            drift_schema_path = temp_path / "schemas" / "drift.schema.json"
            drift_schema_path.write_text(json.dumps(original_schema), encoding="utf-8")

            payload = validate_local_data_integrity(
                result.load_result.duckdb_path,
                schema_path=drift_schema_path,
                vocabulary_index_path=vocabulary_index_path,
            ).to_dict()
            issue_codes = {issue["code"] for check in payload["checks"] for issue in check["issues"]}

            self.assertEqual(payload["overall_status"], "failed")
            self.assertIn("missing_expected_columns", issue_codes)

    def _build_client(self, *, dataset_id: str, resource_name: str, resource_url: str) -> Mock:
        """@notice Build a mock CKAN client that exposes one monthly CIG resource."""

        client = Mock()
        client.transport = "http"
        client.package_show.return_value = CkanPackage(
            id=dataset_id,
            name=dataset_id,
            title=dataset_id,
            notes="Demo dataset",
            resources=[
                CkanResource(
                    id=f"{resource_name}-id",
                    name=resource_name,
                    format="CSV",
                    url=resource_url,
                    size=1234,
                    last_modified="2026-05-10T20:00:00",
                )
            ],
        )
        return client

    def _write_source_zip(self, zip_path: Path, csv_body: str) -> Path:
        """@notice Write a zipped CSV payload used by mocked dataset downloads."""

        with ZipFile(zip_path, "w") as archive:
            archive.writestr("inner/cig.csv", csv_body)
        return zip_path

    def _write_vocabulary_artifacts(self, temp_path: Path) -> Path:
        """@notice Create the minimal vocabulary artifacts required by the validator tests."""

        vocab_dir = temp_path / "vocabularies"
        vocab_dir.mkdir(parents=True, exist_ok=True)
        tipo_artifact_path = vocab_dir / "tipo-scelta.json"
        tipo_artifact_path.write_text(
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
        modalita_artifact_path = vocab_dir / "modalita.json"
        modalita_artifact_path.write_text(
            json.dumps(
                {
                    "tables": [
                        {
                            "name": "modalita_realizzazione",
                            "source_columns": [
                                "modalita-realizzazione_codice",
                                "modalita-realizzazione_denominazione",
                            ],
                            "extra_columns": [],
                            "entries": [
                                {
                                    "code": "1",
                                    "label": "CONTRATTO D'APPALTO",
                                    "usage_count": 1,
                                    "attributes": {},
                                    "raw": {
                                        "modalita-realizzazione_codice": "1",
                                        "modalita-realizzazione_denominazione": "CONTRATTO D'APPALTO",
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
                            "artifact_path": str(tipo_artifact_path),
                        },
                        {
                            "dataset_id": "bandi-cig-modalita-realizzazione",
                            "artifact_path": str(modalita_artifact_path),
                        },
                    ],
                    "field_links": [
                        {
                            "scope": "current_cig_schema",
                            "dataset_id": "bandi-cig-tipo-scelta-contraente",
                            "table_name": "tipo_scelta_contraente",
                            "source_code_field": "cod_tipo_scelta_contraente",
                            "source_label_field": "tipo_scelta_contraente",
                            "target_code_field": "code",
                            "target_label_field": "label",
                            "external_vocabulary_status": "resolved",
                        },
                        {
                            "scope": "current_cig_schema",
                            "dataset_id": "bandi-cig-modalita-realizzazione",
                            "table_name": "modalita_realizzazione",
                            "source_code_field": "cod_modalita_realizzazione",
                            "source_label_field": "modalita_realizzazione",
                            "target_code_field": "code",
                            "target_label_field": "label",
                            "external_vocabulary_status": "resolved",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return index_path
