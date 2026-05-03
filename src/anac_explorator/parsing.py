"""@notice Resource parsing helpers for ANAC CSV and JSON files.

@dev Phase 2 needs reusable parser entry points that produce structured Python
objects instead of stopping at raw file download or schema inference.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from anac_explorator.models import ParsedCsvResource, ParsedCsvRow, ParsedJsonResource


def parse_csv_resource(
    csv_path: str | Path,
    *,
    delimiter: str = ";",
    encoding: str = "utf-8-sig",
    row_limit: int = 100,
) -> ParsedCsvResource:
    """@notice Parse a local CSV resource into structured row objects.

    @param csv_path Path to the CSV file to parse.
    @param delimiter CSV delimiter expected in the source file.
    @param encoding Text encoding used to read the file.
    @param row_limit Maximum number of parsed rows to retain in memory. Values
        less than or equal to zero retain all rows.
    @return Parsed CSV resource with row count, field names, and retained rows.
    """

    source_path = Path(csv_path)
    max_rows = row_limit if row_limit > 0 else None
    rows: list[ParsedCsvRow] = []
    row_count = 0
    with source_path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        field_names = list(reader.fieldnames or [])
        for row_count, row in enumerate(reader, start=1):
            if max_rows is None or len(rows) < max_rows:
                rows.append(
                    ParsedCsvRow(
                        row_number=row_count,
                        values={
                            str(key): "" if value is None else value
                            for key, value in row.items()
                            if key is not None
                        },
                    )
                )

    return ParsedCsvResource(
        source_path=str(source_path),
        delimiter=delimiter,
        encoding=encoding,
        field_names=field_names,
        row_count=row_count,
        rows=rows,
    )


def parse_json_resource(
    json_path: str | Path,
    *,
    encoding: str = "utf-8",
    item_limit: int = 25,
) -> ParsedJsonResource:
    """@notice Parse a local JSON resource into a structured document summary.

    @param json_path Path to the JSON file to parse.
    @param encoding Text encoding used to read the file.
    @param item_limit Maximum number of top-level items to retain in the sample.
        Values less than or equal to zero retain all top-level items.
    @return Parsed JSON resource with top-level shape metadata and sample items.
    """

    source_path = Path(json_path)
    payload = json.loads(source_path.read_text(encoding=encoding))

    max_items = item_limit if item_limit > 0 else None
    if isinstance(payload, list):
        sample_items = payload[:max_items] if max_items is not None else payload
        return ParsedJsonResource(
            source_path=str(source_path),
            encoding=encoding,
            top_level_type="array",
            item_count=len(payload),
            payload=payload,
            sample_items=sample_items,
        )

    if isinstance(payload, dict):
        sample_items = [payload] if max_items is None or max_items > 0 else []
        return ParsedJsonResource(
            source_path=str(source_path),
            encoding=encoding,
            top_level_type="object",
            item_count=len(payload),
            payload=payload,
            sample_items=sample_items,
        )

    return ParsedJsonResource(
        source_path=str(source_path),
        encoding=encoding,
        top_level_type=type(payload).__name__,
        item_count=None,
        payload=payload,
        sample_items=[payload],
    )
