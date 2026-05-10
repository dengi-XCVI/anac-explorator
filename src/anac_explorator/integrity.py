"""@notice Read-only warehouse integrity validation for DuckDB/Parquet assets.

@dev The validator is intentionally warehouse-native: it runs SQL checks against
the existing DuckDB catalog and Parquet-backed views instead of building a
second materialized copy of the data just for validation.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from anac_explorator.comparison import load_schema_mapping
from anac_explorator.models import (
    SchemaMapping,
    WarehouseIntegrityCheckResult,
    WarehouseIntegrityIssue,
    WarehouseIntegrityReport,
)


def validate_local_data_integrity(
    db_path: str | Path,
    *,
    dataset_type: str = "cig",
    schema_path: str | Path | None = None,
    vocabulary_index_path: str | Path | None = None,
) -> WarehouseIntegrityReport:
    """@notice Validate the local warehouse state for the current CIG-focused slice."""

    database_path = Path(db_path)
    if not database_path.exists():
        raise FileNotFoundError(f"DuckDB database not found: {database_path}")

    resolved_schema_path = None if schema_path is None else Path(schema_path)
    resolved_vocabulary_index_path = None if vocabulary_index_path is None else Path(vocabulary_index_path)
    schema_mapping = None if resolved_schema_path is None else load_schema_mapping(resolved_schema_path)

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        checks = [
            _catalog_integrity_check(connection, dataset_type=dataset_type),
            _row_count_integrity_check(connection, dataset_type=dataset_type),
            _schema_consistency_check(
                connection,
                dataset_type=dataset_type,
                schema_path=resolved_schema_path,
                schema_mapping=schema_mapping,
            ),
            _uniqueness_check(
                connection,
                dataset_type=dataset_type,
                vocabulary_index_path=resolved_vocabulary_index_path,
            ),
            _referential_integrity_check(
                connection,
                dataset_type=dataset_type,
                vocabulary_index_path=resolved_vocabulary_index_path,
            ),
            _incremental_integrity_check(connection, dataset_type=dataset_type),
        ]
    finally:
        connection.close()

    return WarehouseIntegrityReport(
        db_path=str(database_path),
        dataset_type=dataset_type,
        schema_path=None if resolved_schema_path is None else str(resolved_schema_path),
        vocabulary_index_path=None if resolved_vocabulary_index_path is None else str(resolved_vocabulary_index_path),
        checked_at=datetime.now(timezone.utc).isoformat(),
        checks=checks,
    )


def _catalog_integrity_check(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_type: str,
) -> WarehouseIntegrityCheckResult:
    """@notice Validate metadata-table coherence against the current warehouse files."""

    check_name = "catalog_integrity"
    issues: list[WarehouseIntegrityIssue] = []
    required_tables = ("loaded_resources", "registered_views", "dataset_period_manifest")
    missing_tables = [table_name for table_name in required_tables if not _table_exists(connection, table_name)]
    if missing_tables:
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="missing_catalog_tables",
                message="Required warehouse catalog tables are missing.",
                details={"missing_tables": missing_tables},
            )
        )
        return _finalize_check(check_name, issues)

    loaded_rows = connection.execute(
        """
        SELECT manifest_path, parquet_path, row_count, resource_name
        FROM loaded_resources
        WHERE table_name = ?
        ORDER BY manifest_path
        """,
        [dataset_type],
    ).fetchall()
    period_rows = connection.execute(
        """
        SELECT period, manifest_path, parquet_path, row_count
        FROM dataset_period_manifest
        WHERE dataset_type = ?
        ORDER BY period
        """,
        [dataset_type],
    ).fetchall()

    for manifest_path_value, parquet_path_value, row_count_value, resource_name_value in loaded_rows:
        parquet_path = Path(str(parquet_path_value))
        if not parquet_path.exists():
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="missing_loaded_parquet",
                    message="A loaded resource references a missing Parquet slice.",
                    details={
                        "manifest_path": str(manifest_path_value),
                        "parquet_path": str(parquet_path_value),
                        "resource_name": str(resource_name_value),
                    },
                )
            )
        if _derive_period_from_resource_name(str(resource_name_value)) is not None:
            matching_period_row = next(
                (
                    row
                    for row in period_rows
                    if str(row[1]) == str(manifest_path_value) and str(row[2]) == str(parquet_path_value)
                ),
                None,
            )
            if matching_period_row is None:
                issues.append(
                    WarehouseIntegrityIssue(
                        check_name=check_name,
                        severity="error",
                        code="missing_period_manifest_row",
                        message="A monthly CIG slice exists in loaded_resources but not in dataset_period_manifest.",
                        details={
                            "manifest_path": str(manifest_path_value),
                            "parquet_path": str(parquet_path_value),
                            "resource_name": str(resource_name_value),
                        },
                    )
                )

    for period_value, manifest_path_value, parquet_path_value, row_count_value in period_rows:
        manifest_path = Path(str(manifest_path_value))
        parquet_path = Path(str(parquet_path_value))
        if not manifest_path.exists():
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="missing_period_manifest_file",
                    message="A period manifest row references a missing raw manifest file.",
                    details={"period": str(period_value), "manifest_path": str(manifest_path_value)},
                )
            )
        if not parquet_path.exists():
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="missing_period_parquet",
                    message="A period manifest row references a missing Parquet slice.",
                    details={"period": str(period_value), "parquet_path": str(parquet_path_value)},
                )
            )
        matching_loaded_resource = next(
            (
                row
                for row in loaded_rows
                if str(row[0]) == str(manifest_path_value) and str(row[1]) == str(parquet_path_value)
            ),
            None,
        )
        if matching_loaded_resource is None:
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="orphan_period_catalog_row",
                    message="A period catalog row does not map back to a loaded CIG resource.",
                    details={
                        "period": str(period_value),
                        "manifest_path": str(manifest_path_value),
                        "parquet_path": str(parquet_path_value),
                    },
                )
            )

    registered_view = connection.execute(
        """
        SELECT parquet_file_count
        FROM registered_views
        WHERE view_name = ?
        """,
        [dataset_type],
    ).fetchone()
    actual_parquet_files = len({str(row[1]) for row in loaded_rows})
    if registered_view is None:
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="missing_registered_view",
                message="The logical CIG view is not registered in the warehouse catalog.",
                details={"view_name": dataset_type},
            )
        )
    elif int(registered_view[0]) != actual_parquet_files:
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="registered_view_file_count_mismatch",
                message="The registered view metadata does not match the current number of loaded Parquet slices.",
                details={
                    "view_name": dataset_type,
                    "registered_file_count": int(registered_view[0]),
                    "actual_file_count": actual_parquet_files,
                },
            )
        )

    return _finalize_check(
        check_name,
        issues,
        metrics={
            "loaded_resources_count": len(loaded_rows),
            "period_manifest_count": len(period_rows),
            "actual_parquet_file_count": actual_parquet_files,
        },
    )


def _row_count_integrity_check(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_type: str,
) -> WarehouseIntegrityCheckResult:
    """@notice Recompute per-slice and merged-view row counts and compare them with catalog metadata."""

    check_name = "row_count_integrity"
    issues: list[WarehouseIntegrityIssue] = []
    if not _table_exists(connection, "loaded_resources") or not _table_exists(connection, "dataset_period_manifest"):
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="missing_catalog_tables",
                message="Row-count validation requires loaded_resources and dataset_period_manifest.",
            )
        )
        return _finalize_check(check_name, issues)

    loaded_rows = connection.execute(
        """
        SELECT manifest_path, parquet_path, row_count
        FROM loaded_resources
        WHERE table_name = ?
        ORDER BY manifest_path
        """,
        [dataset_type],
    ).fetchall()
    actual_slice_counts: dict[str, int] = {}
    for manifest_path_value, parquet_path_value, stored_row_count_value in loaded_rows:
        parquet_path = Path(str(parquet_path_value))
        if not parquet_path.exists():
            continue
        actual_row_count = _count_parquet_rows(connection, parquet_path)
        actual_slice_counts[str(parquet_path_value)] = actual_row_count
        if actual_row_count != int(stored_row_count_value):
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="loaded_resource_row_count_mismatch",
                    message="A loaded resource row count does not match the Parquet slice on disk.",
                    details={
                        "manifest_path": str(manifest_path_value),
                        "parquet_path": str(parquet_path_value),
                        "stored_row_count": int(stored_row_count_value),
                        "actual_row_count": actual_row_count,
                    },
                )
            )

    period_rows = connection.execute(
        """
        SELECT period, parquet_path, row_count
        FROM dataset_period_manifest
        WHERE dataset_type = ?
        ORDER BY period
        """,
        [dataset_type],
    ).fetchall()
    for period_value, parquet_path_value, stored_row_count_value in period_rows:
        parquet_path = Path(str(parquet_path_value))
        if not parquet_path.exists():
            continue
        actual_row_count = actual_slice_counts.get(str(parquet_path_value))
        if actual_row_count is None:
            actual_row_count = _count_parquet_rows(connection, parquet_path)
        if actual_row_count != int(stored_row_count_value):
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="period_manifest_row_count_mismatch",
                    message="A period-manifest row count does not match the Parquet slice on disk.",
                    details={
                        "period": str(period_value),
                        "parquet_path": str(parquet_path_value),
                        "stored_row_count": int(stored_row_count_value),
                        "actual_row_count": actual_row_count,
                    },
                )
            )
        year_value, month_value = str(period_value).split("_", maxsplit=1)
        if _table_exists(connection, dataset_type):
            merged_view_row_count = int(
                connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {_quoted_identifier(dataset_type)}
                    WHERE year = ? AND month = ?
                    """,
                    [year_value, month_value],
                ).fetchone()[0]
            )
            if merged_view_row_count != actual_row_count:
                issues.append(
                    WarehouseIntegrityIssue(
                        check_name=check_name,
                        severity="error",
                        code="merged_view_period_row_count_mismatch",
                        message="The merged CIG view count for one period does not match the stored Parquet slice.",
                        details={
                            "period": str(period_value),
                            "view_row_count": merged_view_row_count,
                            "slice_row_count": actual_row_count,
                        },
                    )
                )

    merged_view_row_count = 0
    if _table_exists(connection, dataset_type):
        merged_view_row_count = int(
            connection.execute(f"SELECT COUNT(*) FROM {_quoted_identifier(dataset_type)}").fetchone()[0]
        )
        total_period_row_count = sum(int(row[2]) for row in period_rows)
        if merged_view_row_count != total_period_row_count:
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="merged_view_total_row_count_mismatch",
                    message="The merged CIG view row count does not match the sum of cataloged period slices.",
                    details={
                        "view_row_count": merged_view_row_count,
                        "period_manifest_total_row_count": total_period_row_count,
                    },
                )
            )

    return _finalize_check(
        check_name,
        issues,
        metrics={
            "loaded_slice_count": len(loaded_rows),
            "period_count": len(period_rows),
            "merged_view_row_count": merged_view_row_count,
        },
    )


def _schema_consistency_check(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_type: str,
    schema_path: Path | None,
    schema_mapping: SchemaMapping | None,
) -> WarehouseIntegrityCheckResult:
    """@notice Validate the live loaded schema against the expected CIG schema artifact and period slices."""

    check_name = "schema_consistency"
    issues: list[WarehouseIntegrityIssue] = []
    if schema_path is None:
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="missing_schema_path",
                message="Schema consistency validation requires a schema artifact path.",
            )
        )
        return _finalize_check(check_name, issues)
    if not schema_path.exists():
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="missing_schema_artifact",
                message="The requested schema artifact was not found.",
                details={"schema_path": str(schema_path)},
            )
        )
        return _finalize_check(check_name, issues)
    if schema_mapping is None:
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="invalid_schema_mapping",
                message="The schema artifact could not be loaded for validation.",
                details={"schema_path": str(schema_path)},
            )
        )
        return _finalize_check(check_name, issues)
    if not _table_exists(connection, dataset_type):
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="missing_dataset_view",
                message="The logical dataset view is missing, so schema consistency cannot be checked.",
                details={"view_name": dataset_type},
            )
        )
        return _finalize_check(check_name, issues)

    actual_schema = _describe_relation(connection, _quoted_identifier(dataset_type))
    expected_fields = {column.name: column.inferred_type for column in schema_mapping.columns}
    missing_fields = [field_name for field_name in expected_fields if field_name not in actual_schema]
    if missing_fields:
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="missing_expected_columns",
                message="The loaded CIG view is missing columns required by the schema artifact.",
                details={"missing_columns": missing_fields},
            )
        )

    for partition_column in ("year", "month"):
        if partition_column not in actual_schema:
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="missing_partition_column",
                    message="The loaded CIG view is missing a required partition column.",
                    details={"column_name": partition_column},
                )
            )

    for field_name, expected_type in expected_fields.items():
        actual_type = actual_schema.get(field_name)
        if actual_type is None:
            continue
        if not _type_is_compatible(expected_type, actual_type):
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="incompatible_column_type",
                    message="A loaded CIG column type does not match the expected schema artifact.",
                    details={
                        "column_name": field_name,
                        "expected_type": expected_type,
                        "actual_type": actual_type,
                    },
                )
            )

    if _table_exists(connection, "dataset_period_manifest"):
        period_rows = connection.execute(
            """
            SELECT period, parquet_path
            FROM dataset_period_manifest
            WHERE dataset_type = ?
            ORDER BY period
            """,
            [dataset_type],
        ).fetchall()
        for period_value, parquet_path_value in period_rows:
            parquet_path = Path(str(parquet_path_value))
            if not parquet_path.exists():
                continue
            slice_schema = _describe_parquet_path(connection, parquet_path)
            for field_name, actual_type in actual_schema.items():
                slice_type = slice_schema.get(field_name)
                if slice_type is None:
                    issues.append(
                        WarehouseIntegrityIssue(
                            check_name=check_name,
                            severity="error",
                            code="slice_missing_column",
                            message="A period Parquet slice is missing a column present in the merged CIG view.",
                            details={"period": str(period_value), "column_name": field_name},
                        )
                    )
                    continue
                if slice_type != actual_type:
                    issues.append(
                        WarehouseIntegrityIssue(
                            check_name=check_name,
                            severity="error",
                            code="slice_schema_drift",
                            message="A period Parquet slice exposes a column type that differs from the merged CIG view.",
                            details={
                                "period": str(period_value),
                                "column_name": field_name,
                                "view_type": actual_type,
                                "slice_type": slice_type,
                            },
                        )
                    )

    return _finalize_check(
        check_name,
        issues,
        metrics={"expected_column_count": len(expected_fields), "actual_column_count": len(actual_schema)},
    )


def _uniqueness_check(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_type: str,
    vocabulary_index_path: Path | None,
) -> WarehouseIntegrityCheckResult:
    """@notice Validate configured unique keys for CIG rows and external vocabulary code tables."""

    check_name = "uniqueness"
    issues: list[WarehouseIntegrityIssue] = []
    if _table_exists(connection, dataset_type):
        view_columns = list(_describe_relation(connection, _quoted_identifier(dataset_type)))
        quoted_view_columns = ", ".join(_quoted_identifier(column_name) for column_name in view_columns)
        exact_duplicate_row_count = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM (
                    SELECT {quoted_view_columns}, COUNT(*) AS duplicate_count
                    FROM {_quoted_identifier(dataset_type)}
                    GROUP BY ALL
                    HAVING COUNT(*) > 1
                ) AS exact_duplicate_rows
                """
            ).fetchone()[0]
        )
        if exact_duplicate_row_count:
            sample_duplicates = connection.execute(
                f"""
                WITH exact_duplicate_rows AS (
                    SELECT {quoted_view_columns}, COUNT(*) AS duplicate_count
                    FROM {_quoted_identifier(dataset_type)}
                    GROUP BY ALL
                    HAVING COUNT(*) > 1
                )
                SELECT cig, duplicate_count
                FROM exact_duplicate_rows
                ORDER BY duplicate_count DESC, cig
                LIMIT 10
                """
            ).fetchall()
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="exact_duplicate_rows",
                    message="The merged CIG dataset contains exact duplicate rows.",
                    details={
                        "exact_duplicate_row_group_count": exact_duplicate_row_count,
                        "sample_duplicates": [
                            {"cig": str(cig_value), "duplicate_count": int(duplicate_count_value)}
                            for cig_value, duplicate_count_value in sample_duplicates
                        ],
                    },
                )
            )

        duplicate_cig_count = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM (
                    SELECT cig
                    FROM {_quoted_identifier(dataset_type)}
                    WHERE cig IS NOT NULL
                    GROUP BY cig
                    HAVING COUNT(*) > 1
                ) AS duplicate_cigs
                """
            ).fetchone()[0]
        )
        if duplicate_cig_count:
            sample_duplicate_keys = connection.execute(
                f"""
                SELECT cig, COUNT(*) AS duplicate_count
                FROM {_quoted_identifier(dataset_type)}
                WHERE cig IS NOT NULL
                GROUP BY cig
                HAVING COUNT(*) > 1
                ORDER BY duplicate_count DESC, cig
                LIMIT 10
                """
            ).fetchall()
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="warning",
                    code="non_unique_cig",
                    message="The merged CIG dataset contains CIG identifiers that span multiple distinct rows.",
                    details={
                        "duplicate_key_count": duplicate_cig_count,
                        "sample_duplicates": [
                            {"cig": str(cig_value), "duplicate_count": int(duplicate_count_value)}
                            for cig_value, duplicate_count_value in sample_duplicate_keys
                        ],
                    },
                )
            )
    else:
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="missing_dataset_view",
                message="The logical CIG view is missing, so uniqueness checks cannot run.",
                details={"view_name": dataset_type},
            )
        )
        return _finalize_check(check_name, issues)

    for field_link in _load_current_external_field_links(vocabulary_index_path):
        target_table = str(field_link["table_name"])
        target_code_field = str(field_link["target_code_field"])
        if not _table_exists(connection, target_table):
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="missing_vocabulary_view",
                    message="A vocabulary view required for uniqueness validation is missing.",
                    details={"table_name": target_table},
                )
            )
            continue
        duplicate_code_count = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM (
                    SELECT {_quoted_identifier(target_code_field)}
                    FROM {_quoted_identifier(target_table)}
                    WHERE {_quoted_identifier(target_code_field)} IS NOT NULL
                    GROUP BY {_quoted_identifier(target_code_field)}
                    HAVING COUNT(*) > 1
                ) AS duplicate_codes
                """
            ).fetchone()[0]
        )
        if duplicate_code_count:
            sample_duplicates = connection.execute(
                f"""
                SELECT {_quoted_identifier(target_code_field)} AS duplicate_code, COUNT(*) AS duplicate_count
                FROM {_quoted_identifier(target_table)}
                WHERE {_quoted_identifier(target_code_field)} IS NOT NULL
                GROUP BY {_quoted_identifier(target_code_field)}
                HAVING COUNT(*) > 1
                ORDER BY duplicate_count DESC, duplicate_code
                LIMIT 10
                """
            ).fetchall()
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="duplicate_vocabulary_code",
                    message="A vocabulary crosswalk contains duplicate code keys.",
                    details={
                        "table_name": target_table,
                        "duplicate_code_count": duplicate_code_count,
                        "sample_duplicates": [
                            {"code": None if code_value is None else str(code_value), "duplicate_count": int(count_value)}
                            for code_value, count_value in sample_duplicates
                        ],
                    },
                )
            )

    return _finalize_check(check_name, issues)


def _referential_integrity_check(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_type: str,
    vocabulary_index_path: Path | None,
) -> WarehouseIntegrityCheckResult:
    """@notice Validate current external CIG code fields against the registered vocabulary views."""

    check_name = "referential_integrity"
    issues: list[WarehouseIntegrityIssue] = []
    field_links = _load_current_external_field_links(vocabulary_index_path)
    if not field_links:
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="missing_field_links",
                message="No external CIG field-link metadata was available for referential validation.",
                details={"vocabulary_index_path": None if vocabulary_index_path is None else str(vocabulary_index_path)},
            )
        )
        return _finalize_check(check_name, issues)
    if not _table_exists(connection, dataset_type):
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="missing_dataset_view",
                message="The logical CIG view is missing, so referential checks cannot run.",
                details={"view_name": dataset_type},
            )
        )
        return _finalize_check(check_name, issues)

    for field_link in field_links:
        source_code_field = str(field_link["source_code_field"])
        source_label_field = str(field_link.get("source_label_field") or "")
        target_table = str(field_link["table_name"])
        target_code_field = str(field_link["target_code_field"])
        target_label_field = str(field_link["target_label_field"])
        source_code_expression = f"CAST(c.{_quoted_identifier(source_code_field)} AS VARCHAR)"
        target_code_expression = f"t.{_quoted_identifier(target_code_field)}"
        if not _table_exists(connection, target_table):
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="missing_vocabulary_view",
                    message="A vocabulary view required for referential validation is missing.",
                    details={"table_name": target_table},
                )
            )
            continue
        unmatched_count = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM {_quoted_identifier(dataset_type)} AS c
                LEFT JOIN {_quoted_identifier(target_table)} AS t
                  ON {source_code_expression} = {target_code_expression}
                WHERE c.{_quoted_identifier(source_code_field)} IS NOT NULL
                  AND {target_code_expression} IS NULL
                """
            ).fetchone()[0]
        )
        if unmatched_count:
            sample_unmatched = connection.execute(
                f"""
                SELECT {source_code_expression} AS unmatched_code, COUNT(*) AS occurrence_count
                FROM {_quoted_identifier(dataset_type)} AS c
                LEFT JOIN {_quoted_identifier(target_table)} AS t
                  ON {source_code_expression} = {target_code_expression}
                WHERE c.{_quoted_identifier(source_code_field)} IS NOT NULL
                  AND {target_code_expression} IS NULL
                GROUP BY {source_code_expression}
                ORDER BY occurrence_count DESC, unmatched_code
                LIMIT 10
                """
            ).fetchall()
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="unmatched_external_code",
                    message="The CIG dataset contains codes that do not resolve in the registered external vocabulary.",
                    details={
                        "source_code_field": source_code_field,
                        "target_table": target_table,
                        "unmatched_code_count": unmatched_count,
                        "sample_unmatched_codes": [
                            {
                                "code": None if code_value is None else str(code_value),
                                "occurrence_count": int(count_value),
                            }
                            for code_value, count_value in sample_unmatched
                        ],
                    },
                )
            )
        if source_label_field:
            label_mismatch_count = int(
                connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {_quoted_identifier(dataset_type)} AS c
                    JOIN {_quoted_identifier(target_table)} AS t
                      ON {source_code_expression} = {target_code_expression}
                    WHERE c.{_quoted_identifier(source_label_field)} IS NOT NULL
                      AND t.{_quoted_identifier(target_label_field)} IS NOT NULL
                      AND trim(c.{_quoted_identifier(source_label_field)}) != trim(t.{_quoted_identifier(target_label_field)})
                    """
                ).fetchone()[0]
            )
            if label_mismatch_count:
                sample_mismatches = connection.execute(
                    f"""
                    SELECT
                        {source_code_expression} AS code,
                        c.{_quoted_identifier(source_label_field)} AS source_label,
                        t.{_quoted_identifier(target_label_field)} AS target_label
                    FROM {_quoted_identifier(dataset_type)} AS c
                    JOIN {_quoted_identifier(target_table)} AS t
                      ON {source_code_expression} = {target_code_expression}
                    WHERE c.{_quoted_identifier(source_label_field)} IS NOT NULL
                      AND t.{_quoted_identifier(target_label_field)} IS NOT NULL
                      AND trim(c.{_quoted_identifier(source_label_field)}) != trim(t.{_quoted_identifier(target_label_field)})
                    LIMIT 10
                    """
                ).fetchall()
                issues.append(
                    WarehouseIntegrityIssue(
                        check_name=check_name,
                        severity="warning",
                        code="code_label_disagreement",
                        message="Some CIG rows carry labels that disagree with the external vocabulary label for the same code.",
                        details={
                            "source_code_field": source_code_field,
                            "target_table": target_table,
                            "label_mismatch_count": label_mismatch_count,
                            "sample_mismatches": [
                                {
                                    "code": None if code_value is None else str(code_value),
                                    "source_label": None if source_label_value is None else str(source_label_value),
                                    "target_label": None if target_label_value is None else str(target_label_value),
                                }
                                for code_value, source_label_value, target_label_value in sample_mismatches
                            ],
                        },
                    )
                )

    return _finalize_check(check_name, issues)


def _incremental_integrity_check(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_type: str,
) -> WarehouseIntegrityCheckResult:
    """@notice Validate that the incremental period catalog remains coherent after sync and refresh operations."""

    check_name = "incremental_integrity"
    issues: list[WarehouseIntegrityIssue] = []
    if not _table_exists(connection, "dataset_period_manifest"):
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="missing_period_manifest_table",
                message="The dataset_period_manifest table is missing, so incremental integrity cannot be validated.",
            )
        )
        return _finalize_check(check_name, issues)

    duplicate_period_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT period
                FROM dataset_period_manifest
                WHERE dataset_type = ?
                GROUP BY period
                HAVING COUNT(*) > 1
            ) AS duplicate_periods
            """,
            [dataset_type],
        ).fetchone()[0]
    )
    if duplicate_period_count:
        issues.append(
            WarehouseIntegrityIssue(
                check_name=check_name,
                severity="error",
                code="duplicate_period_rows",
                message="The period catalog contains duplicate active rows for the same period.",
                details={"duplicate_period_count": duplicate_period_count},
            )
        )

    period_rows = connection.execute(
        """
        SELECT period, manifest_path, parquet_path, content_checksum, remote_modified, remote_size
        FROM dataset_period_manifest
        WHERE dataset_type = ?
        ORDER BY period
        """,
        [dataset_type],
    ).fetchall()
    for period_value, manifest_path_value, parquet_path_value, checksum_value, remote_modified_value, remote_size_value in period_rows:
        year_value, month_value = str(period_value).split("_", maxsplit=1)
        parquet_path = Path(str(parquet_path_value))
        if f"year={year_value}" not in str(parquet_path) or f"month={month_value}" not in str(parquet_path):
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="error",
                    code="period_partition_path_mismatch",
                    message="A period catalog row points to a Parquet path whose partitions do not match the cataloged period.",
                    details={"period": str(period_value), "parquet_path": str(parquet_path_value)},
                )
            )
        if checksum_value in (None, ""):
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="warning",
                    code="missing_period_checksum",
                    message="A period catalog row is missing its content checksum.",
                    details={"period": str(period_value), "manifest_path": str(manifest_path_value)},
                )
            )
        if remote_modified_value in (None, "") and remote_size_value is None:
            issues.append(
                WarehouseIntegrityIssue(
                    check_name=check_name,
                    severity="warning",
                    code="missing_remote_change_metadata",
                    message="A period catalog row has neither remote_modified nor remote_size metadata for refresh detection.",
                    details={"period": str(period_value), "manifest_path": str(manifest_path_value)},
                )
            )

    return _finalize_check(check_name, issues, metrics={"period_count": len(period_rows)})


def _finalize_check(
    check_name: str,
    issues: list[WarehouseIntegrityIssue],
    *,
    metrics: dict[str, object] | None = None,
) -> WarehouseIntegrityCheckResult:
    """@notice Build a final check result status from the emitted issues."""

    status = "passed"
    if any(issue.severity == "error" for issue in issues):
        status = "failed"
    elif any(issue.severity == "warning" for issue in issues):
        status = "warning"
    return WarehouseIntegrityCheckResult(
        check_name=check_name,
        status=status,
        metrics={} if metrics is None else metrics,
        issues=issues,
    )


def _table_exists(connection: duckdb.DuckDBPyConnection, relation_name: str) -> bool:
    """@notice Return whether a table or view exists in the current DuckDB database."""

    row = connection.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        LIMIT 1
        """,
        [relation_name],
    ).fetchone()
    return row is not None


def _count_parquet_rows(connection: duckdb.DuckDBPyConnection, parquet_path: Path) -> int:
    """@notice Count rows in one Parquet slice through DuckDB."""

    return int(
        connection.execute(
            f"SELECT COUNT(*) FROM read_parquet({_sql_literal(str(parquet_path))}, hive_partitioning=true)"
        ).fetchone()[0]
    )


def _describe_relation(connection: duckdb.DuckDBPyConnection, relation_sql: str) -> dict[str, str]:
    """@notice Describe a DuckDB relation and return a column-to-type mapping."""

    rows = connection.execute(f"DESCRIBE SELECT * FROM {relation_sql}").fetchall()
    return {str(column_name): str(column_type) for column_name, column_type, *_ in rows}


def _describe_parquet_path(connection: duckdb.DuckDBPyConnection, parquet_path: Path) -> dict[str, str]:
    """@notice Describe one Parquet file using DuckDB's Parquet reader."""

    rows = connection.execute(
        f"DESCRIBE SELECT * FROM read_parquet({_sql_literal(str(parquet_path))}, hive_partitioning=true)"
    ).fetchall()
    return {str(column_name): str(column_type) for column_name, column_type, *_ in rows}


def _type_is_compatible(expected_type: str, actual_type: str) -> bool:
    """@notice Compare one inferred schema type with the live DuckDB column type."""

    normalized_expected = expected_type.casefold()
    normalized_actual = actual_type.casefold()
    if normalized_expected in {"text", "unknown"}:
        return normalized_actual in {"varchar", "text"}
    if normalized_expected == "boolean":
        return normalized_actual == "boolean"
    if normalized_expected == "integer":
        return normalized_actual in {"bigint", "integer", "smallint", "tinyint"}
    if normalized_expected == "decimal":
        return normalized_actual.startswith("decimal")
    if normalized_expected == "date":
        return normalized_actual == "date"
    if normalized_expected == "datetime":
        return normalized_actual.startswith("timestamp")
    return False


def _load_current_external_field_links(vocabulary_index_path: Path | None) -> list[dict[str, object]]:
    """@notice Load the current external CIG field links from the vocabulary index artifact."""

    if vocabulary_index_path is None or not vocabulary_index_path.exists():
        return []
    payload = json.loads(vocabulary_index_path.read_text(encoding="utf-8"))
    raw_field_links = payload.get("field_links", [])
    if not isinstance(raw_field_links, list):
        return []
    return [
        field_link
        for field_link in raw_field_links
        if isinstance(field_link, dict)
        and field_link.get("scope") == "current_cig_schema"
        and field_link.get("external_vocabulary_status") == "resolved"
        and isinstance(field_link.get("source_code_field"), str)
        and isinstance(field_link.get("table_name"), str)
        and isinstance(field_link.get("target_code_field"), str)
        and isinstance(field_link.get("target_label_field"), str)
    ]


def _derive_period_from_resource_name(resource_name: str) -> str | None:
    """@notice Derive a `YYYY_MM` period from a monthly CIG resource name."""

    match = re.fullmatch(r"cig_csv_(\d{4})_(\d{2})", resource_name.casefold())
    if match is None:
        return None
    return f"{match.group(1)}_{match.group(2)}"


def _quoted_identifier(value: str) -> str:
    """@notice Quote one DuckDB identifier."""

    return '"' + value.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    """@notice Quote one SQL string literal."""

    return "'" + value.replace("'", "''") + "'"
