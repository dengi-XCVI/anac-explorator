"""@notice Tests for executing reusable download plans."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from shutil import copyfile
from unittest.mock import Mock, patch
from zipfile import ZipFile

from anac_explorator.catalog import DATASET_FAMILY_REGISTRY, execute_download_plan
from anac_explorator.models import CkanPackage, CkanResource, DownloadPlan, DownloadPlanItem
from anac_explorator.sample import DownloadedResourceArtifact
from anac_explorator.loader import run_local_query
from anac_explorator.selection import parse_temporal_selection


class DownloadExecutionTests(unittest.TestCase):
    """@notice Verify the execution engine for raw, parquet, and both download modes."""

    def test_execute_raw_plan_downloads_resource_without_warehouse_load(self) -> None:
        """@notice Keep raw mode on the manifest-backed download path only."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_csv = temp_path / "source.csv"
            source_csv.write_text("code;label\n1;ONE\n", encoding="utf-8")

            client = Mock()
            client.transport = "http"
            client.package_show.return_value = CkanPackage(
                id="snapshot-pkg",
                name="stazioni-appaltanti",
                title="Stazioni appaltanti",
                notes="Snapshot registry",
                resources=[
                    CkanResource(
                        id="snapshot-csv",
                        name="stazioni_appaltanti_csv",
                        format="CSV",
                        url="https://example.invalid/stazioni.csv",
                    )
                ],
            )

            plan = DATASET_FAMILY_REGISTRY.plan_download(
                "stazioni-appaltanti",
                client,
                output_format="raw",
            )

            def fake_download(_client, _url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                copyfile(source_csv, destination)
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                applied = execute_download_plan(
                    plan,
                    client,
                    output_dir=temp_path / "data" / "raw",
                )

            self.assertEqual(len(applied), 1)
            self.assertIsInstance(applied[0], DownloadedResourceArtifact)
            manifest = applied[0].manifest
            self.assertEqual(manifest.dataset_id, "stazioni-appaltanti")
            self.assertTrue(Path(applied[0].manifest_path).exists())
            self.assertTrue(Path(manifest.materialized_path).exists())
            self.assertFalse((temp_path / "warehouse").exists())

    def test_execute_parquet_plan_prunes_materialized_csv_after_load(self) -> None:
        """@notice Let parquet mode reuse the existing direct-to-Parquet workflow and prune raw files."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_zip = temp_path / "source.zip"
            with ZipFile(source_zip, "w") as archive:
                archive.writestr("inner/cig.csv", "cig;label\n0001;ONE\n0002;TWO\n")

            client = self._build_cig_client("https://example.invalid/cig_2025_01.zip")
            plan = DATASET_FAMILY_REGISTRY.plan_download(
                "cig",
                client,
                selection=parse_temporal_selection(year="2025", month="1"),
                output_format="parquet",
            )

            def fake_download(_client, _url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                copyfile(source_zip, destination)
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                applied = execute_download_plan(
                    plan,
                    client,
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                )

            result = applied[0]
            query_result = run_local_query(
                result.load_result.duckdb_path,
                "SELECT COUNT(*) AS total_rows FROM cig",
            )

            self.assertTrue(result.removed_materialized_path)
            self.assertFalse(Path(result.load_result.source_path).exists())
            self.assertEqual(result.load_result.row_count, 2)
            self.assertEqual(query_result.rows[0]["total_rows"], 2)

    def test_execute_both_plan_preserves_materialized_csv_after_load(self) -> None:
        """@notice Preserve the raw working file when output format is `both`."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_zip = temp_path / "source.zip"
            with ZipFile(source_zip, "w") as archive:
                archive.writestr("inner/cig.csv", "cig;label\n0001;ONE\n")

            client = self._build_cig_client("https://example.invalid/cig_2025_01.zip")
            plan = DATASET_FAMILY_REGISTRY.plan_download(
                "cig",
                client,
                selection=parse_temporal_selection(year="2025", month="1"),
                output_format="both",
            )

            def fake_download(_client, _url, destination):  # noqa: ANN001
                destination.parent.mkdir(parents=True, exist_ok=True)
                copyfile(source_zip, destination)
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                applied = execute_download_plan(
                    plan,
                    client,
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                )

            result = applied[0]
            self.assertFalse(result.removed_materialized_path)
            self.assertTrue(Path(result.load_result.source_path).exists())
            self.assertEqual(result.load_result.row_count, 1)

    def test_execute_parquet_plan_reuses_existing_raw_cache_without_redownloading(self) -> None:
        """@notice Build only the missing Parquet artifact when a raw CSV is already cached."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_csv = temp_path / "source.csv"
            source_csv.write_text("code;label\n1;ONE\n", encoding="utf-8")

            client = self._build_cig_client("https://example.invalid/cig_2025_01.csv")
            raw_plan = DATASET_FAMILY_REGISTRY.plan_download(
                "cig",
                client,
                selection=parse_temporal_selection(year="2025", month="1"),
                output_format="raw",
            )
            parquet_plan = DATASET_FAMILY_REGISTRY.plan_download(
                "cig",
                client,
                selection=parse_temporal_selection(year="2025", month="1"),
                output_format="parquet",
            )
            download_calls = 0

            def fake_download(_client, _url, destination):  # noqa: ANN001
                nonlocal download_calls
                download_calls += 1
                destination.parent.mkdir(parents=True, exist_ok=True)
                copyfile(source_csv, destination)
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                execute_download_plan(
                    raw_plan,
                    client,
                    output_dir=temp_path / "data" / "raw",
                )
                applied = execute_download_plan(
                    parquet_plan,
                    client,
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                )

            result = applied[0]
            query_result = run_local_query(
                result.load_result.duckdb_path,
                "SELECT COUNT(*) AS total_rows FROM cig",
            )

            self.assertEqual(download_calls, 1)
            self.assertFalse(result.removed_materialized_path)
            self.assertTrue(Path(result.load_result.source_path).exists())
            self.assertEqual(result.load_result.row_count, 1)
            self.assertEqual(query_result.rows[0]["total_rows"], 1)

    def test_execute_both_plan_adds_only_parquet_when_raw_cache_already_exists(self) -> None:
        """@notice Reuse the cached raw CSV and preserve it when upgrading a download to `both`."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_csv = temp_path / "source.csv"
            source_csv.write_text("code;label\n1;ONE\n", encoding="utf-8")

            client = self._build_cig_client("https://example.invalid/cig_2025_01.csv")
            raw_plan = DATASET_FAMILY_REGISTRY.plan_download(
                "cig",
                client,
                selection=parse_temporal_selection(year="2025", month="1"),
                output_format="raw",
            )
            both_plan = DATASET_FAMILY_REGISTRY.plan_download(
                "cig",
                client,
                selection=parse_temporal_selection(year="2025", month="1"),
                output_format="both",
            )
            download_calls = 0

            def fake_download(_client, _url, destination):  # noqa: ANN001
                nonlocal download_calls
                download_calls += 1
                destination.parent.mkdir(parents=True, exist_ok=True)
                copyfile(source_csv, destination)
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                execute_download_plan(
                    raw_plan,
                    client,
                    output_dir=temp_path / "data" / "raw",
                )
                applied = execute_download_plan(
                    both_plan,
                    client,
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                )

            result = applied[0]
            self.assertEqual(download_calls, 1)
            self.assertFalse(result.removed_materialized_path)
            self.assertTrue(Path(result.load_result.source_path).exists())
            self.assertEqual(result.load_result.row_count, 1)

    def test_execute_parquet_plan_reuses_cached_parquet_without_redownloading_raw(self) -> None:
        """@notice Skip the remote download when Parquet is cached but the raw working file is gone."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_csv = temp_path / "source.csv"
            source_csv.write_text("code;label\n1;ONE\n", encoding="utf-8")

            client = self._build_cig_client("https://example.invalid/cig_2025_01.csv")
            parquet_plan = DATASET_FAMILY_REGISTRY.plan_download(
                "cig",
                client,
                selection=parse_temporal_selection(year="2025", month="1"),
                output_format="parquet",
            )
            download_calls = 0

            def fake_download(_client, _url, destination):  # noqa: ANN001
                nonlocal download_calls
                download_calls += 1
                destination.parent.mkdir(parents=True, exist_ok=True)
                copyfile(source_csv, destination)
                return "fresh", True

            with patch("anac_explorator.sample._download_resource", side_effect=fake_download):
                first_applied = execute_download_plan(
                    parquet_plan,
                    client,
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                )
                first_result = first_applied[0]
                Path(first_result.load_result.source_path).unlink()
                self.assertFalse(Path(first_result.load_result.source_path).exists())
                second_applied = execute_download_plan(
                    parquet_plan,
                    client,
                    output_dir=temp_path / "data" / "raw",
                    schemas_dir=temp_path / "schemas",
                    warehouse_dir=temp_path / "warehouse",
                    register_crosswalks=False,
                )

            second_result = second_applied[0]
            self.assertEqual(download_calls, 1)
            self.assertEqual(second_result.load_result.load_status, "cache_hit")
            self.assertFalse(second_result.removed_materialized_path)

    def test_execute_plan_passes_force_flags_to_low_level_helpers(self) -> None:
        """@notice Forward force-download and force-load without reimplementing lower-level behavior."""

        raw_plan = DownloadPlan(
            dataset="stazioni-appaltanti",
            output_format="raw",
            requested_scope={},
            resolved_dataset_ids=["stazioni-appaltanti"],
            resolved_resource_names=["stazioni_appaltanti_csv"],
            plan=[
                DownloadPlanItem(
                    slice=None,
                    dataset_id="stazioni-appaltanti",
                    resource_name="stazioni_appaltanti_csv",
                    source_format="csv",
                    action="download",
                    reason="output_format_raw",
                )
            ],
        )
        parquet_plan = DownloadPlan(
            dataset="cig",
            output_format="parquet",
            requested_scope={},
            normalized_slices=["2025-01"],
            resolved_dataset_ids=["cig-2025"],
            resolved_resource_names=["cig_csv_2025_01"],
            plan=[
                DownloadPlanItem(
                    slice="2025-01",
                    dataset_id="cig-2025",
                    resource_name="cig_csv_2025_01",
                    source_format="csv",
                    action="download_and_load",
                    reason="output_format_parquet",
                )
            ],
        )
        client = Mock()

        with patch("anac_explorator.catalog.download_dataset_resource", return_value=Mock()) as raw_download_mock:
            execute_download_plan(raw_plan, client, force_download=True, force_load=True)

        with patch("anac_explorator.catalog.download_dataset_to_parquet", return_value=Mock()) as parquet_download_mock:
            execute_download_plan(parquet_plan, client, force_download=True, force_load=True)

        raw_download_mock.assert_called_once()
        self.assertTrue(raw_download_mock.call_args.kwargs["force_download"])
        parquet_download_mock.assert_called_once()
        self.assertTrue(parquet_download_mock.call_args.kwargs["force_download"])
        self.assertTrue(parquet_download_mock.call_args.kwargs["force_load"])

    @staticmethod
    def _build_cig_client(resource_url: str) -> Mock:
        """@notice Build a fake CKAN client that exposes one monthly CIG CSV resource."""

        client = Mock()
        client.transport = "http"
        client.package_show.return_value = CkanPackage(
            id="cig-2025",
            name="cig-2025",
            title="CIG 2025",
            notes="Monthly CIG package",
            resources=[
                CkanResource(
                    id="cig-csv-01",
                    name="cig_csv_2025_01",
                    format="CSV",
                    url=resource_url,
                )
            ],
        )
        return client

if __name__ == "__main__":
    unittest.main()
