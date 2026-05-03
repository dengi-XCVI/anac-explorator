"""@notice Tests for Phase 2 parsing helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anac_explorator.parsing import parse_csv_resource, parse_json_resource


class ParsingTests(unittest.TestCase):
    """@notice Verify CSV and JSON parsing into structured dataclass-backed payloads."""

    def test_parse_csv_resource_preserves_field_names_and_row_count(self) -> None:
        """@notice Parse semicolon-delimited CSV rows into structured row objects."""

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "demo.csv"
            csv_path.write_text("cig;importo\nA001;10.50\nA002;11.75\n", encoding="utf-8")

            parsed = parse_csv_resource(csv_path, row_limit=1)

        self.assertEqual(parsed.row_count, 2)
        self.assertEqual(parsed.field_names, ["cig", "importo"])
        self.assertEqual(len(parsed.rows), 1)
        self.assertEqual(parsed.rows[0].values["cig"], "A001")

    def test_parse_json_resource_reports_top_level_array(self) -> None:
        """@notice Parse JSON arrays and keep a bounded top-level sample."""

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "demo.json"
            json_path.write_text(json.dumps([{"id": 1}, {"id": 2}, {"id": 3}]), encoding="utf-8")

            parsed = parse_json_resource(json_path, item_limit=2)

        self.assertEqual(parsed.top_level_type, "array")
        self.assertEqual(parsed.item_count, 3)
        self.assertEqual(parsed.sample_items, [{"id": 1}, {"id": 2}])
