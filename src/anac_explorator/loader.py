"""@notice DuckDB/Parquet loader helpers for manifest-backed ANAC resources.

@dev The scalable loader path differs from the inspection-oriented parser/cleaner
flow in one important way: it keeps the heavy scan inside DuckDB so large ANAC
resources can be loaded without retaining all rows in Python memory.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from anac_explorator.cleaner import DATETIME_FORMATS, DATE_FORMATS, NULL_MARKERS
from anac_explorator.comparison import load_schema_mapping
from anac_explorator.models import (
    DownloadManifest,
    SchemaMapping,
    WarehouseLoadResult,
    WarehousePartitionValue,
    WarehouseQueryResult,
)

_BOOLEAN_TRUE_LITERALS = ("1", "true", "yes")
_BOOLEAN_FALSE_LITERALS = ("0", "false", "no")
_TEXT_LIKE_TYPES = {"text", "unknown"}


@dataclass(slots=True)
class _WarehouseTargetPlan:
    """@notice Internal plan for one Parquet target path and DuckDB view."""

    table_name: str
    view_name: str
    parquet_root: Path
    parquet_path: Path
    partition_values: list[WarehousePartitionValue]


def load_downloaded_resource(
    manifest_path: str | Path,
    *,
    schema_path: str | Path | None = None,
    warehouse_dir: str | Path = "data/warehouse",
    delimiter: str = ";",
    encoding: str = "utf-8-sig",
) -> WarehouseLoadResult:
    """@notice Load one manifest-backed CSV resource into partitioned Parquet and DuckDB views."""

    manifest_location = Path(manifest_path)
    manifest = _load_download_manifest(manifest_location)
    if manifest.materialized_kind.casefold() != "csv":
        raise ValueError("The current loader supports manifest-backed CSV resources only.")

    source_path = Path(manifest.materialized_path)
    if not source_path.exists():
        raise FileNotFoundError(f"Materialized source file not found: {source_path}")

    schema_location = None if schema_path is None else Path(schema_path)
    schema_mapping = None if schema_location is None else load_schema_mapping(schema_location)
    field_names = _resolve_field_names(source_path, schema_mapping=schema_mapping, delimiter=delimiter, encoding=encoding)

    warehouse_root = Path(warehouse_dir)
    warehouse_root.mkdir(parents=True, exist_ok=True)
    duckdb_path = warehouse_root / "anac.duckdb"
    target_plan = _plan_target(manifest, warehouse_root)

    connection = duckdb.connect(str(duckdb_path))
    try:
        _ensure_warehouse_catalog(connection)

        source_relation_sql = _build_csv_source_relation_sql(source_path, field_names=field_names, delimiter=delimiter)
        _validate_projected_types(connection, source_relation_sql, field_names=field_names, schema_mapping=schema_mapping)

        projected_select_sql = _build_projected_select_sql(
            source_relation_sql,
            field_names=field_names,
            schema_mapping=schema_mapping,
            partition_values=target_plan.partition_values,
        )
        target_plan.parquet_path.parent.mkdir(parents=True, exist_ok=True)
        if target_plan.parquet_path.exists():
            target_plan.parquet_path.unlink()

        connection.execute(
            f"COPY ({projected_select_sql}) TO {_sql_literal(str(target_plan.parquet_path))} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        row_count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM read_parquet({_sql_literal(str(target_plan.parquet_path))}, hive_partitioning=true)"
            ).fetchone()[0]
        )

        registered_files, view_sql = _refresh_registered_view(
            connection,
            view_name=target_plan.view_name,
            parquet_root=target_plan.parquet_root,
        )
        _record_loaded_resource(
            connection,
            manifest_path=manifest_location,
            manifest=manifest,
            schema_path=schema_location,
            target_plan=target_plan,
            row_count=row_count,
        )
        _record_registered_view(
            connection,
            view_name=target_plan.view_name,
            table_name=target_plan.table_name,
            parquet_root=target_plan.parquet_root,
            registered_files=registered_files,
            view_sql=view_sql,
        )
    finally:
        connection.close()

    return WarehouseLoadResult(
        dataset_id=manifest.dataset_id,
        resource_name=manifest.resource_name,
        table_name=target_plan.table_name,
        view_name=target_plan.view_name,
        manifest_path=str(manifest_location),
        schema_path=None if schema_location is None else str(schema_location),
        source_path=str(source_path),
        warehouse_dir=str(warehouse_root),
        duckdb_path=str(duckdb_path),
        parquet_root=str(target_plan.parquet_root),
        parquet_path=str(target_plan.parquet_path),
        row_count=row_count,
        partition_values=target_plan.partition_values,
        registered_parquet_files=registered_files,
        view_sql=view_sql,
    )


def run_local_query(
    db_path: str | Path,
    sql_query: str,
    *,
    row_limit: int = 1_000,
) -> WarehouseQueryResult:
    """@notice Execute one SQL query against the local DuckDB warehouse and return JSON-friendly rows."""

    database_path = Path(db_path)
    if not database_path.exists():
        raise FileNotFoundError(f"DuckDB database not found: {database_path}")

    normalized_query = sql_query.strip().rstrip(";")
    if not normalized_query:
        raise ValueError("SQL query must not be empty.")

    executable_sql = normalized_query
    if row_limit > 0:
        executable_sql = f"SELECT * FROM ({normalized_query}) AS warehouse_query LIMIT {row_limit}"

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        cursor = connection.execute(executable_sql)
        column_names = [str(column[0]) for column in (cursor.description or [])]
        raw_rows = cursor.fetchall()
    finally:
        connection.close()

    rows = [dict(zip(column_names, row)) for row in raw_rows]
    return WarehouseQueryResult(
        db_path=str(database_path),
        sql_query=normalized_query,
        row_limit=row_limit,
        column_names=column_names,
        row_count=len(rows),
        rows=rows,
    )


def _load_download_manifest(manifest_path: Path) -> DownloadManifest:
    """@notice Read one manifest JSON file into the shared manifest dataclass."""

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return DownloadManifest.from_dict(payload)


def _resolve_field_names(
    source_path: Path,
    *,
    schema_mapping: SchemaMapping | None,
    delimiter: str,
    encoding: str,
) -> list[str]:
    """@notice Resolve the ordered source fields from a schema artifact or the CSV header."""

    if schema_mapping is not None:
        return [column.name for column in schema_mapping.columns]

    with source_path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        header = next(reader, None)
    if not header:
        raise ValueError(f"CSV file {source_path} does not contain a header row.")
    return [str(field) for field in header]


def _build_csv_source_relation_sql(source_path: Path, *, field_names: list[str], delimiter: str) -> str:
    """@notice Build the DuckDB table-function call that scans the raw CSV as all-text columns."""

    columns_sql = ", ".join(f"{_sql_literal(field_name)}: 'VARCHAR'" for field_name in field_names)
    return (
        f"read_csv({_sql_literal(str(source_path))}, delim={_sql_literal(delimiter)}, "
        f"header=false, skip=1, columns={{ {columns_sql} }})"
    )


def _build_projected_select_sql(
    source_relation_sql: str,
    *,
    field_names: list[str],
    schema_mapping: SchemaMapping | None,
    partition_values: list[WarehousePartitionValue],
) -> str:
    """@notice Build the typed SELECT that writes the cleaned warehouse projection."""

    type_hints = _build_type_hints(field_names, schema_mapping=schema_mapping)
    projected_columns = [
        f"{_projected_expression(field_name, type_hints[field_name])} AS {_quoted_identifier(field_name)}"
        for field_name in field_names
    ]
    projected_columns.extend(
        f"{_sql_literal(partition.value)} AS {_quoted_identifier(partition.key)}"
        for partition in partition_values
    )
    return f"SELECT {', '.join(projected_columns)} FROM {source_relation_sql}"


def _validate_projected_types(
    connection: duckdb.DuckDBPyConnection,
    source_relation_sql: str,
    *,
    field_names: list[str],
    schema_mapping: SchemaMapping | None,
) -> None:
    """@notice Fail fast if typed projections would silently drop invalid values.

    The SQL-native loader uses explicit validation before writing Parquet so the
    scalable path keeps the cleaner's "no silent coercion failure" behavior.
    """

    type_hints = _build_type_hints(field_names, schema_mapping=schema_mapping)
    validation_checks = []
    for field_name in field_names:
        invalid_condition = _invalid_value_condition(field_name, type_hints[field_name])
        if invalid_condition is None:
            continue
        validation_checks.append(
            f"SUM(CASE WHEN {invalid_condition} THEN 1 ELSE 0 END) AS {_quoted_identifier(field_name)}"
        )

    if not validation_checks:
        return

    cursor = connection.execute(f"SELECT {', '.join(validation_checks)} FROM {source_relation_sql}")
    invalid_counts = cursor.fetchone()
    if invalid_counts is None:
        return

    failures = [
        f"{column_name}={int(count)}"
        for (column_name, *_), count in zip(cursor.description or [], invalid_counts)
        if count not in (None, 0)
    ]
    if failures:
        raise ValueError(
            "Could not load resource because some values do not match the requested typed projection: "
            + ", ".join(failures)
        )


def _build_type_hints(field_names: list[str], *, schema_mapping: SchemaMapping | None) -> dict[str, str]:
    """@notice Build a complete field-to-type mapping for SQL projection generation."""

    if schema_mapping is None:
        return {field_name: "text" for field_name in field_names}
    inferred = {column.name: column.inferred_type for column in schema_mapping.columns}
    return {field_name: inferred.get(field_name, "text") for field_name in field_names}


def _projected_expression(field_name: str, target_type: str) -> str:
    """@notice Build the warehouse projection expression for one source column."""

    normalized_expression = _normalized_text_expression(field_name)
    normalized_lower_expression = f"lower({normalized_expression})"
    if target_type in _TEXT_LIKE_TYPES:
        return normalized_expression
    if target_type == "boolean":
        return (
            "CASE "
            f"WHEN {normalized_expression} IS NULL THEN NULL "
            f"WHEN {normalized_lower_expression} IN {_sql_tuple(_BOOLEAN_TRUE_LITERALS)} THEN TRUE "
            f"WHEN {normalized_lower_expression} IN {_sql_tuple(_BOOLEAN_FALSE_LITERALS)} THEN FALSE "
            "END"
        )
    if target_type == "integer":
        return f"CAST({normalized_expression} AS BIGINT)"
    if target_type == "decimal":
        return f"CAST({normalized_expression} AS DECIMAL(38, 9))"
    if target_type == "date":
        return f"CAST({_coalesced_strptime_expression(normalized_expression, DATE_FORMATS)} AS DATE)"
    if target_type == "datetime":
        return f"CAST({_coalesced_strptime_expression(normalized_expression, DATETIME_FORMATS)} AS TIMESTAMP)"
    return normalized_expression


def _invalid_value_condition(field_name: str, target_type: str) -> str | None:
    """@notice Build the boolean condition that marks invalid typed source values."""

    normalized_expression = _normalized_text_expression(field_name)
    normalized_lower_expression = f"lower({normalized_expression})"
    if target_type in _TEXT_LIKE_TYPES:
        return None
    if target_type == "boolean":
        return (
            f"{normalized_expression} IS NOT NULL "
            f"AND {normalized_lower_expression} NOT IN "
            f"{_sql_tuple(_BOOLEAN_TRUE_LITERALS + _BOOLEAN_FALSE_LITERALS)}"
        )
    if target_type == "integer":
        return f"{normalized_expression} IS NOT NULL AND TRY_CAST({normalized_expression} AS BIGINT) IS NULL"
    if target_type == "decimal":
        return (
            f"{normalized_expression} IS NOT NULL "
            f"AND TRY_CAST({normalized_expression} AS DECIMAL(38, 9)) IS NULL"
        )
    if target_type == "date":
        return (
            f"{normalized_expression} IS NOT NULL "
            f"AND {_coalesced_strptime_expression(normalized_expression, DATE_FORMATS)} IS NULL"
        )
    if target_type == "datetime":
        return (
            f"{normalized_expression} IS NOT NULL "
            f"AND {_coalesced_strptime_expression(normalized_expression, DATETIME_FORMATS)} IS NULL"
        )
    return None


def _normalized_text_expression(field_name: str) -> str:
    """@notice Build the SQL expression that mirrors the cleaner's trim/BOM/NULL handling."""

    raw_identifier = _quoted_identifier(field_name)
    stripped_expression = f"trim(replace({raw_identifier}, {_sql_literal(chr(0xFEFF))}, ''))"
    return (
        "CASE "
        f"WHEN {raw_identifier} IS NULL THEN NULL "
        f"WHEN lower({stripped_expression}) IN {_sql_tuple(sorted(NULL_MARKERS))} THEN NULL "
        f"ELSE {stripped_expression} "
        "END"
    )


def _coalesced_strptime_expression(source_expression: str, formats: tuple[str, ...]) -> str:
    """@notice Try several datetime/date formats in order and keep the first successful parse."""

    return "COALESCE(" + ", ".join(
        f"try_strptime({source_expression}, {_sql_literal(candidate)})" for candidate in formats
    ) + ")"


def _plan_target(manifest: DownloadManifest, warehouse_root: Path) -> _WarehouseTargetPlan:
    """@notice Derive the logical table name, partitions, and output Parquet path."""

    table_name = _logical_table_name(manifest)
    partition_values = _derive_partition_values(manifest)
    parquet_root = warehouse_root / "parquet" / table_name
    parquet_path = parquet_root
    for partition in partition_values:
        parquet_path /= f"{partition.key}={partition.value}"
    parquet_path /= f"{_normalize_identifier(manifest.resource_name)}.parquet"
    return _WarehouseTargetPlan(
        table_name=table_name,
        view_name=table_name,
        parquet_root=parquet_root,
        parquet_path=parquet_path,
        partition_values=partition_values,
    )


def _logical_table_name(manifest: DownloadManifest) -> str:
    """@notice Collapse year-specific CKAN dataset ids into stable logical table names."""

    dataset_id = manifest.dataset_id.casefold()
    resource_name = manifest.resource_name.casefold()
    if dataset_id.startswith("cig-") and resource_name.startswith("cig_csv_"):
        return "cig"
    if dataset_id.startswith("smartcig-") and resource_name.startswith("smartcig"):
        return "smartcig"
    return _normalize_identifier(manifest.dataset_id)


def _derive_partition_values(manifest: DownloadManifest) -> list[WarehousePartitionValue]:
    """@notice Derive warehouse partition values from the manifest naming conventions."""

    resource_name = manifest.resource_name.casefold()
    cig_match = re.fullmatch(r"cig_csv_(\d{4})_(\d{2})", resource_name)
    if cig_match is None:
        return []
    return [
        WarehousePartitionValue(key="year", value=cig_match.group(1)),
        WarehousePartitionValue(key="month", value=cig_match.group(2)),
    ]


def _refresh_registered_view(
    connection: duckdb.DuckDBPyConnection,
    *,
    view_name: str,
    parquet_root: Path,
) -> tuple[int, str]:
    """@notice Recompute the DuckDB view from the current Parquet file inventory."""

    parquet_files = sorted(parquet_root.rglob("*.parquet"))
    if not parquet_files:
        raise ValueError(f"No Parquet files found under {parquet_root}.")

    file_list_sql = "[" + ", ".join(_sql_literal(str(path)) for path in parquet_files) + "]"
    view_sql = (
        f"CREATE OR REPLACE VIEW {_quoted_identifier(view_name)} AS "
        f"SELECT * FROM read_parquet({file_list_sql}, hive_partitioning=true)"
    )
    connection.execute(view_sql)
    return len(parquet_files), view_sql


def _record_loaded_resource(
    connection: duckdb.DuckDBPyConnection,
    *,
    manifest_path: Path,
    manifest: DownloadManifest,
    schema_path: Path | None,
    target_plan: _WarehouseTargetPlan,
    row_count: int,
) -> None:
    """@notice Upsert metadata for the specific manifest-backed resource load."""

    loaded_at = datetime.now(timezone.utc).isoformat()
    partition_payload = json.dumps(
        [partition.to_dict() for partition in target_plan.partition_values],
        ensure_ascii=False,
    )
    connection.execute("DELETE FROM loaded_resources WHERE manifest_path = ?", [str(manifest_path)])
    connection.execute(
        """
        INSERT INTO loaded_resources (
            manifest_path,
            dataset_id,
            resource_name,
            table_name,
            view_name,
            source_path,
            schema_path,
            parquet_root,
            parquet_path,
            row_count,
            partition_values_json,
            loaded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            str(manifest_path),
            manifest.dataset_id,
            manifest.resource_name,
            target_plan.table_name,
            target_plan.view_name,
            manifest.materialized_path,
            None if schema_path is None else str(schema_path),
            str(target_plan.parquet_root),
            str(target_plan.parquet_path),
            row_count,
            partition_payload,
            loaded_at,
        ],
    )


def _record_registered_view(
    connection: duckdb.DuckDBPyConnection,
    *,
    view_name: str,
    table_name: str,
    parquet_root: Path,
    registered_files: int,
    view_sql: str,
) -> None:
    """@notice Upsert metadata for the current DuckDB view over Parquet files."""

    updated_at = datetime.now(timezone.utc).isoformat()
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
        [
            view_name,
            table_name,
            str(parquet_root),
            registered_files,
            view_sql,
            updated_at,
        ],
    )


def _ensure_warehouse_catalog(connection: duckdb.DuckDBPyConnection) -> None:
    """@notice Create the small warehouse metadata catalog when it does not exist yet."""

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


def _normalize_identifier(value: str) -> str:
    """@notice Convert a dataset or resource name into a stable DuckDB-safe identifier."""

    normalized = re.sub(r"[^0-9a-zA-Z]+", "_", value.casefold()).strip("_")
    normalized = re.sub(r"_+", "_", normalized)
    if not normalized:
        normalized = "dataset"
    if normalized[0].isdigit():
        normalized = f"dataset_{normalized}"
    return normalized


def _quoted_identifier(value: str) -> str:
    """@notice Quote a DuckDB identifier."""

    return '"' + value.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    """@notice Quote a string literal for inline SQL generation."""

    return "'" + value.replace("'", "''") + "'"


def _sql_tuple(values: tuple[str, ...] | list[str]) -> str:
    """@notice Quote a sequence of string literals as an SQL tuple."""

    return "(" + ", ".join(_sql_literal(value) for value in values) + ")"
