"""@notice Backend planning helpers for the future Phase 3 `drop` command."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence

import duckdb

from anac_explorator.errors import CliCommandError
from anac_explorator.models import DatasetPeriodManifestRecord, DownloadManifest, DropPlan, DropPlanTarget
from anac_explorator.selection import TemporalSelection, period_to_slice_identifier, select_available_slices

_VALID_DROP_LAYERS = {"raw", "parquet", "all"}


def plan_cig_drop(
    *,
    scope: TemporalSelection | None = None,
    layers: str = "all",
    warehouse_dir: str | Path = "data/warehouse",
    resource_ids: Sequence[str] | None = None,
) -> DropPlan:
    """@notice Build the reusable dry-run drop plan for local CIG storage."""

    normalized_scope = TemporalSelection(mode="all") if scope is None else scope
    normalized_layer = _normalize_drop_layer(layers)
    period_records = _load_period_manifest_records(Path(warehouse_dir), dataset_type="cig")
    if not period_records:
        raise CliCommandError(
            "LOCAL_DATASET_NOT_AVAILABLE",
            "No local CIG warehouse slices are available to drop.",
            details={"dataset": "cig", "warehouse_dir": str(warehouse_dir)},
        )

    selected_records, selected_slices = _select_period_records(
        period_records,
        scope=normalized_scope,
        resource_ids=resource_ids,
    )
    targets = _build_drop_targets(selected_records, layer=normalized_layer)
    return DropPlan(
        dataset="cig",
        scope={
            "selection_mode": normalized_scope.mode,
            "requested_slices": list(normalized_scope.slices),
            "requested_years": list(normalized_scope.years),
            "requested_months": list(normalized_scope.months),
            "start_slice": normalized_scope.start_slice,
            "end_slice": normalized_scope.end_slice,
            "selected_slices": selected_slices,
            "resource_ids": [] if resource_ids is None else sorted({value for value in resource_ids if value}),
        },
        layer=normalized_layer,
        targets=targets,
    )


def execute_drop_plan(
    plan: DropPlan,
    *,
    warehouse_dir: str | Path = "data/warehouse",
) -> list[DropPlanTarget]:
    """@notice Execute one reusable drop plan and reconcile local catalog state."""

    deleted_targets: list[DropPlanTarget] = []
    for target in _sorted_deletion_targets(plan.targets):
        target_path = Path(target.path)
        if not target_path.exists():
            continue
        if not target_path.is_file():
            continue
        target_path.unlink()
        deleted_targets.append(target)

    _reconcile_cig_drop_state(plan, warehouse_dir=Path(warehouse_dir), deleted_targets=deleted_targets)
    return deleted_targets


def _normalize_drop_layer(layer: str) -> str:
    """@notice Normalize one requested drop layer string."""

    normalized_layer = layer.casefold()
    if normalized_layer not in _VALID_DROP_LAYERS:
        raise ValueError(f"Unsupported drop layer {layer!r}.")
    return normalized_layer


def _select_period_records(
    records: Sequence[DatasetPeriodManifestRecord],
    *,
    scope: TemporalSelection,
    resource_ids: Sequence[str] | None,
) -> tuple[list[DatasetPeriodManifestRecord], list[str]]:
    """@notice Restrict the local period catalog to the requested temporal and resource scope."""

    available_slices = [period_to_slice_identifier(record.period) for record in records]
    try:
        selected_slices = select_available_slices(scope, available_slices)
    except ValueError as exc:
        raise CliCommandError(
            "TEMPORAL_SLICE_NOT_FOUND",
            str(exc),
            details={"dataset": "cig", "requested_slices": list(scope.slices)},
            cause=exc,
        ) from exc

    selected_slice_set = set(selected_slices)
    filtered_records = [
        record
        for record in records
        if period_to_slice_identifier(record.period) in selected_slice_set
    ]
    if resource_ids is None:
        return filtered_records, selected_slices

    requested_ids = {value for value in resource_ids if value}
    matched_records = [
        record
        for record in filtered_records
        if record.resource_id in requested_ids or record.resource_name in requested_ids
    ]
    if not matched_records:
        raise CliCommandError(
            "LOCAL_DATASET_NOT_AVAILABLE",
            "The requested local resources were not found in the selected drop scope.",
            details={"dataset": "cig", "resource_ids": sorted(requested_ids)},
        )
    return matched_records, sorted({period_to_slice_identifier(record.period) for record in matched_records})


def _build_drop_targets(
    records: Sequence[DatasetPeriodManifestRecord],
    *,
    layer: str,
) -> list[DropPlanTarget]:
    """@notice Expand the selected local period records into concrete file targets."""

    targets_by_path: dict[str, DropPlanTarget] = {}
    for record in records:
        slice_value = period_to_slice_identifier(record.period)
        if layer in {"raw", "all"}:
            manifest_path = Path(record.manifest_path)
            manifest = _load_manifest_if_present(manifest_path)
            _register_target(
                targets_by_path,
                path=manifest_path,
                layer="raw",
                dataset="cig",
                dataset_id=record.dataset_id,
                slice_value=slice_value,
                resource_id=record.resource_id,
                resource_name=record.resource_name,
                target_kind="manifest",
            )
            if manifest is not None and manifest.archive_path is not None:
                _register_target(
                    targets_by_path,
                    path=Path(manifest.archive_path),
                    layer="raw",
                    dataset="cig",
                    dataset_id=record.dataset_id,
                    slice_value=slice_value,
                    resource_id=record.resource_id,
                    resource_name=record.resource_name,
                    target_kind="archive",
                )
            if manifest is not None:
                _register_target(
                    targets_by_path,
                    path=Path(manifest.materialized_path),
                    layer="raw",
                    dataset="cig",
                    dataset_id=record.dataset_id,
                    slice_value=slice_value,
                    resource_id=record.resource_id,
                    resource_name=record.resource_name,
                    target_kind=f"materialized_{manifest.materialized_kind.casefold()}",
                )
        if layer in {"parquet", "all"}:
            _register_target(
                targets_by_path,
                path=Path(record.parquet_path),
                layer="parquet",
                dataset="cig",
                dataset_id=record.dataset_id,
                slice_value=slice_value,
                resource_id=record.resource_id,
                resource_name=record.resource_name,
                target_kind="parquet",
            )
    return [targets_by_path[path_key] for path_key in sorted(targets_by_path)]


def _sorted_deletion_targets(targets: Sequence[DropPlanTarget]) -> list[DropPlanTarget]:
    """@notice Delete materialized files before their manifest metadata when both are targeted."""

    deletion_order = {
        "archive": 0,
        "materialized_csv": 1,
        "materialized_json": 1,
        "parquet": 2,
        "manifest": 3,
    }
    return sorted(targets, key=lambda target: (deletion_order.get(target.target_kind or "", 9), target.path))


def _register_target(
    targets_by_path: dict[str, DropPlanTarget],
    *,
    path: Path,
    layer: str,
    dataset: str,
    dataset_id: str | None,
    slice_value: str | None,
    resource_id: str | None,
    resource_name: str | None,
    target_kind: str,
) -> None:
    """@notice Add one existing file to the plan while keeping paths unique."""

    if not path.exists() or not path.is_file():
        return
    path_key = str(path)
    if path_key in targets_by_path:
        return
    targets_by_path[path_key] = DropPlanTarget(
        path=path_key,
        layer=layer,
        size_bytes=path.stat().st_size,
        dataset=dataset,
        dataset_id=dataset_id,
        slice=slice_value,
        resource_id=resource_id,
        resource_name=resource_name,
        target_kind=target_kind,
    )


def _load_manifest_if_present(manifest_path: Path) -> DownloadManifest | None:
    """@notice Load one persisted raw-resource manifest when it still exists locally."""

    if not manifest_path.exists():
        return None
    return DownloadManifest.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))


def _load_period_manifest_records(
    warehouse_dir: Path,
    *,
    dataset_type: str,
) -> list[DatasetPeriodManifestRecord]:
    """@notice Load the local period-manifest catalog for one update-aware family."""

    duckdb_path = warehouse_dir / "anac.duckdb"
    if not duckdb_path.exists():
        return []

    connection = duckdb.connect(str(duckdb_path))
    try:
        table_exists = bool(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = 'dataset_period_manifest'
                """
            ).fetchone()[0]
        )
        if not table_exists:
            return []
        rows = connection.execute(
            """
            SELECT
                dataset_type,
                period,
                dataset_id,
                resource_name,
                manifest_path,
                parquet_path,
                resource_id,
                resource_url,
                remote_modified,
                remote_size,
                content_checksum,
                row_count,
                imported_at,
                refreshed_at
            FROM dataset_period_manifest
            WHERE dataset_type = ?
            ORDER BY period, resource_name
            """,
            [dataset_type],
        ).fetchall()
    finally:
        connection.close()

    return [
        DatasetPeriodManifestRecord(
            dataset_type=str(dataset_type_value),
            period=str(period_value),
            dataset_id=str(dataset_id_value),
            resource_name=str(resource_name_value),
            manifest_path=str(manifest_path_value),
            parquet_path=str(parquet_path_value),
            resource_id=None if resource_id_value is None else str(resource_id_value),
            resource_url=None if resource_url_value is None else str(resource_url_value),
            remote_modified=None if remote_modified_value is None else str(remote_modified_value),
            remote_size=None if remote_size_value is None else int(remote_size_value),
            content_checksum=None if content_checksum_value is None else str(content_checksum_value),
            row_count=None if row_count_value is None else int(row_count_value),
            imported_at=None if imported_at_value is None else str(imported_at_value),
            refreshed_at=None if refreshed_at_value is None else str(refreshed_at_value),
        )
        for (
            dataset_type_value,
            period_value,
            dataset_id_value,
            resource_name_value,
            manifest_path_value,
            parquet_path_value,
            resource_id_value,
            resource_url_value,
            remote_modified_value,
            remote_size_value,
            content_checksum_value,
            row_count_value,
            imported_at_value,
            refreshed_at_value,
        ) in rows
    ]


def _reconcile_cig_drop_state(
    plan: DropPlan,
    *,
    warehouse_dir: Path,
    deleted_targets: Sequence[DropPlanTarget],
) -> None:
    """@notice Prune raw-manifest and warehouse-catalog state after one CIG drop run."""

    duckdb_path = warehouse_dir / "anac.duckdb"
    if not duckdb_path.exists():
        return

    deleted_paths = {target.path for target in deleted_targets}
    raw_deleted = any(target.layer == "raw" for target in deleted_targets)
    parquet_deleted = any(target.layer == "parquet" for target in deleted_targets)
    if not deleted_paths or not (raw_deleted or parquet_deleted):
        return

    connection = duckdb.connect(str(duckdb_path))
    try:
        affected_views = _load_affected_registered_views(connection, dataset_type=plan.dataset)
        if raw_deleted:
            _prune_loaded_resource_raw_paths(connection, dataset_type=plan.dataset, deleted_paths=deleted_paths)
            _prune_period_manifest_raw_paths(connection, dataset_type=plan.dataset, deleted_paths=deleted_paths)
        if parquet_deleted:
            _prune_loaded_resource_parquet_rows(connection, dataset_type=plan.dataset, deleted_paths=deleted_paths)
            _prune_period_manifest_parquet_rows(connection, dataset_type=plan.dataset, deleted_paths=deleted_paths)
        _refresh_registered_views_catalog(connection, affected_views=affected_views)
    finally:
        connection.close()


def _load_affected_registered_views(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_type: str,
) -> list[tuple[str, str, str]]:
    """@notice Collect the registered-view rows that may need refresh after one drop."""

    if not _table_exists(connection, "registered_views"):
        return []
    rows = connection.execute(
        """
        SELECT DISTINCT view_name, table_name, parquet_root
        FROM registered_views
        WHERE table_name = ?
        ORDER BY view_name
        """,
        [dataset_type],
    ).fetchall()
    return [
        (str(view_name_value), str(table_name_value), str(parquet_root_value))
        for view_name_value, table_name_value, parquet_root_value in rows
    ]


def _prune_loaded_resource_raw_paths(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_type: str,
    deleted_paths: set[str],
) -> None:
    """@notice Rewrite loaded-resource metadata so it no longer points at deleted raw files."""

    if not _table_exists(connection, "loaded_resources"):
        return
    rows = connection.execute(
        """
        SELECT manifest_path, dataset_id, resource_name, source_path, parquet_path
        FROM loaded_resources
        WHERE table_name = ?
        ORDER BY resource_name
        """,
        [dataset_type],
    ).fetchall()
    for manifest_path_value, dataset_id_value, resource_name_value, source_path_value, parquet_path_value in rows:
        manifest_path = str(manifest_path_value)
        source_path = str(source_path_value)
        parquet_path = str(parquet_path_value)
        if parquet_path in deleted_paths:
            continue
        if manifest_path not in deleted_paths and source_path not in deleted_paths:
            continue
        pruned_manifest_path = _pruned_reference(str(dataset_id_value), str(resource_name_value), kind="manifest")
        pruned_source_path = _pruned_reference(str(dataset_id_value), str(resource_name_value), kind="source")
        connection.execute(
            """
            UPDATE loaded_resources
            SET manifest_path = ?, source_path = ?
            WHERE manifest_path = ?
            """,
            [pruned_manifest_path, pruned_source_path, manifest_path],
        )


def _prune_period_manifest_raw_paths(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_type: str,
    deleted_paths: set[str],
) -> None:
    """@notice Rewrite period-manifest rows so they no longer reference deleted raw manifests."""

    if not _table_exists(connection, "dataset_period_manifest"):
        return
    rows = connection.execute(
        """
        SELECT period, dataset_id, resource_name, manifest_path, parquet_path
        FROM dataset_period_manifest
        WHERE dataset_type = ?
        ORDER BY period
        """,
        [dataset_type],
    ).fetchall()
    for period_value, dataset_id_value, resource_name_value, manifest_path_value, parquet_path_value in rows:
        manifest_path = str(manifest_path_value)
        parquet_path = str(parquet_path_value)
        if parquet_path in deleted_paths:
            continue
        if manifest_path not in deleted_paths:
            continue
        connection.execute(
            """
            UPDATE dataset_period_manifest
            SET manifest_path = ?
            WHERE dataset_type = ? AND period = ?
            """,
            [
                _pruned_reference(str(dataset_id_value), str(resource_name_value), kind="manifest"),
                dataset_type,
                str(period_value),
            ],
        )


def _prune_loaded_resource_parquet_rows(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_type: str,
    deleted_paths: set[str],
) -> None:
    """@notice Remove loaded-resource rows whose Parquet payload was deleted."""

    if not _table_exists(connection, "loaded_resources"):
        return
    connection.execute(
        """
        DELETE FROM loaded_resources
        WHERE table_name = ? AND parquet_path IN ({placeholders})
        """.format(placeholders=", ".join("?" for _ in deleted_paths)),
        [dataset_type, *sorted(deleted_paths)],
    )


def _prune_period_manifest_parquet_rows(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_type: str,
    deleted_paths: set[str],
) -> None:
    """@notice Remove period-manifest rows whose local Parquet slice no longer exists."""

    if not _table_exists(connection, "dataset_period_manifest"):
        return
    connection.execute(
        """
        DELETE FROM dataset_period_manifest
        WHERE dataset_type = ? AND parquet_path IN ({placeholders})
        """.format(placeholders=", ".join("?" for _ in deleted_paths)),
        [dataset_type, *sorted(deleted_paths)],
    )


def _refresh_registered_views_catalog(
    connection: duckdb.DuckDBPyConnection,
    *,
    affected_views: Sequence[tuple[str, str, str]],
) -> None:
    """@notice Refresh or prune registered-view metadata after Parquet deletion."""

    if not affected_views or not _table_exists(connection, "registered_views"):
        return

    updated_at = datetime.now(timezone.utc).isoformat()
    for view_name, table_name, parquet_root_value in affected_views:
        parquet_root = Path(parquet_root_value)
        parquet_files = sorted(parquet_root.rglob("*.parquet"))
        if not parquet_files:
            connection.execute("DELETE FROM registered_views WHERE view_name = ?", [view_name])
            connection.execute(f"DROP VIEW IF EXISTS {_quoted_identifier(view_name)}")
            continue

        file_list_sql = "[" + ", ".join(_sql_literal(str(path)) for path in parquet_files) + "]"
        view_sql = (
            f"CREATE OR REPLACE VIEW {_quoted_identifier(view_name)} AS "
            f"SELECT * FROM read_parquet({file_list_sql}, hive_partitioning=true)"
        )
        connection.execute(view_sql)
        connection.execute("DELETE FROM registered_views WHERE view_name = ?", [view_name])
        connection.execute(
            """
            INSERT INTO registered_views (
                view_name,
                table_name,
                parquet_root,
                parquet_file_count,
                view_sql,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [view_name, table_name, str(parquet_root), len(parquet_files), view_sql, updated_at],
        )


def _table_exists(connection: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    """@notice Check whether one persistent warehouse catalog table exists."""

    existing = connection.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
          AND table_name = ?
        """,
        [table_name],
    ).fetchone()
    return existing is not None


def _pruned_reference(dataset_id: str, resource_name: str, *, kind: str) -> str:
    """@notice Build one stable non-file reference for pruned raw cache metadata."""

    return f"pruned://{dataset_id}/{resource_name}/{kind}"


def _quoted_identifier(value: str) -> str:
    """@notice Quote one SQL identifier for simple dynamic DuckDB DDL."""

    return '"' + value.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    """@notice Quote one SQL string literal for simple dynamic DuckDB DDL."""

    return "'" + value.replace("'", "''") + "'"
