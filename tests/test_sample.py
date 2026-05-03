"""@notice Tests for monthly CIG sample resolution and extraction."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import Mock

from anac_explorator.models import CkanResource
from anac_explorator.sample import (
    _extract_first_csv,
    _download_resource_over_http,
    _materialize_csv,
    download_dataset_resource,
    select_cig_monthly_resource,
    select_csv_resource,
)


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

    def test_select_csv_resource_skips_log_csv_entries(self) -> None:
        """@notice Prefer downloadable zipped CSV resources over logCsv entries."""

        resources = [
            CkanResource(id="1", name="dataset_csv_logCsv", format="CSV", url="https://example.invalid/log.csv"),
            CkanResource(id="2", name="dataset_csv", format="CSV", url="https://example.invalid/data.zip"),
        ]

        resource = select_csv_resource(resources)

        self.assertEqual(resource.name, "dataset_csv")

    def test_materialize_csv_copies_plain_csv_payloads(self) -> None:
        """@notice Treat mislabeled non-ZIP payloads as plain CSV files."""

        with tempfile.TemporaryDirectory() as temp_dir:
            download_path = Path(temp_dir) / "dataset_csv.zip"
            download_path.write_text("code;label\n1;VALUE\n", encoding="utf-8")

            csv_path = _materialize_csv(download_path, Path(temp_dir) / "out")

            self.assertEqual(csv_path.name, "dataset_csv.csv")
            self.assertIn("code;label", csv_path.read_text(encoding="utf-8"))

    def test_download_dataset_resource_reuses_cached_manifest_before_ckan_lookup(self) -> None:
        """@notice Reuse a manifest-backed local cache hit without hitting CKAN again."""

        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir) / "demo-dataset" / "demo_csv" / "extracted"
            base_path.mkdir(parents=True)
            csv_path = base_path / "demo_csv.csv"
            csv_path.write_text("code;label\n1;ONE\n", encoding="utf-8")

            manifest_path = base_path.parent / "manifest.json"
            manifest_path.write_text(
                """
{
  "dataset_id": "demo-dataset",
  "resource_id": "123",
  "resource_name": "demo_csv",
  "resource_format": "CSV",
  "resource_url": "https://example.invalid/demo.zip",
  "transport": "playwright",
  "archive_path": null,
  "materialized_path": "%s",
  "materialized_kind": "csv",
  "cache_status": "fresh",
  "resume_supported": false,
  "source_size": 10,
  "source_last_modified": null,
  "downloaded_at": "2026-05-03T10:00:00+00:00"
}
"""
                % str(csv_path).replace("\\", "\\\\"),
                encoding="utf-8",
            )

            client = Mock()
            client.package_show.side_effect = AssertionError("CKAN should not be queried for a manifest cache hit.")

            artifact = download_dataset_resource(
                client,
                dataset_id="demo-dataset",
                output_dir=temp_dir,
                preferred_resource_name="demo_csv",
                preferred_format="CSV",
            )

            self.assertEqual(artifact.manifest.cache_status, "cache_hit")
            self.assertEqual(artifact.manifest.materialized_path, str(csv_path))

    def test_download_resource_over_http_resumes_partial_downloads(self) -> None:
        """@notice Append HTTP byte ranges onto an existing partial file when available."""

        class _FakeResponse:
            def __init__(self, body: bytes, status: int) -> None:
                self._body = body
                self.status = status

            def read(self) -> bytes:
                return self._body

            def __enter__(self) -> "_FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def getcode(self) -> int:
                return self.status

        class _FakeOpener:
            def open(self, req, timeout):  # noqa: ANN001
                self.last_request = req
                return _FakeResponse(b"world", 206)

        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "demo.zip"
            partial_path = Path(f"{destination}.part")
            partial_path.write_bytes(b"hello ")
            opener = _FakeOpener()
            client = Mock()
            client.timeout = 30
            client._request_headers.return_value = {}
            client._build_opener.return_value = opener

            cache_status, resume_supported = _download_resource_over_http(
                client,
                "https://example.invalid/demo.zip",
                destination,
            )

            self.assertEqual(cache_status, "resumed")
            self.assertTrue(resume_supported)
            self.assertEqual(destination.read_bytes(), b"hello world")
            self.assertEqual(opener.last_request.get_header("Range"), "bytes=6-")
