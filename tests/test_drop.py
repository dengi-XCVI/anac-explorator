"""@notice Tests for the Phase 3 drop-planning backend."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from anac_explorator.catalog import DATASET_FAMILY_REGISTRY
from anac_explorator.metadata_views import ensure_metadata_views
from anac_explorator.selection import parse_temporal_selection


class DropPlanningTests(unittest.TestCase):
    """@notice Verify the dry-run planning layer for the future Phase 3 drop command."""

    def test_cig_drop_planning_returns_target_files_and_byte_totals_for_selected_slice(self) -> None:
        """@notice Resolve the selected local CIG slice into concrete raw and Parquet file targets."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            warehouse_dir = root / "warehouse"

            selected = self._seed_local_cig_slice(root, warehouse_dir, year=2025, month=1, resource_id="csv-01")
            self._seed_local_cig_slice(root, warehouse_dir, year=2024, month=12, resource_id="csv-12")

            plan = DATASET_FAMILY_REGISTRY.build_drop_plan(
                "cig",
                scope=parse_temporal_selection(year="2025", month="1"),
                layers="all",
                warehouse_dir=warehouse_dir,
            )

        self.assertEqual(plan.dataset, "cig")
        self.assertEqual(plan.layer, "all")
        self.assertEqual(plan.scope["selection_mode"], "range")
        self.assertEqual(plan.scope["selected_slices"], ["2025-01"])
        self.assertEqual(
            sorted(target.path for target in plan.targets),
            sorted(selected["all_paths"]),
        )
        self.assertEqual(plan.total_size_bytes, selected["all_size"])
        self.assertEqual(plan.to_dict()["totals"]["target_count"], 4)
        self.assertEqual(
            sorted((target.layer, target.target_kind) for target in plan.targets),
            [
                ("parquet", "parquet"),
                ("raw", "archive"),
                ("raw", "manifest"),
                ("raw", "materialized_csv"),
            ],
        )

    def test_cig_drop_planning_honors_raw_layer_filter(self) -> None:
        """@notice Restrict the plan to raw-layer files while leaving Parquet out of scope."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            warehouse_dir = root / "warehouse"

            selected = self._seed_local_cig_slice(root, warehouse_dir, year=2025, month=1, resource_id="csv-01")

            plan = DATASET_FAMILY_REGISTRY.build_drop_plan(
                "cig",
                scope=parse_temporal_selection(year="2025", month="1"),
                layers="raw",
                warehouse_dir=warehouse_dir,
            )

        self.assertEqual(plan.layer, "raw")
        self.assertEqual(
            sorted(target.path for target in plan.targets),
            sorted(selected["raw_paths"]),
        )
        self.assertTrue(all(target.layer == "raw" for target in plan.targets))
        self.assertEqual(plan.total_size_bytes, selected["raw_size"])

    def test_cig_drop_execution_raw_layer_removes_files_and_prunes_manifest_references(self) -> None:
        """@notice Drop raw cache files while keeping Parquet query state and pruning stale raw references."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            warehouse_dir = root / "warehouse"
            duckdb_path = warehouse_dir / "anac.duckdb"

            selected = self._seed_local_cig_slice(
                root,
                warehouse_dir,
                year=2025,
                month=1,
                resource_id="csv-01",
                seed_loaded_catalog=True,
            )

            plan = DATASET_FAMILY_REGISTRY.build_drop_plan(
                "cig",
                scope=parse_temporal_selection(year="2025", month="1"),
                layers="raw",
                warehouse_dir=warehouse_dir,
            )
            deleted = DATASET_FAMILY_REGISTRY.apply_drop_plan(
                "cig",
                plan=plan,
                warehouse_dir=warehouse_dir,
            )

            connection = duckdb.connect(str(duckdb_path))
            try:
                loaded_row = connection.execute(
                    """
                    SELECT manifest_path, source_path, parquet_path
                    FROM loaded_resources
                    """
                ).fetchone()
                period_row = connection.execute(
                    """
                    SELECT manifest_path, parquet_path
                    FROM dataset_period_manifest
                    """
                ).fetchone()
                ensure_metadata_views(
                    connection,
                    db_path=duckdb_path,
                    raw_dir=root / "raw",
                )
                metadata_loaded_row = connection.execute(
                    """
                    SELECT manifest_path, source_path, parquet_path
                    FROM anac_loaded_resources
                    """
                ).fetchone()
                metadata_partition_row = connection.execute(
                    """
                    SELECT manifest_path, parquet_path
                    FROM anac_partitions
                    """
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(sorted(target.path for target in deleted), sorted(selected["raw_paths"]))
            self.assertTrue(all(not Path(path).exists() for path in selected["raw_paths"]))
            self.assertTrue(Path(selected["parquet_path"]).exists())
            self.assertIsNotNone(loaded_row)
            self.assertTrue(str(loaded_row[0]).startswith("pruned://"))
            self.assertTrue(str(loaded_row[1]).startswith("pruned://"))
            self.assertEqual(str(loaded_row[2]), selected["parquet_path"])
            self.assertIsNotNone(period_row)
            self.assertTrue(str(period_row[0]).startswith("pruned://"))
            self.assertEqual(str(period_row[1]), selected["parquet_path"])
            self.assertEqual(str(metadata_loaded_row[0]), str(loaded_row[0]))
            self.assertEqual(str(metadata_loaded_row[1]), str(loaded_row[1]))
            self.assertEqual(str(metadata_loaded_row[2]), selected["parquet_path"])
            self.assertEqual(str(metadata_partition_row[0]), str(period_row[0]))
            self.assertEqual(str(metadata_partition_row[1]), selected["parquet_path"])

    def test_cig_drop_execution_all_layer_removes_files_and_catalog_rows(self) -> None:
        """@notice Drop both raw and Parquet files and prune the associated warehouse metadata."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            warehouse_dir = root / "warehouse"
            duckdb_path = warehouse_dir / "anac.duckdb"

            selected = self._seed_local_cig_slice(
                root,
                warehouse_dir,
                year=2025,
                month=1,
                resource_id="csv-01",
                seed_loaded_catalog=True,
            )

            plan = DATASET_FAMILY_REGISTRY.build_drop_plan(
                "cig",
                scope=parse_temporal_selection(year="2025", month="1"),
                layers="all",
                warehouse_dir=warehouse_dir,
            )
            deleted = DATASET_FAMILY_REGISTRY.apply_drop_plan(
                "cig",
                plan=plan,
                warehouse_dir=warehouse_dir,
            )

            connection = duckdb.connect(str(duckdb_path))
            try:
                loaded_count = int(connection.execute("SELECT COUNT(*) FROM loaded_resources").fetchone()[0])
                period_count = int(connection.execute("SELECT COUNT(*) FROM dataset_period_manifest").fetchone()[0])
                registered_count = int(connection.execute("SELECT COUNT(*) FROM registered_views").fetchone()[0])
                ensure_metadata_views(
                    connection,
                    db_path=duckdb_path,
                    raw_dir=root / "raw",
                )
                metadata_loaded_count = int(connection.execute("SELECT COUNT(*) FROM anac_loaded_resources").fetchone()[0])
                metadata_partition_count = int(connection.execute("SELECT COUNT(*) FROM anac_partitions").fetchone()[0])
            finally:
                connection.close()

            self.assertEqual(sorted(target.path for target in deleted), sorted(selected["all_paths"]))
            self.assertTrue(all(not Path(path).exists() for path in selected["all_paths"]))
            self.assertEqual(loaded_count, 0)
            self.assertEqual(period_count, 0)
            self.assertEqual(registered_count, 0)
            self.assertEqual(metadata_loaded_count, 0)
            self.assertEqual(metadata_partition_count, 0)

    @staticmethod
    def _seed_local_cig_slice(
        root: Path,
        warehouse_dir: Path,
        *,
        year: int,
        month: int,
        resource_id: str,
        seed_loaded_catalog: bool = False,
    ) -> dict[str, object]:
        """@notice Create one local CIG raw/Parquet slice plus its warehouse period record."""

        dataset_id = f"cig-{year:04d}"
        resource_name = f"cig_csv_{year:04d}_{month:02d}"
        resource_dir = root / "raw" / dataset_id / resource_name
        extracted_dir = resource_dir / "extracted"
        extracted_dir.mkdir(parents=True, exist_ok=True)

        archive_path = resource_dir / f"{resource_name}.zip"
        archive_path.write_bytes(b"a" * (10 + month))

        materialized_path = extracted_dir / f"{resource_name}.csv"
        materialized_path.write_bytes((f"cig;anno\n{resource_id};{year}\n").encode("utf-8"))

        manifest_path = resource_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "dataset_id": dataset_id,
                    "resource_id": resource_id,
                    "resource_name": resource_name,
                    "resource_format": "CSV",
                    "resource_url": f"https://example.invalid/{resource_name}.zip",
                    "transport": "playwright",
                    "archive_path": str(archive_path),
                    "materialized_path": str(materialized_path),
                    "materialized_kind": "csv",
                    "cache_status": "fresh",
                    "resume_supported": False,
                    "source_size": archive_path.stat().st_size,
                    "source_last_modified": "2026-05-24T20:00:00",
                    "downloaded_at": "2026-05-24T20:05:00",
                }
            ),
            encoding="utf-8",
        )

        parquet_path = warehouse_dir / "parquet" / "cig" / f"year={year:04d}" / f"month={month:02d}" / f"{resource_name}.parquet"
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        parquet_connection = duckdb.connect()
        try:
            parquet_connection.execute(
                f"""
                COPY (
                    SELECT
                        '{resource_id}' AS cig,
                        {year} AS anno
                ) TO '{str(parquet_path).replace("'", "''")}' (FORMAT PARQUET)
                """
            )
        finally:
            parquet_connection.close()

        DropPlanningTests._write_local_period_record(
            warehouse_dir,
            year=year,
            month=month,
            dataset_id=dataset_id,
            resource_name=resource_name,
            resource_id=resource_id,
            manifest_path=manifest_path,
            parquet_path=parquet_path,
        )
        if seed_loaded_catalog:
            DropPlanningTests._write_loaded_resource_record(
                warehouse_dir,
                dataset_id=dataset_id,
                resource_name=resource_name,
                manifest_path=manifest_path,
                materialized_path=materialized_path,
                parquet_path=parquet_path,
            )

        raw_paths = [str(manifest_path), str(archive_path), str(materialized_path)]
        all_paths = [*raw_paths, str(parquet_path)]
        return {
            "raw_paths": raw_paths,
            "all_paths": all_paths,
            "parquet_path": str(parquet_path),
            "raw_size": sum(Path(path).stat().st_size for path in raw_paths),
            "all_size": sum(Path(path).stat().st_size for path in all_paths),
        }

    @staticmethod
    def _write_local_period_record(
        warehouse_dir: Path,
        *,
        year: int,
        month: int,
        dataset_id: str,
        resource_name: str,
        resource_id: str,
        manifest_path: Path,
        parquet_path: Path,
    ) -> None:
        """@notice Insert one local period manifest record used by the drop planner."""

        warehouse_dir.mkdir(parents=True, exist_ok=True)
        duckdb_path = warehouse_dir / "anac.duckdb"
        connection = duckdb.connect(str(duckdb_path))
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dataset_period_manifest (
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
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    '2026-05-24T20:00:00',
                    ?,
                    'checksum',
                    10,
                    '2026-05-24T20:10:00',
                    '2026-05-24T20:11:00'
                )
                """,
                [
                    f"{year:04d}_{month:02d}",
                    dataset_id,
                    resource_name,
                    str(manifest_path),
                    str(parquet_path),
                    resource_id,
                    f"https://example.invalid/{resource_name}.zip",
                    parquet_path.stat().st_size,
                ],
            )
        finally:
            connection.close()

    @staticmethod
    def _write_loaded_resource_record(
        warehouse_dir: Path,
        *,
        dataset_id: str,
        resource_name: str,
        manifest_path: Path,
        materialized_path: Path,
        parquet_path: Path,
    ) -> None:
        """@notice Insert one loaded-resource row and its registered view metadata."""

        warehouse_dir.mkdir(parents=True, exist_ok=True)
        duckdb_path = warehouse_dir / "anac.duckdb"
        connection = duckdb.connect(str(duckdb_path))
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS loaded_resources (
                    manifest_path VARCHAR PRIMARY KEY,
                    dataset_id VARCHAR NOT NULL,
                    resource_name VARCHAR NOT NULL,
                    table_name VARCHAR NOT NULL,
                    view_name VARCHAR NOT NULL,
                    source_path VARCHAR NOT NULL,
                    schema_path VARCHAR,
                    parquet_root VARCHAR NOT NULL,
                    parquet_path VARCHAR NOT NULL,
                    row_count BIGINT NOT NULL,
                    partition_values_json VARCHAR NOT NULL,
                    loaded_at VARCHAR NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS registered_views (
                    view_name VARCHAR PRIMARY KEY,
                    table_name VARCHAR NOT NULL,
                    parquet_root VARCHAR NOT NULL,
                    parquet_file_count BIGINT NOT NULL,
                    view_sql VARCHAR NOT NULL,
                    updated_at VARCHAR NOT NULL
                )
                """
            )
            parquet_root = warehouse_dir / "parquet" / "cig"
            parquet_sql_literal = str(parquet_path).replace("'", "''")
            view_sql = (
                "CREATE OR REPLACE VIEW cig AS "
                f"SELECT * FROM read_parquet(['{parquet_sql_literal}'], hive_partitioning=true)"
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO loaded_resources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    str(manifest_path),
                    dataset_id,
                    resource_name,
                    "cig",
                    "cig",
                    str(materialized_path),
                    None,
                    str(parquet_root),
                    str(parquet_path),
                    10,
                    '[{"key":"year","value":"2025"},{"key":"month","value":"01"}]',
                    "2026-05-24T20:12:00",
                ],
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO registered_views VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    "cig",
                    "cig",
                    str(parquet_root),
                    1,
                    view_sql,
                    "2026-05-24T20:13:00",
                ],
            )
            connection.execute(view_sql)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
