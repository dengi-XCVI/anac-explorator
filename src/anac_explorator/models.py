"""@notice Shared data models for metadata and schema inspection.

@dev These dataclasses back the completed Phase 1 workflow: CKAN metadata
lookup, sample download reporting, and raw CSV schema mapping.
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
