"""@notice Tests for the explicit dataset-family registry and adapter dispatch."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from anac_explorator.catalog import DATASET_FAMILY_REGISTRY, get_dataset_family, list_dataset_families
from anac_explorator.ckan import CkanClientError
from anac_explorator.errors import CliCommandError
from anac_explorator.models import CkanPackage, CkanResource, DownloadManifest
from anac_explorator.selection import parse_temporal_selection
from anac_explorator.vocabulary import VOCABULARY_DATASET_CONFIGS


class DatasetFamilyRegistryTests(unittest.TestCase):
    """@notice Verify the shared Phase 3 dataset-family registry and adapter interface."""

    def test_registry_includes_required_initial_families_and_vocabularies(self) -> None:
        """@notice Register the required top-level families plus all wired vocabulary datasets."""

        family_ids = [family.dataset for family in DATASET_FAMILY_REGISTRY.list_families()]

        self.assertIn("cig", family_ids)
        self.assertIn("smartcig", family_ids)
        self.assertIn("stazioni-appaltanti", family_ids)
        self.assertIn("aggiudicatari", family_ids)
        for dataset_id in VOCABULARY_DATASET_CONFIGS:
            self.assertIn(dataset_id, family_ids)

    def test_cig_family_metadata_matches_phase3_registry_contract(self) -> None:
        """@notice Expose the explicit family metadata required by the Phase 3 catalog."""

        family = DATASET_FAMILY_REGISTRY.get_family("cig")
        payload = family.to_dict()

        self.assertEqual(payload["title"], "CIG")
        self.assertEqual(payload["category"], "procurement")
        self.assertEqual(payload["coverage_kind"], "periodic_monthly")
        self.assertEqual(payload["available_source_formats"], ["csv", "json"])
        self.assertEqual(payload["remote_first_year"], 2007)
        self.assertEqual(payload["remote_last_year"], 2025)
        self.assertEqual(payload["query_view_name"], "cig")
        self.assertTrue(payload["update_supported"])
        self.assertTrue(payload["dictionary_available"])
        self.assertEqual(payload["remote_dataset_ids"][0], "cig-2007")
        self.assertEqual(payload["remote_dataset_ids"][-1], "cig-2025")
        self.assertEqual(payload["adapter_name"], "CigDatasetFamilyAdapter")

    def test_periodized_family_resolution_uses_shared_temporal_selection(self) -> None:
        """@notice Resolve yearly CKAN package ids from shared canonical temporal selections."""

        self.assertEqual(
            DATASET_FAMILY_REGISTRY.resolve_remote_dataset_ids(
                "smartcig",
                selection=parse_temporal_selection(year="2024-2025", month="2-3"),
            ),
            ["smartcig-2024", "smartcig-2025"],
        )
        self.assertEqual(
            DATASET_FAMILY_REGISTRY.resolve_remote_dataset_ids(
                "cig",
                selection=parse_temporal_selection(latest=True),
            ),
            ["cig-2025"],
        )

    def test_snapshot_family_rejects_temporal_selection(self) -> None:
        """@notice Reject shared temporal selectors for snapshot-only families."""

        with self.assertRaises(CliCommandError) as context:
            DATASET_FAMILY_REGISTRY.resolve_remote_dataset_ids(
                "stazioni-appaltanti",
                selection=parse_temporal_selection(year="2025"),
            )

        self.assertEqual(context.exception.code, "DATASET_NOT_SUPPORTED")

    def test_registry_resolves_raw_dataset_ids_back_to_logical_families(self) -> None:
        """@notice Map raw CKAN package ids back to their stable logical family ids."""

        self.assertEqual(DATASET_FAMILY_REGISTRY.resolve_family_for_dataset_id("cig-2025").dataset, "cig")
        self.assertEqual(DATASET_FAMILY_REGISTRY.resolve_family_for_dataset_id("smartcig-2011").dataset, "smartcig")
        self.assertEqual(
            DATASET_FAMILY_REGISTRY.resolve_family_for_dataset_id("bandi-cig-tipo-scelta-contraente").dataset,
            "bandi-cig-tipo-scelta-contraente",
        )
        self.assertIsNone(DATASET_FAMILY_REGISTRY.resolve_family_for_dataset_id("unknown-dataset"))

    def test_cig_download_dispatch_uses_family_adapter(self) -> None:
        """@notice Route direct-to-Parquet downloads through the CIG family adapter."""

        expected_result = Mock()
        client = Mock()
        with patch("anac_explorator.catalog.download_dataset_to_parquet", return_value=expected_result) as download_mock:
            result = DATASET_FAMILY_REGISTRY.download_to_parquet(
                "cig",
                client,
                dataset_id="cig-2025",
                output_dir=Path("data/raw"),
                schemas_dir=Path("schemas"),
                warehouse_dir=Path("data/warehouse"),
                preferred_resource_name="cig_csv_2025_01",
                schema_path=None,
                vocabulary_index_path=Path("vocabularies/index.json"),
                delimiter=";",
                encoding="utf-8-sig",
                schema_sample_limit=2000,
                keep_materialized=False,
                register_crosswalks=True,
            )

        self.assertIs(result, expected_result)
        download_mock.assert_called_once()
        self.assertEqual(download_mock.call_args.kwargs["dataset_id"], "cig-2025")

    def test_unsupported_download_dispatch_raises_stable_error(self) -> None:
        """@notice Raise DATASET_NOT_SUPPORTED when a known family lacks a download adapter."""

        with self.assertRaises(CliCommandError) as context:
            DATASET_FAMILY_REGISTRY.download_to_parquet(
                "smartcig",
                Mock(),
                dataset_id="smartcig-2025",
                output_dir=Path("data/raw"),
                schemas_dir=Path("schemas"),
                warehouse_dir=Path("data/warehouse"),
                preferred_resource_name=None,
                schema_path=None,
                vocabulary_index_path=Path("vocabularies/index.json"),
                delimiter=";",
                encoding="utf-8-sig",
                schema_sample_limit=2000,
                keep_materialized=False,
                register_crosswalks=True,
            )

        self.assertEqual(context.exception.code, "DATASET_NOT_SUPPORTED")

    def test_cig_update_dispatch_uses_family_adapter(self) -> None:
        """@notice Route incremental updates through the CIG family adapter."""

        expected_result = Mock()
        client = Mock()
        with patch("anac_explorator.catalog.sync_cig_periods_to_parquet", return_value=expected_result) as sync_mock:
            result = DATASET_FAMILY_REGISTRY.update(
                "cig",
                client,
                dataset_id="cig-2025",
                output_dir=Path("data/raw"),
                schemas_dir=Path("schemas"),
                warehouse_dir=Path("data/warehouse"),
                periods=["2025_01"],
                period_start=None,
                period_end=None,
                vocabulary_index_path=Path("vocabularies/index.json"),
                delimiter=";",
                encoding="utf-8-sig",
                schema_sample_limit=2000,
                keep_materialized=False,
                register_crosswalks=True,
                refresh_changed=False,
            )

        self.assertIs(result, expected_result)
        sync_mock.assert_called_once()
        self.assertEqual(sync_mock.call_args.kwargs["dataset_id"], "cig-2025")

    def test_unsupported_update_dispatch_raises_stable_error(self) -> None:
        """@notice Raise DATASET_UPDATE_NOT_SUPPORTED for non-updatable families."""

        with self.assertRaises(CliCommandError) as context:
            DATASET_FAMILY_REGISTRY.update(
                "aggiudicatari",
                Mock(),
                dataset_id="aggiudicatari",
                output_dir=Path("data/raw"),
                schemas_dir=Path("schemas"),
                warehouse_dir=Path("data/warehouse"),
                periods=None,
                period_start=None,
                period_end=None,
                vocabulary_index_path=Path("vocabularies/index.json"),
                delimiter=";",
                encoding="utf-8-sig",
                schema_sample_limit=2000,
                keep_materialized=False,
                register_crosswalks=True,
                refresh_changed=False,
            )

        self.assertEqual(context.exception.code, "DATASET_UPDATE_NOT_SUPPORTED")

    def test_list_dataset_families_applies_search_and_state_filters(self) -> None:
        """@notice Filter the catalog by free text, remote year, source format, and local state."""

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            db_path = project_root / "data" / "warehouse" / "anac.duckdb"
            self._write_manifest(
                project_root,
                DownloadManifest(
                    dataset_id="cig-2025",
                    resource_id="demo-id",
                    resource_name="cig_csv_2025_01",
                    resource_format="csv",
                    resource_url="https://example.invalid/cig_csv_2025_01.zip",
                    transport="playwright",
                    archive_path="data/raw/cig-2025/cig_csv_2025_01/cig_csv_2025_01.zip",
                    materialized_path="data/raw/cig-2025/cig_csv_2025_01/extracted/cig_csv_2025_01.csv",
                    materialized_kind="csv",
                    cache_status="fresh",
                    resume_supported=False,
                    downloaded_at="2026-05-19T12:00:00",
                ),
            )

            downloaded = list_dataset_families(
                db_path=db_path,
                raw_dir=project_root / "data" / "raw",
                schemas_dir=project_root / "schemas",
                dictionaries_dir=project_root / "dictionaries",
                vocabulary_index_path=project_root / "vocabularies" / "index.json",
                downloaded=True,
            )
            missing = list_dataset_families(
                db_path=db_path,
                raw_dir=project_root / "data" / "raw",
                schemas_dir=project_root / "schemas",
                dictionaries_dir=project_root / "dictionaries",
                vocabulary_index_path=project_root / "vocabularies" / "index.json",
                missing=True,
            )
            searched = list_dataset_families(
                db_path=db_path,
                raw_dir=project_root / "data" / "raw",
                schemas_dir=project_root / "schemas",
                dictionaries_dir=project_root / "dictionaries",
                vocabulary_index_path=project_root / "vocabularies" / "index.json",
                search="smart",
                year=2025,
                source_format="json",
            )

        self.assertEqual([item.dataset for item in downloaded.items], ["cig"])
        self.assertNotIn("cig", [item.dataset for item in missing.items])
        self.assertEqual([item.dataset for item in searched.items], ["smartcig"])
        self.assertEqual(downloaded.filters, {"downloaded": True})
        self.assertEqual(missing.filters, {"missing": True})
        self.assertEqual(
            searched.filters,
            {"search": "smart", "year": 2025, "source_format": "json"},
        )

    def test_get_dataset_family_returns_detail_payload_and_remote_warning_when_live_refresh_fails(self) -> None:
        """@notice Keep local and registry detail visible when live CKAN metadata fails."""

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            db_path = project_root / "data" / "warehouse" / "anac.duckdb"
            self._write_manifest(
                project_root,
                DownloadManifest(
                    dataset_id="cig-2025",
                    resource_id="demo-id",
                    resource_name="cig_csv_2025_01",
                    resource_format="csv",
                    resource_url="https://example.invalid/cig_csv_2025_01.zip",
                    transport="playwright",
                    archive_path="data/raw/cig-2025/cig_csv_2025_01/cig_csv_2025_01.zip",
                    materialized_path="data/raw/cig-2025/cig_csv_2025_01/extracted/cig_csv_2025_01.csv",
                    materialized_kind="csv",
                    cache_status="fresh",
                    resume_supported=False,
                    downloaded_at="2026-05-19T12:00:00",
                ),
            )
            client = Mock()
            client.package_show.side_effect = CkanClientError("blocked or filtered by WAF")

            result = get_dataset_family(
                "cig",
                db_path=db_path,
                raw_dir=project_root / "data" / "raw",
                schemas_dir=project_root / "schemas",
                dictionaries_dir=project_root / "dictionaries",
                vocabulary_index_path=project_root / "vocabularies" / "index.json",
                client=client,
            )

        payload = result.to_dict()
        self.assertEqual(payload["dataset"], "cig")
        self.assertEqual(payload["title"], "CIG")
        self.assertEqual(payload["category"], "procurement")
        self.assertEqual(payload["coverage_kind"], "periodic_monthly")
        self.assertEqual(payload["local_status"], "raw")
        self.assertEqual(payload["query_view_name"], "cig")
        self.assertTrue(payload["update_supported"])
        self.assertTrue(payload["dictionary_available"])
        self.assertEqual(payload["vocabulary_views"], [])
        self.assertEqual(payload["remote_coverage"]["status"], "registry")
        self.assertEqual(result.warnings[0].code, "REMOTE_METADATA_UNAVAILABLE")
        self.assertEqual(result.warnings[0].details["dataset"], "cig")

    def test_get_dataset_family_uses_live_remote_metadata_when_available(self) -> None:
        """@notice Enrich detail mode with live package metadata when CKAN access works."""

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            db_path = project_root / "data" / "warehouse" / "anac.duckdb"
            client = Mock()
            client.package_show.return_value = CkanPackage(
                id="demo-package",
                name="stazioni-appaltanti",
                title="Stazioni appaltanti",
                notes="Snapshot registry",
                resources=[
                    CkanResource(
                        id="resource-1",
                        name="stazioni_appaltanti_csv",
                        format="CSV",
                        url="https://example.invalid/stazioni.csv",
                        last_modified="2026-05-19T10:00:00",
                    )
                ],
            )

            result = get_dataset_family(
                "stazioni-appaltanti",
                db_path=db_path,
                raw_dir=project_root / "data" / "raw",
                schemas_dir=project_root / "schemas",
                dictionaries_dir=project_root / "dictionaries",
                vocabulary_index_path=project_root / "vocabularies" / "index.json",
                client=client,
            )

        self.assertEqual(result.warnings, [])
        self.assertEqual(result.to_dict()["remote_coverage"]["status"], "live")
        self.assertEqual(result.to_dict()["remote_coverage"]["packages"][0]["resource_count"], 1)

    def test_get_dataset_family_raises_stable_not_found_for_unknown_family(self) -> None:
        """@notice Surface DATASET_NOT_FOUND when detail mode targets an unknown logical family."""

        with self.assertRaises(CliCommandError) as context:
            get_dataset_family("unknown-family")

        self.assertEqual(context.exception.code, "DATASET_NOT_FOUND")

    def _write_manifest(self, project_root: Path, manifest: DownloadManifest) -> None:
        """@notice Persist one manifest under the expected raw-data directory layout."""

        manifest_path = project_root / "data" / "raw" / manifest.dataset_id / manifest.resource_name / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
