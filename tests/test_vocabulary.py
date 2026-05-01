"""@notice Tests for vocabulary cross-reference generation."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from anac_explorator.vocabulary import VocabularyDatasetConfig, VocabularyTableConfig, _build_tables


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
