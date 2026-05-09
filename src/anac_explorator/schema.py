"""@notice CSV schema inspection utilities for ANAC source files.

@dev The inference is intentionally pragmatic: it keeps raw column names intact,
infers lightweight scalar types from sampled values, and treats `flag*` fields
as booleans without misclassifying numeric code columns seen in the live CIG
January 2025 sample.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from anac_explorator.models import SchemaColumn, SchemaMapping

_BOOLEAN_VALUES = {"0", "1", "false", "true", "no", "yes"}
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y")
_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z")
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
class _ColumnAccumulator:
    """@notice Track sampled values while one CSV column is being inspected."""

    name: str
    saw_empty: bool = False
    values: list[str] = field(default_factory=list)

    def record(self, value: str) -> None:
        """@notice Add a sampled value to the accumulator."""

        if value == "":
            self.saw_empty = True
            return
        if len(self.values) < 5:
            self.values.append(value)


def map_csv_schema(
    csv_path: str | Path,
    *,
    delimiter: str = ";",
    encoding: str = "utf-8-sig",
    sample_limit: int = 2_000,
) -> SchemaMapping:
    """@notice Inspect a CSV file and derive a lightweight schema map.

    @param csv_path Path to the CSV file to inspect.
    @param delimiter CSV delimiter expected in the source file.
    @param encoding Text encoding used to read the file.
    @param sample_limit Maximum number of data rows to inspect. Values less than
        or equal to zero scan the full file.
    @return Ordered schema description with type inference and representative values.
    """

    source_path = Path(csv_path)
    max_rows = sample_limit if sample_limit > 0 else None
    with source_path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        header = next(reader)
        accumulators = [_ColumnAccumulator(name=column_name) for column_name in header]
        row_length_mismatches = 0
        rows_sampled = 0

        for row in reader:
            rows_sampled += 1
            if len(row) != len(accumulators):
                row_length_mismatches += 1

            padded_row = row[: len(accumulators)] + [""] * max(0, len(accumulators) - len(row))
            for accumulator, value in zip(accumulators, padded_row):
                accumulator.record(value.strip())

            if max_rows is not None and rows_sampled >= max_rows:
                break

    columns = [
        SchemaColumn(
            name=accumulator.name,
            inferred_type=_infer_type(accumulator.name, accumulator.values),
            nullable=accumulator.saw_empty,
            non_empty_samples=accumulator.values,
        )
        for accumulator in accumulators
    ]
    return SchemaMapping(
        source_path=str(source_path),
        delimiter=delimiter,
        encoding=encoding,
        rows_sampled=rows_sampled,
        row_length_mismatches=row_length_mismatches,
        columns=columns,
    )


def _infer_type(column_name: str, values: list[str]) -> str:
    """@notice Infer a pragmatic scalar type from sampled string values."""

    if not values:
        return "unknown"
    if _is_text_identifier_column(column_name):
        return "text"
    if _is_boolean_candidate(column_name, values):
        return "boolean"
    if _all_match(values, _is_integer):
        return "integer"
    if _all_match(values, _is_decimal):
        return "decimal"
    if _all_match(values, _is_datetime):
        return "datetime"
    if _all_match(values, _is_date):
        return "date"
    return "text"


def _is_text_identifier_column(column_name: str) -> bool:
    """@notice Detect identifier columns that must remain text even when they look numeric."""

    normalized_name = column_name.casefold()
    return normalized_name in _TEXT_IDENTIFIER_COLUMNS or normalized_name.startswith("cf_")


def _all_match(values: list[str], predicate: Callable[[str], bool]) -> bool:
    """@notice Return whether all values satisfy a predicate."""

    return all(predicate(value) for value in values)


def _is_boolean(value: str) -> bool:
    """@notice Detect common boolean-like ANAC values."""

    return value.lower() in _BOOLEAN_VALUES


def _is_boolean_candidate(column_name: str, values: list[str]) -> bool:
    """@notice Decide whether a column should be treated as boolean-like."""

    if not _all_match(values, _is_boolean):
        return False

    normalized_values = {value.lower() for value in values}
    textual_booleans = normalized_values.intersection({"true", "false", "yes", "no"})
    if textual_booleans:
        return True

    return column_name.lower().startswith("flag")


def _is_integer(value: str) -> bool:
    """@notice Detect integer-like values."""

    try:
        int(value)
        return True
    except ValueError:
        return False


def _is_decimal(value: str) -> bool:
    """@notice Detect decimal-like values using a stable parser."""

    try:
        Decimal(value)
        return True
    except InvalidOperation:
        return False


def _is_date(value: str) -> bool:
    """@notice Detect date values against the supported date formats."""

    return _matches_formats(value, _DATE_FORMATS)


def _is_datetime(value: str) -> bool:
    """@notice Detect datetime values against the supported datetime formats."""

    return _matches_formats(value, _DATETIME_FORMATS)


def _matches_formats(value: str, formats: tuple[str, ...]) -> bool:
    """@notice Check whether a value matches at least one datetime format."""

    for candidate in formats:
        try:
            datetime.strptime(value, candidate)
            return True
        except ValueError:
            continue
    return False
