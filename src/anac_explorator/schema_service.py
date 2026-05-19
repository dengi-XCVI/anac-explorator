"""@notice Local schema-resolution and semantic describe helpers for the Phase 3 schema surface."""

from __future__ import annotations

from dataclasses import dataclass
import duckdb
from pathlib import Path
import re
from typing import TYPE_CHECKING, Sequence

from anac_explorator.catalog import DATASET_FAMILY_REGISTRY
from anac_explorator.comparison import compare_schema_mappings, load_schema_mapping
from anac_explorator.errors import CliCommandError
from anac_explorator.metadata_views import ensure_metadata_views, load_data_dictionary_artifact, load_data_dictionary_artifacts
from anac_explorator.models import (
    DataDictionaryArtifact,
    SchemaInspectionColumn,
    SchemaInspectionResult,
    SchemaInspectionTarget,
    SchemaMapping,
)

if TYPE_CHECKING:
    from anac_explorator.catalog import DatasetFamilyRegistry


@dataclass(frozen=True, slots=True)
class _LocalSchemaArtifact:
    """@notice Capture one local schema artifact plus its optional dictionary overlay."""

    dataset: str
    target: str
    schema_path: Path
    dictionary_path: Path | None = None


def inspect_schema(
    dataset: str,
    *,
    target: str | None = None,
    describe: bool = False,
    schemas_dir: str | Path = "schemas",
    dictionaries_dir: str | Path = "dictionaries",
    registry: "DatasetFamilyRegistry | None" = None,
) -> SchemaInspectionResult:
    """@notice Resolve one local schema artifact and optionally enrich it with dictionary metadata."""

    resolved_target = resolve_schema_target(
        dataset,
        target=target,
        schemas_dir=schemas_dir,
        dictionaries_dir=dictionaries_dir,
        registry=registry,
    )
    schema_mapping = _load_schema_artifact(Path(resolved_target.schema_path), dataset=dataset, target=resolved_target.requested)
    dictionary_artifact = None
    if describe and resolved_target.dictionary_path is not None:
        dictionary_artifact = load_data_dictionary_artifact(resolved_target.dictionary_path)
    mode = "canonical" if resolved_target.requested == "canonical" else "target"
    return SchemaInspectionResult(
        dataset=dataset,
        mode=mode,
        target=None if mode == "canonical" else resolved_target,
        columns=describe_schema_columns(schema_mapping, dictionary_artifact=dictionary_artifact),
        diff=None,
        ddl=None,
    )


def inspect_schema_ddl(
    dataset: str,
    *,
    db_path: str | Path = "data/warehouse/anac.duckdb",
    registry: "DatasetFamilyRegistry | None" = None,
) -> SchemaInspectionResult:
    """@notice Resolve the registered DuckDB view SQL for one logical dataset family."""

    active_registry = DATASET_FAMILY_REGISTRY if registry is None else registry
    family = active_registry.get_family(dataset)
    view_name = family.query_view_name
    if view_name in (None, ""):
        raise _warehouse_schema_not_available(
            dataset=dataset,
            db_path=Path(db_path),
            view_name=None,
            available_views=[],
        )

    database_path = Path(db_path)
    if not database_path.exists():
        raise _warehouse_schema_not_available(
            dataset=dataset,
            db_path=database_path,
            view_name=view_name,
            available_views=[],
        )

    connection = duckdb.connect(str(database_path))
    try:
        ensure_metadata_views(connection, db_path=database_path)
        row = connection.execute(
            """
            SELECT view_name, view_sql
            FROM anac_registered_views
            WHERE view_name = ? OR table_name = ?
            ORDER BY CASE WHEN view_name = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            [view_name, view_name, view_name],
        ).fetchone()
        if row is None or row[1] in (None, ""):
            available_views = [
                str(value[0])
                for value in connection.execute(
                    "SELECT view_name FROM anac_registered_views ORDER BY view_name"
                ).fetchall()
            ]
            raise _warehouse_schema_not_available(
                dataset=dataset,
                db_path=database_path,
                view_name=view_name,
                available_views=available_views,
            )
        resolved_view_name, view_sql = row
    finally:
        connection.close()

    return SchemaInspectionResult(
        dataset=dataset,
        mode="ddl",
        target=SchemaInspectionTarget(
            requested="ddl",
            resolved=str(resolved_view_name),
            schema_path=str(database_path),
            dictionary_path=None,
        ),
        columns=[],
        diff=None,
        ddl=str(view_sql),
    )


def diff_schema_targets(
    dataset: str,
    *,
    left_target: str | None,
    right_target: str | None,
    schemas_dir: str | Path = "schemas",
    dictionaries_dir: str | Path = "dictionaries",
    registry: "DatasetFamilyRegistry | None" = None,
) -> SchemaInspectionResult:
    """@notice Compare two local schema targets by reusing the existing schema diff utility."""

    active_registry = DATASET_FAMILY_REGISTRY if registry is None else registry
    left = resolve_schema_target(
        dataset,
        target=left_target,
        schemas_dir=schemas_dir,
        dictionaries_dir=dictionaries_dir,
        registry=active_registry,
    )
    right = resolve_schema_target(
        dataset,
        target=right_target,
        schemas_dir=schemas_dir,
        dictionaries_dir=dictionaries_dir,
        registry=active_registry,
    )
    left_schema = _load_schema_artifact(Path(left.schema_path), dataset=dataset, target=left.requested)
    right_schema = _load_schema_artifact(Path(right.schema_path), dataset=dataset, target=right.requested)
    comparison = compare_schema_mappings(left_schema, right_schema)
    return SchemaInspectionResult(
        dataset=dataset,
        mode="diff",
        target=None,
        columns=[],
        diff={
            "left_target": left.to_dict(),
            "right_target": right.to_dict(),
            **comparison,
        },
        ddl=None,
    )


def resolve_schema_target(
    dataset: str,
    *,
    target: str | None = None,
    schemas_dir: str | Path = "schemas",
    dictionaries_dir: str | Path = "dictionaries",
    registry: "DatasetFamilyRegistry | None" = None,
) -> SchemaInspectionTarget:
    """@notice Resolve one logical family and optional target token to a local schema artifact."""

    active_registry = DATASET_FAMILY_REGISTRY if registry is None else registry
    family = active_registry.get_family(dataset)
    requested_target = _normalize_target_token(target)
    if requested_target != "canonical" and family.coverage_kind == "snapshot":
        raise CliCommandError(
            "DATASET_NOT_SUPPORTED",
            f"Temporal schema selection is not supported for snapshot dataset family {dataset!r}.",
            details={"dataset": dataset, "target": requested_target},
        )

    artifacts = _load_local_schema_artifacts(
        schemas_dir=schemas_dir,
        dictionaries_dir=dictionaries_dir,
        registry=active_registry,
    )
    matching_artifacts = [artifact for artifact in artifacts if artifact.dataset == dataset]
    if not matching_artifacts:
        raise _schema_not_available(
            dataset=dataset,
            target=requested_target,
            schemas_dir=Path(schemas_dir),
            available_targets=[],
        )

    if requested_target == "canonical":
        resolved_artifact = _select_canonical_artifact(matching_artifacts)
    elif _YEAR_TOKEN.fullmatch(requested_target):
        resolved_artifact = _select_year_artifact(dataset, requested_target, matching_artifacts, schemas_dir=Path(schemas_dir))
    else:
        resolved_artifact = next((artifact for artifact in matching_artifacts if artifact.target == requested_target), None)
        if resolved_artifact is None:
            raise _schema_not_available(
                dataset=dataset,
                target=requested_target,
                schemas_dir=Path(schemas_dir),
                available_targets=sorted(artifact.target for artifact in matching_artifacts),
            )

    return SchemaInspectionTarget(
        requested=requested_target,
        resolved=resolved_artifact.target,
        schema_path=str(resolved_artifact.schema_path),
        dictionary_path=None if resolved_artifact.dictionary_path is None else str(resolved_artifact.dictionary_path),
    )


def describe_schema_columns(
    schema_mapping: SchemaMapping,
    *,
    dictionary_artifact: DataDictionaryArtifact | None = None,
) -> list[SchemaInspectionColumn]:
    """@notice Join raw schema columns with optional data-dictionary metadata."""

    dictionary_by_field = {}
    if dictionary_artifact is not None:
        dictionary_by_field = {entry.name: entry for entry in dictionary_artifact.entries}

    columns: list[SchemaInspectionColumn] = []
    for ordinal_position, column in enumerate(schema_mapping.columns, start=1):
        dictionary_entry = dictionary_by_field.get(column.name)
        code_reference = None if dictionary_entry is None else dictionary_entry.code_reference
        columns.append(
            SchemaInspectionColumn(
                name=column.name,
                ordinal_position=ordinal_position,
                inferred_type=column.inferred_type,
                duckdb_type=_duckdb_type_for_inferred_type(column.inferred_type),
                nullable=column.nullable,
                section=None if dictionary_entry is None else dictionary_entry.section,
                description=None if dictionary_entry is None else dictionary_entry.description,
                semantic_type=None if dictionary_entry is None else dictionary_entry.semantic_type,
                value_pattern=None if dictionary_entry is None else dictionary_entry.value_pattern,
                paired_field=None if dictionary_entry is None else dictionary_entry.paired_field,
                code_meaning_status=None if dictionary_entry is None else dictionary_entry.code_meaning_status,
                external_vocabulary_status=None
                if dictionary_entry is None
                else dictionary_entry.external_vocabulary_status,
                vocabulary_dataset_id=None if code_reference is None else code_reference.dataset_id,
                vocabulary_table=None if code_reference is None else code_reference.table_name,
            )
        )
    return columns


_YEAR_TOKEN = re.compile(r"\d{4}")
_SLICE_TOKEN = re.compile(r"\d{4}-\d{2}")


def _load_local_schema_artifacts(
    *,
    schemas_dir: str | Path,
    dictionaries_dir: str | Path,
    registry: "DatasetFamilyRegistry",
) -> list[_LocalSchemaArtifact]:
    """@notice Index the local schema artifacts and their paired dictionary overlays."""

    schemas_root = Path(schemas_dir)
    if not schemas_root.exists():
        return []

    dictionary_by_schema_name = {
        Path(artifact.source_schema_path).name: artifact
        for artifact in load_data_dictionary_artifacts(dictionaries_dir)
    }
    artifacts: list[_LocalSchemaArtifact] = []
    for schema_path in sorted(schemas_root.glob("*.schema.json")):
        dataset, artifact_target = _infer_artifact_identity(schema_path, registry=registry)
        dictionary_artifact = dictionary_by_schema_name.get(schema_path.name)
        artifacts.append(
            _LocalSchemaArtifact(
                dataset=dataset,
                target=artifact_target,
                schema_path=schema_path,
                dictionary_path=None
                if dictionary_artifact is None
                else Path(dictionaries_dir) / f"{dictionary_artifact.dictionary_name}.dictionary.json",
            )
        )
    return artifacts


def _infer_artifact_identity(schema_path: Path, *, registry: "DatasetFamilyRegistry") -> tuple[str, str]:
    """@notice Infer the logical family and local target token from one schema filename."""

    schema_name = schema_path.name
    monthly_match = re.fullmatch(r"(cig|smartcig)_(\d{4})_(\d{2})\.schema\.json", schema_name)
    if monthly_match is not None:
        return monthly_match.group(1), f"{monthly_match.group(2)}-{monthly_match.group(3)}"
    yearly_match = re.fullmatch(r"(cig|smartcig)_(\d{4})\.schema\.json", schema_name)
    if yearly_match is not None:
        return yearly_match.group(1), yearly_match.group(2)

    dataset_stem = schema_name.removesuffix(".schema.json")
    family = registry.resolve_family_for_dataset_id(dataset_stem)
    if family is not None:
        return family.dataset, "canonical"
    return registry.get_family(dataset_stem).dataset, "canonical"


def _normalize_target_token(target: str | None) -> str:
    """@notice Normalize the supported schema target token forms."""

    if target in (None, "", "canonical"):
        return "canonical"
    normalized = str(target).strip()
    if _YEAR_TOKEN.fullmatch(normalized) or _SLICE_TOKEN.fullmatch(normalized):
        return normalized
    raise ValueError(f"Invalid schema target {target!r}; expected canonical, YYYY, or YYYY-MM.")


def _select_canonical_artifact(artifacts: Sequence[_LocalSchemaArtifact]) -> _LocalSchemaArtifact:
    """@notice Pick the best artifact-driven canonical schema for one family."""

    dictionary_backed = [artifact for artifact in artifacts if artifact.dictionary_path is not None]
    if dictionary_backed:
        return max(dictionary_backed, key=lambda artifact: _artifact_sort_key(artifact.target))

    explicit_canonical = [artifact for artifact in artifacts if artifact.target == "canonical"]
    if explicit_canonical:
        return explicit_canonical[0]
    return max(artifacts, key=lambda artifact: _artifact_sort_key(artifact.target))


def _select_year_artifact(
    dataset: str,
    target: str,
    artifacts: Sequence[_LocalSchemaArtifact],
    *,
    schemas_dir: Path,
) -> _LocalSchemaArtifact:
    """@notice Resolve a year token to an exact yearly artifact or the first local monthly slice."""

    exact_year = next((artifact for artifact in artifacts if artifact.target == target), None)
    if exact_year is not None:
        return exact_year

    monthly_candidates = sorted(
        (artifact for artifact in artifacts if artifact.target.startswith(f"{target}-")),
        key=lambda artifact: artifact.target,
    )
    if monthly_candidates:
        return monthly_candidates[0]

    raise _schema_not_available(
        dataset=dataset,
        target=target,
        schemas_dir=schemas_dir,
        available_targets=sorted(artifact.target for artifact in artifacts),
    )


def _artifact_sort_key(target: str) -> tuple[int, int, int]:
    """@notice Sort artifact targets so canonical defaults can pick the newest local representative."""

    if target == "canonical":
        return (0, 0, 0)
    year_match = _YEAR_TOKEN.fullmatch(target)
    if year_match is not None:
        return (1, int(target), 0)
    slice_match = _SLICE_TOKEN.fullmatch(target)
    if slice_match is not None:
        year_value, month_value = target.split("-", 1)
        return (2, int(year_value), int(month_value))
    return (-1, 0, 0)


def _load_schema_artifact(path: Path, *, dataset: str, target: str) -> SchemaMapping:
    """@notice Load a schema artifact and translate missing-file failures into the stable schema error."""

    try:
        return load_schema_mapping(path)
    except FileNotFoundError as exc:
        raise _schema_not_available(dataset=dataset, target=target, schemas_dir=path.parent, available_targets=[]) from exc


def _duckdb_type_for_inferred_type(inferred_type: str | None) -> str | None:
    """@notice Map one schema-artifact inferred type to its projected DuckDB type."""

    if inferred_type in (None, ""):
        return None
    if inferred_type in {"text", "unknown"}:
        return "VARCHAR"
    if inferred_type == "boolean":
        return "BOOLEAN"
    if inferred_type == "integer":
        return "BIGINT"
    if inferred_type == "decimal":
        return "DECIMAL(38, 9)"
    if inferred_type == "date":
        return "DATE"
    if inferred_type == "datetime":
        return "TIMESTAMP"
    return "VARCHAR"


def _schema_not_available(
    *,
    dataset: str,
    target: str,
    schemas_dir: Path,
    available_targets: Sequence[str],
) -> CliCommandError:
    """@notice Build the stable missing-schema error with useful recovery details."""

    details = {
        "dataset": dataset,
        "target": target,
        "schemas_dir": str(schemas_dir),
        "available_targets": list(available_targets),
    }
    if target == "canonical":
        message = f"No local schema artifact is available for dataset family {dataset!r}."
    else:
        message = f"No local schema artifact is available for dataset family {dataset!r} and target {target!r}."
    return CliCommandError("SCHEMA_NOT_AVAILABLE", message, details=details)


def _warehouse_schema_not_available(
    *,
    dataset: str,
    db_path: Path,
    view_name: str | None,
    available_views: Sequence[str],
) -> CliCommandError:
    """@notice Build the stable missing-DDL error when no registered warehouse view is available."""

    details = {
        "dataset": dataset,
        "db_path": str(db_path),
        "view_name": view_name,
        "available_views": list(available_views),
    }
    if view_name in (None, ""):
        message = f"Dataset family {dataset!r} does not expose a registered warehouse view."
    else:
        message = f"No registered warehouse view SQL is available for dataset family {dataset!r}."
    return CliCommandError("SCHEMA_NOT_AVAILABLE", message, details=details)
