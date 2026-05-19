"""@notice Typed metadata-row models and pure-Python loaders for the discoverability layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Iterable, Sequence

import duckdb

from anac_explorator.comparison import load_schema_mapping
from anac_explorator.errors import CliCommandError
from anac_explorator.models import (
    DataDictionaryArtifact,
    DatasetPeriodManifestRecord,
    DownloadManifest,
    JoinContract,
    SchemaMapping,
    WarehouseCrosswalkRegistrationResult,
    WarehouseLoadResult,
)
from anac_explorator.selection import period_to_slice_identifier

if TYPE_CHECKING:
    from anac_explorator.catalog import DatasetFamilyRegistry

METADATA_VIEW_NAMES = (
    "anac_datasets",
    "anac_dataset_resources",
    "anac_partitions",
    "anac_registered_views",
    "anac_loaded_resources",
    "anac_schema_columns",
    "anac_dictionary_fields",
    "anac_crosswalks",
    "anac_update_status",
)

_METADATA_VIEW_SCHEMAS: dict[str, list[tuple[str, str]]] = {
    "anac_datasets": [
        ("dataset", "VARCHAR"),
        ("title", "VARCHAR"),
        ("category", "VARCHAR"),
        ("description", "VARCHAR"),
        ("coverage_kind", "VARCHAR"),
        ("available_source_formats", "VARCHAR"),
        ("remote_dataset_ids", "VARCHAR"),
        ("remote_first_year", "INTEGER"),
        ("remote_last_year", "INTEGER"),
        ("local_slice_count", "BIGINT"),
        ("local_first_slice", "VARCHAR"),
        ("local_last_slice", "VARCHAR"),
        ("query_view_name", "VARCHAR"),
        ("update_supported", "BOOLEAN"),
        ("dictionary_available", "BOOLEAN"),
    ],
    "anac_dataset_resources": [
        ("dataset", "VARCHAR"),
        ("dataset_id", "VARCHAR"),
        ("resource_name", "VARCHAR"),
        ("source_format", "VARCHAR"),
        ("slice", "VARCHAR"),
        ("remote_size_bytes", "BIGINT"),
        ("remote_modified", "VARCHAR"),
        ("manifest_path", "VARCHAR"),
        ("materialized_path", "VARCHAR"),
        ("parquet_path", "VARCHAR"),
        ("local_status", "VARCHAR"),
        ("row_count", "BIGINT"),
    ],
    "anac_partitions": [
        ("dataset", "VARCHAR"),
        ("slice", "VARCHAR"),
        ("year", "INTEGER"),
        ("month", "INTEGER"),
        ("dataset_id", "VARCHAR"),
        ("resource_name", "VARCHAR"),
        ("manifest_path", "VARCHAR"),
        ("parquet_path", "VARCHAR"),
        ("row_count", "BIGINT"),
        ("remote_size_bytes", "BIGINT"),
        ("remote_modified", "VARCHAR"),
        ("content_checksum", "VARCHAR"),
        ("imported_at", "VARCHAR"),
        ("refreshed_at", "VARCHAR"),
    ],
    "anac_registered_views": [
        ("view_name", "VARCHAR"),
        ("table_name", "VARCHAR"),
        ("parquet_root", "VARCHAR"),
        ("parquet_file_count", "BIGINT"),
        ("updated_at", "VARCHAR"),
        ("view_sql", "VARCHAR"),
    ],
    "anac_loaded_resources": [
        ("manifest_path", "VARCHAR"),
        ("dataset_id", "VARCHAR"),
        ("dataset", "VARCHAR"),
        ("resource_name", "VARCHAR"),
        ("view_name", "VARCHAR"),
        ("source_path", "VARCHAR"),
        ("schema_path", "VARCHAR"),
        ("parquet_path", "VARCHAR"),
        ("row_count", "BIGINT"),
        ("partition_values_json", "VARCHAR"),
        ("loaded_at", "VARCHAR"),
    ],
    "anac_schema_columns": [
        ("dataset", "VARCHAR"),
        ("target", "VARCHAR"),
        ("source_kind", "VARCHAR"),
        ("ordinal_position", "BIGINT"),
        ("column_name", "VARCHAR"),
        ("inferred_type", "VARCHAR"),
        ("duckdb_type", "VARCHAR"),
        ("nullable", "BOOLEAN"),
        ("description", "VARCHAR"),
        ("semantic_type", "VARCHAR"),
        ("paired_field", "VARCHAR"),
        ("code_meaning_status", "VARCHAR"),
        ("vocabulary_dataset_id", "VARCHAR"),
        ("vocabulary_table", "VARCHAR"),
    ],
    "anac_dictionary_fields": [
        ("dataset", "VARCHAR"),
        ("field_name", "VARCHAR"),
        ("section", "VARCHAR"),
        ("description", "VARCHAR"),
        ("semantic_type", "VARCHAR"),
        ("value_pattern", "VARCHAR"),
        ("inferred_type", "VARCHAR"),
        ("nullable", "BOOLEAN"),
        ("paired_field", "VARCHAR"),
        ("code_meaning_status", "VARCHAR"),
        ("external_vocabulary_status", "VARCHAR"),
        ("vocabulary_dataset_id", "VARCHAR"),
        ("vocabulary_table", "VARCHAR"),
        ("join_key", "VARCHAR"),
        ("label_field", "VARCHAR"),
    ],
    "anac_crosswalks": [
        ("dataset_id", "VARCHAR"),
        ("view_name", "VARCHAR"),
        ("table_name", "VARCHAR"),
        ("parquet_path", "VARCHAR"),
        ("row_count", "BIGINT"),
    ],
    "anac_update_status": [
        ("dataset", "VARCHAR"),
        ("update_supported", "BOOLEAN"),
        ("local_slice_count", "BIGINT"),
        ("latest_local_slice", "VARCHAR"),
        ("latest_imported_at", "VARCHAR"),
        ("latest_refreshed_at", "VARCHAR"),
    ],
}


@dataclass(slots=True)
class VocabularyIndexDatasetEntry:
    """@notice Describe one dataset entry from the local vocabulary index artifact."""

    dataset_id: str
    resource_name: str
    csv_path: str
    schema_path: str
    artifact_path: str
    table_count: int

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the dataset entry into a serializable dictionary."""

        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "VocabularyIndexDatasetEntry":
        """@notice Rebuild one vocabulary-index dataset entry from JSON."""

        return cls(
            dataset_id=str(payload["dataset_id"]),
            resource_name=str(payload.get("resource_name", "")),
            csv_path=str(payload.get("csv_path", "")),
            schema_path=str(payload.get("schema_path", "")),
            artifact_path=str(payload["artifact_path"]),
            table_count=int(payload.get("table_count", 0)),
        )


@dataclass(slots=True)
class VocabularyIndexFieldLink:
    """@notice Describe one current field-link entry from the vocabulary index artifact."""

    scope: str
    dataset_id: str
    table_name: str
    source_code_field: str | None = None
    source_label_field: str | None = None
    target_code_field: str | None = None
    target_label_field: str | None = None
    code_meaning_status: str | None = None
    external_vocabulary_status: str | None = None
    resolved_fields: list[str] = field(default_factory=list)
    join_contract: JoinContract | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the field-link entry into a serializable dictionary."""

        payload = asdict(self)
        payload["join_contract"] = None if self.join_contract is None else self.join_contract.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "VocabularyIndexFieldLink":
        """@notice Rebuild one vocabulary-index field link from JSON."""

        raw_join_contract = payload.get("join_contract")
        return cls(
            scope=str(payload["scope"]),
            dataset_id=str(payload["dataset_id"]),
            table_name=str(payload["table_name"]),
            source_code_field=None
            if payload.get("source_code_field") in (None, "")
            else str(payload["source_code_field"]),
            source_label_field=None
            if payload.get("source_label_field") in (None, "")
            else str(payload["source_label_field"]),
            target_code_field=None
            if payload.get("target_code_field") in (None, "")
            else str(payload["target_code_field"]),
            target_label_field=None
            if payload.get("target_label_field") in (None, "")
            else str(payload["target_label_field"]),
            code_meaning_status=None
            if payload.get("code_meaning_status") in (None, "")
            else str(payload["code_meaning_status"]),
            external_vocabulary_status=None
            if payload.get("external_vocabulary_status") in (None, "")
            else str(payload["external_vocabulary_status"]),
            resolved_fields=[str(value) for value in payload.get("resolved_fields", [])],
            join_contract=None
            if not isinstance(raw_join_contract, dict)
            else JoinContract.from_dict(raw_join_contract),
            notes=str(payload.get("notes", "")),
        )


@dataclass(slots=True)
class VocabularyIndexArtifact:
    """@notice Capture the typed contents of the local vocabulary index artifact."""

    dataset_count: int
    datasets: list[VocabularyIndexDatasetEntry] = field(default_factory=list)
    code_meaning_status_taxonomy: dict[str, str] = field(default_factory=dict)
    field_links: list[VocabularyIndexFieldLink] = field(default_factory=list)
    current_cig_schema_gaps: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the vocabulary index into a serializable dictionary."""

        return {
            "dataset_count": self.dataset_count,
            "datasets": [dataset.to_dict() for dataset in self.datasets],
            "code_meaning_status_taxonomy": self.code_meaning_status_taxonomy,
            "field_links": [field_link.to_dict() for field_link in self.field_links],
            "current_cig_schema_gaps": self.current_cig_schema_gaps,
        }


@dataclass(slots=True)
class AnacDatasetsRow:
    """@notice Typed row for the `anac_datasets` metadata view."""

    dataset: str
    title: str
    category: str
    description: str
    coverage_kind: str
    available_source_formats: list[str]
    remote_dataset_ids: list[str]
    remote_first_year: int | None
    remote_last_year: int | None
    local_slice_count: int
    local_first_slice: str | None
    local_last_slice: str | None
    query_view_name: str | None
    update_supported: bool
    dictionary_available: bool

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the row into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class AnacDatasetResourcesRow:
    """@notice Typed row for the `anac_dataset_resources` metadata view."""

    dataset: str
    dataset_id: str
    resource_name: str
    source_format: str
    slice: str | None
    remote_size_bytes: int | None
    remote_modified: str | None
    manifest_path: str | None
    materialized_path: str | None
    parquet_path: str | None
    local_status: str
    row_count: int | None

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the row into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class AnacPartitionsRow:
    """@notice Typed row for the `anac_partitions` metadata view."""

    dataset: str
    slice: str
    year: int
    month: int
    dataset_id: str
    resource_name: str
    manifest_path: str
    parquet_path: str
    row_count: int | None
    remote_size_bytes: int | None
    remote_modified: str | None
    content_checksum: str | None
    imported_at: str | None
    refreshed_at: str | None

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the row into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class AnacRegisteredViewsRow:
    """@notice Typed row for the `anac_registered_views` metadata view."""

    view_name: str
    table_name: str
    parquet_root: str
    parquet_file_count: int
    updated_at: str | None
    view_sql: str

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the row into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class AnacLoadedResourcesRow:
    """@notice Typed row for the `anac_loaded_resources` metadata view."""

    manifest_path: str
    dataset_id: str
    dataset: str
    resource_name: str
    view_name: str
    source_path: str
    schema_path: str | None
    parquet_path: str
    row_count: int
    partition_values_json: str
    loaded_at: str | None

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the row into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class AnacSchemaColumnsRow:
    """@notice Typed row for the `anac_schema_columns` metadata view."""

    dataset: str
    target: str
    source_kind: str
    ordinal_position: int
    column_name: str
    inferred_type: str | None
    duckdb_type: str | None
    nullable: bool | None
    description: str | None
    semantic_type: str | None
    paired_field: str | None
    code_meaning_status: str | None
    vocabulary_dataset_id: str | None
    vocabulary_table: str | None

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the row into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class AnacDictionaryFieldsRow:
    """@notice Typed row for the `anac_dictionary_fields` metadata view."""

    dataset: str
    field_name: str
    section: str
    description: str
    semantic_type: str
    value_pattern: str
    inferred_type: str
    nullable: bool
    paired_field: str | None
    code_meaning_status: str
    external_vocabulary_status: str
    vocabulary_dataset_id: str | None
    vocabulary_table: str | None
    join_key: str | None
    label_field: str | None

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the row into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class AnacCrosswalksRow:
    """@notice Typed row for the `anac_crosswalks` metadata view."""

    dataset_id: str
    view_name: str
    table_name: str
    parquet_path: str
    row_count: int

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the row into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class AnacUpdateStatusRow:
    """@notice Typed row for the `anac_update_status` metadata view."""

    dataset: str
    update_supported: bool
    local_slice_count: int
    latest_local_slice: str | None
    latest_imported_at: str | None
    latest_refreshed_at: str | None

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the row into a serializable dictionary."""

        return asdict(self)


def load_download_manifest(path: str | Path) -> DownloadManifest:
    """@notice Load one manifest-backed raw resource artifact from disk."""

    payload = _load_json_object(path)
    return DownloadManifest.from_dict(payload)


def load_data_dictionary_artifact(path: str | Path) -> DataDictionaryArtifact:
    """@notice Load one serialized data-dictionary artifact from disk."""

    payload = _load_json_object(path)
    return DataDictionaryArtifact.from_dict(payload)


def load_vocabulary_index(path: str | Path) -> VocabularyIndexArtifact:
    """@notice Load the typed vocabulary index artifact from disk."""

    payload = _load_json_object(path)
    raw_datasets = payload.get("datasets", [])
    raw_field_links = payload.get("field_links", [])
    return VocabularyIndexArtifact(
        dataset_count=int(payload.get("dataset_count", len(raw_datasets))),
        datasets=[VocabularyIndexDatasetEntry.from_dict(entry) for entry in raw_datasets if isinstance(entry, dict)],
        code_meaning_status_taxonomy={
            str(key): str(value) for key, value in payload.get("code_meaning_status_taxonomy", {}).items()
        },
        field_links=[VocabularyIndexFieldLink.from_dict(entry) for entry in raw_field_links if isinstance(entry, dict)],
        current_cig_schema_gaps=[
            gap_entry for gap_entry in payload.get("current_cig_schema_gaps", []) if isinstance(gap_entry, dict)
        ],
    )


def build_datasets_rows(
    *,
    registry: "DatasetFamilyRegistry | None" = None,
    period_manifest: Sequence[DatasetPeriodManifestRecord] = (),
    dictionary_artifacts: Sequence[DataDictionaryArtifact] = (),
) -> list[AnacDatasetsRow]:
    """@notice Build the logical dataset-family inventory rows from registry plus local aggregates."""

    registry = _default_registry() if registry is None else registry
    partition_rows = build_partitions_rows(period_manifest, registry=registry)
    partition_groups = _group_partition_rows(partition_rows)
    dictionary_datasets = {
        _resolve_dataset_family_id(artifact.dataset_id, registry=registry) for artifact in dictionary_artifacts
    }
    rows: list[AnacDatasetsRow] = []
    for family in registry.list_families():
        family_partitions = partition_groups.get(family.dataset, [])
        local_slices = sorted(row.slice for row in family_partitions)
        rows.append(
            AnacDatasetsRow(
                dataset=family.dataset,
                title=family.title,
                category=family.category,
                description=family.description,
                coverage_kind=family.coverage_kind,
                available_source_formats=list(family.available_source_formats),
                remote_dataset_ids=family.remote_dataset_ids,
                remote_first_year=family.remote_first_year,
                remote_last_year=family.remote_last_year,
                local_slice_count=len(local_slices),
                local_first_slice=None if not local_slices else local_slices[0],
                local_last_slice=None if not local_slices else local_slices[-1],
                query_view_name=family.query_view_name,
                update_supported=family.update_supported,
                dictionary_available=family.dictionary_available or family.dataset in dictionary_datasets,
            )
        )
    return rows


def build_dataset_resource_rows(
    manifests: Sequence[DownloadManifest],
    *,
    load_results: Sequence[WarehouseLoadResult] = (),
    registry: "DatasetFamilyRegistry | None" = None,
) -> list[AnacDatasetResourcesRow]:
    """@notice Build local dataset-resource rows from manifests plus optional warehouse loads."""

    registry = _default_registry() if registry is None else registry
    load_results_by_manifest = {result.manifest_path: result for result in load_results}
    seen_manifest_paths: set[str] = set()
    rows: list[AnacDatasetResourcesRow] = []

    for manifest in manifests:
        manifest_path = _infer_manifest_path(manifest)
        matching_load = load_results_by_manifest.get(manifest_path)
        rows.append(
            AnacDatasetResourcesRow(
                dataset=_resolve_dataset_family_id(manifest.dataset_id, registry=registry),
                dataset_id=manifest.dataset_id,
                resource_name=manifest.resource_name,
                source_format=_normalize_source_format(manifest.resource_format, manifest.resource_name, manifest.materialized_path),
                slice=_extract_slice(dataset_id=manifest.dataset_id, resource_name=manifest.resource_name, registry=registry),
                remote_size_bytes=manifest.source_size,
                remote_modified=manifest.source_last_modified,
                manifest_path=manifest_path,
                materialized_path=manifest.materialized_path,
                parquet_path=None if matching_load is None else matching_load.parquet_path,
                local_status="loaded" if matching_load is not None else "raw",
                row_count=None if matching_load is None else matching_load.row_count,
            )
        )
        seen_manifest_paths.add(manifest_path)

    for load_result in load_results:
        if load_result.manifest_path in seen_manifest_paths:
            continue
        rows.append(
            AnacDatasetResourcesRow(
                dataset=_resolve_dataset_family_id(load_result.dataset_id, registry=registry, fallback=load_result.table_name),
                dataset_id=load_result.dataset_id,
                resource_name=load_result.resource_name,
                source_format=_normalize_source_format(None, load_result.resource_name, load_result.source_path),
                slice=_extract_slice(dataset_id=load_result.dataset_id, resource_name=load_result.resource_name, registry=registry),
                remote_size_bytes=None,
                remote_modified=None,
                manifest_path=load_result.manifest_path,
                materialized_path=load_result.source_path,
                parquet_path=load_result.parquet_path,
                local_status="loaded",
                row_count=load_result.row_count,
            )
        )

    return sorted(rows, key=lambda row: (row.dataset, row.dataset_id, row.resource_name))


def build_partitions_rows(
    period_manifest: Sequence[DatasetPeriodManifestRecord],
    *,
    registry: "DatasetFamilyRegistry | None" = None,
) -> list[AnacPartitionsRow]:
    """@notice Build local partition rows from the current dataset-period manifest records."""

    registry = _default_registry() if registry is None else registry
    rows: list[AnacPartitionsRow] = []
    for record in period_manifest:
        slice_value = period_to_slice_identifier(record.period)
        year_text, month_text = slice_value.split("-", 1)
        rows.append(
            AnacPartitionsRow(
                dataset=_resolve_dataset_family_id(record.dataset_id, registry=registry, fallback=record.dataset_type),
                slice=slice_value,
                year=int(year_text),
                month=int(month_text),
                dataset_id=record.dataset_id,
                resource_name=record.resource_name,
                manifest_path=record.manifest_path,
                parquet_path=record.parquet_path,
                row_count=record.row_count,
                remote_size_bytes=record.remote_size,
                remote_modified=record.remote_modified,
                content_checksum=record.content_checksum,
                imported_at=record.imported_at,
                refreshed_at=record.refreshed_at,
            )
        )
    return sorted(rows, key=lambda row: (row.dataset, row.slice))


def build_registered_views_rows(
    load_results: Sequence[WarehouseLoadResult],
    *,
    updated_at_by_view: dict[str, str] | None = None,
) -> list[AnacRegisteredViewsRow]:
    """@notice Build registered-view rows from warehouse load results without querying DuckDB."""

    rows_by_view: dict[str, AnacRegisteredViewsRow] = {}
    updated_lookup = {} if updated_at_by_view is None else updated_at_by_view
    for load_result in load_results:
        rows_by_view[load_result.view_name] = AnacRegisteredViewsRow(
            view_name=load_result.view_name,
            table_name=load_result.table_name,
            parquet_root=load_result.parquet_root,
            parquet_file_count=max(load_result.registered_parquet_files, 1),
            updated_at=updated_lookup.get(load_result.view_name),
            view_sql=load_result.view_sql,
        )
    return sorted(rows_by_view.values(), key=lambda row: row.view_name)


def build_loaded_resources_rows(
    load_results: Sequence[WarehouseLoadResult],
    *,
    registry: "DatasetFamilyRegistry | None" = None,
    manifests: Sequence[DownloadManifest] = (),
) -> list[AnacLoadedResourcesRow]:
    """@notice Build loaded-resource rows from typed warehouse load results."""

    registry = _default_registry() if registry is None else registry
    manifest_loaded_at = {_infer_manifest_path(manifest): manifest.downloaded_at for manifest in manifests}
    rows = [
        AnacLoadedResourcesRow(
            manifest_path=load_result.manifest_path,
            dataset_id=load_result.dataset_id,
            dataset=_resolve_dataset_family_id(load_result.dataset_id, registry=registry, fallback=load_result.table_name),
            resource_name=load_result.resource_name,
            view_name=load_result.view_name,
            source_path=load_result.source_path,
            schema_path=load_result.schema_path,
            parquet_path=load_result.parquet_path,
            row_count=load_result.row_count,
            partition_values_json=json.dumps(
                [partition.to_dict() for partition in load_result.partition_values],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            loaded_at=manifest_loaded_at.get(load_result.manifest_path),
        )
        for load_result in load_results
    ]
    return sorted(rows, key=lambda row: (row.dataset, row.resource_name))


def build_schema_columns_rows(
    schema_mapping: SchemaMapping,
    *,
    dataset: str,
    dictionary_artifact: DataDictionaryArtifact | None = None,
    target: str = "canonical",
    source_kind: str = "schema_artifact",
) -> list[AnacSchemaColumnsRow]:
    """@notice Build schema-column rows from a schema artifact plus optional dictionary overlay."""

    dictionary_by_field = {}
    if dictionary_artifact is not None:
        dictionary_by_field = {entry.name: entry for entry in dictionary_artifact.entries}

    rows: list[AnacSchemaColumnsRow] = []
    for index, column in enumerate(schema_mapping.columns, start=1):
        dictionary_entry = dictionary_by_field.get(column.name)
        rows.append(
            AnacSchemaColumnsRow(
                dataset=dataset,
                target=target,
                source_kind=source_kind,
                ordinal_position=index,
                column_name=column.name,
                inferred_type=column.inferred_type,
                duckdb_type=None,
                nullable=column.nullable,
                description=None if dictionary_entry is None else dictionary_entry.description,
                semantic_type=None if dictionary_entry is None else dictionary_entry.semantic_type,
                paired_field=None if dictionary_entry is None else dictionary_entry.paired_field,
                code_meaning_status=None if dictionary_entry is None else dictionary_entry.code_meaning_status,
                vocabulary_dataset_id=None
                if dictionary_entry is None or dictionary_entry.code_reference is None
                else dictionary_entry.code_reference.dataset_id,
                vocabulary_table=None
                if dictionary_entry is None or dictionary_entry.code_reference is None
                else dictionary_entry.code_reference.table_name,
            )
        )
    return rows


def load_schema_columns_rows(
    schema_path: str | Path,
    *,
    dataset: str,
    dictionary_path: str | Path | None = None,
    target: str = "canonical",
    source_kind: str = "schema_artifact",
) -> list[AnacSchemaColumnsRow]:
    """@notice Load schema-column rows directly from serialized local artifacts."""

    schema_mapping = load_schema_mapping(schema_path)
    dictionary_artifact = None if dictionary_path is None else load_data_dictionary_artifact(dictionary_path)
    return build_schema_columns_rows(
        schema_mapping,
        dataset=dataset,
        dictionary_artifact=dictionary_artifact,
        target=target,
        source_kind=source_kind,
    )


def build_dictionary_fields_rows(
    artifact: DataDictionaryArtifact,
    *,
    dataset: str,
) -> list[AnacDictionaryFieldsRow]:
    """@notice Build dictionary-field rows from a typed data-dictionary artifact."""

    rows = [
        AnacDictionaryFieldsRow(
            dataset=dataset,
            field_name=entry.name,
            section=entry.section,
            description=entry.description,
            semantic_type=entry.semantic_type,
            value_pattern=entry.value_pattern,
            inferred_type=entry.inferred_type,
            nullable=entry.nullable,
            paired_field=entry.paired_field,
            code_meaning_status=entry.code_meaning_status,
            external_vocabulary_status=entry.external_vocabulary_status,
            vocabulary_dataset_id=None if entry.code_reference is None else entry.code_reference.dataset_id,
            vocabulary_table=None if entry.code_reference is None else entry.code_reference.table_name,
            join_key=None
            if entry.code_reference is None or entry.code_reference.join_contract is None
            else entry.code_reference.join_contract.source_field,
            label_field=None if entry.code_reference is None else entry.code_reference.target_label_field,
        )
        for entry in artifact.entries
    ]
    return sorted(rows, key=lambda row: row.field_name)


def load_dictionary_fields_rows(path: str | Path, *, dataset: str | None = None) -> list[AnacDictionaryFieldsRow]:
    """@notice Load dictionary-field rows directly from one serialized dictionary artifact."""

    artifact = load_data_dictionary_artifact(path)
    dataset_name = _resolve_dataset_family_id(artifact.dataset_id, registry=_default_registry()) if dataset is None else dataset
    return build_dictionary_fields_rows(artifact, dataset=dataset_name)


def build_crosswalk_rows(
    registration_result: WarehouseCrosswalkRegistrationResult,
) -> list[AnacCrosswalksRow]:
    """@notice Build crosswalk rows from a typed registration result."""

    rows = [
        AnacCrosswalksRow(
            dataset_id=view.dataset_id,
            view_name=view.view_name,
            table_name=view.table_name,
            parquet_path=view.parquet_path,
            row_count=view.row_count,
        )
        for view in registration_result.registered_views
    ]
    return sorted(rows, key=lambda row: (row.dataset_id, row.view_name))


def build_update_status_rows(
    *,
    registry: "DatasetFamilyRegistry | None" = None,
    period_manifest: Sequence[DatasetPeriodManifestRecord] = (),
) -> list[AnacUpdateStatusRow]:
    """@notice Build per-family update-status rows from registry plus local partition state."""

    registry = _default_registry() if registry is None else registry
    partition_rows = build_partitions_rows(period_manifest, registry=registry)
    partition_groups = _group_partition_rows(partition_rows)
    rows: list[AnacUpdateStatusRow] = []
    for family in registry.list_families():
        family_partitions = partition_groups.get(family.dataset, [])
        latest_slice = None if not family_partitions else max(row.slice for row in family_partitions)
        latest_imported_at = max((row.imported_at for row in family_partitions if row.imported_at), default=None)
        latest_refreshed_at = max((row.refreshed_at for row in family_partitions if row.refreshed_at), default=None)
        rows.append(
            AnacUpdateStatusRow(
                dataset=family.dataset,
                update_supported=family.update_supported,
                local_slice_count=len(family_partitions),
                latest_local_slice=latest_slice,
                latest_imported_at=latest_imported_at,
                latest_refreshed_at=latest_refreshed_at,
            )
        )
    return rows


def ensure_metadata_views(
    connection: duckdb.DuckDBPyConnection,
    *,
    db_path: str | Path,
    raw_dir: str | Path | None = None,
    schemas_dir: str | Path | None = None,
    dictionaries_dir: str | Path | None = None,
    vocabulary_index_path: str | Path | None = None,
    registry: "DatasetFamilyRegistry | None" = None,
) -> None:
    """@notice Recompute and register the full `anac_*` metadata layer in one DuckDB session."""

    registry = _default_registry() if registry is None else registry
    resolved_paths = _resolve_metadata_paths(
        db_path,
        raw_dir=raw_dir,
        schemas_dir=schemas_dir,
        dictionaries_dir=dictionaries_dir,
        vocabulary_index_path=vocabulary_index_path,
    )
    manifests = load_download_manifests(resolved_paths["raw_dir"])
    dictionary_artifacts = load_data_dictionary_artifacts(resolved_paths["dictionaries_dir"])
    load_results = _load_warehouse_load_results(connection, db_path=db_path)
    registered_views_rows = _load_registered_views_catalog_rows(connection)
    period_manifest = _load_period_manifest_records(connection)
    schema_rows = load_all_schema_columns_rows(
        resolved_paths["schemas_dir"],
        dictionary_artifacts=dictionary_artifacts,
        registry=registry,
    )
    dictionary_rows: list[AnacDictionaryFieldsRow] = []
    for artifact in dictionary_artifacts:
        dictionary_rows.extend(
            build_dictionary_fields_rows(
                artifact,
                dataset=_resolve_dataset_family_id(artifact.dataset_id, registry=registry),
            )
        )
    vocabulary_index = _load_optional_vocabulary_index(resolved_paths["vocabulary_index_path"])
    crosswalk_rows = build_crosswalk_rows_from_catalog(
        registered_views_rows,
        vocabulary_index=vocabulary_index,
    )

    metadata_rows: dict[str, list[object]] = {
        "anac_datasets": build_datasets_rows(
            registry=registry,
            period_manifest=period_manifest,
            dictionary_artifacts=dictionary_artifacts,
        ),
        "anac_dataset_resources": build_dataset_resource_rows(
            manifests,
            load_results=load_results,
            registry=registry,
        ),
        "anac_partitions": build_partitions_rows(period_manifest, registry=registry),
        "anac_registered_views": registered_views_rows,
        "anac_loaded_resources": build_loaded_resources_rows(
            load_results,
            registry=registry,
            manifests=manifests,
        ),
        "anac_schema_columns": schema_rows,
        "anac_dictionary_fields": sorted(dictionary_rows, key=lambda row: (row.dataset, row.field_name)),
        "anac_crosswalks": crosswalk_rows,
        "anac_update_status": build_update_status_rows(
            registry=registry,
            period_manifest=period_manifest,
        ),
    }

    for view_name in METADATA_VIEW_NAMES:
        _register_metadata_view(connection, view_name, metadata_rows[view_name])


def _load_json_object(path: str | Path) -> dict[str, object]:
    """@notice Load one JSON object from disk with a predictable shape check."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Artifact at {path} did not contain a JSON object.")
    return payload


def _default_registry() -> "DatasetFamilyRegistry":
    """@notice Resolve the shared dataset-family registry lazily to avoid import cycles."""

    from anac_explorator.catalog import DATASET_FAMILY_REGISTRY

    return DATASET_FAMILY_REGISTRY


def load_download_manifests(raw_dir: str | Path) -> list[DownloadManifest]:
    """@notice Load all manifest-backed raw resources found under one raw-data root."""

    raw_root = Path(raw_dir)
    if not raw_root.exists():
        return []
    manifests = [load_download_manifest(path) for path in sorted(raw_root.glob("**/manifest.json"))]
    return sorted(manifests, key=lambda manifest: (manifest.dataset_id, manifest.resource_name))


def load_data_dictionary_artifacts(dictionaries_dir: str | Path) -> list[DataDictionaryArtifact]:
    """@notice Load all serialized dictionary artifacts from one dictionaries directory."""

    dictionaries_root = Path(dictionaries_dir)
    if not dictionaries_root.exists():
        return []
    artifacts = [
        load_data_dictionary_artifact(path)
        for path in sorted(dictionaries_root.glob("*.dictionary.json"))
    ]
    return sorted(artifacts, key=lambda artifact: artifact.dictionary_name)


def load_all_schema_columns_rows(
    schemas_dir: str | Path,
    *,
    dictionary_artifacts: Sequence[DataDictionaryArtifact] = (),
    registry: "DatasetFamilyRegistry | None" = None,
) -> list[AnacSchemaColumnsRow]:
    """@notice Load schema-column rows for all local schema artifacts currently on disk."""

    registry = _default_registry() if registry is None else registry
    schemas_root = Path(schemas_dir)
    if not schemas_root.exists():
        return []

    dictionary_by_schema_name = {
        Path(artifact.source_schema_path).name: artifact for artifact in dictionary_artifacts
    }
    rows: list[AnacSchemaColumnsRow] = []
    for schema_path in sorted(schemas_root.glob("*.schema.json")):
        schema_mapping = load_schema_mapping(schema_path)
        dataset_name, target = _infer_schema_dataset_target(schema_path, schema_mapping, registry=registry)
        rows.extend(
            build_schema_columns_rows(
                schema_mapping,
                dataset=dataset_name,
                dictionary_artifact=dictionary_by_schema_name.get(schema_path.name),
                target=target,
                source_kind="schema_artifact",
            )
        )
    return sorted(rows, key=lambda row: (row.dataset, row.target, row.ordinal_position))


def build_crosswalk_rows_from_catalog(
    registered_views_rows: Sequence[AnacRegisteredViewsRow],
    *,
    vocabulary_index: VocabularyIndexArtifact | None,
) -> list[AnacCrosswalksRow]:
    """@notice Rebuild crosswalk metadata from registered DuckDB views plus vocabulary artifacts."""

    if vocabulary_index is None:
        return []

    registered_by_view = {row.view_name: row for row in registered_views_rows}
    rows: list[AnacCrosswalksRow] = []
    for dataset_entry in vocabulary_index.datasets:
        artifact_path = Path(dataset_entry.artifact_path)
        if not artifact_path.exists():
            continue
        artifact_payload = _load_json_object(artifact_path)
        tables = artifact_payload.get("tables", [])
        if not isinstance(tables, list):
            continue
        for table in tables:
            if not isinstance(table, dict):
                continue
            table_name = str(table.get("name", ""))
            registered_view = registered_by_view.get(table_name)
            if registered_view is None:
                continue
            entry_count = table.get("entry_count")
            raw_entries = table.get("entries", [])
            row_count = int(entry_count) if entry_count not in (None, "") else len(raw_entries if isinstance(raw_entries, list) else [])
            rows.append(
                AnacCrosswalksRow(
                    dataset_id=dataset_entry.dataset_id,
                    view_name=registered_view.view_name,
                    table_name=registered_view.table_name,
                    parquet_path=str(Path(registered_view.parquet_root) / f"{registered_view.table_name}.parquet"),
                    row_count=row_count,
                )
            )
    return sorted(rows, key=lambda row: (row.dataset_id, row.view_name))


def _resolve_dataset_family_id(
    dataset_id: str,
    *,
    registry: DatasetFamilyRegistry,
    fallback: str | None = None,
) -> str:
    """@notice Map a raw CKAN dataset id back to one logical family when possible."""

    family = registry.resolve_family_for_dataset_id(dataset_id)
    if family is not None:
        return family.dataset
    try:
        return registry.get_family(dataset_id).dataset
    except CliCommandError:
        return dataset_id if fallback is None else fallback


def _resolve_metadata_paths(
    db_path: str | Path,
    *,
    raw_dir: str | Path | None,
    schemas_dir: str | Path | None,
    dictionaries_dir: str | Path | None,
    vocabulary_index_path: str | Path | None,
) -> dict[str, Path]:
    """@notice Resolve local artifact roots relative to the warehouse database when omitted."""

    database_path = Path(db_path)
    warehouse_dir = database_path.parent
    project_root = warehouse_dir.parent.parent if warehouse_dir.parent.name == "data" else warehouse_dir.parent
    default_raw_dir = warehouse_dir.parent / "raw" if warehouse_dir.parent.name == "data" else project_root / "raw"
    return {
        "raw_dir": default_raw_dir if raw_dir is None else Path(raw_dir),
        "schemas_dir": project_root / "schemas" if schemas_dir is None else Path(schemas_dir),
        "dictionaries_dir": project_root / "dictionaries" if dictionaries_dir is None else Path(dictionaries_dir),
        "vocabulary_index_path": project_root / "vocabularies" / "index.json"
        if vocabulary_index_path is None
        else Path(vocabulary_index_path),
    }


def _group_partition_rows(rows: Iterable[AnacPartitionsRow]) -> dict[str, list[AnacPartitionsRow]]:
    """@notice Group partition rows by logical dataset family."""

    grouped: dict[str, list[AnacPartitionsRow]] = {}
    for row in rows:
        grouped.setdefault(row.dataset, []).append(row)
    return grouped


def _register_metadata_view(
    connection: duckdb.DuckDBPyConnection,
    view_name: str,
    rows: Sequence[object],
) -> None:
    """@notice Recreate one temporary metadata table and stable `anac_*` view."""

    table_name = f"__tmp_{view_name}"
    schema = _METADATA_VIEW_SCHEMAS[view_name]
    connection.execute(f"DROP TABLE IF EXISTS {table_name}")
    connection.execute(
        f"CREATE TEMP TABLE {table_name} (" + ", ".join(f"{column_name} {column_type}" for column_name, column_type in schema) + ")"
    )
    if rows:
        placeholders = "(" + ", ".join("?" for _ in schema) + ")"
        connection.executemany(
            f"INSERT INTO {table_name} VALUES {placeholders}",
            [_serialize_metadata_row(row, schema) for row in rows],
        )
    connection.execute(f"CREATE OR REPLACE TEMP VIEW {view_name} AS SELECT * FROM {table_name}")


def _serialize_metadata_row(row: object, schema: Sequence[tuple[str, str]]) -> list[object]:
    """@notice Serialize one dataclass row into the explicit table-column order."""

    row_payload = row.to_dict() if hasattr(row, "to_dict") else asdict(row)  # type: ignore[arg-type]
    values: list[object] = []
    for column_name, _column_type in schema:
        value = row_payload.get(column_name)
        if isinstance(value, (list, dict)):
            values.append(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        else:
            values.append(value)
    return values


def _load_warehouse_load_results(
    connection: duckdb.DuckDBPyConnection,
    *,
    db_path: str | Path,
) -> list[WarehouseLoadResult]:
    """@notice Load persisted manifest-backed warehouse loads from the DuckDB catalog."""

    if not _persistent_table_exists(connection, "loaded_resources"):
        return []

    rows = connection.execute(
        """
        SELECT
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
        FROM loaded_resources
        ORDER BY loaded_at, manifest_path
        """
    ).fetchall()
    registered_views = {
        row.view_name: row for row in _load_registered_views_catalog_rows(connection)
    }
    warehouse_dir = str(Path(db_path).parent)
    duckdb_path = str(Path(db_path))
    results: list[WarehouseLoadResult] = []
    for (
        manifest_path_value,
        dataset_id_value,
        resource_name_value,
        table_name_value,
        view_name_value,
        source_path_value,
        schema_path_value,
        parquet_root_value,
        parquet_path_value,
        row_count_value,
        partition_values_json_value,
        _loaded_at_value,
    ) in rows:
        registered_view = registered_views.get(str(view_name_value))
        results.append(
            WarehouseLoadResult(
                dataset_id=str(dataset_id_value),
                resource_name=str(resource_name_value),
                table_name=str(table_name_value),
                view_name=str(view_name_value),
                manifest_path=str(manifest_path_value),
                schema_path=None if schema_path_value in (None, "") else str(schema_path_value),
                source_path=str(source_path_value),
                warehouse_dir=warehouse_dir,
                duckdb_path=duckdb_path,
                parquet_root=str(parquet_root_value),
                parquet_path=str(parquet_path_value),
                row_count=int(row_count_value),
                partition_values=_parse_partition_values(partition_values_json_value),
                registered_parquet_files=0 if registered_view is None else registered_view.parquet_file_count,
            )
        )
    return results


def _load_registered_views_catalog_rows(
    connection: duckdb.DuckDBPyConnection,
) -> list[AnacRegisteredViewsRow]:
    """@notice Load registered DuckDB view metadata from the warehouse catalog when present."""

    if not _persistent_table_exists(connection, "registered_views"):
        return []
    rows = connection.execute(
        """
        SELECT view_name, table_name, parquet_root, parquet_file_count, updated_at, view_sql
        FROM registered_views
        ORDER BY view_name
        """
    ).fetchall()
    return [
        AnacRegisteredViewsRow(
            view_name=str(view_name_value),
            table_name=str(table_name_value),
            parquet_root=str(parquet_root_value),
            parquet_file_count=int(parquet_file_count_value),
            updated_at=None if updated_at_value in (None, "") else str(updated_at_value),
            view_sql=str(view_sql_value),
        )
        for view_name_value, table_name_value, parquet_root_value, parquet_file_count_value, updated_at_value, view_sql_value in rows
    ]


def _load_period_manifest_records(
    connection: duckdb.DuckDBPyConnection,
) -> list[DatasetPeriodManifestRecord]:
    """@notice Load all persisted partition-manifest records from the warehouse catalog."""

    if not _persistent_table_exists(connection, "dataset_period_manifest"):
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
        ORDER BY dataset_type, period
        """
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
            imported_at=None if imported_at_value in (None, "") else str(imported_at_value),
            refreshed_at=None if refreshed_at_value in (None, "") else str(refreshed_at_value),
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


def _load_optional_vocabulary_index(path: Path) -> VocabularyIndexArtifact | None:
    """@notice Load the vocabulary index when it exists, otherwise return no artifact."""

    if not path.exists():
        return None
    return load_vocabulary_index(path)


def _persistent_table_exists(connection: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    """@notice Check whether one persistent catalog table exists in the current database."""

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


def _parse_partition_values(payload: object) -> list["WarehousePartitionValue"]:
    """@notice Parse serialized partition metadata from `loaded_resources` rows."""

    from anac_explorator.models import WarehousePartitionValue

    if payload in (None, ""):
        return []
    raw_payload = json.loads(str(payload))
    if not isinstance(raw_payload, list):
        return []
    return [
        WarehousePartitionValue(key=str(entry["key"]), value=str(entry["value"]))
        for entry in raw_payload
        if isinstance(entry, dict) and "key" in entry and "value" in entry
    ]


def _infer_schema_dataset_target(
    schema_path: Path,
    schema_mapping: SchemaMapping,
    *,
    registry: DatasetFamilyRegistry,
) -> tuple[str, str]:
    """@notice Infer the logical family and target label for one local schema artifact."""

    schema_name = schema_path.name
    monthly_match = re.fullmatch(r"(cig|smartcig)_(\d{4})_(\d{2})\.schema\.json", schema_name)
    if monthly_match is not None:
        return monthly_match.group(1), f"{monthly_match.group(2)}-{monthly_match.group(3)}"
    yearly_match = re.fullmatch(r"(cig|smartcig)_(\d{4})\.schema\.json", schema_name)
    if yearly_match is not None:
        return yearly_match.group(1), yearly_match.group(2)

    dataset_stem = schema_name.removesuffix(".schema.json")
    try:
        return registry.get_family(dataset_stem).dataset, "canonical"
    except CliCommandError:
        pass

    source_path = Path(schema_mapping.source_path)
    for part in source_path.parts:
        family = registry.resolve_family_for_dataset_id(part)
        if family is not None:
            return family.dataset, "canonical"
        try:
            return registry.get_family(part).dataset, "canonical"
        except CliCommandError:
            continue

    return dataset_stem, "canonical"


def _normalize_source_format(format_value: str | None, resource_name: str, materialized_path: str | None) -> str:
    """@notice Normalize the source format into the metadata-view convention."""

    candidate = "" if format_value is None else str(format_value).strip().casefold()
    if candidate in {"csv", "json"}:
        return candidate.upper()
    resource_lower = resource_name.casefold()
    if "_json_" in resource_lower or resource_lower.endswith("_json"):
        return "JSON"
    if "_csv_" in resource_lower or resource_lower.endswith("_csv"):
        return "CSV"
    if materialized_path is not None:
        suffix = Path(materialized_path).suffix.casefold()
        if suffix == ".json":
            return "JSON"
    return "CSV"


def _extract_slice(
    *,
    dataset_id: str,
    resource_name: str,
    registry: "DatasetFamilyRegistry | None" = None,
) -> str | None:
    """@notice Infer a canonical slice from the known monthly CKAN resource naming patterns."""

    registry = _default_registry() if registry is None else registry
    match = re.search(r"_(\d{4})_(\d{2})$", resource_name.casefold())
    if match is None:
        return None
    family = registry.resolve_family_for_dataset_id(dataset_id)
    if family is None or family.coverage_kind != "periodic_monthly":
        return None
    return f"{match.group(1)}-{match.group(2)}"


def _infer_manifest_path(manifest: DownloadManifest) -> str:
    """@notice Derive the default cache manifest path for one raw downloaded resource."""

    materialized_path = Path(manifest.materialized_path)
    base_output_dir = materialized_path.parent.parent if materialized_path.parent.name == "extracted" else materialized_path.parent
    return str(base_output_dir / "manifest.json")
