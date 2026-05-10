"""@notice Shared data models for metadata, schema inspection, dictionary generation, and pipeline parsing.

@dev These dataclasses back the completed Phase 1 workflow: CKAN metadata
lookup, dataset download reporting, raw CSV schema mapping, data-dictionary
generation, and the first Phase 2 downloader/parser/cleaner pipeline pieces.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class CkanResource:
    """@notice Describe a single CKAN resource attached to a dataset.

    @param id CKAN resource identifier.
    @param name Human-readable resource label.
    @param format Resource format reported by CKAN.
    @param url Direct download URL when present.
    @param size Size in bytes when reported by CKAN.
    @param last_modified CKAN last-modified timestamp.
    @param description CKAN resource description or empty string.
    """

    id: str
    name: str
    format: str
    url: str
    size: int | None = None
    last_modified: str | None = None
    description: str = ""

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the resource into a JSON-serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class CkanPackage:
    """@notice Describe the dataset metadata returned by CKAN package_show.

    @param id CKAN package identifier.
    @param name CKAN dataset slug.
    @param title Display title.
    @param notes Dataset notes or description.
    @param resources Attached downloadable resources.
    """

    id: str
    name: str
    title: str
    notes: str
    resources: list[CkanResource] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the package into a JSON-serializable dictionary."""

        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "notes": self.notes,
            "resources": [resource.to_dict() for resource in self.resources],
        }


@dataclass(slots=True)
class SchemaColumn:
    """@notice Describe one raw CSV column discovered during schema mapping.

    @param name Raw ANAC source column name, preserved exactly as encountered.
    @param inferred_type Pragmatic inferred scalar type from sampled non-empty values.
    @param nullable Whether at least one sampled row contained an empty value.
    @param non_empty_samples Small representative sample of non-empty values.
    """

    name: str
    inferred_type: str
    nullable: bool
    non_empty_samples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the column description into a serializable dictionary."""

        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "SchemaColumn":
        """@notice Rebuild a schema column from a serialized dictionary."""

        return cls(
            name=str(payload["name"]),
            inferred_type=str(payload["inferred_type"]),
            nullable=bool(payload["nullable"]),
            non_empty_samples=[str(value) for value in payload.get("non_empty_samples", [])],
        )


@dataclass(slots=True)
class SchemaMapping:
    """@notice Capture the schema-mapping output for one CSV file.

    @param source_path Path of the inspected CSV file.
    @param delimiter CSV delimiter used for parsing.
    @param encoding Text encoding used for parsing.
    @param rows_sampled Number of data rows inspected.
    @param row_length_mismatches Count of rows whose field count differed from the header.
    @param columns Ordered raw columns discovered from the header row.
    """

    source_path: str
    delimiter: str
    encoding: str
    rows_sampled: int
    row_length_mismatches: int
    columns: list[SchemaColumn] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the schema mapping into a serializable dictionary."""

        return {
            "source_path": self.source_path,
            "delimiter": self.delimiter,
            "encoding": self.encoding,
            "rows_sampled": self.rows_sampled,
            "row_length_mismatches": self.row_length_mismatches,
            "columns": [column.to_dict() for column in self.columns],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "SchemaMapping":
        """@notice Rebuild a schema mapping from a serialized dictionary."""

        raw_columns = payload.get("columns", [])
        columns = [SchemaColumn.from_dict(column) for column in raw_columns if isinstance(column, dict)]
        return cls(
            source_path=str(payload["source_path"]),
            delimiter=str(payload["delimiter"]),
            encoding=str(payload["encoding"]),
            rows_sampled=int(payload["rows_sampled"]),
            row_length_mismatches=int(payload["row_length_mismatches"]),
            columns=columns,
        )


@dataclass(slots=True)
class JoinContract:
    """@notice Describe how a source field joins to a vocabulary table.

    @param target_dataset Dataset identifier that owns the target table.
    @param target_table Logical target table name inside the generated artifact.
    @param source_field Source field name from the dictionary surface.
    @param target_field Target field name used as the join key.
    @param target_label_field Target field name that carries the human-readable label.
    @param join_type Recommended SQL join type.
    """

    target_dataset: str
    target_table: str
    source_field: str
    target_field: str
    target_label_field: str
    join_type: str = "left"

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the join contract into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class DataDictionaryCodeReference:
    """@notice Describe how one field can be decoded as a code/label concept.

    @param reference_kind Whether the field resolves through an external vocabulary
    dataset or an inline label field.
    @param dataset_id Vocabulary dataset identifier when an external table exists.
    @param table_name Vocabulary table name inside the generated artifact.
    @param source_code_field Source field that carries the coded value.
    @param source_label_field Source field that carries the human-readable label.
    @param target_code_field Target field name used as the code key in the
    normalized vocabulary table.
    @param target_label_field Target field name used as the label in the normalized
    vocabulary table.
    @param table_code_column Raw source code column preserved by the vocabulary table.
    @param table_label_column Raw source label column preserved by the vocabulary table.
    @param resolved_fields Fields covered by this reference.
    @param external_vocabulary_status Whether a dedicated external vocabulary exists.
    @param join_contract Explicit join contract for SQL generation when applicable.
    @param notes Human-readable note about the scope of the link.
    @param artifact_path Path to the generated vocabulary artifact.
    @param entry_count Number of available code/label entries in the linked table.
    @param preview_entries Small preview of code meanings for quick inspection.
    """

    reference_kind: str
    dataset_id: str | None = None
    table_name: str | None = None
    source_code_field: str | None = None
    source_label_field: str | None = None
    target_code_field: str | None = None
    target_label_field: str | None = None
    table_code_column: str | None = None
    table_label_column: str | None = None
    resolved_fields: list[str] = field(default_factory=list)
    external_vocabulary_status: str = "unknown"
    join_contract: JoinContract | None = None
    notes: str = ""
    artifact_path: str | None = None
    entry_count: int | None = None
    preview_entries: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the code reference into a serializable dictionary."""

        payload = asdict(self)
        payload["join_contract"] = None if self.join_contract is None else self.join_contract.to_dict()
        return payload


@dataclass(slots=True)
class DataDictionaryEntry:
    """@notice Describe one field in the generated data dictionary.

    @param name Raw source field name.
    @param section Logical section used in the human-readable dictionary.
    @param description Human-readable field description.
    @param semantic_type High-level semantic category used by downstream tools.
    @param value_pattern Pragmatic pattern summary for the observed values.
    @param inferred_type Current inferred type from the schema artifact.
    @param nullable Whether the field is nullable in the current schema artifact.
    @param non_empty_samples Representative sample values from the schema artifact.
    @param related_fields Other fields closely tied to this one.
    @param paired_field Sibling field that carries the paired code or label meaning.
    @param code_meaning_status Systematic status describing how coded values are resolved.
    @param external_vocabulary_status Whether a dedicated external vocabulary exists.
    @param code_reference Linked vocabulary reference when available.
    @param cross_year_notes Cross-year notes derived from the comparison artifact.
    @param notes Additional field-specific notes or caveats.
    """

    name: str
    section: str
    description: str
    semantic_type: str
    value_pattern: str
    inferred_type: str
    nullable: bool
    non_empty_samples: list[str] = field(default_factory=list)
    related_fields: list[str] = field(default_factory=list)
    paired_field: str | None = None
    code_meaning_status: str = "unknown"
    external_vocabulary_status: str = "not_applicable"
    code_reference: DataDictionaryCodeReference | None = None
    cross_year_notes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the dictionary entry into a serializable dictionary."""

        payload = {
            "name": self.name,
            "section": self.section,
            "description": self.description,
            "semantic_type": self.semantic_type,
            "value_pattern": self.value_pattern,
            "inferred_type": self.inferred_type,
            "nullable": self.nullable,
            "non_empty_samples": self.non_empty_samples,
            "related_fields": self.related_fields,
            "paired_field": self.paired_field,
            "code_meaning_status": self.code_meaning_status,
            "external_vocabulary_status": self.external_vocabulary_status,
            "cross_year_notes": self.cross_year_notes,
            "notes": self.notes,
        }
        payload["code_reference"] = None if self.code_reference is None else self.code_reference.to_dict()
        return payload


@dataclass(slots=True)
class DataDictionaryArtifact:
    """@notice Capture the generated field dictionary for one schema surface.

    @param dictionary_name Stable dictionary artifact name.
    @param dataset_id Dataset identifier represented by the dictionary.
    @param source_schema_path Source schema artifact path.
    @param comparison_path Optional comparison artifact path used for cross-year notes.
    @param vocabulary_index_path Optional vocabulary index path used for code links.
    @param sections Ordered section names present in the dictionary.
    @param entries Field-level dictionary entries.
    """

    dictionary_name: str
    dataset_id: str
    source_schema_path: str
    comparison_path: str | None = None
    vocabulary_index_path: str | None = None
    sections: list[str] = field(default_factory=list)
    entries: list[DataDictionaryEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the dictionary artifact into a serializable dictionary."""

        return {
            "dictionary_name": self.dictionary_name,
            "dataset_id": self.dataset_id,
            "source_schema_path": self.source_schema_path,
            "comparison_path": self.comparison_path,
            "vocabulary_index_path": self.vocabulary_index_path,
            "section_count": len(self.sections),
            "entry_count": len(self.entries),
            "sections": self.sections,
            "entries": [entry.to_dict() for entry in self.entries],
        }


@dataclass(slots=True)
class DownloadManifest:
    """@notice Persist cache and resume metadata for one downloaded resource.

    @param dataset_id CKAN dataset slug used for resolution.
    @param resource_id CKAN resource identifier when known.
    @param resource_name CKAN resource name selected for download.
    @param resource_format CKAN format string for the selected resource.
    @param resource_url Direct resource URL used for download.
    @param transport Transport used for the completed download.
    @param archive_path Local archive path when the source resource was materialized as an archive.
    @param materialized_path Local path of the extracted or downloaded working file.
    @param materialized_kind Working file kind such as `csv` or `json`.
    @param cache_status Whether the current result was freshly downloaded, resumed, restarted, or reused from cache.
    @param resume_supported Whether the chosen transport can resume partial downloads.
    @param source_size Size reported by CKAN when available.
    @param source_last_modified CKAN last-modified timestamp when available.
    @param downloaded_at ISO timestamp for the completed local materialization.
    """

    dataset_id: str
    resource_id: str | None
    resource_name: str
    resource_format: str
    resource_url: str
    transport: str
    archive_path: str | None
    materialized_path: str
    materialized_kind: str
    cache_status: str
    resume_supported: bool
    source_size: int | None = None
    source_last_modified: str | None = None
    downloaded_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the manifest into a serializable dictionary."""

        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "DownloadManifest":
        """@notice Rebuild a manifest from a serialized dictionary."""

        return cls(
            dataset_id=str(payload["dataset_id"]),
            resource_id=None if payload.get("resource_id") in (None, "") else str(payload["resource_id"]),
            resource_name=str(payload["resource_name"]),
            resource_format=str(payload["resource_format"]),
            resource_url=str(payload["resource_url"]),
            transport=str(payload["transport"]),
            archive_path=None if payload.get("archive_path") in (None, "") else str(payload["archive_path"]),
            materialized_path=str(payload["materialized_path"]),
            materialized_kind=str(payload["materialized_kind"]),
            cache_status=str(payload["cache_status"]),
            resume_supported=bool(payload["resume_supported"]),
            source_size=None if payload.get("source_size") in (None, "") else int(payload["source_size"]),
            source_last_modified=None
            if payload.get("source_last_modified") in (None, "")
            else str(payload["source_last_modified"]),
            downloaded_at=None if payload.get("downloaded_at") in (None, "") else str(payload["downloaded_at"]),
        )


@dataclass(slots=True)
class DownloadedResourceArtifact:
    """@notice Capture the local outputs and manifest for one downloaded resource.

    @param manifest Resolved manifest for the downloaded resource.
    @param manifest_path Path to the persisted manifest JSON file.
    """

    manifest: DownloadManifest
    manifest_path: str

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the downloaded resource artifact into a serializable dictionary."""

        payload = self.manifest.to_dict()
        payload["manifest_path"] = self.manifest_path
        return payload


@dataclass(slots=True)
class DownloadedCsvResource:
    """@notice Capture the local outputs of one downloaded CKAN CSV resource.

    @param artifact Generic downloaded resource artifact.
    """

    artifact: DownloadedResourceArtifact

    @property
    def dataset_id(self) -> str:
        """@notice Dataset identifier used for resolution."""

        return self.artifact.manifest.dataset_id

    @property
    def resource_name(self) -> str:
        """@notice CKAN resource name that was selected."""

        return self.artifact.manifest.resource_name

    @property
    def resource_url(self) -> str:
        """@notice Direct resource URL used for download."""

        return self.artifact.manifest.resource_url

    @property
    def archive_path(self) -> str | None:
        """@notice Local archive path when the source resource was zipped."""

        return self.artifact.manifest.archive_path

    @property
    def csv_path(self) -> str:
        """@notice Local materialized CSV path."""

        return self.artifact.manifest.materialized_path

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the downloaded CSV resource into a serializable dictionary."""

        payload = self.artifact.to_dict()
        payload["csv_path"] = self.csv_path
        payload["zip_path"] = self.archive_path
        return payload


@dataclass(slots=True)
class ParsedCsvRow:
    """@notice Represent one parsed CSV row before cleaning.

    @param row_number One-based row number excluding the header.
    @param values Raw string values keyed by preserved source column name.
    """

    row_number: int
    values: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the parsed row into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class ParsedCsvResource:
    """@notice Capture parsed CSV records for a local resource.

    @param source_path Path to the parsed CSV file.
    @param delimiter CSV delimiter used for parsing.
    @param encoding Text encoding used for parsing.
    @param field_names Ordered header names from the source file.
    @param row_count Total data rows parsed.
    @param rows Parsed records retained in memory for inspection or downstream work.
    """

    source_path: str
    delimiter: str
    encoding: str
    field_names: list[str] = field(default_factory=list)
    row_count: int = 0
    rows: list[ParsedCsvRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the parsed CSV resource into a serializable dictionary."""

        return {
            "source_path": self.source_path,
            "delimiter": self.delimiter,
            "encoding": self.encoding,
            "field_names": self.field_names,
            "row_count": self.row_count,
            "rows": [row.to_dict() for row in self.rows],
        }


@dataclass(slots=True)
class ParsedJsonResource:
    """@notice Capture a parsed JSON resource before cleaning.

    @param source_path Path to the parsed JSON file.
    @param encoding Text encoding used for parsing.
    @param top_level_type Top-level JSON container type such as `object` or `array`.
    @param item_count Number of top-level items when countable.
    @param payload Full parsed JSON payload retained for downstream cleaning.
    @param sample_items Small sample of parsed top-level items.
    """

    source_path: str
    encoding: str
    top_level_type: str
    item_count: int | None = None
    payload: object | None = None
    sample_items: list[object] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the parsed JSON resource into a serializable dictionary."""

        return {
            "source_path": self.source_path,
            "encoding": self.encoding,
            "top_level_type": self.top_level_type,
            "item_count": self.item_count,
            "sample_items": [_to_json_compatible(item) for item in self.sample_items],
        }


@dataclass(slots=True)
class CleaningIssue:
    """@notice Describe one cleaning or coercion issue for a parsed field.

    @param field_name Field name affected by the issue.
    @param raw_value Raw source value before cleaning.
    @param target_type Target scalar type requested for coercion.
    @param message Human-readable explanation of the issue.
    """

    field_name: str
    raw_value: object
    target_type: str
    message: str

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the cleaning issue into a serializable dictionary."""

        return {
            "field_name": self.field_name,
            "raw_value": _to_json_compatible(self.raw_value),
            "target_type": self.target_type,
            "message": self.message,
        }


@dataclass(slots=True)
class CleanedRecord:
    """@notice Capture one cleaned record ready for downstream loading.

    @param row_number One-based row number when available.
    @param raw_values Original parsed values before cleaning.
    @param cleaned_values Cleaned and coerced values.
    @param issues Cleaning issues observed while normalizing the record.
    """

    row_number: int | None
    raw_values: dict[str, object] = field(default_factory=dict)
    cleaned_values: dict[str, object] = field(default_factory=dict)
    issues: list[CleaningIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the cleaned record into a serializable dictionary."""

        return {
            "row_number": self.row_number,
            "raw_values": {key: _to_json_compatible(value) for key, value in self.raw_values.items()},
            "cleaned_values": {key: _to_json_compatible(value) for key, value in self.cleaned_values.items()},
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(slots=True)
class CleanedTabularResource:
    """@notice Capture cleaned tabular records for later loading.

    @param source_path Path to the cleaned source file.
    @param format_name Resource format such as `csv`.
    @param record_count Number of parsed records processed.
    @param cleaned_records Cleaned records retained in memory.
    @param type_hints Type hints applied during coercion.
    """

    source_path: str
    format_name: str
    record_count: int
    cleaned_records: list[CleanedRecord] = field(default_factory=list)
    type_hints: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the cleaned tabular resource into a serializable dictionary."""

        return {
            "source_path": self.source_path,
            "format_name": self.format_name,
            "record_count": self.record_count,
            "type_hints": self.type_hints,
            "cleaned_records": [record.to_dict() for record in self.cleaned_records],
        }


@dataclass(slots=True)
class CleanedJsonResource:
    """@notice Capture a cleaned JSON document for later loading.

    @param source_path Path to the cleaned JSON source.
    @param top_level_type Top-level JSON container type.
    @param cleaned_payload Cleaned JSON-compatible payload.
    @param issues Cleaning issues observed while normalizing the document.
    """

    source_path: str
    top_level_type: str
    cleaned_payload: object
    issues: list[CleaningIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the cleaned JSON resource into a serializable dictionary."""

        return {
            "source_path": self.source_path,
            "top_level_type": self.top_level_type,
            "cleaned_payload": _to_json_compatible(self.cleaned_payload),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(slots=True)
class WarehousePartitionValue:
    """@notice Capture one partition key/value attached to a loaded Parquet slice.

    @param key Partition column name such as `year` or `month`.
    @param value Partition value stored in the directory layout.
    """

    key: str
    value: str

    def to_dict(self) -> dict[str, str]:
        """@notice Convert the partition value into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class WarehouseLoadResult:
    """@notice Describe one manifest-backed load into the local DuckDB/Parquet warehouse.

    @param dataset_id CKAN dataset slug used for the source resource.
    @param resource_name CKAN resource name loaded into the warehouse.
    @param table_name Stable logical table name represented by the load.
    @param view_name DuckDB view name registered for querying the Parquet files.
    @param manifest_path Path to the manifest that drove the load.
    @param schema_path Optional schema artifact used for typed projections.
    @param source_path Local source file loaded into Parquet.
    @param warehouse_dir Base directory of the local warehouse assets.
    @param duckdb_path Path to the DuckDB catalog database.
    @param parquet_root Root directory that owns the table's Parquet files.
    @param parquet_path Path of the specific Parquet file written for this load.
    @param row_count Row count written for this load.
    @param partition_values Partition metadata applied to this file.
    @param registered_parquet_files Number of Parquet files currently registered in the view.
    @param load_status Whether the warehouse load was freshly written or reused from cache.
    @param view_sql SQL used to create or refresh the DuckDB view.
    """

    dataset_id: str
    resource_name: str
    table_name: str
    view_name: str
    manifest_path: str
    schema_path: str | None
    source_path: str
    warehouse_dir: str
    duckdb_path: str
    parquet_root: str
    parquet_path: str
    row_count: int
    partition_values: list[WarehousePartitionValue] = field(default_factory=list)
    registered_parquet_files: int = 0
    load_status: str = "fresh"
    view_sql: str = ""

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the load result into a serializable dictionary."""

        return {
            "dataset_id": self.dataset_id,
            "resource_name": self.resource_name,
            "table_name": self.table_name,
            "view_name": self.view_name,
            "manifest_path": self.manifest_path,
            "schema_path": self.schema_path,
            "source_path": self.source_path,
            "warehouse_dir": self.warehouse_dir,
            "duckdb_path": self.duckdb_path,
            "parquet_root": self.parquet_root,
            "parquet_path": self.parquet_path,
            "row_count": self.row_count,
            "partition_values": [partition.to_dict() for partition in self.partition_values],
            "registered_parquet_files": self.registered_parquet_files,
            "load_status": self.load_status,
            "view_sql": self.view_sql,
        }


@dataclass(slots=True)
class WarehouseCrosswalkView:
    """@notice Describe one queryable DuckDB view registered from a vocabulary artifact.

    @param dataset_id Dataset identifier that owns the vocabulary artifact.
    @param table_name Logical cross-reference table name from the artifact.
    @param view_name DuckDB view name registered for querying.
    @param parquet_path Parquet file that backs the registered view.
    @param row_count Number of rows emitted into the cross-reference Parquet file.
    """

    dataset_id: str
    table_name: str
    view_name: str
    parquet_path: str
    row_count: int

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the crosswalk registration into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class WarehouseCrosswalkRegistrationResult:
    """@notice Capture the result of registering vocabulary crosswalk views in DuckDB.

    @param duckdb_path Path to the DuckDB catalog database.
    @param vocabulary_index_path Vocabulary index used for registration when available.
    @param status High-level outcome such as `registered` or `missing_index`.
    @param registered_views Registered cross-reference views.
    """

    duckdb_path: str
    vocabulary_index_path: str
    status: str
    registered_views: list[WarehouseCrosswalkView] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the registration result into a serializable dictionary."""

        return {
            "duckdb_path": self.duckdb_path,
            "vocabulary_index_path": self.vocabulary_index_path,
            "status": self.status,
            "registered_views": [view.to_dict() for view in self.registered_views],
        }


@dataclass(slots=True)
class DatasetParquetDownloadResult:
    """@notice Capture the end-to-end download-to-Parquet workflow result.

    @param dataset_id CKAN dataset slug requested by the user.
    @param resource_name CKAN resource name selected for download.
    @param manifest_path Manifest path produced or reused by the downloader.
    @param download_cache_status Download-layer cache status for the selected resource.
    @param schema_path Schema artifact path used for loading.
    @param schema_generated Whether the schema artifact had to be generated during the workflow.
    @param removed_materialized_path Whether the extracted uncompressed working file was removed after loading.
    @param load_result Warehouse load result for the dataset resource.
    @param crosswalk_registration Optional vocabulary crosswalk registration result.
    """

    dataset_id: str
    resource_name: str
    manifest_path: str
    download_cache_status: str
    schema_path: str
    schema_generated: bool
    removed_materialized_path: bool
    load_result: WarehouseLoadResult
    crosswalk_registration: WarehouseCrosswalkRegistrationResult | None = None

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the end-to-end workflow result into a serializable dictionary."""

        return {
            "dataset_id": self.dataset_id,
            "resource_name": self.resource_name,
            "manifest_path": self.manifest_path,
            "download_cache_status": self.download_cache_status,
            "schema_path": self.schema_path,
            "schema_generated": self.schema_generated,
            "removed_materialized_path": self.removed_materialized_path,
            "load_result": self.load_result.to_dict(),
            "crosswalk_registration": None
            if self.crosswalk_registration is None
            else self.crosswalk_registration.to_dict(),
        }


@dataclass(slots=True)
class DatasetPeriodManifestRecord:
    """@notice Capture one warehouse-level period manifest entry for incremental updates.

    @param dataset_type Stable logical dataset family such as `cig`.
    @param period Period identifier in `YYYY_MM` format.
    @param dataset_id CKAN dataset slug that exposed the resource.
    @param resource_name CKAN resource name for the period slice.
    @param manifest_path Local raw manifest path backing the downloaded period.
    @param parquet_path Local Parquet slice path currently registered for the period.
    @param resource_id CKAN resource identifier when known.
    @param resource_url Direct CKAN resource URL when known.
    @param remote_modified CKAN last-modified timestamp used for refresh detection.
    @param remote_size CKAN size metadata used for refresh detection.
    @param content_checksum Deterministic checksum of the downloaded period content.
    @param row_count Current row count for the Parquet slice.
    @param imported_at ISO timestamp for the first successful import of the period.
    @param refreshed_at ISO timestamp for the latest successful refresh of the period.
    """

    dataset_type: str
    period: str
    dataset_id: str
    resource_name: str
    manifest_path: str
    parquet_path: str
    resource_id: str | None = None
    resource_url: str | None = None
    remote_modified: str | None = None
    remote_size: int | None = None
    content_checksum: str | None = None
    row_count: int | None = None
    imported_at: str | None = None
    refreshed_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the warehouse period manifest entry into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class DatasetUpdatePlanItem:
    """@notice Describe one planned incremental-update decision for a dataset period.

    @param dataset_type Stable logical dataset family such as `cig`.
    @param period Period identifier in `YYYY_MM` format.
    @param dataset_id CKAN dataset slug that exposed the resource.
    @param resource_name CKAN resource name associated with the period.
    @param action Planned action such as `download`, `refresh`, or `skip`.
    @param reason Short explanation of why the planner chose the action.
    @param remote_modified CKAN last-modified timestamp currently reported by CKAN.
    @param remote_size CKAN size metadata currently reported by CKAN.
    @param manifest_path Existing local manifest path when the period is already cataloged.
    @param parquet_path Existing local Parquet path when the period is already cataloged.
    @param content_checksum Existing stored content checksum when available locally.
    """

    dataset_type: str
    period: str
    dataset_id: str
    resource_name: str
    action: str
    reason: str
    remote_modified: str | None = None
    remote_size: int | None = None
    manifest_path: str | None = None
    parquet_path: str | None = None
    content_checksum: str | None = None

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the incremental update plan item into a serializable dictionary."""

        return asdict(self)


@dataclass(slots=True)
class DatasetIncrementalUpdateResult:
    """@notice Capture the result of an incremental multi-period warehouse sync.

    @param dataset_type Stable logical dataset family such as `cig`.
    @param dataset_id CKAN dataset slug inspected for remote period resources.
    @param selection_mode High-level planning mode such as `forward`, `bootstrap`, `explicit`, or `range`.
    @param latest_local_period Newest locally imported period before planning, when any.
    @param requested_periods Periods considered by the planner in this run.
    @param plan Planned actions for all candidate periods.
    @param applied_loads Periods that were actually downloaded/loaded during this run.
    @param period_manifest Current warehouse period catalog after the sync.
    @param duckdb_path Path to the DuckDB catalog database.
    @param crosswalk_registration Optional vocabulary crosswalk registration result.
    """

    dataset_type: str
    dataset_id: str
    selection_mode: str
    latest_local_period: str | None
    requested_periods: list[str] = field(default_factory=list)
    plan: list[DatasetUpdatePlanItem] = field(default_factory=list)
    applied_loads: list[DatasetParquetDownloadResult] = field(default_factory=list)
    period_manifest: list[DatasetPeriodManifestRecord] = field(default_factory=list)
    duckdb_path: str = ""
    crosswalk_registration: WarehouseCrosswalkRegistrationResult | None = None

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the incremental sync result into a serializable dictionary."""

        return {
            "dataset_type": self.dataset_type,
            "dataset_id": self.dataset_id,
            "selection_mode": self.selection_mode,
            "latest_local_period": self.latest_local_period,
            "requested_periods": self.requested_periods,
            "plan": [item.to_dict() for item in self.plan],
            "applied_loads": [load.to_dict() for load in self.applied_loads],
            "period_manifest": [record.to_dict() for record in self.period_manifest],
            "duckdb_path": self.duckdb_path,
            "crosswalk_registration": None
            if self.crosswalk_registration is None
            else self.crosswalk_registration.to_dict(),
        }


@dataclass(slots=True)
class WarehouseIntegrityIssue:
    """@notice Describe one integrity issue detected in the local warehouse.

    @param check_name Stable name of the check that emitted the issue.
    @param severity Issue severity such as `error` or `warning`.
    @param code Stable machine-readable issue code.
    @param message Human-readable explanation of the integrity problem.
    @param details Additional machine-readable context attached to the issue.
    """

    check_name: str
    severity: str
    code: str
    message: str
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the integrity issue into a serializable dictionary."""

        return {
            "check_name": self.check_name,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "details": _to_json_compatible(self.details),
        }


@dataclass(slots=True)
class WarehouseIntegrityCheckResult:
    """@notice Capture the result of one warehouse integrity check.

    @param check_name Stable machine-readable check name.
    @param status High-level outcome such as `passed`, `warning`, or `failed`.
    @param metrics Optional machine-readable metrics emitted by the check.
    @param issues Errors and warnings emitted by the check.
    """

    check_name: str
    status: str
    metrics: dict[str, object] = field(default_factory=dict)
    issues: list[WarehouseIntegrityIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the check result into a serializable dictionary."""

        error_count = sum(1 for issue in self.issues if issue.severity == "error")
        warning_count = sum(1 for issue in self.issues if issue.severity == "warning")
        return {
            "check_name": self.check_name,
            "status": self.status,
            "metrics": _to_json_compatible(self.metrics),
            "error_count": error_count,
            "warning_count": warning_count,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(slots=True)
class WarehouseIntegrityReport:
    """@notice Capture the full integrity-validation result for the local warehouse.

    @param db_path Path to the DuckDB warehouse database that was validated.
    @param dataset_type Dataset family currently validated, such as `cig`.
    @param schema_path Optional schema artifact used for schema validation.
    @param vocabulary_index_path Optional vocabulary index used for referential checks.
    @param checked_at ISO timestamp when validation completed.
    @param checks Ordered list of executed integrity checks.
    """

    db_path: str
    dataset_type: str
    schema_path: str | None
    vocabulary_index_path: str | None
    checked_at: str
    checks: list[WarehouseIntegrityCheckResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the integrity report into a serializable dictionary."""

        failed_checks = sum(1 for check in self.checks if check.status == "failed")
        warning_checks = sum(1 for check in self.checks if check.status == "warning")
        passed_checks = sum(1 for check in self.checks if check.status == "passed")
        error_count = sum(
            1 for check in self.checks for issue in check.issues if issue.severity == "error"
        )
        warning_count = sum(
            1 for check in self.checks for issue in check.issues if issue.severity == "warning"
        )
        overall_status = "passed"
        if failed_checks:
            overall_status = "failed"
        elif warning_checks:
            overall_status = "warning"
        return {
            "db_path": self.db_path,
            "dataset_type": self.dataset_type,
            "schema_path": self.schema_path,
            "vocabulary_index_path": self.vocabulary_index_path,
            "checked_at": self.checked_at,
            "overall_status": overall_status,
            "total_checks": len(self.checks),
            "passed_checks": passed_checks,
            "warning_checks": warning_checks,
            "failed_checks": failed_checks,
            "error_count": error_count,
            "warning_count": warning_count,
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(slots=True)
class WarehouseQueryResult:
    """@notice Capture one JSON-friendly query result from the local DuckDB warehouse.

    @param db_path Path to the DuckDB database that served the query.
    @param sql_query SQL query text that was executed.
    @param row_limit Maximum number of rows returned, or zero when unlimited.
    @param column_names Ordered result column names.
    @param row_count Number of rows returned to the caller.
    @param rows JSON-friendly result rows keyed by column name.
    """

    db_path: str
    sql_query: str
    row_limit: int
    column_names: list[str] = field(default_factory=list)
    row_count: int = 0
    rows: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the warehouse query result into a serializable dictionary."""

        return {
            "db_path": self.db_path,
            "sql_query": self.sql_query,
            "row_limit": self.row_limit,
            "column_names": self.column_names,
            "row_count": self.row_count,
            "rows": [
                {key: _to_json_compatible(value) for key, value in row.items()}
                for row in self.rows
            ],
        }


def _to_json_compatible(value: object) -> object:
    """@notice Convert richer Python scalars into JSON-compatible values."""

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_json_compatible(item) for item in value]
    return value
