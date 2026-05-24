"""@notice Tests for the Phase 3 update planning backend."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import duckdb

from anac_explorator.catalog import DATASET_FAMILY_REGISTRY
from anac_explorator.errors import CliCommandError
from anac_explorator.models import CkanPackage, CkanResource
from anac_explorator.selection import parse_temporal_selection


class UpdatePlanningTests(unittest.TestCase):
    """@notice Verify the dry-run planning layer for the future Phase 3 update command."""

    def test_cig_update_planning_returns_forward_plan_from_local_state(self) -> None:
        """@notice Compare local and remote monthly CIG state and emit the incremental dry-run plan."""

        with tempfile.TemporaryDirectory() as temp_dir:
            warehouse_dir = Path(temp_dir) / "warehouse"
            self._write_local_cig_period_record(warehouse_dir)

            client = Mock()
            client.package_show.return_value = CkanPackage(
                id="pkg-2025",
                name="cig-2025",
                title="CIG 2025",
                notes="Monthly CIG package",
                resources=[
                    CkanResource(
                        id="csv-01",
                        name="cig_csv_2025_01",
                        format="CSV",
                        url="https://example.invalid/01.zip",
                        size=128,
                        last_modified="2026-05-01T08:00:00",
                    ),
                    CkanResource(
                        id="csv-02",
                        name="cig_csv_2025_02",
                        format="CSV",
                        url="https://example.invalid/02.zip",
                        size=256,
                        last_modified="2026-05-02T08:00:00",
                    ),
                ],
            )

            plan = DATASET_FAMILY_REGISTRY.plan_update(
                "cig",
                client,
                warehouse_dir=warehouse_dir,
            )

        self.assertEqual(plan.dataset, "cig")
        self.assertEqual(plan.scope["selection_mode"], "forward")
        self.assertFalse(plan.scope["refresh_changed"])
        self.assertEqual(plan.latest_local_state["latest_period"], "2025_01")
        self.assertEqual(plan.latest_local_state["latest_slice"], "2025-01")
        self.assertEqual(plan.latest_local_state["local_period_count"], 1)
        self.assertEqual(plan.latest_local_state["local_periods"], ["2025_01"])
        self.assertEqual(plan.resolved_dataset_ids, ["cig-2025"])
        self.assertEqual(plan.requested_periods, ["2025_02"])
        self.assertEqual(
            [item.to_dict() for item in plan.plan],
            [
                {
                    "dataset": "cig",
                    "slice": "2025-02",
                    "dataset_id": "cig-2025",
                    "resource_name": "cig_csv_2025_02",
                    "action": "download",
                    "reason": "newer_than_latest_local",
                    "remote_modified": "2026-05-02T08:00:00",
                    "remote_size": 256,
                    "manifest_path": None,
                    "parquet_path": None,
                    "content_checksum": None,
                }
            ],
        )
        client.package_show.assert_called_once_with("cig-2025")

    def test_unsupported_family_update_planning_raises_stable_error(self) -> None:
        """@notice Reject dry-run update planning for families without an incremental adapter."""

        client = Mock()

        with self.assertRaises(CliCommandError) as context:
            DATASET_FAMILY_REGISTRY.plan_update("aggiudicatari", client)

        self.assertEqual(context.exception.code, "DATASET_UPDATE_NOT_SUPPORTED")
        client.package_show.assert_not_called()

    def test_cig_update_planning_force_full_rebuilds_selected_slice(self) -> None:
        """@notice Force-full planning should refresh selected loaded slices even without remote drift."""

        with tempfile.TemporaryDirectory() as temp_dir:
            warehouse_dir = Path(temp_dir) / "warehouse"
            self._write_local_cig_period_record(warehouse_dir)

            client = Mock()
            client.package_show.return_value = CkanPackage(
                id="pkg-2025",
                name="cig-2025",
                title="CIG 2025",
                notes="Monthly CIG package",
                resources=[
                    CkanResource(
                        id="csv-01",
                        name="cig_csv_2025_01",
                        format="CSV",
                        url="https://example.invalid/01.zip",
                        size=128,
                        last_modified="2026-05-01T08:00:00",
                    ),
                    CkanResource(
                        id="csv-02",
                        name="cig_csv_2025_02",
                        format="CSV",
                        url="https://example.invalid/02.zip",
                        size=256,
                        last_modified="2026-05-02T08:00:00",
                    ),
                ],
            )

            plan = DATASET_FAMILY_REGISTRY.plan_update(
                "cig",
                client,
                selection=parse_temporal_selection(year="2025", month="1"),
                warehouse_dir=warehouse_dir,
                force_full=True,
            )

        self.assertEqual(plan.dataset, "cig")
        self.assertEqual(plan.scope["selection_mode"], "range")
        self.assertTrue(plan.scope["force_full"])
        self.assertEqual(plan.scope["requested_slices"], ["2025-01"])
        self.assertEqual(plan.requested_periods, ["2025_01"])
        self.assertEqual(
            [item.to_dict() for item in plan.plan],
            [
                {
                    "dataset": "cig",
                    "slice": "2025-01",
                    "dataset_id": "cig-2025",
                    "resource_name": "cig_csv_2025_01",
                    "action": "refresh",
                    "reason": "force_full",
                    "remote_modified": "2026-05-01T08:00:00",
                    "remote_size": 128,
                    "manifest_path": "data/raw/cig-2025/cig_csv_2025_01/manifest.json",
                    "parquet_path": "data/warehouse/parquet/cig/year=2025/month=01/cig_csv_2025_01.parquet",
                    "content_checksum": "abc123",
                }
            ],
        )
        client.package_show.assert_called_once_with("cig-2025")

    @staticmethod
    def _write_local_cig_period_record(warehouse_dir: Path) -> None:
        """@notice Seed one local CIG period record in the warehouse manifest catalog."""

        warehouse_dir.mkdir(parents=True, exist_ok=True)
        duckdb_path = warehouse_dir / "anac.duckdb"
        connection = duckdb.connect(str(duckdb_path))
        try:
            connection.execute(
                """
                CREATE TABLE dataset_period_manifest (
                    dataset_type VARCHAR NOT NULL,
                    period VARCHAR NOT NULL,
                    dataset_id VARCHAR NOT NULL,
                    resource_name VARCHAR NOT NULL,
                    manifest_path VARCHAR NOT NULL,
                    parquet_path VARCHAR NOT NULL,
                    resource_id VARCHAR,
                    resource_url VARCHAR,
                    remote_modified VARCHAR,
                    remote_size BIGINT,
                    content_checksum VARCHAR,
                    row_count BIGINT,
                    imported_at VARCHAR NOT NULL,
                    refreshed_at VARCHAR NOT NULL,
                    PRIMARY KEY (dataset_type, period)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO dataset_period_manifest VALUES (
                    'cig',
                    '2025_01',
                    'cig-2025',
                    'cig_csv_2025_01',
                    'data/raw/cig-2025/cig_csv_2025_01/manifest.json',
                    'data/warehouse/parquet/cig/year=2025/month=01/cig_csv_2025_01.parquet',
                    'csv-01',
                    'https://example.invalid/01.zip',
                    '2026-05-01T08:00:00',
                    128,
                    'abc123',
                    10,
                    '2026-05-10T09:00:00',
                    '2026-05-10T10:00:00'
                )
                """
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
