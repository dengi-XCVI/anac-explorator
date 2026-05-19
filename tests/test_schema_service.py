"""@notice Tests for the Phase 3 schema backend service helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from anac_explorator.errors import CliCommandError
from anac_explorator.models import (
    DataDictionaryArtifact,
    DataDictionaryCodeReference,
    DataDictionaryEntry,
    SchemaColumn,
    SchemaMapping,
)
from anac_explorator.schema_service import diff_schema_targets, inspect_schema, inspect_schema_ddl, resolve_schema_target


class SchemaServiceTests(unittest.TestCase):
    """@notice Verify canonical resolution, semantic describe overlays, and missing-schema errors."""

    def test_inspect_schema_describe_includes_semantic_fields(self) -> None:
        """@notice Join raw schema columns with dictionary metadata in describe mode."""

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
                        SchemaColumn(
                            name="cod_tipo_scelta_contraente",
                            inferred_type="integer",
                            nullable=True,
                            non_empty_samples=["1", "2"],
                        ),
                    ],
                ),
            )
            self._write_dictionary_artifact(
                temp_path / "dictionaries" / "cig_2025_01.dictionary.json",
                DataDictionaryArtifact(
                    dictionary_name="cig_2025_01",
                    dataset_id="cig-2025",
                    source_schema_path="schemas/cig_2025_01.schema.json",
                    sections=["identificativi"],
                    entries=[
                        DataDictionaryEntry(
                            name="cod_tipo_scelta_contraente",
                            section="identificativi",
                            description="Codice della procedura di scelta del contraente.",
                            semantic_type="code",
                            value_pattern="numeric_code",
                            inferred_type="integer",
                            nullable=True,
                            paired_field="tipo_scelta_contraente",
                            code_meaning_status="resolved_external",
                            external_vocabulary_status="available",
                            code_reference=DataDictionaryCodeReference(
                                reference_kind="external_dataset",
                                dataset_id="bandi-cig-tipo-scelta-contraente",
                                table_name="tipo_scelta_contraente",
                            ),
                        )
                    ],
                ),
            )

            result = inspect_schema(
                "cig",
                describe=True,
                schemas_dir=temp_path / "schemas",
                dictionaries_dir=temp_path / "dictionaries",
            )

        self.assertEqual(result.dataset, "cig")
        self.assertEqual(result.mode, "canonical")
        self.assertIsNone(result.target)
        self.assertEqual([column.name for column in result.columns], ["cig", "cod_tipo_scelta_contraente"])
        self.assertEqual(result.columns[0].duckdb_type, "VARCHAR")
        self.assertEqual(result.columns[1].duckdb_type, "BIGINT")
        self.assertEqual(result.columns[1].description, "Codice della procedura di scelta del contraente.")
        self.assertEqual(result.columns[1].semantic_type, "code")
        self.assertEqual(result.columns[1].value_pattern, "numeric_code")
        self.assertEqual(result.columns[1].paired_field, "tipo_scelta_contraente")
        self.assertEqual(result.columns[1].code_meaning_status, "resolved_external")
        self.assertEqual(result.columns[1].external_vocabulary_status, "available")
        self.assertEqual(result.columns[1].vocabulary_dataset_id, "bandi-cig-tipo-scelta-contraente")
        self.assertEqual(result.columns[1].vocabulary_table, "tipo_scelta_contraente")

    def test_resolve_schema_target_supports_year_resolution_from_monthly_artifact(self) -> None:
        """@notice Resolve a year token to the first local monthly schema when no yearly artifact exists."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self._write_schema_artifact(
                temp_path / "schemas" / "cig_2025_01.schema.json",
                SchemaMapping(
                    source_path="data/raw/cig-2025/cig_csv_2025_01/extracted/cig.csv",
                    delimiter=";",
                    encoding="utf-8-sig",
                    rows_sampled=1,
                    row_length_mismatches=0,
                    columns=[SchemaColumn(name="cig", inferred_type="text", nullable=False, non_empty_samples=["0001"])],
                ),
            )

            resolved = resolve_schema_target(
                "cig",
                target="2025",
                schemas_dir=temp_path / "schemas",
                dictionaries_dir=temp_path / "dictionaries",
            )

        self.assertEqual(resolved.requested, "2025")
        self.assertEqual(resolved.resolved, "2025-01")
        self.assertTrue(resolved.schema_path.endswith("cig_2025_01.schema.json"))

    def test_missing_schema_raises_stable_schema_not_available_error(self) -> None:
        """@notice Translate missing local schema targets into the shared schema error code."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with self.assertRaises(CliCommandError) as context:
                inspect_schema(
                    "cig",
                    target="2024",
                    schemas_dir=temp_path / "schemas",
                    dictionaries_dir=temp_path / "dictionaries",
                )

        self.assertEqual(context.exception.code, "SCHEMA_NOT_AVAILABLE")
        self.assertEqual(context.exception.details["dataset"], "cig")
        self.assertEqual(context.exception.details["target"], "2024")
        self.assertEqual(context.exception.details["available_targets"], [])

    def test_inspect_schema_ddl_returns_registered_view_sql(self) -> None:
        """@notice Read the raw DuckDB view definition through the metadata-view facade."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "warehouse" / "anac.duckdb"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute(
                    """
                    CREATE TABLE registered_views (
                        view_name VARCHAR NOT NULL,
                        table_name VARCHAR NOT NULL,
                        parquet_root VARCHAR NOT NULL,
                        parquet_file_count BIGINT NOT NULL,
                        updated_at VARCHAR,
                        view_sql VARCHAR NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO registered_views VALUES (
                        'cig',
                        'cig',
                        'data/warehouse/parquet/cig',
                        1,
                        '2026-05-19T22:00:00',
                        'CREATE OR REPLACE VIEW cig AS SELECT * FROM read_parquet(''data/warehouse/parquet/cig/**/*.parquet'', hive_partitioning = true);'
                    )
                    """
                )
            finally:
                connection.close()

            result = inspect_schema_ddl("cig", db_path=db_path)

        self.assertEqual(result.dataset, "cig")
        self.assertEqual(result.mode, "ddl")
        self.assertEqual(result.target.resolved, "cig")
        self.assertIn("CREATE OR REPLACE VIEW cig AS SELECT * FROM read_parquet", result.ddl)

    def test_diff_schema_targets_reuses_schema_comparison_logic(self) -> None:
        """@notice Compare two local schema targets through the shared comparison utility."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self._write_schema_artifact(
                temp_path / "schemas" / "cig_2007_01.schema.json",
                SchemaMapping(
                    source_path="data/raw/cig-2007/cig_csv_2007_01/extracted/cig.csv",
                    delimiter=";",
                    encoding="utf-8-sig",
                    rows_sampled=1,
                    row_length_mismatches=0,
                    columns=[
                        SchemaColumn(name="cig", inferred_type="text", nullable=False, non_empty_samples=["0001"]),
                        SchemaColumn(name="importo", inferred_type="integer", nullable=False, non_empty_samples=["10"]),
                    ],
                ),
            )
            self._write_schema_artifact(
                temp_path / "schemas" / "cig_2025_01.schema.json",
                SchemaMapping(
                    source_path="data/raw/cig-2025/cig_csv_2025_01/extracted/cig.csv",
                    delimiter=";",
                    encoding="utf-8-sig",
                    rows_sampled=1,
                    row_length_mismatches=0,
                    columns=[
                        SchemaColumn(name="cig", inferred_type="text", nullable=False, non_empty_samples=["0001"]),
                        SchemaColumn(name="importo", inferred_type="decimal", nullable=True, non_empty_samples=["10.50"]),
                        SchemaColumn(name="flag_pnrr_pnc", inferred_type="boolean", nullable=True, non_empty_samples=["1"]),
                    ],
                ),
            )

            result = diff_schema_targets(
                "cig",
                left_target="2007-01",
                right_target="2025-01",
                schemas_dir=temp_path / "schemas",
                dictionaries_dir=temp_path / "dictionaries",
            )

        self.assertEqual(result.dataset, "cig")
        self.assertEqual(result.mode, "diff")
        self.assertIsNone(result.target)
        self.assertEqual(result.diff["left_target"]["resolved"], "2007-01")
        self.assertEqual(result.diff["right_target"]["resolved"], "2025-01")
        self.assertEqual(result.diff["left_column_count"], 2)
        self.assertEqual(result.diff["right_column_count"], 3)
        self.assertEqual(result.diff["right_only_columns"], ["flag_pnrr_pnc"])
        self.assertEqual(
            result.diff["type_changes"],
            [{"name": "importo", "left_type": "integer", "right_type": "decimal"}],
        )
        self.assertEqual(
            result.diff["nullable_changes"],
            [{"name": "importo", "left_nullable": False, "right_nullable": True}],
        )

    @staticmethod
    def _write_schema_artifact(path: Path, mapping: SchemaMapping) -> None:
        """@notice Persist one serialized schema artifact for a temp test repository."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(mapping.to_dict(), indent=2), encoding="utf-8")

    @staticmethod
    def _write_dictionary_artifact(path: Path, artifact: DataDictionaryArtifact) -> None:
        """@notice Persist one serialized dictionary artifact for a temp test repository."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(artifact.to_dict(), indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
