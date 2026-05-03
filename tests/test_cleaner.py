"""@notice Tests for Phase 2 cleaning helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anac_explorator.cleaner import clean_csv_resource, clean_json_resource
from anac_explorator.models import ParsedCsvResource, ParsedCsvRow, ParsedJsonResource, SchemaColumn, SchemaMapping


class CleanerTests(unittest.TestCase):
    """@notice Verify normalization and coercion for parsed resources."""

    def test_clean_csv_resource_applies_schema_type_hints(self) -> None:
        """@notice Coerce typed CSV fields and normalize null-like blanks."""

        parsed = ParsedCsvResource(
            source_path="demo.csv",
            delimiter=";",
            encoding="utf-8-sig",
            field_names=["flag_prevalente", "importo", "data_pubblicazione", "note"],
            row_count=1,
            rows=[
                ParsedCsvRow(
                    row_number=1,
                    values={
                        "flag_prevalente": "1",
                        "importo": "10.50",
                        "data_pubblicazione": "2025-01-24",
                        "note": "  null  ",
                    },
                )
            ],
        )
        schema = SchemaMapping(
            source_path="demo.csv",
            delimiter=";",
            encoding="utf-8-sig",
            rows_sampled=1,
            row_length_mismatches=0,
            columns=[
                SchemaColumn(name="flag_prevalente", inferred_type="boolean", nullable=False),
                SchemaColumn(name="importo", inferred_type="decimal", nullable=False),
                SchemaColumn(name="data_pubblicazione", inferred_type="date", nullable=False),
                SchemaColumn(name="note", inferred_type="text", nullable=True),
            ],
        )

        cleaned = clean_csv_resource(parsed, schema_mapping=schema)

        row = cleaned.cleaned_records[0]
        self.assertTrue(row.cleaned_values["flag_prevalente"])
        self.assertEqual(str(row.cleaned_values["importo"]), "10.50")
        self.assertEqual(row.cleaned_values["data_pubblicazione"].isoformat(), "2025-01-24")
        self.assertIsNone(row.cleaned_values["note"])

    def test_clean_json_resource_normalizes_null_like_strings(self) -> None:
        """@notice Recursively clean JSON string leaves without discarding structure."""

        parsed = ParsedJsonResource(
            source_path="demo.json",
            encoding="utf-8",
            top_level_type="object",
            item_count=2,
            payload={"value": " null ", "items": ["A", " none "]},
            sample_items=[{"value": " null ", "items": ["A", " none "]}],
        )

        cleaned = clean_json_resource(parsed)

        self.assertIsNone(cleaned.cleaned_payload["value"])
        self.assertEqual(cleaned.cleaned_payload["items"][0], "A")
        self.assertIsNone(cleaned.cleaned_payload["items"][1])
