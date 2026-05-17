"""@notice Tests for typed metadata-row models and local artifact loaders."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from anac_explorator.metadata_views import (
    METADATA_VIEW_NAMES,
    build_crosswalk_rows,
    build_dataset_resource_rows,
    build_datasets_rows,
    ensure_metadata_views,
    build_dictionary_fields_rows,
    build_loaded_resources_rows,
    build_partitions_rows,
    build_registered_views_rows,
    build_update_status_rows,
    load_data_dictionary_artifact,
    load_dictionary_fields_rows,
    load_schema_columns_rows,
    load_vocabulary_index,
)
from anac_explorator.loader import run_local_query
from anac_explorator.models import (
    DataDictionaryArtifact,
    DataDictionaryCodeReference,
    DataDictionaryEntry,
    DatasetPeriodManifestRecord,
    JoinContract,
    DownloadManifest,
    WarehouseCrosswalkRegistrationResult,
    WarehouseCrosswalkView,
    WarehouseLoadResult,
    WarehousePartitionValue,
)


class MetadataViewsTests(unittest.TestCase):
    """@notice Verify the pure-Python metadata discoverability models and loaders."""

    def test_metadata_view_names_cover_all_nine_required_views(self) -> None:
        """@notice Expose the full stable metadata-view inventory."""

        self.assertEqual(
            METADATA_VIEW_NAMES,
            (
                "anac_datasets",
                "anac_dataset_resources",
                "anac_partitions",
                "anac_registered_views",
                "anac_loaded_resources",
                "anac_schema_columns",
                "anac_dictionary_fields",
                "anac_crosswalks",
                "anac_update_status",
            ),
        )

    def test_load_data_dictionary_artifact_rehydrates_nested_dataclasses(self) -> None:
        """@notice Rebuild dictionary artifacts through the new dataclass loader path."""

        artifact = self._build_dictionary_artifact()

        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_path = Path(temp_dir) / "cig.dictionary.json"
            artifact_path.write_text(json.dumps(artifact.to_dict()), encoding="utf-8")

            loaded = load_data_dictionary_artifact(artifact_path)

        self.assertEqual(loaded.dictionary_name, artifact.dictionary_name)
        self.assertEqual(loaded.entries[0].name, "cod_tipo_scelta_contraente")
        self.assertEqual(loaded.entries[0].code_reference.dataset_id, "bandi-cig-tipo-scelta-contraente")
        self.assertEqual(loaded.entries[0].code_reference.join_contract.target_table, "tipo_scelta_contraente")

    def test_load_vocabulary_index_rehydrates_typed_entries(self) -> None:
        """@notice Load the vocabulary index through typed dataset and field-link dataclasses."""

        payload = {
            "dataset_count": 1,
            "datasets": [
                {
                    "dataset_id": "bandi-cig-tipo-scelta-contraente",
                    "resource_name": "bandi-cig-tipo-scelta-contraente_csv",
                    "csv_path": "data/raw/demo.csv",
                    "schema_path": "schemas/demo.schema.json",
                    "artifact_path": "vocabularies/demo.json",
                    "table_count": 1,
                }
            ],
            "code_meaning_status_taxonomy": {"resolved_external": "Available"},
            "field_links": [
                {
                    "scope": "current_cig_schema",
                    "dataset_id": "bandi-cig-tipo-scelta-contraente",
                    "table_name": "tipo_scelta_contraente",
                    "source_code_field": "cod_tipo_scelta_contraente",
                    "target_code_field": "code",
                    "target_label_field": "label",
                    "resolved_fields": ["cod_tipo_scelta_contraente"],
                    "join_contract": {
                        "target_dataset": "bandi-cig-tipo-scelta-contraente",
                        "target_table": "tipo_scelta_contraente",
                        "source_field": "cod_tipo_scelta_contraente",
                        "target_field": "code",
                        "target_label_field": "label",
                    },
                }
            ],
            "current_cig_schema_gaps": [{"field": "cod_esito"}],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "index.json"
            index_path.write_text(json.dumps(payload), encoding="utf-8")

            loaded = load_vocabulary_index(index_path)

        self.assertEqual(loaded.dataset_count, 1)
        self.assertEqual(loaded.datasets[0].dataset_id, "bandi-cig-tipo-scelta-contraente")
        self.assertEqual(loaded.field_links[0].join_contract.source_field, "cod_tipo_scelta_contraente")
        self.assertEqual(loaded.current_cig_schema_gaps[0]["field"], "cod_esito")

    def test_load_schema_columns_rows_overlays_dictionary_metadata(self) -> None:
        """@notice Overlay dictionary semantics onto schema-column metadata rows."""

        artifact = self._build_dictionary_artifact()
        schema_payload = {
            "source_path": "data/raw/cig.csv",
            "delimiter": ";",
            "encoding": "utf-8-sig",
            "rows_sampled": 1,
            "row_length_mismatches": 0,
            "columns": [
                {
                    "name": "cod_tipo_scelta_contraente",
                    "inferred_type": "text",
                    "nullable": False,
                    "non_empty_samples": ["24"],
                },
                {
                    "name": "importo_lotto",
                    "inferred_type": "decimal",
                    "nullable": True,
                    "non_empty_samples": ["100.50"],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            schema_path = Path(temp_dir) / "cig.schema.json"
            dictionary_path = Path(temp_dir) / "cig.dictionary.json"
            schema_path.write_text(json.dumps(schema_payload), encoding="utf-8")
            dictionary_path.write_text(json.dumps(artifact.to_dict()), encoding="utf-8")

            rows = load_schema_columns_rows(schema_path, dataset="cig", dictionary_path=dictionary_path)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].column_name, "cod_tipo_scelta_contraente")
        self.assertEqual(rows[0].description, "ANAC procedure-choice code.")
        self.assertEqual(rows[0].vocabulary_dataset_id, "bandi-cig-tipo-scelta-contraente")
        self.assertEqual(rows[1].column_name, "importo_lotto")
        self.assertIsNone(rows[1].vocabulary_table)

    def test_load_dictionary_fields_rows_defaults_to_logical_family(self) -> None:
        """@notice Resolve dictionary rows to the logical family id when possible."""

        artifact = self._build_dictionary_artifact()

        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_path = Path(temp_dir) / "cig.dictionary.json"
            artifact_path.write_text(json.dumps(artifact.to_dict()), encoding="utf-8")

            rows = load_dictionary_fields_rows(artifact_path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].dataset, "cig")
        self.assertEqual(rows[0].join_key, "cod_tipo_scelta_contraente")

    def test_build_dataset_and_update_status_rows_aggregate_partitions_and_dictionary_presence(self) -> None:
        """@notice Aggregate per-family metadata from the registry plus local partition state."""

        artifact = self._build_dictionary_artifact()
        period_manifest = [
            DatasetPeriodManifestRecord(
                dataset_type="cig",
                period="2025_01",
                dataset_id="cig-2025",
                resource_name="cig_csv_2025_01",
                manifest_path="data/raw/cig-2025/cig_csv_2025_01/manifest.json",
                parquet_path="data/warehouse/parquet/cig/year=2025/month=01/cig_csv_2025_01.parquet",
                row_count=2,
                imported_at="2026-05-17T10:00:00",
                refreshed_at="2026-05-17T11:00:00",
            ),
            DatasetPeriodManifestRecord(
                dataset_type="cig",
                period="2025_02",
                dataset_id="cig-2025",
                resource_name="cig_csv_2025_02",
                manifest_path="data/raw/cig-2025/cig_csv_2025_02/manifest.json",
                parquet_path="data/warehouse/parquet/cig/year=2025/month=02/cig_csv_2025_02.parquet",
                row_count=3,
                imported_at="2026-05-18T10:00:00",
                refreshed_at="2026-05-18T12:00:00",
            ),
        ]

        dataset_rows = build_datasets_rows(period_manifest=period_manifest, dictionary_artifacts=[artifact])
        update_rows = build_update_status_rows(period_manifest=period_manifest)

        cig_dataset_row = next(row for row in dataset_rows if row.dataset == "cig")
        cig_update_row = next(row for row in update_rows if row.dataset == "cig")
        self.assertEqual(cig_dataset_row.local_slice_count, 2)
        self.assertEqual(cig_dataset_row.local_first_slice, "2025-01")
        self.assertEqual(cig_dataset_row.local_last_slice, "2025-02")
        self.assertTrue(cig_dataset_row.dictionary_available)
        self.assertEqual(cig_update_row.latest_local_slice, "2025-02")
        self.assertEqual(cig_update_row.latest_imported_at, "2026-05-18T10:00:00")
        self.assertEqual(cig_update_row.latest_refreshed_at, "2026-05-18T12:00:00")

    def test_build_dataset_resource_and_loaded_resource_rows_merge_raw_and_loaded_state(self) -> None:
        """@notice Merge manifests with load results into local resource metadata rows."""

        manifest = DownloadManifest(
            dataset_id="cig-2025",
            resource_id="demo-id",
            resource_name="cig_csv_2025_01",
            resource_format="csv",
            resource_url="https://example.invalid/cig_2025_01.zip",
            transport="playwright",
            archive_path="data/raw/cig-2025/cig_csv_2025_01/cig_csv_2025_01.zip",
            materialized_path="data/raw/cig-2025/cig_csv_2025_01/extracted/cig_csv_2025_01.csv",
            materialized_kind="csv",
            cache_status="fresh",
            resume_supported=False,
            source_size=1234,
            source_last_modified="2026-05-17T09:00:00",
            downloaded_at="2026-05-17T09:05:00",
        )
        load_result = WarehouseLoadResult(
            dataset_id="cig-2025",
            resource_name="cig_csv_2025_01",
            table_name="cig",
            view_name="cig",
            manifest_path="data/raw/cig-2025/cig_csv_2025_01/manifest.json",
            schema_path="schemas/cig_2025_01.schema.json",
            source_path=manifest.materialized_path,
            warehouse_dir="data/warehouse",
            duckdb_path="data/warehouse/anac.duckdb",
            parquet_root="data/warehouse/parquet/cig",
            parquet_path="data/warehouse/parquet/cig/year=2025/month=01/cig_csv_2025_01.parquet",
            row_count=2,
            partition_values=[WarehousePartitionValue(key="year", value="2025"), WarehousePartitionValue(key="month", value="01")],
            registered_parquet_files=1,
        )

        resource_rows = build_dataset_resource_rows([manifest], load_results=[load_result])
        loaded_rows = build_loaded_resources_rows([load_result], manifests=[manifest])

        self.assertEqual(len(resource_rows), 1)
        self.assertEqual(resource_rows[0].dataset, "cig")
        self.assertEqual(resource_rows[0].source_format, "CSV")
        self.assertEqual(resource_rows[0].slice, "2025-01")
        self.assertEqual(resource_rows[0].local_status, "loaded")
        self.assertEqual(resource_rows[0].row_count, 2)
        self.assertEqual(loaded_rows[0].dataset, "cig")
        self.assertEqual(loaded_rows[0].loaded_at, "2026-05-17T09:05:00")
        self.assertEqual(
            loaded_rows[0].partition_values_json,
            '[{"key":"year","value":"2025"},{"key":"month","value":"01"}]',
        )

    def test_build_partition_registered_view_and_crosswalk_rows(self) -> None:
        """@notice Normalize partition slices and summarize registered view metadata."""

        period_rows = build_partitions_rows(
            [
                DatasetPeriodManifestRecord(
                    dataset_type="cig",
                    period="2025_03",
                    dataset_id="cig-2025",
                    resource_name="cig_csv_2025_03",
                    manifest_path="data/raw/cig-2025/cig_csv_2025_03/manifest.json",
                    parquet_path="data/warehouse/parquet/cig/year=2025/month=03/cig_csv_2025_03.parquet",
                    remote_size=456,
                    remote_modified="2026-05-19T10:00:00",
                    content_checksum="abc123",
                    row_count=4,
                    imported_at="2026-05-19T11:00:00",
                    refreshed_at="2026-05-19T12:00:00",
                )
            ]
        )
        load_result = WarehouseLoadResult(
            dataset_id="cig-2025",
            resource_name="cig_csv_2025_03",
            table_name="cig",
            view_name="cig",
            manifest_path="data/raw/cig-2025/cig_csv_2025_03/manifest.json",
            schema_path=None,
            source_path="data/raw/cig-2025/cig_csv_2025_03/extracted/cig_csv_2025_03.csv",
            warehouse_dir="data/warehouse",
            duckdb_path="data/warehouse/anac.duckdb",
            parquet_root="data/warehouse/parquet/cig",
            parquet_path="data/warehouse/parquet/cig/year=2025/month=03/cig_csv_2025_03.parquet",
            row_count=4,
            registered_parquet_files=3,
        )
        registration_result = WarehouseCrosswalkRegistrationResult(
            duckdb_path="data/warehouse/anac.duckdb",
            vocabulary_index_path="vocabularies/index.json",
            status="registered",
            registered_views=[
                WarehouseCrosswalkView(
                    dataset_id="bandi-cig-tipo-scelta-contraente",
                    table_name="tipo_scelta_contraente",
                    view_name="tipo_scelta_contraente",
                    parquet_path="data/warehouse/parquet/tipo_scelta_contraente/part.parquet",
                    row_count=39,
                )
            ],
        )

        registered_rows = build_registered_views_rows([load_result], updated_at_by_view={"cig": "2026-05-19T13:00:00"})
        crosswalk_rows = build_crosswalk_rows(registration_result)

        self.assertEqual(period_rows[0].slice, "2025-03")
        self.assertEqual(period_rows[0].year, 2025)
        self.assertEqual(period_rows[0].month, 3)
        self.assertEqual(registered_rows[0].parquet_file_count, 3)
        self.assertEqual(registered_rows[0].updated_at, "2026-05-19T13:00:00")
        self.assertEqual(crosswalk_rows[0].dataset_id, "bandi-cig-tipo-scelta-contraente")
        self.assertEqual(crosswalk_rows[0].row_count, 39)

    def test_build_dictionary_fields_rows_preserves_vocabulary_link_columns(self) -> None:
        """@notice Project dictionary entries into the metadata-view row shape."""

        rows = build_dictionary_fields_rows(self._build_dictionary_artifact(), dataset="cig")

        self.assertEqual(rows[0].field_name, "cod_tipo_scelta_contraente")
        self.assertEqual(rows[0].vocabulary_table, "tipo_scelta_contraente")
        self.assertEqual(rows[0].label_field, "label")

    def test_ensure_metadata_views_recomputes_on_demand(self) -> None:
        """@notice Rebuild the temp metadata views when the underlying warehouse catalog changes."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse" / "anac.duckdb"
            db_path.parent.mkdir(parents=True)
            connection = duckdb.connect(str(db_path))
            try:
                ensure_metadata_views(connection, db_path=db_path)
                self.assertEqual(
                    connection.execute(
                        "SELECT local_slice_count FROM anac_datasets WHERE dataset = 'cig'"
                    ).fetchone()[0],
                    0,
                )

                connection.execute(
                    """
                    CREATE TABLE dataset_period_manifest (
                        dataset_type VARCHAR NOT NULL,
                        period VARCHAR NOT NULL,
                        dataset_id VARCHAR NOT NULL,
                        resource_name VARCHAR NOT NULL,
                        manifest_path VARCHAR NOT NULL,
                        parquet_path VARCHAR NOT NULL,
                        resource_id VARCHAR,
                        resource_url VARCHAR,
                        remote_modified VARCHAR,
                        remote_size BIGINT,
                        content_checksum VARCHAR,
                        row_count BIGINT,
                        imported_at VARCHAR NOT NULL,
                        refreshed_at VARCHAR NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO dataset_period_manifest VALUES (
                        'cig',
                        '2025_01',
                        'cig-2025',
                        'cig_csv_2025_01',
                        'data/raw/cig-2025/cig_csv_2025_01/manifest.json',
                        'data/warehouse/parquet/cig/year=2025/month=01/cig_csv_2025_01.parquet',
                        NULL,
                        NULL,
                        NULL,
                        NULL,
                        NULL,
                        2,
                        '2026-05-17T10:00:00',
                        '2026-05-17T11:00:00'
                    )
                    """
                )

                ensure_metadata_views(connection, db_path=db_path)
                refreshed = connection.execute(
                    "SELECT local_slice_count, local_first_slice, local_last_slice FROM anac_datasets WHERE dataset = 'cig'"
                ).fetchone()
            finally:
                connection.close()

        self.assertEqual(refreshed, (1, "2025-01", "2025-01"))

    def test_run_local_query_can_select_from_anac_datasets(self) -> None:
        """@notice Bootstrap the metadata layer automatically before local query execution."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse" / "anac.duckdb"
            db_path.parent.mkdir(parents=True)
            duckdb.connect(str(db_path)).close()

            result = run_local_query(
                db_path,
                "SELECT dataset, update_supported FROM anac_datasets ORDER BY dataset",
                row_limit=3,
            )

        self.assertEqual(result.row_count, 3)
        self.assertEqual([row["dataset"] for row in result.rows], ["aggiudicatari", "bandi-cig-modalita-realizzazione", "bandi-cig-tipo-scelta-contraente"])
        self.assertFalse(result.rows[0]["update_supported"])

    def _build_dictionary_artifact(self) -> DataDictionaryArtifact:
        """@notice Build one small but fully linked dictionary artifact for loader tests."""

        return DataDictionaryArtifact(
            dictionary_name="cig_2025_01",
            dataset_id="cig-2025",
            source_schema_path="schemas/cig_2025_01.schema.json",
            vocabulary_index_path="vocabularies/index.json",
            sections=["Publication and procedure"],
            entries=[
                DataDictionaryEntry(
                    name="cod_tipo_scelta_contraente",
                    section="Publication and procedure",
                    description="ANAC procedure-choice code.",
                    semantic_type="categorical_code",
                    value_pattern="numeric code",
                    inferred_type="text",
                    nullable=False,
                    paired_field="tipo_scelta_contraente",
                    code_meaning_status="resolved_external",
                    external_vocabulary_status="resolved",
                    code_reference=DataDictionaryCodeReference(
                        reference_kind="external_vocabulary",
                        dataset_id="bandi-cig-tipo-scelta-contraente",
                        table_name="tipo_scelta_contraente",
                        source_code_field="cod_tipo_scelta_contraente",
                        target_code_field="code",
                        target_label_field="label",
                        external_vocabulary_status="resolved",
                        join_contract=JoinContract(
                            target_dataset="bandi-cig-tipo-scelta-contraente",
                            target_table="tipo_scelta_contraente",
                            source_field="cod_tipo_scelta_contraente",
                            target_field="code",
                            target_label_field="label",
                        ),
                    ),
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
