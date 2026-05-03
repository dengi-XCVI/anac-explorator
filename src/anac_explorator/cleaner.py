"""@notice Data cleaning helpers for parsed ANAC resources.

@dev Phase 2 prepares records for database loading by centralizing whitespace and
NULL normalization plus explicit scalar coercion.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from anac_explorator.models import (
    CleanedJsonResource,
    CleanedRecord,
    CleanedTabularResource,
    CleaningIssue,
    ParsedCsvResource,
    ParsedJsonResource,
    SchemaMapping,
)

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y")
_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z")
_NULL_MARKERS = {"", "null", "none", "n/a", "nan"}


def clean_csv_resource(
    parsed_resource: ParsedCsvResource,
    *,
    schema_mapping: SchemaMapping | None = None,
    null_markers: set[str] | None = None,
) -> CleanedTabularResource:
    """@notice Clean parsed CSV rows using optional schema-derived type hints.

    @param parsed_resource Parsed CSV resource to clean.
    @param schema_mapping Optional schema mapping whose inferred types drive scalar coercion.
    @param null_markers Optional normalized string markers treated as null values.
    @return Cleaned tabular resource with cleaned records and explicit issues.
    """

    active_null_markers = {marker.casefold() for marker in (null_markers or _NULL_MARKERS)}
    type_hints = _build_type_hints(schema_mapping)
    cleaned_records = []
    for row in parsed_resource.rows:
        cleaned_values = {}
        issues = []
        for field_name, raw_value in row.values.items():
            target_type = type_hints.get(field_name, "text")
            cleaned_value, issue = clean_scalar_value(raw_value, target_type=target_type, null_markers=active_null_markers)
            cleaned_values[field_name] = cleaned_value
            if issue is not None:
                issues.append(CleaningIssue(field_name=field_name, raw_value=raw_value, target_type=target_type, message=issue))

        cleaned_records.append(
            CleanedRecord(
                row_number=row.row_number,
                raw_values=dict(row.values),
                cleaned_values=cleaned_values,
                issues=issues,
            )
        )

    return CleanedTabularResource(
        source_path=parsed_resource.source_path,
        format_name="csv",
        record_count=parsed_resource.row_count,
        cleaned_records=cleaned_records,
        type_hints=type_hints,
    )


def clean_json_resource(
    parsed_resource: ParsedJsonResource,
    *,
    null_markers: set[str] | None = None,
) -> CleanedJsonResource:
    """@notice Clean a parsed JSON document by normalizing scalar leaf values.

    @param parsed_resource Parsed JSON resource to clean.
    @param null_markers Optional normalized string markers treated as null values.
    @return Cleaned JSON resource with normalized payload and any issues observed.
    """

    active_null_markers = {marker.casefold() for marker in (null_markers or _NULL_MARKERS)}
    cleaned_payload = _clean_json_value(parsed_resource.payload, active_null_markers)
    return CleanedJsonResource(
        source_path=parsed_resource.source_path,
        top_level_type=parsed_resource.top_level_type,
        cleaned_payload=cleaned_payload,
        issues=[],
    )


def clean_scalar_value(
    raw_value: object,
    *,
    target_type: str,
    null_markers: set[str],
) -> tuple[object, str | None]:
    """@notice Normalize and coerce one scalar value for database-ready use."""

    if raw_value is None:
        return None, None

    if not isinstance(raw_value, str):
        return raw_value, None

    normalized = raw_value.strip().replace("\ufeff", "")
    if normalized.casefold() in null_markers:
        return None, None

    if target_type in {"text", "unknown"}:
        return normalized, None
    if target_type == "boolean":
        lower_value = normalized.casefold()
        if lower_value in {"1", "true", "yes"}:
            return True, None
        if lower_value in {"0", "false", "no"}:
            return False, None
        return normalized, f"Could not coerce {normalized!r} to boolean."
    if target_type == "integer":
        try:
            return int(normalized), None
        except ValueError:
            return normalized, f"Could not coerce {normalized!r} to integer."
    if target_type == "decimal":
        try:
            return Decimal(normalized), None
        except InvalidOperation:
            return normalized, f"Could not coerce {normalized!r} to decimal."
    if target_type == "date":
        for candidate in _DATE_FORMATS:
            try:
                return datetime.strptime(normalized, candidate).date(), None
            except ValueError:
                continue
        return normalized, f"Could not coerce {normalized!r} to date."
    if target_type == "datetime":
        for candidate in _DATETIME_FORMATS:
            try:
                return datetime.strptime(normalized, candidate), None
            except ValueError:
                continue
        return normalized, f"Could not coerce {normalized!r} to datetime."

    return normalized, None


def _build_type_hints(schema_mapping: SchemaMapping | None) -> dict[str, str]:
    """@notice Convert a schema artifact into a simple field-to-type mapping."""

    if schema_mapping is None:
        return {}
    return {column.name: column.inferred_type for column in schema_mapping.columns}


def _clean_json_value(value: object, null_markers: set[str]) -> object:
    """@notice Recursively normalize scalar JSON leaf values."""

    if isinstance(value, dict):
        return {str(key): _clean_json_value(item, null_markers) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_json_value(item, null_markers) for item in value]
    if isinstance(value, str):
        cleaned_value, _ = clean_scalar_value(value, target_type="text", null_markers=null_markers)
        return cleaned_value
    return value
