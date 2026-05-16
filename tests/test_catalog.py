"""@notice Tests for the explicit dataset-family registry and adapter dispatch."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from anac_explorator.catalog import DATASET_FAMILY_REGISTRY
from anac_explorator.errors import CliCommandError
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


if __name__ == "__main__":
    unittest.main()

