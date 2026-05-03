"""@notice Integration tests for the Phase 2 downloader/parser/cleaner flow."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

from anac_explorator.cleaner import clean_csv_resource, clean_json_resource
from anac_explorator.models import CkanPackage, CkanResource
from anac_explorator.parsing import parse_csv_resource, parse_json_resource
from anac_explorator.sample import download_dataset_resource
from anac_explorator.schema import map_csv_schema


class PipelineIntegrationTests(unittest.TestCase):
    """@notice Verify the Phase 2 pipeline works end to end on representative resources."""

    def test_csv_download_parse_and_clean_pipeline_reuses_manifest_cache(self) -> None:
        """@notice Download a CSV resource, write a manifest, parse it, clean it, and reuse the cache."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_zip = temp_path / "source.zip"
            with ZipFile(source_zip, "w") as archive:
                archive.writestr(
                    "inner/demo.csv",
                    "flag_prevalente;importo;data_pubblicazione;note\n"
                    "1;10.50;2025-01-24;null\n"
                    "0;11.75;2025-01-25;  HELLO  \n",
                )

            client = Mock()
            client.transport = "http"
            client.package_show.return_value = CkanPackage(
                id="demo-dataset",
                name="demo-dataset",
                title="Demo dataset",
                notes="Demo notes",
                resources=[
                    CkanResource(
                        id="resource-1",
                        name="demo_csv",
                        format="CSV",
                        url="https://example.invalid/demo.zip",
                    )
                ],
            )

            def fake_download(_client, _url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_zip, destination)
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download) as download_mock:
                first_artifact = download_dataset_resource(
                    client,
                    dataset_id="demo-dataset",
                    preferred_resource_name="demo_csv",
                    preferred_format="CSV",
                    output_dir=temp_path / "data",
                )
                second_artifact = download_dataset_resource(
                    client,
                    dataset_id="demo-dataset",
                    preferred_resource_name="demo_csv",
                    preferred_format="CSV",
                    output_dir=temp_path / "data",
                )

            self.assertEqual(download_mock.call_count, 1)
            self.assertEqual(client.package_show.call_count, 1)
            self.assertEqual(first_artifact.manifest.cache_status, "fresh")
            self.assertEqual(second_artifact.manifest.cache_status, "cache_hit")
            self.assertTrue(Path(first_artifact.manifest_path).exists())

            parsed = parse_csv_resource(first_artifact.manifest.materialized_path, row_limit=0)
            schema = map_csv_schema(first_artifact.manifest.materialized_path, sample_limit=0)
            cleaned = clean_csv_resource(parsed, schema_mapping=schema)

            self.assertEqual(parsed.row_count, 2)
            self.assertTrue(cleaned.cleaned_records[0].cleaned_values["flag_prevalente"])
            self.assertEqual(str(cleaned.cleaned_records[0].cleaned_values["importo"]), "10.50")
            self.assertIsNone(cleaned.cleaned_records[0].cleaned_values["note"])
            self.assertEqual(cleaned.cleaned_records[1].cleaned_values["note"], "HELLO")

    def test_json_download_parse_and_clean_pipeline_materializes_direct_json(self) -> None:
        """@notice Download a JSON resource, parse it, and clean null-like string leaves."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_json = temp_path / "source.json"
            source_json.write_text(
                json.dumps(
                    {
                        "note": " demo ",
                        "items": [
                            {"value": " null ", "status": "READY"},
                            {"value": " 11 ", "status": "DONE"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            client = Mock()
            client.transport = "http"
            client.package_show.return_value = CkanPackage(
                id="demo-json-dataset",
                name="demo-json-dataset",
                title="Demo JSON dataset",
                notes="Demo notes",
                resources=[
                    CkanResource(
                        id="resource-2",
                        name="demo_json",
                        format="JSON",
                        url="https://example.invalid/demo.json",
                    )
                ],
            )

            def fake_download(_client, _url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_json, destination)
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                artifact = download_dataset_resource(
                    client,
                    dataset_id="demo-json-dataset",
                    preferred_resource_name="demo_json",
                    preferred_format="JSON",
                    output_dir=temp_path / "data",
                )

            parsed = parse_json_resource(artifact.manifest.materialized_path)
            cleaned = clean_json_resource(parsed)

            self.assertEqual(parsed.top_level_type, "object")
            self.assertEqual(cleaned.cleaned_payload["note"], "demo")
            self.assertIsNone(cleaned.cleaned_payload["items"][0]["value"])
            self.assertEqual(cleaned.cleaned_payload["items"][1]["value"], "11")
