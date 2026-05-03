"""@notice Tests for vocabulary cross-reference generation."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from anac_explorator.vocabulary import (
    VocabularyDatasetConfig,
    VocabularyTableConfig,
    _build_current_cig_schema_gap_entries,
    _build_tables,
)


class VocabularyTests(unittest.TestCase):
    """@notice Verify vocabulary normalization behavior."""

    def test_build_tables_deduplicates_entries_and_counts_usage(self) -> None:
        """@notice Aggregate repeated code/label rows into stable crosswalk entries."""

        config = VocabularyDatasetConfig(
            dataset_id="demo",
            preferred_resource_name="demo_csv",
            description="Demo dataset",
            tables=(
                VocabularyTableConfig(
                    name="demo_table",
                    description="Demo table",
                    source_columns=("code", "label"),
                    key_columns=("code",),
                    code_column="code",
                    label_column="label",
                    resolved_fields=("code", "label"),
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "demo.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, delimiter=";")
                writer.writerow(["code", "label"])
                writer.writerow(["1", "One"])
                writer.writerow(["1", "One"])
                writer.writerow(["2", "Two"])

            tables = _build_tables(config, csv_path)

        self.assertEqual(tables[0]["entry_count"], 2)
        self.assertEqual(tables[0]["entries"][0]["usage_count"], 2)

    def test_build_tables_emits_multiple_tables_from_one_source(self) -> None:
        """@notice Support datasets that contain more than one vocabulary dimension."""

        config = VocabularyDatasetConfig(
            dataset_id="demo",
            preferred_resource_name="demo_csv",
            description="Demo dataset",
            tables=(
                VocabularyTableConfig(
                    name="codes",
                    description="Primary code table",
                    source_columns=("primary_code", "primary_label"),
                    key_columns=("primary_code",),
                    code_column="primary_code",
                    label_column="primary_label",
                ),
                VocabularyTableConfig(
                    name="secondary",
                    description="Secondary code table",
                    source_columns=("secondary_code", "secondary_label"),
                    key_columns=("secondary_code",),
                    code_column="secondary_code",
                    label_column="secondary_label",
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "demo.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, delimiter=";")
                writer.writerow(["primary_code", "primary_label", "secondary_code", "secondary_label"])
                writer.writerow(["1", "One", "A", "Alpha"])
                writer.writerow(["2", "Two", "", ""])

            tables = _build_tables(config, csv_path)

        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[0]["entry_count"], 2)
        self.assertEqual(tables[1]["entry_count"], 1)

    def test_build_tables_reports_unsafe_zero_stripping_collisions(self) -> None:
        """@notice Preserve raw codes when naive canonicalization would merge different meanings."""

        config = VocabularyDatasetConfig(
            dataset_id="demo",
            preferred_resource_name="demo_csv",
            description="Demo dataset",
            tables=(
                VocabularyTableConfig(
                    name="demo_table",
                    description="Demo table",
                    source_columns=("code", "label"),
                    key_columns=("code",),
                    code_column="code",
                    label_column="label",
                    resolved_fields=("code", "label"),
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "demo.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, delimiter=";")
                writer.writerow(["code", "label"])
                writer.writerow(["01", "APPALTO"])
                writer.writerow(["1", "CONTRATTO D'APPALTO"])

            tables = _build_tables(config, csv_path)

        normalization = tables[0]["normalization"]
        self.assertFalse(normalization["canonicalization_safe"])
        self.assertEqual(normalization["unsafe_rules"][0]["canonical_code"], "1")

    def test_build_current_cig_schema_gap_entries_classifies_inline_code_lists(self) -> None:
        """@notice Distinguish inline-resolved coded fields from free-text or unknown gaps."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            csv_path = temp_path / "cig.csv"
            schema_path = temp_path / "schema.json"

            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, delimiter=";")
                writer.writerow(
                    [
                        "COD_MOTIVO_URGENZA",
                        "MOTIVO_URGENZA",
                        "COD_ESITO",
                        "ESITO",
                    ]
                )
                writer.writerow(["7.0", "NON APPLICABILE", "1.0", "AGGIUDICATA"])
                writer.writerow(["7.0", "non applicabile", "1.0", "AGGIUDICATA"])
                writer.writerow(["4.0", "PROCEDURA NEGOZIATA PER ESTREMA URGENZA", "99.0", "NON AGGIUDICATA"])

            schema_path.write_text(
                json.dumps(
                    {
                        "source_path": str(csv_path),
                        "delimiter": ";",
                        "encoding": "utf-8-sig",
                        "rows_sampled": 3,
                        "row_length_mismatches": 0,
                        "columns": [],
                    }
                ),
                encoding="utf-8",
            )

            analyses = _build_current_cig_schema_gap_entries(schema_path)

        by_field = {entry["field"]: entry for entry in analyses}
        self.assertEqual(by_field["cod_motivo_urgenza"]["code_meaning_status"], "resolved_inline")
        self.assertEqual(by_field["cod_motivo_urgenza"]["observed_pattern"], "code_like")
        self.assertEqual(by_field["cod_motivo_urgenza"]["label_field"], "MOTIVO_URGENZA")
        self.assertEqual(
            by_field["cod_motivo_urgenza"]["normalization"]["label_alias_groups"][0]["variants"],
            ["NON APPLICABILE", "non applicabile"],
        )
