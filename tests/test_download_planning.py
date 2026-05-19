"""@notice Tests for download planning and family-adapter resource resolution."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from anac_explorator.catalog import (
    DATASET_FAMILY_REGISTRY,
    PeriodizedAdapter,
    SnapshotAdapter,
)
from anac_explorator.errors import CliCommandError
from anac_explorator.models import CkanPackage, CkanResource
from anac_explorator.selection import parse_temporal_selection


class DownloadPlanningTests(unittest.TestCase):
    """@notice Verify the pure planning layer for the future Phase 3 download command."""

    def test_cig_planning_builds_download_plan_for_selected_slices(self) -> None:
        """@notice Resolve monthly CIG slices into one reusable download plan."""

        client = Mock()
        client.package_show.return_value = CkanPackage(
            id="pkg-2025",
            name="cig-2025",
            title="CIG 2025",
            notes="Monthly CIG package",
            resources=[
                CkanResource(id="csv-01", name="cig_csv_2025_01", format="CSV", url="https://example.invalid/01.zip"),
                CkanResource(id="csv-02", name="cig_csv_2025_02", format="CSV", url="https://example.invalid/02.zip"),
                CkanResource(id="json-02", name="cig_json_2025_02", format="JSON", url="https://example.invalid/02.json"),
            ],
        )

        plan = DATASET_FAMILY_REGISTRY.plan_download(
            "cig",
            client,
            selection=parse_temporal_selection(year="2025", month="1-2"),
            source_format="auto",
            output_format="parquet",
        )

        self.assertEqual(plan.dataset, "cig")
        self.assertEqual(plan.output_format, "parquet")
        self.assertEqual(plan.requested_scope["resolved_source_format"], "csv")
        self.assertEqual(plan.normalized_slices, ["2025-01", "2025-02"])
        self.assertEqual(plan.resolved_dataset_ids, ["cig-2025"])
        self.assertEqual(plan.resolved_resource_names, ["cig_csv_2025_01", "cig_csv_2025_02"])
        self.assertEqual(
            [item.to_dict() for item in plan.plan],
            [
                {
                    "slice": "2025-01",
                    "dataset_id": "cig-2025",
                    "resource_name": "cig_csv_2025_01",
                    "source_format": "csv",
                    "action": "download_and_load",
                    "reason": "output_format_parquet",
                },
                {
                    "slice": "2025-02",
                    "dataset_id": "cig-2025",
                    "resource_name": "cig_csv_2025_02",
                    "source_format": "csv",
                    "action": "download_and_load",
                    "reason": "output_format_parquet",
                },
            ],
        )
        client.package_show.assert_called_once_with("cig-2025")

    def test_cig_planning_supports_latest_selection(self) -> None:
        """@notice Resolve `latest` against the live remote slice inventory."""

        client = Mock()
        client.package_show.return_value = CkanPackage(
            id="pkg-2025",
            name="cig-2025",
            title="CIG 2025",
            notes="Monthly CIG package",
            resources=[
                CkanResource(id="csv-01", name="cig_csv_2025_01", format="CSV", url="https://example.invalid/01.zip"),
                CkanResource(id="csv-03", name="cig_csv_2025_03", format="CSV", url="https://example.invalid/03.zip"),
            ],
        )

        plan = DATASET_FAMILY_REGISTRY.plan_download(
            "cig",
            client,
            selection=parse_temporal_selection(latest=True),
            output_format="raw",
        )

        self.assertEqual(plan.normalized_slices, ["2025-03"])
        self.assertEqual([item.resource_name for item in plan.plan], ["cig_csv_2025_03"])
        self.assertEqual(plan.plan[0].action, "download")

    def test_snapshot_planning_rejects_temporal_flags(self) -> None:
        """@notice Reject shared temporal selectors for snapshot download planning."""

        client = Mock()
        with self.assertRaises(CliCommandError) as context:
            DATASET_FAMILY_REGISTRY.plan_download(
                "stazioni-appaltanti",
                client,
                selection=parse_temporal_selection(year="2025"),
                output_format="raw",
            )

        self.assertEqual(context.exception.code, "DATASET_NOT_SUPPORTED")
        client.package_show.assert_not_called()

    def test_snapshot_planning_resolves_one_shot_resource_in_raw_mode(self) -> None:
        """@notice Resolve one snapshot resource without any temporal slices."""

        client = Mock()
        client.package_show.return_value = CkanPackage(
            id="pkg-snapshot",
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
            source_format="auto",
        )

        self.assertEqual(plan.normalized_slices, [])
        self.assertEqual(plan.resolved_dataset_ids, ["stazioni-appaltanti"])
        self.assertEqual(plan.resolved_resource_names, ["stazioni_appaltanti_csv"])
        self.assertEqual(plan.plan[0].slice, None)
        self.assertEqual(plan.plan[0].source_format, "csv")
        self.assertEqual(plan.plan[0].action, "download")

    def test_snapshot_adapter_rejects_parquet_when_warehouse_loading_is_unsupported(self) -> None:
        """@notice Let the adapter own whether warehouse loading is supported."""

        adapter = SnapshotAdapter(
            family="demo-snapshot",
            dataset_id="demo-snapshot",
            supported_source_formats=("csv",),
            warehouse_load_supported=False,
        )
        client = Mock()

        with self.assertRaises(CliCommandError) as context:
            adapter.plan_download(client, output_format="parquet")

        self.assertEqual(context.exception.code, "DATASET_NOT_SUPPORTED")
        client.package_show.assert_not_called()

    def test_periodized_adapter_rejects_unavailable_source_format(self) -> None:
        """@notice Fail early when the requested source format is unsupported for the family."""

        adapter = PeriodizedAdapter(
            family="demo-periodized",
            dataset_prefix="demo",
            first_year=2025,
            last_year=2025,
            supported_source_formats=("csv",),
            warehouse_load_supported=True,
        )
        client = Mock()

        with self.assertRaises(CliCommandError) as context:
            adapter.plan_download(
                client,
                selection=parse_temporal_selection(year="2025"),
                source_format="json",
                output_format="raw",
            )

        self.assertEqual(context.exception.code, "DATASET_NOT_SUPPORTED")
        client.package_show.assert_not_called()


if __name__ == "__main__":
    unittest.main()
