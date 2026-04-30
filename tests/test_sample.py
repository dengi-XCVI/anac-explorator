"""@notice Tests for monthly CIG sample resolution and extraction."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from anac_explorator.models import CkanResource
from anac_explorator.sample import _extract_first_csv, select_cig_monthly_resource


class SampleTests(unittest.TestCase):
    """@notice Verify monthly CIG sample helpers."""

    def test_select_cig_monthly_resource_matches_expected_name(self) -> None:
        """@notice Pick the expected monthly CSV resource from CKAN metadata."""

        resources = [
            CkanResource(id="1", name="cig_csv_2025_01", format="CSV", url="https://example.invalid/1.zip"),
            CkanResource(id="2", name="cig_csv_2025_02", format="CSV", url="https://example.invalid/2.zip"),
        ]

        resource = select_cig_monthly_resource(resources, 2025, 2)

        self.assertEqual(resource.name, "cig_csv_2025_02")

    def test_extract_first_csv_extracts_csv_member(self) -> None:
        """@notice Extract the first CSV payload from a downloaded ZIP archive."""

        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = Path(temp_dir) / "sample.zip"
            with ZipFile(zip_path, "w") as archive:
                archive.writestr("inner/sample.csv", "cig;importo\nA001;10.50\n")

            extracted_path = _extract_first_csv(zip_path, Path(temp_dir) / "out")

            self.assertEqual(extracted_path.name, "sample.csv")
            self.assertIn("cig;importo", extracted_path.read_text(encoding="utf-8"))
