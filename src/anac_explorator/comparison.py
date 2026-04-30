"""@notice Schema comparison helpers for cross-year CIG analysis.

@dev This module supports the next research step after the completed monthly
schema mapping task: identifying how column presence, types, and nullability
change across years.
"""

from __future__ import annotations

import json
from pathlib import Path

from anac_explorator.models import SchemaMapping


def load_schema_mapping(path: str | Path) -> SchemaMapping:
    """@notice Load a serialized schema-mapping artifact from disk."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Schema artifact at {path} did not contain a JSON object.")
    return SchemaMapping.from_dict(payload)


def compare_schema_mappings(left: SchemaMapping, right: SchemaMapping) -> dict[str, object]:
    """@notice Compare two schema mappings and summarize their differences."""

    left_columns = {column.name: column for column in left.columns}
    right_columns = {column.name: column for column in right.columns}

    left_names = set(left_columns)
    right_names = set(right_columns)
    shared_names = sorted(left_names & right_names)

    type_changes = []
    nullable_changes = []
    for name in shared_names:
        left_column = left_columns[name]
        right_column = right_columns[name]
        if left_column.inferred_type != right_column.inferred_type:
            type_changes.append(
                {
                    "name": name,
                    "left_type": left_column.inferred_type,
                    "right_type": right_column.inferred_type,
                }
            )
        if left_column.nullable != right_column.nullable:
            nullable_changes.append(
                {
                    "name": name,
                    "left_nullable": left_column.nullable,
                    "right_nullable": right_column.nullable,
                }
            )

    return {
        "left_source_path": left.source_path,
        "right_source_path": right.source_path,
        "left_column_count": len(left.columns),
        "right_column_count": len(right.columns),
        "shared_column_count": len(shared_names),
        "left_only_columns": sorted(left_names - right_names),
        "right_only_columns": sorted(right_names - left_names),
        "type_changes": type_changes,
        "nullable_changes": nullable_changes,
    }
