"""@notice DuckDB/Parquet loader helpers for manifest-backed ANAC resources.

@dev The scalable loader path differs from the inspection-oriented parser/cleaner
flow in one important way: it keeps the heavy scan inside DuckDB so large ANAC
resources can be loaded without retaining all rows in Python memory.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import duckdb

from anac_explorator.ckan import CkanClient
from anac_explorator.cleaner import DATETIME_FORMATS, DATE_FORMATS, NULL_MARKERS
from anac_explorator.comparison import load_schema_mapping
from anac_explorator.models import (
    CkanResource,
    DatasetParquetDownloadResult,
    DatasetIncrementalUpdateResult,
    DatasetPeriodManifestRecord,
    DatasetUpdatePlanItem,
    DownloadManifest,
    SchemaMapping,
    WarehouseCrosswalkRegistrationResult,
    WarehouseCrosswalkView,
    WarehouseLoadResult,
    WarehousePartitionValue,
    WarehouseQueryResult,
)
from anac_explorator.sample import download_dataset_resource
from anac_explorator.schema import map_csv_schema

_BOOLEAN_TRUE_LITERALS = ("1", "true", "yes")
_BOOLEAN_FALSE_LITERALS = ("0", "false", "no")
_TEXT_LIKE_TYPES = {"text", "unknown"}
_TEXT_IDENTIFIER_COLUMNS = {
    "cig",
    "cig_accordo_quadro",
    "numero_gara",
    "codice_ausa",
    "cf_amministrazione_appaltante",
    "cf_sa_delegante",
    "cf_sa_delegata",
    "id_centro_costo",
    "cui_programma",
    "cig_collegamento",
    "cod_cpv",
}


@dataclass(slots=True)
class _WarehouseTargetPlan:
    """@notice Internal plan for one Parquet target path and DuckDB view."""

    table_name: str
    view_name: str
    parquet_root: Path
    parquet_path: Path
    partition_values: list[WarehousePartitionValue]


@dataclass(slots=True)
class _RemotePeriodResource:
    """@notice Internal representation of one remote periodized CKAN resource."""

    dataset_type: str
    dataset_id: str
    period: str
    resource: CkanResource


def load_downloaded_resource(
    manifest_path: str | Path,
    *,
    schema_path: str | Path | None = None,
    warehouse_dir: str | Path = "data/warehouse",
    delimiter: str = ";",
    encoding: str = "utf-8-sig",
    force_load: bool = False,
) -> WarehouseLoadResult:
    """@notice Load one manifest-backed CSV resource into partitioned Parquet and DuckDB views."""

    manifest_location = Path(manifest_path)
    manifest = _load_download_manifest(manifest_location)
    if manifest.materialized_kind.casefold() != "csv":
        raise ValueError("The current loader supports manifest-backed CSV resources only.")

    schema_location = None if schema_path is None else Path(schema_path)
    warehouse_root = Path(warehouse_dir)
    warehouse_root.mkdir(parents=True, exist_ok=True)
    duckdb_path = warehouse_root / "anac.duckdb"
    target_plan = _plan_target(manifest, warehouse_root)

    connection = duckdb.connect(str(duckdb_path))
    try:
        _ensure_warehouse_catalog(connection)
        if not force_load:
            cached_result = _load_cached_load_result(
                connection,
                manifest_path=manifest_location,
                manifest=manifest,
                schema_path=schema_location,
                target_plan=target_plan,
                warehouse_root=warehouse_root,
            )
            if cached_result is not None:
                return cached_result
    finally:
        connection.close()

    schema_mapping = None if schema_location is None else load_schema_mapping(schema_location)
    with _resolve_source_path(manifest) as source_path:
        field_names = _resolve_field_names(
            source_path,
            schema_mapping=schema_mapping,
            delimiter=delimiter,
            encoding=encoding,
        )

        connection = duckdb.connect(str(duckdb_path))
        try:
            _ensure_warehouse_catalog(connection)

            source_relation_sql = _build_csv_source_relation_sql(
                source_path,
                field_names=field_names,
                delimiter=delimiter,
            )
            _validate_projected_types(
                connection,
                source_relation_sql,
                field_names=field_names,
                schema_mapping=schema_mapping,
            )

            projected_select_sql = _build_projected_select_sql(
                source_relation_sql,
                field_names=field_names,
                schema_mapping=schema_mapping,
                partition_values=target_plan.partition_values,
            )
            target_plan.parquet_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_parquet_path = target_plan.parquet_path.with_name(f".{target_plan.parquet_path.name}.tmp")
            temporary_parquet_path.unlink(missing_ok=True)
            try:
                connection.execute(
                    f"COPY ({projected_select_sql}) TO {_sql_literal(str(temporary_parquet_path))} "
                    "(FORMAT PARQUET, COMPRESSION ZSTD)"
                )
                temporary_parquet_path.replace(target_plan.parquet_path)
            finally:
                temporary_parquet_path.unlink(missing_ok=True)
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
        source_path=manifest.materialized_path,
        warehouse_dir=str(warehouse_root),
        duckdb_path=str(duckdb_path),
        parquet_root=str(target_plan.parquet_root),
        parquet_path=str(target_plan.parquet_path),
        row_count=row_count,
        partition_values=target_plan.partition_values,
        registered_parquet_files=registered_files,
        load_status="fresh",
        view_sql=view_sql,
    )


def download_dataset_to_parquet(
    client: CkanClient,
    *,
    dataset_id: str,
    output_dir: str | Path = "data/raw",
    schemas_dir: str | Path = "schemas",
    warehouse_dir: str | Path = "data/warehouse",
    preferred_resource_name: str | None = None,
    schema_path: str | Path | None = None,
    vocabulary_index_path: str | Path = "vocabularies/index.json",
    delimiter: str = ";",
    encoding: str = "utf-8-sig",
    schema_sample_limit: int = 2_000,
    keep_materialized: bool = False,
    register_crosswalks: bool = True,
    force_download: bool = False,
    force_load: bool = False,
) -> DatasetParquetDownloadResult:
    """@notice Download one CSV dataset resource and load it directly into Parquet-backed DuckDB views."""

    artifact = download_dataset_resource(
        client,
        dataset_id=dataset_id,
        output_dir=Path(output_dir),
        preferred_resource_name=preferred_resource_name,
        preferred_format="CSV",
        force_download=force_download,
    )
    manifest = artifact.manifest
    schema_location, schema_generated = _ensure_schema_artifact(
        manifest,
        schema_path=None if schema_path is None else Path(schema_path),
        schemas_dir=Path(schemas_dir),
        delimiter=delimiter,
        encoding=encoding,
        sample_limit=schema_sample_limit,
    )
    load_result = load_downloaded_resource(
        artifact.manifest_path,
        schema_path=schema_location,
        warehouse_dir=Path(warehouse_dir),
        delimiter=delimiter,
        encoding=encoding,
        force_load=force_load,
    )
    _record_period_manifest_if_supported(
        Path(load_result.duckdb_path),
        manifest_path=Path(artifact.manifest_path),
        manifest=manifest,
        load_result=load_result,
    )
    crosswalk_result = None
    if register_crosswalks:
        crosswalk_result = register_vocabulary_crosswalks(
            Path(load_result.duckdb_path),
            vocabulary_index_path=Path(vocabulary_index_path),
        )

    removed_materialized_path = False
    if not keep_materialized:
        removed_materialized_path = _prune_materialized_resource(manifest)

    return DatasetParquetDownloadResult(
        dataset_id=manifest.dataset_id,
        resource_name=manifest.resource_name,
        manifest_path=artifact.manifest_path,
        download_cache_status=manifest.cache_status,
        schema_path=str(schema_location),
        schema_generated=schema_generated,
        removed_materialized_path=removed_materialized_path,
        load_result=load_result,
        crosswalk_registration=crosswalk_result,
    )


def sync_cig_periods_to_parquet(
    client: CkanClient,
    *,
    dataset_id: str,
    output_dir: str | Path = "data/raw",
    schemas_dir: str | Path = "schemas",
    warehouse_dir: str | Path = "data/warehouse",
    periods: list[str] | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    vocabulary_index_path: str | Path = "vocabularies/index.json",
    delimiter: str = ";",
    encoding: str = "utf-8-sig",
    schema_sample_limit: int = 2_000,
    keep_materialized: bool = False,
    register_crosswalks: bool = True,
    refresh_changed: bool = False,
) -> DatasetIncrementalUpdateResult:
    """@notice Incrementally sync selected or newer monthly CIG periods into the warehouse."""

    warehouse_root = Path(warehouse_dir)
    warehouse_root.mkdir(parents=True, exist_ok=True)
    duckdb_path = warehouse_root / "anac.duckdb"

    connection = duckdb.connect(str(duckdb_path))
    try:
        _ensure_warehouse_catalog(connection)
        _backfill_cig_period_manifest(connection)
        local_manifest = _load_dataset_period_manifest(connection, dataset_type="cig")
    finally:
        connection.close()

    latest_local_period = max((record.period for record in local_manifest), default=None)
    local_by_period = {record.period: record for record in local_manifest}

    package = client.package_show(dataset_id)
    remote_resources = _list_cig_period_resources(dataset_id=dataset_id, resources=package.resources)
    if not remote_resources:
        raise ValueError(f"No monthly CIG CSV resources were found in CKAN dataset {dataset_id!r}.")

    selection_mode, requested_periods, selected_resources = _select_period_resources(
        remote_resources,
        periods=periods or [],
        period_start=period_start,
        period_end=period_end,
        latest_local_period=latest_local_period,
    )

    missing_reason = "newer_than_latest_local" if selection_mode == "forward" else "not_loaded"
    refresh_selected_periods = selection_mode in {"explicit", "range"}
    plan_by_period: dict[str, DatasetUpdatePlanItem] = {}
    for remote_resource in selected_resources:
        local_record = local_by_period.get(remote_resource.period)
        plan_by_period[remote_resource.period] = _build_period_plan_item(
            remote_resource,
            local_record,
            refresh_if_changed=refresh_selected_periods,
            missing_reason=missing_reason,
        )

    if refresh_changed and selection_mode in {"bootstrap", "forward"}:
        for remote_resource in remote_resources:
            if remote_resource.period in plan_by_period:
                continue
            local_record = local_by_period.get(remote_resource.period)
            if local_record is None or not _remote_period_changed(local_record, remote_resource.resource):
                continue
            plan_by_period[remote_resource.period] = _build_period_plan_item(
                remote_resource,
                local_record,
                refresh_if_changed=True,
                missing_reason="not_loaded",
            )

    plan = [plan_by_period[period] for period in sorted(plan_by_period)]
    applied_loads = []
    for plan_item in plan:
        if plan_item.action == "skip":
            continue
        is_refresh = plan_item.action == "refresh"
        workflow_result = download_dataset_to_parquet(
            client,
            dataset_id=dataset_id,
            output_dir=Path(output_dir),
            schemas_dir=Path(schemas_dir),
            warehouse_dir=warehouse_root,
            preferred_resource_name=plan_item.resource_name,
            vocabulary_index_path=Path(vocabulary_index_path),
            delimiter=delimiter,
            encoding=encoding,
            schema_sample_limit=schema_sample_limit,
            keep_materialized=keep_materialized,
            register_crosswalks=False,
            force_download=is_refresh,
            force_load=is_refresh,
        )
        applied_loads.append(workflow_result)

    crosswalk_result = None
    if register_crosswalks and applied_loads:
        crosswalk_result = register_vocabulary_crosswalks(
            duckdb_path,
            vocabulary_index_path=Path(vocabulary_index_path),
        )

    connection = duckdb.connect(str(duckdb_path))
    try:
        _ensure_warehouse_catalog(connection)
        current_manifest = _load_dataset_period_manifest(connection, dataset_type="cig")
    finally:
        connection.close()

    return DatasetIncrementalUpdateResult(
        dataset_type="cig",
        dataset_id=dataset_id,
        selection_mode=selection_mode,
        latest_local_period=latest_local_period,
        requested_periods=requested_periods,
        plan=plan,
        applied_loads=applied_loads,
        period_manifest=current_manifest,
        duckdb_path=str(duckdb_path),
        crosswalk_registration=crosswalk_result,
    )


def register_vocabulary_crosswalks(
    duckdb_path: str | Path,
    *,
    vocabulary_index_path: str | Path = "vocabularies/index.json",
) -> WarehouseCrosswalkRegistrationResult:
    """@notice Register vocabulary crosswalk artifacts as queryable DuckDB views."""

    database_path = Path(duckdb_path)
    if not database_path.exists():
        raise FileNotFoundError(f"DuckDB database not found: {database_path}")

    index_path = Path(vocabulary_index_path)
    if not index_path.exists():
        return WarehouseCrosswalkRegistrationResult(
            duckdb_path=str(database_path),
            vocabulary_index_path=str(index_path),
            status="missing_index",
            registered_views=[],
        )

    payload = _read_json_object(index_path)
    dataset_entries = payload.get("datasets", [])
    if not isinstance(dataset_entries, list):
        raise ValueError(f"Expected 'datasets' to be a list in {index_path}.")

    connection = duckdb.connect(str(database_path))
    try:
        _ensure_warehouse_catalog(connection)
        registered_views = []
        warehouse_root = database_path.parent
        for dataset_entry in dataset_entries:
            if not isinstance(dataset_entry, dict):
                continue
            artifact_path_value = dataset_entry.get("artifact_path")
            dataset_id_value = dataset_entry.get("dataset_id")
            if not isinstance(artifact_path_value, str) or not isinstance(dataset_id_value, str):
                continue
            registered_views.extend(
                _register_crosswalk_artifact(
                    connection,
                    warehouse_root=warehouse_root,
                    dataset_id=dataset_id_value,
                    artifact_path=Path(artifact_path_value),
                )
            )
    finally:
        connection.close()

    return WarehouseCrosswalkRegistrationResult(
        duckdb_path=str(database_path),
        vocabulary_index_path=str(index_path),
        status="registered",
        registered_views=registered_views,
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


def _list_cig_period_resources(
    *,
    dataset_id: str,
    resources: list[CkanResource],
) -> list[_RemotePeriodResource]:
    """@notice Enumerate CKAN CIG monthly CSV resources as periodized remote slices."""

    remote_resources = []
    for resource in resources:
        if resource.format.upper() != "CSV":
            continue
        match = re.fullmatch(r"cig_csv_(\d{4})_(\d{2})", resource.name.casefold())
        if match is None:
            continue
        remote_resources.append(
            _RemotePeriodResource(
                dataset_type="cig",
                dataset_id=dataset_id,
                period=f"{match.group(1)}_{match.group(2)}",
                resource=resource,
            )
        )
    return sorted(remote_resources, key=lambda item: item.period)


def _select_period_resources(
    remote_resources: list[_RemotePeriodResource],
    *,
    periods: list[str],
    period_start: str | None,
    period_end: str | None,
    latest_local_period: str | None,
) -> tuple[str, list[str], list[_RemotePeriodResource]]:
    """@notice Pick the remote periods that should be considered by the incremental planner."""

    normalized_periods = [_normalize_period(period) for period in periods]
    normalized_start = None if period_start is None else _normalize_period(period_start)
    normalized_end = None if period_end is None else _normalize_period(period_end)
    if normalized_periods and (normalized_start is not None or normalized_end is not None):
        raise ValueError("Use either explicit --period values or a --from-period/--to-period range, not both.")
    if normalized_start is not None and normalized_end is not None and normalized_start > normalized_end:
        raise ValueError("The starting period must not be greater than the ending period.")

    remote_by_period = {resource.period: resource for resource in remote_resources}
    if normalized_periods:
        missing_periods = [period for period in normalized_periods if period not in remote_by_period]
        if missing_periods:
            raise ValueError(
                "The requested CIG periods were not found in the remote CKAN dataset: "
                + ", ".join(sorted(missing_periods))
            )
        requested_periods = sorted(set(normalized_periods))
        return "explicit", requested_periods, [remote_by_period[period] for period in requested_periods]

    if normalized_start is not None or normalized_end is not None:
        selected_resources = [
            resource
            for resource in remote_resources
            if (normalized_start is None or resource.period >= normalized_start)
            and (normalized_end is None or resource.period <= normalized_end)
        ]
        if not selected_resources:
            raise ValueError("No remote CIG periods matched the requested period range.")
        requested_periods = [resource.period for resource in selected_resources]
        return "range", requested_periods, selected_resources

    if latest_local_period is None:
        requested_periods = [resource.period for resource in remote_resources]
        return "bootstrap", requested_periods, remote_resources

    selected_resources = [resource for resource in remote_resources if resource.period > latest_local_period]
    requested_periods = [resource.period for resource in selected_resources]
    return "forward", requested_periods, selected_resources


def _normalize_period(value: str) -> str:
    """@notice Normalize supported period inputs into `YYYY_MM` identifiers."""

    match = re.fullmatch(r"(\d{4})[-_](\d{1,2})", value.strip())
    if match is None:
        raise ValueError(f"Invalid period {value!r}; expected YYYY_MM.")
    month = int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"Invalid period {value!r}; month must be between 1 and 12.")
    return f"{match.group(1)}_{month:02d}"


def _build_period_plan_item(
    remote_resource: _RemotePeriodResource,
    local_record: DatasetPeriodManifestRecord | None,
    *,
    refresh_if_changed: bool,
    missing_reason: str,
) -> DatasetUpdatePlanItem:
    """@notice Build one incremental planner decision for a remote period resource."""

    resource = remote_resource.resource
    if local_record is None:
        return DatasetUpdatePlanItem(
            dataset_type=remote_resource.dataset_type,
            period=remote_resource.period,
            dataset_id=remote_resource.dataset_id,
            resource_name=resource.name,
            action="download",
            reason=missing_reason,
            remote_modified=resource.last_modified,
            remote_size=resource.size,
        )
    if refresh_if_changed and _remote_period_changed(local_record, resource):
        return DatasetUpdatePlanItem(
            dataset_type=remote_resource.dataset_type,
            period=remote_resource.period,
            dataset_id=remote_resource.dataset_id,
            resource_name=resource.name,
            action="refresh",
            reason="remote_metadata_changed",
            remote_modified=resource.last_modified,
            remote_size=resource.size,
            manifest_path=local_record.manifest_path,
            parquet_path=local_record.parquet_path,
            content_checksum=local_record.content_checksum,
        )
    return DatasetUpdatePlanItem(
        dataset_type=remote_resource.dataset_type,
        period=remote_resource.period,
        dataset_id=remote_resource.dataset_id,
        resource_name=resource.name,
        action="skip",
        reason="already_loaded",
        remote_modified=resource.last_modified,
        remote_size=resource.size,
        manifest_path=local_record.manifest_path,
        parquet_path=local_record.parquet_path,
        content_checksum=local_record.content_checksum,
    )


def _remote_period_changed(local_record: DatasetPeriodManifestRecord, remote_resource: CkanResource) -> bool:
    """@notice Detect whether remote CKAN metadata indicates a corrected period slice."""

    return any(
        (
            local_record.resource_id != (remote_resource.id or None),
            local_record.resource_url != remote_resource.url,
            local_record.remote_modified != remote_resource.last_modified,
            local_record.remote_size != remote_resource.size,
        )
    )


def _compute_manifest_content_checksum(manifest: DownloadManifest) -> str:
    """@notice Compute a deterministic checksum for the downloaded period content."""

    with _resolve_source_path(manifest) as source_path:
        return _sha256_for_path(source_path)


def _record_period_manifest_if_supported(
    duckdb_path: Path,
    *,
    manifest_path: Path,
    manifest: DownloadManifest,
    load_result: WarehouseLoadResult,
) -> None:
    """@notice Record the warehouse period manifest entry when the resource belongs to a supported family."""

    period_key = _dataset_period_key(manifest)
    if period_key is None:
        return
    dataset_type, period = period_key
    connection = duckdb.connect(str(duckdb_path))
    try:
        _ensure_warehouse_catalog(connection)
        _upsert_dataset_period_manifest(
            connection,
            dataset_type=dataset_type,
            period=period,
            manifest=manifest,
            manifest_path=manifest_path,
            load_result=load_result,
            content_checksum=_compute_manifest_content_checksum(manifest),
        )
    finally:
        connection.close()


def _sha256_for_path(path: Path) -> str:
    """@notice Hash a file with SHA-256 without reading it fully into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _dataset_period_key(manifest: DownloadManifest) -> tuple[str, str] | None:
    """@notice Derive the supported dataset family and period identifier from a manifest."""

    resource_name = manifest.resource_name.casefold()
    match = re.fullmatch(r"cig_csv_(\d{4})_(\d{2})", resource_name)
    if match is None:
        return None
    return "cig", f"{match.group(1)}_{match.group(2)}"


def _load_download_manifest(manifest_path: Path) -> DownloadManifest:
    """@notice Read one manifest JSON file into the shared manifest dataclass."""

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return DownloadManifest.from_dict(payload)


def _load_cached_load_result(
    connection: duckdb.DuckDBPyConnection,
    *,
    manifest_path: Path,
    manifest: DownloadManifest,
    schema_path: Path | None,
    target_plan: _WarehouseTargetPlan,
    warehouse_root: Path,
) -> WarehouseLoadResult | None:
    """@notice Reuse a prior load when the same manifest already owns a valid Parquet slice."""

    row = connection.execute(
        """
        SELECT parquet_path, row_count, schema_path
        FROM loaded_resources
        WHERE manifest_path = ?
        """,
        [str(manifest_path)],
    ).fetchone()
    if row is None:
        return None

    parquet_path_value, row_count_value, stored_schema_path = row
    current_schema_path = None if schema_path is None else str(schema_path)
    if stored_schema_path != current_schema_path:
        return None
    if str(parquet_path_value) != str(target_plan.parquet_path):
        return None
    if not Path(str(parquet_path_value)).exists():
        return None

    registered_files, view_sql = _refresh_registered_view(
        connection,
        view_name=target_plan.view_name,
        parquet_root=target_plan.parquet_root,
    )
    _record_registered_view(
        connection,
        view_name=target_plan.view_name,
        table_name=target_plan.table_name,
        parquet_root=target_plan.parquet_root,
        registered_files=registered_files,
        view_sql=view_sql,
    )
    return WarehouseLoadResult(
        dataset_id=manifest.dataset_id,
        resource_name=manifest.resource_name,
        table_name=target_plan.table_name,
        view_name=target_plan.view_name,
        manifest_path=str(manifest_path),
        schema_path=current_schema_path,
        source_path=manifest.materialized_path,
        warehouse_dir=str(warehouse_root),
        duckdb_path=str(warehouse_root / "anac.duckdb"),
        parquet_root=str(target_plan.parquet_root),
        parquet_path=str(target_plan.parquet_path),
        row_count=int(row_count_value),
        partition_values=target_plan.partition_values,
        registered_parquet_files=registered_files,
        load_status="cache_hit",
        view_sql=view_sql,
    )


def _ensure_schema_artifact(
    manifest: DownloadManifest,
    *,
    schema_path: Path | None,
    schemas_dir: Path,
    delimiter: str,
    encoding: str,
    sample_limit: int,
) -> tuple[Path, bool]:
    """@notice Reuse or create the schema artifact used for typed warehouse loading."""

    target_schema_path = schema_path or _default_schema_path(
        dataset_id=manifest.dataset_id,
        resource_name=manifest.resource_name,
        schemas_dir=schemas_dir,
    )
    if target_schema_path.exists():
        return target_schema_path, False

    with _resolve_source_path(manifest) as source_path:
        schema_mapping = map_csv_schema(
            source_path,
            delimiter=delimiter,
            encoding=encoding,
            sample_limit=sample_limit,
        )
    target_schema_path.parent.mkdir(parents=True, exist_ok=True)
    target_schema_path.write_text(
        json.dumps(schema_mapping.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target_schema_path, True


def _default_schema_path(*, dataset_id: str, resource_name: str, schemas_dir: Path) -> Path:
    """@notice Derive the schema artifact path used by the direct-to-Parquet workflow."""

    cig_match = re.fullmatch(r"cig_csv_(\d{4})_(\d{2})", resource_name.casefold())
    if cig_match is not None:
        return schemas_dir / f"cig_{cig_match.group(1)}_{cig_match.group(2)}.schema.json"
    if resource_name.casefold().endswith("_csv"):
        return schemas_dir / f"{resource_name[:-4]}.schema.json"
    return schemas_dir / f"{dataset_id}.schema.json"


def _resolve_source_path(manifest: DownloadManifest):
    """@notice Yield a usable CSV path, extracting temporarily from the archive if needed."""

    source_path = Path(manifest.materialized_path)
    if source_path.exists():
        return _existing_path_context(source_path)
    if manifest.archive_path is None:
        raise FileNotFoundError(f"Materialized source file not found: {source_path}")
    return _temporary_archive_materialization_context(manifest)


def _existing_path_context(path: Path):
    """@notice Wrap an existing path in a lightweight context manager interface."""

    class _ExistingPathContext:
        def __enter__(self) -> Path:
            return path

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    return _ExistingPathContext()


def _temporary_archive_materialization_context(manifest: DownloadManifest):
    """@notice Extract the expected archive member into a temporary directory for loading."""

    archive_path = Path(manifest.archive_path or "")
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive path not found for manifest-backed load: {archive_path}")

    class _TemporaryArchiveContext:
        def __init__(self) -> None:
            self._temp_dir = TemporaryDirectory()
            self._resolved_path: Path | None = None

        def __enter__(self) -> Path:
            temp_dir_path = Path(self._temp_dir.name)
            self._resolved_path = _extract_first_archive_member(
                archive_path,
                temp_dir_path,
                manifest.materialized_kind,
            )
            return self._resolved_path

        def __exit__(self, exc_type, exc, tb) -> None:
            self._temp_dir.cleanup()
            return None

    return _TemporaryArchiveContext()


def _extract_first_archive_member(archive_path: Path, output_dir: Path, expected_extension: str) -> Path:
    """@notice Extract the first archive member matching the expected extension."""

    output_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive_path) as archive:
        matching_members = [
            member_name
            for member_name in archive.namelist()
            if member_name.lower().endswith(f".{expected_extension.lower()}")
        ]
        if not matching_members:
            raise ValueError(f"No .{expected_extension} members were found in {archive_path}.")
        member_name = matching_members[0]
        extracted_path = output_dir / Path(member_name).name
        with archive.open(member_name) as source_handle, extracted_path.open("wb") as target_handle:
            target_handle.write(source_handle.read())
    return extracted_path


def _prune_materialized_resource(manifest: DownloadManifest) -> bool:
    """@notice Remove the extracted working file when the archive remains available."""

    if manifest.archive_path is None:
        return False
    materialized_path = Path(manifest.materialized_path)
    if not materialized_path.exists():
        return False
    materialized_path.unlink()
    return True


def _read_json_object(path: Path) -> dict[str, object]:
    """@notice Read a JSON object from disk and fail loudly on invalid shapes."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object at {path}.")
    return payload


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
    return {
        field_name: _normalized_target_type(field_name, inferred.get(field_name, "text"))
        for field_name in field_names
    }


def _normalized_target_type(field_name: str, target_type: str) -> str:
    """@notice Override schema hints for known identifier columns that must stay textual."""

    normalized_name = field_name.casefold()
    if normalized_name in _TEXT_IDENTIFIER_COLUMNS or normalized_name.startswith("cf_"):
        return "text"
    return target_type


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


def _register_crosswalk_artifact(
    connection: duckdb.DuckDBPyConnection,
    *,
    warehouse_root: Path,
    dataset_id: str,
    artifact_path: Path,
) -> list[WarehouseCrosswalkView]:
    """@notice Materialize one vocabulary artifact's tables into Parquet-backed DuckDB views."""

    if not artifact_path.exists():
        return []

    payload = _read_json_object(artifact_path)
    tables = payload.get("tables", [])
    if not isinstance(tables, list):
        raise ValueError(f"Expected 'tables' to be a list in {artifact_path}.")

    registered_views = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = table.get("name")
        if not isinstance(table_name, str):
            continue
        rows = _build_crosswalk_rows(dataset_id, table)
        if not rows:
            continue
        parquet_root = warehouse_root / "parquet" / table_name / f"dataset={_normalize_identifier(dataset_id)}"
        parquet_path = parquet_root / "artifact.parquet"
        if parquet_path.exists():
            parquet_path.unlink()
        _write_crosswalk_rows_to_parquet(
            connection,
            parquet_path=parquet_path,
            rows=rows,
        )
        registered_files, view_sql = _refresh_registered_view(
            connection,
            view_name=table_name,
            parquet_root=warehouse_root / "parquet" / table_name,
        )
        _record_registered_view(
            connection,
            view_name=table_name,
            table_name=table_name,
            parquet_root=warehouse_root / "parquet" / table_name,
            registered_files=registered_files,
            view_sql=view_sql,
        )
        registered_views.append(
            WarehouseCrosswalkView(
                dataset_id=dataset_id,
                table_name=table_name,
                view_name=table_name,
                parquet_path=str(parquet_path),
                row_count=len(rows),
            )
        )

    return registered_views


def _build_crosswalk_rows(dataset_id: str, table_payload: dict[str, object]) -> list[dict[str, object]]:
    """@notice Flatten one normalized vocabulary table into JSON-friendly row dictionaries."""

    source_columns = [
        str(column_name)
        for column_name in table_payload.get("source_columns", [])
        if isinstance(column_name, str)
    ]
    extra_columns = [
        str(column_name)
        for column_name in table_payload.get("extra_columns", [])
        if isinstance(column_name, str)
    ]
    rows = []
    for entry in table_payload.get("entries", []):
        if not isinstance(entry, dict):
            continue
        raw_payload = entry.get("raw", {})
        attributes_payload = entry.get("attributes", {})
        if not isinstance(raw_payload, dict):
            raw_payload = {}
        if not isinstance(attributes_payload, dict):
            attributes_payload = {}
        row = {
            "dataset_id": dataset_id,
            "table_name": str(table_payload.get("name", "")),
            "code": None if entry.get("code") is None else str(entry.get("code")),
            "label": None if entry.get("label") is None else str(entry.get("label")),
            "usage_count": int(entry.get("usage_count", 0)),
        }
        for column_name in source_columns:
            value = raw_payload.get(column_name)
            row[column_name] = None if value is None else str(value)
        for column_name in extra_columns:
            value = attributes_payload.get(column_name, raw_payload.get(column_name))
            row[column_name] = None if value is None else str(value)
        rows.append(row)
    return rows


def _write_crosswalk_rows_to_parquet(
    connection: duckdb.DuckDBPyConnection,
    *,
    parquet_path: Path,
    rows: list[dict[str, object]],
) -> None:
    """@notice Write flattened crosswalk rows into a Parquet file through DuckDB."""

    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    column_names = list(rows[0])
    column_definitions = ", ".join(
        f"{_quoted_identifier(column_name)} {'BIGINT' if column_name == 'usage_count' else 'VARCHAR'}"
        for column_name in column_names
    )
    placeholder_sql = "(" + ", ".join("?" for _ in column_names) + ")"
    connection.execute("DROP TABLE IF EXISTS temp_crosswalk_rows")
    connection.execute(f"CREATE TEMP TABLE temp_crosswalk_rows ({column_definitions})")
    connection.executemany(
        f"INSERT INTO temp_crosswalk_rows VALUES {placeholder_sql}",
        [[row[column_name] for column_name in column_names] for row in rows],
    )
    connection.execute(
        f"COPY temp_crosswalk_rows TO {_sql_literal(str(parquet_path))} (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    connection.execute("DROP TABLE temp_crosswalk_rows")


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


def _load_dataset_period_manifest(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_type: str,
) -> list[DatasetPeriodManifestRecord]:
    """@notice Read the warehouse-level period manifest catalog for one dataset family."""

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
        ORDER BY period
        """,
        [dataset_type],
    ).fetchall()
    return [
        DatasetPeriodManifestRecord(
            dataset_type=str(dataset_type_value),
            period=str(period_value),
            dataset_id=str(dataset_id_value),
            resource_name=str(resource_name_value),
            manifest_path=str(manifest_path_value),
            parquet_path=str(parquet_path_value),
            resource_id=None if resource_id_value in (None, "") else str(resource_id_value),
            resource_url=None if resource_url_value in (None, "") else str(resource_url_value),
            remote_modified=None if remote_modified_value in (None, "") else str(remote_modified_value),
            remote_size=None if remote_size_value is None else int(remote_size_value),
            content_checksum=None if content_checksum_value in (None, "") else str(content_checksum_value),
            row_count=None if row_count_value is None else int(row_count_value),
            imported_at=str(imported_at_value),
            refreshed_at=str(refreshed_at_value),
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


def _backfill_cig_period_manifest(connection: duckdb.DuckDBPyConnection) -> None:
    """@notice Populate the period manifest catalog from existing loaded CIG resources when needed."""

    rows = connection.execute(
        """
        SELECT manifest_path, parquet_path, row_count, loaded_at
        FROM loaded_resources
        WHERE table_name = 'cig'
        ORDER BY loaded_at
        """
    ).fetchall()
    for manifest_path_value, parquet_path_value, row_count_value, loaded_at_value in rows:
        manifest_path = Path(str(manifest_path_value))
        if not manifest_path.exists():
            continue
        manifest = _load_download_manifest(manifest_path)
        period_key = _dataset_period_key(manifest)
        if period_key is None:
            continue
        dataset_type, period = period_key
        existing = connection.execute(
            """
            SELECT 1
            FROM dataset_period_manifest
            WHERE dataset_type = ? AND period = ?
            """,
            [dataset_type, period],
        ).fetchone()
        if existing is not None:
            continue
        _upsert_dataset_period_manifest(
            connection,
            dataset_type=dataset_type,
            period=period,
            manifest=manifest,
            manifest_path=manifest_path,
            load_result=WarehouseLoadResult(
                dataset_id=manifest.dataset_id,
                resource_name=manifest.resource_name,
                table_name="cig",
                view_name="cig",
                manifest_path=str(manifest_path),
                schema_path=None,
                source_path=manifest.materialized_path,
                warehouse_dir=str(Path(str(parquet_path_value)).parents[4]),
                duckdb_path="",
                parquet_root=str(Path(str(parquet_path_value)).parents[2]),
                parquet_path=str(parquet_path_value),
                row_count=int(row_count_value),
            ),
            content_checksum=_safe_compute_manifest_content_checksum(manifest),
            imported_at=str(loaded_at_value),
            refreshed_at=str(loaded_at_value),
        )


def _upsert_dataset_period_manifest(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_type: str,
    period: str,
    manifest: DownloadManifest,
    manifest_path: Path,
    load_result: WarehouseLoadResult,
    content_checksum: str | None,
    imported_at: str | None = None,
    refreshed_at: str | None = None,
) -> None:
    """@notice Upsert one warehouse period catalog row after a successful load."""

    current_refresh_time = refreshed_at or datetime.now(timezone.utc).isoformat()
    existing = connection.execute(
        """
        SELECT imported_at
        FROM dataset_period_manifest
        WHERE dataset_type = ? AND period = ?
        """,
        [dataset_type, period],
    ).fetchone()
    current_import_time = imported_at or (current_refresh_time if existing is None else str(existing[0]))
    connection.execute(
        """
        DELETE FROM dataset_period_manifest
        WHERE dataset_type = ? AND period = ?
        """,
        [dataset_type, period],
    )
    connection.execute(
        """
        INSERT INTO dataset_period_manifest (
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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            dataset_type,
            period,
            manifest.dataset_id,
            manifest.resource_name,
            str(manifest_path),
            load_result.parquet_path,
            manifest.resource_id,
            manifest.resource_url,
            manifest.source_last_modified,
            manifest.source_size,
            content_checksum,
            load_result.row_count,
            current_import_time,
            current_refresh_time,
        ],
    )


def _safe_compute_manifest_content_checksum(manifest: DownloadManifest) -> str | None:
    """@notice Compute a checksum when the raw source is still accessible, otherwise return null."""

    try:
        return _compute_manifest_content_checksum(manifest)
    except (FileNotFoundError, ValueError):
        return None


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
