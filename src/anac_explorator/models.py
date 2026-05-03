"""@notice Shared data models for metadata, schema inspection, and dictionary generation.

@dev These dataclasses back the completed Phase 1 workflow: CKAN metadata
lookup, dataset download reporting, raw CSV schema mapping, and data-dictionary
generation.
"""

from __future__ import annotations

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
