"""@notice Backend helpers for Phase 3 stats summaries and partition inspection."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from anac_explorator.catalog import DATASET_FAMILY_REGISTRY, DatasetFamilyRegistry
from anac_explorator.errors import CliCommandError
from anac_explorator.metadata_views import ensure_metadata_views
from anac_explorator.models import StatsCommandResult
from anac_explorator.selection import TemporalSelection


def compute_global_stats(
    *,
    db_path: str | Path,
    raw_dir: str | Path | None = None,
    schemas_dir: str | Path | None = None,
    dictionaries_dir: str | Path | None = None,
    vocabulary_index_path: str | Path | None = None,
    registry: DatasetFamilyRegistry | None = None,
) -> StatsCommandResult:
    """@notice Compute the global metadata-backed warehouse summary without scanning dataset tables."""

    with _metadata_connection(
        db_path,
        raw_dir=raw_dir,
        schemas_dir=schemas_dir,
        dictionaries_dir=dictionaries_dir,
        vocabulary_index_path=vocabulary_index_path,
        registry=registry,
    ) as connection:
        summary = _fetch_one_dict(
            connection,
            """
            SELECT
                (SELECT COUNT(*) FROM anac_datasets) AS dataset_family_count,
                (SELECT COUNT(*) FROM anac_datasets WHERE dictionary_available) AS dictionary_dataset_count,
                (SELECT COUNT(DISTINCT dataset) FROM anac_loaded_resources) AS loaded_dataset_count,
                (SELECT COUNT(*) FROM anac_loaded_resources) AS loaded_resource_count,
                (SELECT COALESCE(SUM(row_count), 0) FROM anac_loaded_resources) AS loaded_row_count,
                (SELECT COUNT(*) FROM anac_registered_views) AS registered_view_count,
                (SELECT COALESCE(SUM(parquet_file_count), 0) FROM anac_registered_views) AS parquet_file_count,
                (SELECT COUNT(*) FROM anac_partitions) AS partition_count,
                (SELECT MAX(updated_at) FROM anac_registered_views) AS latest_view_updated_at,
                (SELECT MAX(latest_imported_at) FROM anac_update_status) AS latest_imported_at,
                (SELECT MAX(latest_refreshed_at) FROM anac_update_status) AS latest_refreshed_at
            """
        )
    return StatsCommandResult(scope="global", dataset=None, summary=summary)


def compute_dataset_stats(
    dataset: str,
    *,
    selection: TemporalSelection | None = None,
    db_path: str | Path,
    raw_dir: str | Path | None = None,
    schemas_dir: str | Path | None = None,
    dictionaries_dir: str | Path | None = None,
    vocabulary_index_path: str | Path | None = None,
    registry: DatasetFamilyRegistry | None = None,
) -> StatsCommandResult:
    """@notice Compute the metadata-backed summary for one logical dataset family."""

    registry = DATASET_FAMILY_REGISTRY if registry is None else registry
    normalized_selection = _normalize_dataset_selection(dataset, selection, registry)
    with _metadata_connection(
        db_path,
        raw_dir=raw_dir,
        schemas_dir=schemas_dir,
        dictionaries_dir=dictionaries_dir,
        vocabulary_index_path=vocabulary_index_path,
        registry=registry,
    ) as connection:
        summary = _build_dataset_summary(connection, dataset, selection=normalized_selection, registry=registry)
    return StatsCommandResult(scope=_stats_scope(normalized_selection), dataset=dataset, summary=summary)


def profile_dataset(
    dataset: str,
    *,
    selection: TemporalSelection | None = None,
    db_path: str | Path,
    raw_dir: str | Path | None = None,
    schemas_dir: str | Path | None = None,
    dictionaries_dir: str | Path | None = None,
    vocabulary_index_path: str | Path | None = None,
    registry: DatasetFamilyRegistry | None = None,
) -> StatsCommandResult:
    """@notice Profile the live target relation for one dataset family through DuckDB aggregates."""

    registry = DATASET_FAMILY_REGISTRY if registry is None else registry
    normalized_selection = _normalize_dataset_selection(dataset, selection, registry)
    with _metadata_connection(
        db_path,
        raw_dir=raw_dir,
        schemas_dir=schemas_dir,
        dictionaries_dir=dictionaries_dir,
        vocabulary_index_path=vocabulary_index_path,
        registry=registry,
    ) as connection:
        summary = _build_dataset_summary(connection, dataset, selection=normalized_selection, registry=registry)
        relation_name = _resolve_profile_relation_name(connection, dataset, summary)
        selected_slices = (
            None
            if normalized_selection.mode == "all"
            else _selected_partition_slices(connection, dataset, normalized_selection)
        )
        profile = _build_dataset_profile(connection, relation_name, selected_slices=selected_slices)
    return StatsCommandResult(
        scope=_stats_scope(normalized_selection),
        dataset=dataset,
        summary=summary,
        profile=profile,
    )


def list_dataset_partitions(
    dataset: str,
    *,
    selection: TemporalSelection | None = None,
    db_path: str | Path,
    raw_dir: str | Path | None = None,
    schemas_dir: str | Path | None = None,
    dictionaries_dir: str | Path | None = None,
    vocabulary_index_path: str | Path | None = None,
    registry: DatasetFamilyRegistry | None = None,
) -> StatsCommandResult:
    """@notice Return the periodized partition rows for one dataset family from `anac_partitions`."""

    registry = DATASET_FAMILY_REGISTRY if registry is None else registry
    normalized_selection = _normalize_dataset_selection(dataset, selection, registry)
    family = registry.get_family(dataset)
    if family.coverage_kind == "snapshot":
        raise CliCommandError(
            "DATASET_NOT_SUPPORTED",
            f"Partitions are only available for temporal dataset families, not {dataset!r}.",
            details={"dataset": dataset, "coverage_kind": family.coverage_kind},
        )

    with _metadata_connection(
        db_path,
        raw_dir=raw_dir,
        schemas_dir=schemas_dir,
        dictionaries_dir=dictionaries_dir,
        vocabulary_index_path=vocabulary_index_path,
        registry=registry,
    ) as connection:
        summary = _build_dataset_summary(connection, dataset, selection=normalized_selection, registry=registry)
        partitions = _filter_partition_rows(_load_dataset_partitions(connection, dataset), normalized_selection)
    return StatsCommandResult(
        scope=_stats_scope(normalized_selection),
        dataset=dataset,
        summary=summary,
        partitions=partitions,
    )


@contextmanager
def _metadata_connection(
    db_path: str | Path,
    *,
    raw_dir: str | Path | None = None,
    schemas_dir: str | Path | None = None,
    dictionaries_dir: str | Path | None = None,
    vocabulary_index_path: str | Path | None = None,
    registry: DatasetFamilyRegistry | None = None,
) -> Iterator[duckdb.DuckDBPyConnection]:
    """@notice Open a DuckDB session with fresh `anac_*` metadata views registered."""

    resolved_db_path = Path(db_path)
    if not resolved_db_path.exists():
        raise FileNotFoundError(resolved_db_path)
    resolved_registry = DATASET_FAMILY_REGISTRY if registry is None else registry
    connection = duckdb.connect(str(resolved_db_path))
    try:
        ensure_metadata_views(
            connection,
            db_path=resolved_db_path,
            raw_dir=raw_dir,
            schemas_dir=schemas_dir,
            dictionaries_dir=dictionaries_dir,
            vocabulary_index_path=vocabulary_index_path,
            registry=resolved_registry,
        )
        yield connection
    finally:
        connection.close()


def _build_dataset_profile(
    connection: duckdb.DuckDBPyConnection,
    relation_name: str,
    *,
    selected_slices: list[str] | None = None,
) -> dict[str, object]:
    """@notice Aggregate null, range, and cardinality metrics from the live target relation."""

    relation_sql = _profile_relation_sql(relation_name, selected_slices)
    columns = _describe_relation(connection, relation_sql)
    if not columns:
        return {
            "relation": relation_name,
            "row_count": 0,
            "columns": [],
        }

    expressions = ["COUNT(*) AS total_row_count"]
    column_specs: list[tuple[str, str, str]] = []
    for index, (column_name, column_type) in enumerate(columns.items()):
        quoted_column = _quote_identifier(column_name)
        alias_prefix = f"column_{index}"
        expressions.append(
            f"SUM(CASE WHEN {quoted_column} IS NULL THEN 1 ELSE 0 END) AS {alias_prefix}_null_count"
        )
        if _supports_approx_distinct(column_type):
            expressions.append(f"APPROX_COUNT_DISTINCT({quoted_column}) AS {alias_prefix}_approx_distinct_count")
        else:
            expressions.append(f"NULL AS {alias_prefix}_approx_distinct_count")
        if _supports_min_max(column_type):
            expressions.append(f"MIN({quoted_column}) AS {alias_prefix}_min_value")
            expressions.append(f"MAX({quoted_column}) AS {alias_prefix}_max_value")
        else:
            expressions.append(f"NULL AS {alias_prefix}_min_value")
            expressions.append(f"NULL AS {alias_prefix}_max_value")
        column_specs.append((column_name, column_type, alias_prefix))

    profile_row = _fetch_one_dict(
        connection,
        f"SELECT {', '.join(expressions)} FROM {relation_sql}",
    )
    assert profile_row is not None
    total_row_count = int(profile_row["total_row_count"])
    column_profiles = []
    for column_name, column_type, alias_prefix in column_specs:
        null_count = int(profile_row[f"{alias_prefix}_null_count"] or 0)
        approx_distinct_value = profile_row[f"{alias_prefix}_approx_distinct_count"]
        column_profiles.append(
            {
                "name": column_name,
                "duckdb_type": column_type,
                "null_count": null_count,
                "null_ratio": None if total_row_count == 0 else null_count / total_row_count,
                "approx_distinct_count": None
                if approx_distinct_value is None
                else int(approx_distinct_value),
                "min": profile_row[f"{alias_prefix}_min_value"],
                "max": profile_row[f"{alias_prefix}_max_value"],
            }
        )
    return {
        "relation": relation_name,
        "row_count": total_row_count,
        "columns": column_profiles,
    }


def _build_dataset_summary(
    connection: duckdb.DuckDBPyConnection,
    dataset: str,
    *,
    selection: TemporalSelection | None = None,
    registry: DatasetFamilyRegistry | None = None,
) -> dict[str, object]:
    """@notice Join dataset-level metadata aggregates into one stable summary mapping."""

    registry = DATASET_FAMILY_REGISTRY if registry is None else registry
    normalized_selection = TemporalSelection(mode="all") if selection is None else selection
    summary = _fetch_one_dict(
        connection,
        """
        WITH dataset_row AS (
            SELECT
                dataset,
                title,
                category,
                coverage_kind,
                query_view_name,
                update_supported,
                dictionary_available,
                local_slice_count,
                local_first_slice,
                local_last_slice
            FROM anac_datasets
            WHERE dataset = ?
        )
        SELECT
            dataset_row.title,
            dataset_row.category,
            dataset_row.coverage_kind,
            dataset_row.query_view_name,
            dataset_row.update_supported,
            dataset_row.dictionary_available,
            dataset_row.local_slice_count,
            dataset_row.local_first_slice,
            dataset_row.local_last_slice,
            (SELECT COUNT(*) FROM anac_loaded_resources loaded WHERE loaded.dataset = dataset_row.dataset) AS loaded_resource_count,
            (SELECT COALESCE(SUM(row_count), 0) FROM anac_loaded_resources loaded WHERE loaded.dataset = dataset_row.dataset) AS loaded_row_count,
            (SELECT COUNT(*) FROM anac_partitions partition WHERE partition.dataset = dataset_row.dataset) AS partition_count,
            (SELECT COUNT(*) FROM anac_registered_views view_row WHERE view_row.view_name = dataset_row.query_view_name) AS registered_view_count,
            (SELECT COALESCE(SUM(parquet_file_count), 0) FROM anac_registered_views view_row WHERE view_row.view_name = dataset_row.query_view_name) AS parquet_file_count,
            CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM anac_schema_columns column_row
                    WHERE column_row.dataset = dataset_row.dataset
                      AND column_row.target = 'canonical'
                )
                THEN (
                    SELECT COUNT(*)
                    FROM anac_schema_columns column_row
                    WHERE column_row.dataset = dataset_row.dataset
                      AND column_row.target = 'canonical'
                )
                ELSE COALESCE(
                    (
                        SELECT MAX(target_columns.column_count)
                        FROM (
                            SELECT target, COUNT(*) AS column_count
                            FROM anac_schema_columns column_row
                            WHERE column_row.dataset = dataset_row.dataset
                            GROUP BY target
                        ) AS target_columns
                    ),
                    0
                )
            END AS schema_column_count,
            (
                SELECT COUNT(DISTINCT field_row.vocabulary_dataset_id)
                FROM anac_dictionary_fields field_row
                WHERE field_row.dataset = dataset_row.dataset
                  AND field_row.vocabulary_dataset_id IS NOT NULL
            ) AS crosswalk_reference_count,
            (SELECT MAX(view_row.updated_at) FROM anac_registered_views view_row WHERE view_row.view_name = dataset_row.query_view_name) AS latest_view_updated_at,
            (SELECT status.latest_imported_at FROM anac_update_status status WHERE status.dataset = dataset_row.dataset) AS latest_imported_at,
            (SELECT status.latest_refreshed_at FROM anac_update_status status WHERE status.dataset = dataset_row.dataset) AS latest_refreshed_at
        FROM dataset_row
        """,
        [dataset],
    )
    if summary is None:
        raise CliCommandError(
            "DATASET_NOT_FOUND",
            f"Unknown dataset family {dataset!r}.",
            details={"dataset": dataset},
        )
    summary["row_count"] = summary["loaded_row_count"]
    if normalized_selection.mode == "all":
        return summary

    selected_partitions = _filter_partition_rows(_load_dataset_partitions(connection, dataset), normalized_selection)
    selected_slices = [str(row["slice"]) for row in selected_partitions]
    selected_row_counts = [0 if row["row_count"] is None else int(row["row_count"]) for row in selected_partitions]
    imported_values = [str(value) for value in (row["imported_at"] for row in selected_partitions) if value not in (None, "")]
    refreshed_values = [str(value) for value in (row["refreshed_at"] for row in selected_partitions) if value not in (None, "")]
    summary.update(
        {
            "selection_mode": normalized_selection.mode,
            "requested_slices": list(normalized_selection.slices),
            "selected_slices": selected_slices,
            "local_slice_count": len(selected_slices),
            "local_first_slice": None if not selected_slices else selected_slices[0],
            "local_last_slice": None if not selected_slices else selected_slices[-1],
            "loaded_resource_count": len(selected_partitions),
            "loaded_row_count": sum(selected_row_counts),
            "row_count": sum(selected_row_counts),
            "partition_count": len(selected_partitions),
            "parquet_file_count": len(selected_partitions),
            "latest_imported_at": None if not imported_values else max(imported_values),
            "latest_refreshed_at": None if not refreshed_values else max(refreshed_values),
        }
    )
    return summary


def _normalize_dataset_selection(
    dataset: str,
    selection: TemporalSelection | None,
    registry: DatasetFamilyRegistry,
) -> TemporalSelection:
    """@notice Validate one stats temporal selection through the shared family-adapter rules."""

    normalized_selection = TemporalSelection(mode="all") if selection is None else selection
    if normalized_selection.mode != "all":
        registry.resolve_remote_dataset_ids(dataset, selection=normalized_selection)
    return normalized_selection


def _selected_partition_slices(
    connection: duckdb.DuckDBPyConnection,
    dataset: str,
    selection: TemporalSelection,
) -> list[str]:
    """@notice Return the local slices selected for a scoped stats request."""

    return [str(row["slice"]) for row in _filter_partition_rows(_load_dataset_partitions(connection, dataset), selection)]


def _load_dataset_partitions(connection: duckdb.DuckDBPyConnection, dataset: str) -> list[dict[str, object]]:
    """@notice Load the dataset's local partition rows in stable temporal order."""

    return _fetch_all_dicts(
        connection,
        """
        SELECT
            dataset,
            slice,
            year,
            month,
            dataset_id,
            resource_name,
            manifest_path,
            parquet_path,
            row_count,
            remote_size_bytes,
            remote_modified,
            content_checksum,
            imported_at,
            refreshed_at
        FROM anac_partitions
        WHERE dataset = ?
        ORDER BY year, month, slice
        """,
        [dataset],
    )


def _filter_partition_rows(rows: list[dict[str, object]], selection: TemporalSelection) -> list[dict[str, object]]:
    """@notice Apply one temporal selection to local partition rows without requiring remote access."""

    if selection.mode == "all":
        return rows
    if selection.mode == "latest":
        return [] if not rows else [rows[-1]]
    requested_slices = set(selection.slices)
    return [row for row in rows if str(row["slice"]) in requested_slices]


def _resolve_profile_relation_name(
    connection: duckdb.DuckDBPyConnection,
    dataset: str,
    summary: dict[str, object],
) -> str:
    """@notice Resolve the registered local relation that profile mode should aggregate."""

    view_name = summary.get("query_view_name")
    if not isinstance(view_name, str) or not view_name:
        raise CliCommandError(
            "LOCAL_DATASET_NOT_AVAILABLE",
            f"Dataset family {dataset!r} does not expose a local query relation to profile.",
            details={"dataset": dataset},
        )
    registered_view = _fetch_one_dict(
        connection,
        "SELECT view_name FROM anac_registered_views WHERE view_name = ?",
        [view_name],
    )
    if registered_view is None:
        raise CliCommandError(
            "LOCAL_DATASET_NOT_AVAILABLE",
            f"Dataset family {dataset!r} is not currently loaded in the local warehouse.",
            details={"dataset": dataset, "view_name": view_name},
        )
    return view_name


def _profile_relation_sql(relation_name: str, selected_slices: list[str] | None) -> str:
    """@notice Build the profile target relation SQL, optionally filtered to selected period slices."""

    quoted_relation = _quote_identifier(relation_name)
    if selected_slices is None:
        return quoted_relation
    if not selected_slices:
        return f"(SELECT * FROM {quoted_relation} WHERE 1 = 0)"
    predicates = []
    for slice_value in selected_slices:
        year_text, month_text = str(slice_value).split("-", 1)
        predicates.append(f"(year = {int(year_text)} AND month = {int(month_text)})")
    return f"(SELECT * FROM {quoted_relation} WHERE {' OR '.join(predicates)})"


def _describe_relation(connection: duckdb.DuckDBPyConnection, relation_sql: str) -> dict[str, str]:
    """@notice Describe a DuckDB relation and return ordered column types keyed by column name."""

    try:
        rows = connection.execute(f"DESCRIBE SELECT * FROM {relation_sql}").fetchall()
    except duckdb.Error as exc:
        raise CliCommandError(
            "LOCAL_DATASET_NOT_AVAILABLE",
            f"Local relation {relation_sql!r} is not available for profiling.",
            details={"relation": relation_sql},
            cause=exc,
        ) from exc
    return {str(column_name): str(column_type) for column_name, column_type, *_ in rows}


def _fetch_one_dict(
    connection: duckdb.DuckDBPyConnection,
    sql_query: str,
    parameters: list[object] | None = None,
) -> dict[str, object] | None:
    """@notice Execute one metadata query and return the first row as a keyed mapping."""

    cursor = connection.execute(sql_query, [] if parameters is None else parameters)
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip([column[0] for column in cursor.description], row))


def _fetch_all_dicts(
    connection: duckdb.DuckDBPyConnection,
    sql_query: str,
    parameters: list[object] | None = None,
) -> list[dict[str, object]]:
    """@notice Execute one metadata query and return every row as a keyed mapping."""

    cursor = connection.execute(sql_query, [] if parameters is None else parameters)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _quote_identifier(identifier: str) -> str:
    """@notice Quote one SQL identifier for direct interpolation into generated DuckDB queries."""

    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _supports_min_max(column_type: str) -> bool:
    """@notice Return whether ordered min/max aggregates are meaningful for the DuckDB type."""

    normalized_type = column_type.upper()
    if _is_complex_type(normalized_type):
        return False
    return not normalized_type.startswith("BOOLEAN")


def _supports_approx_distinct(column_type: str) -> bool:
    """@notice Return whether `APPROX_COUNT_DISTINCT` should be attempted for the DuckDB type."""

    return not _is_complex_type(column_type.upper())


def _is_complex_type(column_type: str) -> bool:
    """@notice Identify nested or non-scalar DuckDB types that the stats profiler should skip."""

    return column_type.startswith(("STRUCT", "LIST", "MAP", "UNION", "ARRAY"))


def _stats_scope(selection: TemporalSelection) -> str:
    """@notice Map one temporal stats selection to the public result scope."""

    return "dataset" if selection.mode == "all" else "slice"
