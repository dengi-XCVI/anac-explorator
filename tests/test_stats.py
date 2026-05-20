"""Unit tests for the Phase 3 stats backend helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from anac_explorator.catalog import DATASET_FAMILY_REGISTRY
from anac_explorator.selection import parse_temporal_selection
from anac_explorator.stats import compute_dataset_stats, compute_global_stats, list_dataset_partitions, profile_dataset


class StatsServiceTest(unittest.TestCase):
    """Exercise metadata-backed stats helpers without scanning warehouse tables."""

    def test_compute_global_stats_reports_zero_local_counts_for_empty_warehouse(self) -> None:
        """@notice Empty warehouses should still expose registry-backed global stats cleanly."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "warehouse" / "anac.duckdb"
            db_path.parent.mkdir(parents=True)
            duckdb.connect(str(db_path)).close()

            result = compute_global_stats(db_path=db_path)

        self.assertEqual(result.scope, "global")
        self.assertIsNone(result.dataset)
        self.assertEqual(result.summary["dataset_family_count"], len(DATASET_FAMILY_REGISTRY.list_families()))
        self.assertEqual(result.summary["loaded_dataset_count"], 0)
        self.assertEqual(result.summary["loaded_resource_count"], 0)
        self.assertEqual(result.summary["loaded_row_count"], 0)
        self.assertEqual(result.summary["registered_view_count"], 0)
        self.assertEqual(result.summary["parquet_file_count"], 0)
        self.assertEqual(result.summary["partition_count"], 0)
        self.assertIsNone(result.summary["latest_view_updated_at"])
        self.assertIsNone(result.summary["latest_imported_at"])
        self.assertIsNone(result.summary["latest_refreshed_at"])

    def test_compute_dataset_stats_aggregates_family_metrics_from_metadata_views(self) -> None:
        """@notice Dataset stats should derive counts and timestamps strictly from `anac_*` metadata views."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = self._build_periodized_warehouse(Path(temp_dir))

            result = compute_dataset_stats("cig", db_path=db_path)

        self.assertEqual(result.scope, "dataset")
        self.assertEqual(result.dataset, "cig")
        self.assertEqual(result.summary["coverage_kind"], "periodic_monthly")
        self.assertEqual(result.summary["query_view_name"], "cig")
        self.assertEqual(result.summary["local_slice_count"], 1)
        self.assertEqual(result.summary["local_first_slice"], "2025-01")
        self.assertEqual(result.summary["local_last_slice"], "2025-01")
        self.assertEqual(result.summary["loaded_resource_count"], 1)
        self.assertEqual(result.summary["loaded_row_count"], 4)
        self.assertEqual(result.summary["partition_count"], 1)
        self.assertEqual(result.summary["registered_view_count"], 1)
        self.assertEqual(result.summary["parquet_file_count"], 3)
        self.assertEqual(result.summary["latest_view_updated_at"], "2026-05-17T12:00:00")
        self.assertEqual(result.summary["latest_imported_at"], "2026-05-17T10:00:00")
        self.assertEqual(result.summary["latest_refreshed_at"], "2026-05-17T11:00:00")

    def test_list_dataset_partitions_returns_periodized_slice_rows(self) -> None:
        """@notice Partition mode should list the local slices for periodized families from metadata only."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = self._build_periodized_warehouse(Path(temp_dir))

            result = list_dataset_partitions("cig", db_path=db_path)

        self.assertEqual(result.scope, "dataset")
        self.assertEqual(result.dataset, "cig")
        self.assertEqual(result.summary["partition_count"], 1)
        self.assertIsNotNone(result.partitions)
        self.assertEqual(len(result.partitions), 1)
        self.assertEqual(result.partitions[0]["slice"], "2025-01")
        self.assertEqual(result.partitions[0]["year"], 2025)
        self.assertEqual(result.partitions[0]["month"], 1)
        self.assertEqual(result.partitions[0]["row_count"], 4)
        self.assertEqual(result.partitions[0]["dataset_id"], "cig-2025")

    def test_compute_dataset_stats_filters_to_requested_temporal_scope(self) -> None:
        """@notice Scoped stats should narrow dataset metrics to the selected local slices."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = self._build_periodized_warehouse(Path(temp_dir), include_second_partition=True)

            result = compute_dataset_stats(
                "cig",
                db_path=db_path,
                selection=parse_temporal_selection(year="2025", month="2"),
            )

        self.assertEqual(result.scope, "slice")
        self.assertEqual(result.summary["selection_mode"], "range")
        self.assertEqual(result.summary["requested_slices"], ["2025-02"])
        self.assertEqual(result.summary["selected_slices"], ["2025-02"])
        self.assertEqual(result.summary["local_slice_count"], 1)
        self.assertEqual(result.summary["loaded_resource_count"], 1)
        self.assertEqual(result.summary["loaded_row_count"], 6)
        self.assertEqual(result.summary["row_count"], 6)
        self.assertEqual(result.summary["partition_count"], 1)
        self.assertEqual(result.summary["parquet_file_count"], 1)
        self.assertEqual(result.summary["latest_imported_at"], "2026-05-18T10:00:00")
        self.assertEqual(result.summary["latest_refreshed_at"], "2026-05-18T12:00:00")

    def test_profile_dataset_computes_nulls_ranges_and_distinct_counts(self) -> None:
        """@notice Profile mode should scan the live relation and report column-level aggregate metrics."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = self._build_periodized_warehouse(Path(temp_dir))
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute(
                    """
                    CREATE TABLE cig_data (
                        cig VARCHAR,
                        amount BIGINT,
                        category VARCHAR,
                        event_date DATE,
                        flag BOOLEAN
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO cig_data VALUES
                        ('A1', 10, 'x', '2025-01-01', TRUE),
                        ('A2', NULL, 'x', '2025-01-02', FALSE),
                        (NULL, 30, 'y', NULL, NULL),
                        ('A2', 10, NULL, '2025-01-03', TRUE)
                    """
                )
                connection.execute("CREATE VIEW cig AS SELECT * FROM cig_data")
            finally:
                connection.close()

            result = profile_dataset("cig", db_path=db_path)

        self.assertEqual(result.scope, "dataset")
        self.assertEqual(result.dataset, "cig")
        self.assertIsNotNone(result.profile)
        assert result.profile is not None
        self.assertEqual(result.profile["relation"], "cig")
        self.assertEqual(result.profile["row_count"], 4)

        columns = {column["name"]: column for column in result.profile["columns"]}
        self.assertEqual(columns["cig"]["null_count"], 1)
        self.assertEqual(columns["cig"]["null_ratio"], 0.25)
        self.assertEqual(columns["cig"]["approx_distinct_count"], 2)
        self.assertEqual(columns["cig"]["min"], "A1")
        self.assertEqual(columns["cig"]["max"], "A2")

        self.assertEqual(columns["amount"]["null_count"], 1)
        self.assertEqual(columns["amount"]["approx_distinct_count"], 2)
        self.assertEqual(columns["amount"]["min"], 10)
        self.assertEqual(columns["amount"]["max"], 30)

        self.assertEqual(columns["event_date"]["null_count"], 1)
        self.assertEqual(columns["event_date"]["approx_distinct_count"], 3)
        self.assertEqual(str(columns["event_date"]["min"]), "2025-01-01")
        self.assertEqual(str(columns["event_date"]["max"]), "2025-01-03")

        self.assertEqual(columns["flag"]["null_count"], 1)
        self.assertEqual(columns["flag"]["approx_distinct_count"], 2)
        self.assertIsNone(columns["flag"]["min"])
        self.assertIsNone(columns["flag"]["max"])

    def test_profile_dataset_filters_to_requested_temporal_scope(self) -> None:
        """@notice Scoped profile mode should aggregate only the selected slice rows."""

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = self._build_periodized_warehouse(Path(temp_dir), include_second_partition=True)
            connection = duckdb.connect(str(db_path))
            try:
                connection.execute(
                    """
                    CREATE TABLE cig_data (
                        cig VARCHAR,
                        amount BIGINT,
                        year INTEGER,
                        month INTEGER
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO cig_data VALUES
                        ('A1', 10, 2025, 1),
                        ('A2', 20, 2025, 1),
                        ('B1', 30, 2025, 2)
                    """
                )
                connection.execute("CREATE VIEW cig AS SELECT * FROM cig_data")
            finally:
                connection.close()

            result = profile_dataset(
                "cig",
                db_path=db_path,
                selection=parse_temporal_selection(year="2025", month="2"),
            )

        self.assertEqual(result.scope, "slice")
        assert result.profile is not None
        self.assertEqual(result.profile["row_count"], 1)
        columns = {column["name"]: column for column in result.profile["columns"]}
        self.assertEqual(columns["cig"]["approx_distinct_count"], 1)
        self.assertEqual(columns["amount"]["min"], 30)
        self.assertEqual(columns["amount"]["max"], 30)
        self.assertEqual(columns["year"]["min"], 2025)
        self.assertEqual(columns["month"]["min"], 2)

    @staticmethod
    def _build_periodized_warehouse(temp_dir: Path, *, include_second_partition: bool = False) -> Path:
        """@notice Create a minimal warehouse catalog that produces one loaded CIG partition."""

        db_path = temp_dir / "warehouse" / "anac.duckdb"
        db_path.parent.mkdir(parents=True)
        connection = duckdb.connect(str(db_path))
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
                    refreshed_at VARCHAR NOT NULL
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
                    NULL,
                    NULL,
                    '2026-05-17T08:00:00',
                    2048,
                    'abc123',
                    4,
                    '2026-05-17T10:00:00',
                    '2026-05-17T11:00:00'
                )
                """
            )
            if include_second_partition:
                connection.execute(
                    """
                    INSERT INTO dataset_period_manifest VALUES (
                        'cig',
                        '2025_02',
                        'cig-2025',
                        'cig_csv_2025_02',
                        'data/raw/cig-2025/cig_csv_2025_02/manifest.json',
                        'data/warehouse/parquet/cig/year=2025/month=02/cig_csv_2025_02.parquet',
                        NULL,
                        NULL,
                        '2026-05-18T09:00:00',
                        4096,
                        'def456',
                        6,
                        '2026-05-18T10:00:00',
                        '2026-05-18T12:00:00'
                    )
                    """
                )
            connection.execute(
                """
                CREATE TABLE loaded_resources (
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
                INSERT INTO loaded_resources VALUES (
                    'data/raw/cig-2025/cig_csv_2025_01/manifest.json',
                    'cig-2025',
                    'cig_csv_2025_01',
                    'cig',
                    'cig',
                    'data/raw/cig-2025/cig_csv_2025_01/data.csv',
                    NULL,
                    'data/warehouse/parquet/cig',
                    'data/warehouse/parquet/cig/year=2025/month=01/cig_csv_2025_01.parquet',
                    4,
                    '[{"key":"year","value":"2025"},{"key":"month","value":"01"}]',
                    '2026-05-17T09:05:00'
                )
                """
            )
            if include_second_partition:
                connection.execute(
                    """
                    INSERT INTO loaded_resources VALUES (
                        'data/raw/cig-2025/cig_csv_2025_02/manifest.json',
                        'cig-2025',
                        'cig_csv_2025_02',
                        'cig',
                        'cig',
                        'data/raw/cig-2025/cig_csv_2025_02/data.csv',
                        NULL,
                        'data/warehouse/parquet/cig',
                        'data/warehouse/parquet/cig/year=2025/month=02/cig_csv_2025_02.parquet',
                        6,
                        '[{"key":"year","value":"2025"},{"key":"month","value":"02"}]',
                        '2026-05-18T09:05:00'
                    )
                    """
                )
            connection.execute(
                """
                CREATE TABLE registered_views (
                    view_name VARCHAR PRIMARY KEY,
                    table_name VARCHAR NOT NULL,
                    parquet_root VARCHAR NOT NULL,
                    parquet_file_count BIGINT NOT NULL,
                    view_sql VARCHAR NOT NULL,
                    updated_at VARCHAR NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO registered_views VALUES (
                    'cig',
                    'cig',
                    'data/warehouse/parquet/cig',
                    3,
                    'CREATE OR REPLACE VIEW cig AS SELECT * FROM read_parquet(''data/warehouse/parquet/cig/**/*.parquet'', hive_partitioning = true);',
                    '2026-05-17T12:00:00'
                )
                """
            )
            if include_second_partition:
                connection.execute(
                    """
                    UPDATE registered_views
                    SET parquet_file_count = 4, updated_at = '2026-05-18T12:30:00'
                    WHERE view_name = 'cig'
                    """
                )
        finally:
            connection.close()
        return db_path


if __name__ == "__main__":
    unittest.main()
