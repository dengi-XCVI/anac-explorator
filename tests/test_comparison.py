"""@notice Tests for schema comparison helpers."""

from __future__ import annotations

import unittest

from anac_explorator.comparison import compare_schema_mappings
from anac_explorator.models import SchemaColumn, SchemaMapping


class SchemaComparisonTests(unittest.TestCase):
    """@notice Verify cross-schema comparison behavior."""

    def test_compare_schema_mappings_reports_added_removed_and_changed_fields(self) -> None:
        """@notice Summarize column-level changes across two schema mappings."""

        left = SchemaMapping(
            source_path="left.csv",
            delimiter=";",
            encoding="utf-8-sig",
            rows_sampled=10,
            row_length_mismatches=0,
            columns=[
                SchemaColumn(name="cig", inferred_type="text", nullable=False),
                SchemaColumn(name="flag", inferred_type="boolean", nullable=False),
                SchemaColumn(name="old_only", inferred_type="integer", nullable=False),
            ],
        )
        right = SchemaMapping(
            source_path="right.csv",
            delimiter=";",
            encoding="utf-8-sig",
            rows_sampled=10,
            row_length_mismatches=0,
            columns=[
                SchemaColumn(name="cig", inferred_type="text", nullable=False),
                SchemaColumn(name="flag", inferred_type="integer", nullable=True),
                SchemaColumn(name="new_only", inferred_type="date", nullable=False),
            ],
        )

        comparison = compare_schema_mappings(left, right)

        self.assertEqual(comparison["left_only_columns"], ["old_only"])
        self.assertEqual(comparison["right_only_columns"], ["new_only"])
        self.assertEqual(comparison["type_changes"][0]["name"], "flag")
        self.assertEqual(comparison["nullable_changes"][0]["name"], "flag")
