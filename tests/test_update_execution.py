"""@notice Tests for the Phase 3 update execution and orchestration backend."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import duckdb

from anac_explorator.catalog import run_dataset_update, run_global_update
from anac_explorator.models import (
    CkanPackage,
    CkanResource,
    DatasetIncrementalUpdateResult,
    DatasetParquetDownloadResult,
    DownloadManifest,
    UpdateCommandResult,
    WarehouseLoadResult,
)
from anac_explorator.selection import parse_temporal_selection


class UpdateExecutionTests(unittest.TestCase):
    """@notice Verify apply-mode orchestration for the Phase 3 update backend."""

    def test_run_dataset_update_applies_only_newer_periods_by_default(self) -> None:
        """@notice Forward updates should execute only periods newer than the latest local slice."""

        with tempfile.TemporaryDirectory() as temp_dir:
            warehouse_dir = Path(temp_dir) / "warehouse"
            self._write_local_cig_period_record(warehouse_dir)

            client = Mock()
            client.package_show.return_value = self._build_cig_package(
                january_modified="2026-05-01T08:00:00",
                february_modified="2026-05-02T08:00:00",
            )

            with patch("anac_explorator.catalog.sync_cig_periods_to_parquet") as mock_sync:
                mock_sync.return_value = self._build_sync_result("cig-2025", ["2025_02"])

                result = run_dataset_update(
                    "cig",
                    client,
                    warehouse_dir=warehouse_dir,
                )

        self.assertEqual([item.slice for item in result.plan], ["2025-02"])
        self.assertEqual([item.action for item in result.plan], ["download"])
        self.assertEqual([item.resource_name for item in result.plan], ["cig_csv_2025_02"])
        self.assertEqual([item.resource_name for item in result.applied], ["cig_csv_2025_02"])
        self.assertEqual(mock_sync.call_count, 1)
        self.assertEqual(mock_sync.call_args.kwargs["dataset_id"], "cig-2025")
        self.assertEqual(mock_sync.call_args.kwargs["periods"], ["2025_02"])
        self.assertFalse(mock_sync.call_args.kwargs["refresh_changed"])

    def test_run_dataset_update_refreshes_changed_periods_when_requested(self) -> None:
        """@notice Refresh mode should re-queue changed local periods alongside newer ones."""

        with tempfile.TemporaryDirectory() as temp_dir:
            warehouse_dir = Path(temp_dir) / "warehouse"
            self._write_local_cig_period_record(warehouse_dir)

            client = Mock()
            client.package_show.side_effect = lambda dataset_id: (
                self._build_cig_package(
                    january_modified="2026-06-01T08:00:00",
                    february_modified="2026-06-02T08:00:00",
                )
                if dataset_id == "cig-2025"
                else CkanPackage(
                    id=f"pkg-{dataset_id}",
                    name=dataset_id,
                    title=dataset_id,
                    notes="Older CIG package",
                    resources=[],
                )
            )

            with patch("anac_explorator.catalog.sync_cig_periods_to_parquet") as mock_sync:
                mock_sync.return_value = self._build_sync_result("cig-2025", ["2025_01", "2025_02"])

                result = run_dataset_update(
                    "cig",
                    client,
                    warehouse_dir=warehouse_dir,
                    refresh_changed=True,
                )

        self.assertEqual([item.slice for item in result.plan], ["2025-01", "2025-02"])
        self.assertEqual([item.action for item in result.plan], ["refresh", "download"])
        self.assertEqual([item.resource_name for item in result.applied], ["cig_csv_2025_01", "cig_csv_2025_02"])
        self.assertEqual(mock_sync.call_count, 1)
        self.assertEqual(mock_sync.call_args.kwargs["dataset_id"], "cig-2025")
        self.assertEqual(mock_sync.call_args.kwargs["periods"], ["2025_01", "2025_02"])
        self.assertTrue(mock_sync.call_args.kwargs["refresh_changed"])

    def test_run_global_update_targets_only_locally_present_update_capable_families(self) -> None:
        """@notice Global mode should only dispatch families discovered from the current local workspace."""

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            db_path = project_root / "data" / "warehouse" / "anac.duckdb"
            self._write_manifest(
                project_root,
                DownloadManifest(
                    dataset_id="cig-2025",
                    resource_id="cig-01",
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
            self._write_manifest(
                project_root,
                DownloadManifest(
                    dataset_id="aggiudicatari-2025",
                    resource_id="aggiudicatari-01",
                    resource_name="aggiudicatari_csv_2025_01",
                    resource_format="csv",
                    resource_url="https://example.invalid/aggiudicatari_csv_2025_01.zip",
                    transport="playwright",
                    archive_path="data/raw/aggiudicatari-2025/aggiudicatari_csv_2025_01/aggiudicatari_csv_2025_01.zip",
                    materialized_path="data/raw/aggiudicatari-2025/aggiudicatari_csv_2025_01/extracted/aggiudicatari_csv_2025_01.csv",
                    materialized_kind="csv",
                    cache_status="fresh",
                    resume_supported=False,
                    downloaded_at="2026-05-19T12:00:00",
                ),
            )

            with patch("anac_explorator.catalog.run_dataset_update") as mock_run_dataset_update:
                mock_run_dataset_update.return_value = UpdateCommandResult(
                    scope={"dataset": "cig"},
                    latest_local_state={},
                    plan=[],
                )

                result = run_global_update(
                    Mock(),
                    db_path=db_path,
                    raw_dir=project_root / "data" / "raw",
                    schemas_dir=project_root / "schemas",
                    dictionaries_dir=project_root / "dictionaries",
                    vocabulary_index_path=project_root / "vocabularies" / "index.json",
                    warehouse_dir=project_root / "data" / "warehouse",
                    dry_run=True,
                )

        self.assertEqual(result.scope["mode"], "global")
        self.assertEqual(result.scope["datasets"], ["cig"])
        self.assertEqual(mock_run_dataset_update.call_count, 1)
        self.assertEqual(mock_run_dataset_update.call_args.args[0], "cig")

    def test_run_dataset_update_force_full_rebuilds_selected_scope(self) -> None:
        """@notice Force-full execution should refresh already loaded slices inside the selected scope."""

        with tempfile.TemporaryDirectory() as temp_dir:
            warehouse_dir = Path(temp_dir) / "warehouse"
            self._write_local_cig_period_record(warehouse_dir)

            client = Mock()
            client.package_show.return_value = self._build_cig_package(
                january_modified="2026-05-01T08:00:00",
                february_modified="2026-05-02T08:00:00",
            )

            with patch("anac_explorator.catalog.sync_cig_periods_to_parquet") as mock_sync:
                mock_sync.return_value = self._build_sync_result("cig-2025", ["2025_01"])

                result = run_dataset_update(
                    "cig",
                    client,
                    selection=parse_temporal_selection(year="2025", month="1"),
                    warehouse_dir=warehouse_dir,
                    force_full=True,
                )

        self.assertEqual([item.slice for item in result.plan], ["2025-01"])
        self.assertEqual([item.action for item in result.plan], ["refresh"])
        self.assertEqual([item.reason for item in result.plan], ["force_full"])
        self.assertEqual([item.resource_name for item in result.applied], ["cig_csv_2025_01"])
        self.assertEqual(mock_sync.call_count, 1)
        self.assertEqual(mock_sync.call_args.kwargs["dataset_id"], "cig-2025")
        self.assertEqual(mock_sync.call_args.kwargs["periods"], ["2025_01"])
        self.assertTrue(mock_sync.call_args.kwargs["force_full"])

    @staticmethod
    def _build_cig_package(*, january_modified: str, february_modified: str) -> CkanPackage:
        """@notice Build a minimal remote CIG package fixture with two monthly slices."""

        return CkanPackage(
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
                    last_modified=january_modified,
                ),
                CkanResource(
                    id="csv-02",
                    name="cig_csv_2025_02",
                    format="CSV",
                    url="https://example.invalid/02.zip",
                    size=256,
                    last_modified=february_modified,
                ),
            ],
        )

    @staticmethod
    def _build_sync_result(dataset_id: str, periods: list[str]) -> DatasetIncrementalUpdateResult:
        """@notice Create one lightweight incremental-sync result for the mocked adapter execution."""

        return DatasetIncrementalUpdateResult(
            dataset_type="cig",
            dataset_id=dataset_id,
            selection_mode="explicit",
            latest_local_period="2025_01",
            requested_periods=periods,
            applied_loads=[UpdateExecutionTests._build_applied_load(dataset_id, period) for period in periods],
            duckdb_path="data/warehouse/anac.duckdb",
        )

    @staticmethod
    def _build_applied_load(dataset_id: str, period: str) -> DatasetParquetDownloadResult:
        """@notice Build one minimal Parquet load artifact for a mocked update run."""

        resource_name = f"cig_csv_{period}"
        return DatasetParquetDownloadResult(
            dataset_id=dataset_id,
            resource_name=resource_name,
            manifest_path=f"data/raw/{dataset_id}/{resource_name}/manifest.json",
            download_cache_status="fresh",
            schema_path="schemas/cig.schema.json",
            schema_generated=False,
            removed_materialized_path=False,
            load_result=WarehouseLoadResult(
                dataset_id=dataset_id,
                resource_name=resource_name,
                table_name="cig",
                view_name="cig",
                manifest_path=f"data/raw/{dataset_id}/{resource_name}/manifest.json",
                schema_path="schemas/cig.schema.json",
                source_path=f"data/raw/{dataset_id}/{resource_name}/extracted/{resource_name}.csv",
                warehouse_dir="data/warehouse",
                duckdb_path="data/warehouse/anac.duckdb",
                parquet_root="data/warehouse/parquet/cig",
                parquet_path=f"data/warehouse/parquet/cig/year={period[:4]}/month={period[5:7]}/{resource_name}.parquet",
                row_count=1,
                load_status="fresh",
            ),
        )

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

    @staticmethod
    def _write_manifest(project_root: Path, manifest: DownloadManifest) -> None:
        """@notice Persist one manifest under the expected raw-data directory layout."""

        manifest_path = project_root / "data" / "raw" / manifest.dataset_id / manifest.resource_name / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
