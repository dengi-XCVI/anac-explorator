"""@notice Controlled vocabulary builders for ANAC reference datasets.

@dev This module implements the current Phase 1 slice: download the configured
vocabulary datasets, map their raw schemas, and emit normalized cross-reference
artifacts that preserve raw source columns for traceability.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from anac_explorator.ckan import CkanClient
from anac_explorator.sample import DownloadedCsvResource, download_dataset_csv_resource
from anac_explorator.schema import map_csv_schema


@dataclass(frozen=True, slots=True)
class VocabularyTableConfig:
    """@notice Describe one normalized cross-reference table to emit.

    @param name Stable table name used in the generated artifact.
    @param description Human-readable explanation of the table.
    @param source_columns Ordered raw source columns preserved in each entry.
    @param key_columns Columns used to decide whether a row contributes to the table.
    @param code_column Raw source column treated as the main code field.
    @param label_column Raw source column treated as the main label field.
    @param extra_columns Extra source columns retained as attributes on each entry.
    @param resolved_fields Target data fields the table is meant to resolve.
    """

    name: str
    description: str
    source_columns: tuple[str, ...]
    key_columns: tuple[str, ...]
    code_column: str
    label_column: str
    extra_columns: tuple[str, ...] = ()
    resolved_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VocabularyDatasetConfig:
    """@notice Describe how one CKAN dataset should be turned into crosswalk tables.

    @param dataset_id CKAN dataset slug.
    @param preferred_resource_name Exact CSV resource name to choose when needed.
    @param description Human-readable description of the dataset role.
    @param tables Cross-reference tables derived from the dataset.
    """

    dataset_id: str
    preferred_resource_name: str
    description: str
    tables: tuple[VocabularyTableConfig, ...]


VOCABULARY_DATASET_CONFIGS: dict[str, VocabularyDatasetConfig] = {
    "bandi-cig-tipo-scelta-contraente": VocabularyDatasetConfig(
        dataset_id="bandi-cig-tipo-scelta-contraente",
        preferred_resource_name="bandi-cig-tipo-scelta-contraente_csv",
        description="Controlled vocabulary for CIG procedure-choice codes.",
        tables=(
            VocabularyTableConfig(
                name="tipo_scelta_contraente",
                description="Crosswalk from CIG procedure-choice code to Italian label.",
                source_columns=(
                    "tipo-scelta-contraente_codice",
                    "tipo-scelta-contraente_denominazione",
                ),
                key_columns=("tipo-scelta-contraente_codice",),
                code_column="tipo-scelta-contraente_codice",
                label_column="tipo-scelta-contraente_denominazione",
                resolved_fields=("cod_tipo_scelta_contraente", "tipo_scelta_contraente"),
            ),
        ),
    ),
    "bandi-cig-modalita-realizzazione": VocabularyDatasetConfig(
        dataset_id="bandi-cig-modalita-realizzazione",
        preferred_resource_name="bandi-cig-modalita-realizzazione_csv",
        description="Controlled vocabulary for CIG execution-mode codes.",
        tables=(
            VocabularyTableConfig(
                name="modalita_realizzazione",
                description="Crosswalk from CIG execution-mode code to Italian label.",
                source_columns=(
                    "modalita-realizzazione_codice",
                    "modalita-realizzazione_denominazione",
                ),
                key_columns=("modalita-realizzazione_codice",),
                code_column="modalita-realizzazione_codice",
                label_column="modalita-realizzazione_denominazione",
                resolved_fields=("cod_modalita_realizzazione", "modalita_realizzazione"),
            ),
        ),
    ),
    "categorie-dpcm-aggregazione": VocabularyDatasetConfig(
        dataset_id="categorie-dpcm-aggregazione",
        preferred_resource_name="20260401-categorie-dpcm-aggregazione_csv",
        description="Per-CIG mappings that expose DPCM aggregation categories and derogation codes.",
        tables=(
            VocabularyTableConfig(
                name="categorie_dpcm_aggregazione",
                description="Unique DPCM aggregation category codes observed in the source dataset.",
                source_columns=(
                    "cod_categoria_merceologica_dpcm_aggregazione",
                    "categoria_merceologica_dpcm_aggregazione",
                ),
                key_columns=("cod_categoria_merceologica_dpcm_aggregazione",),
                code_column="cod_categoria_merceologica_dpcm_aggregazione",
                label_column="categoria_merceologica_dpcm_aggregazione",
                resolved_fields=(
                    "cod_categoria_merceologica_dpcm_aggregazione",
                    "categoria_merceologica_dpcm_aggregazione",
                ),
            ),
            VocabularyTableConfig(
                name="deroghe_soggetto_aggregatore",
                description="Unique derogation codes associated with DPCM aggregation obligations.",
                source_columns=(
                    "cod_deroga_soggetto_aggregatore",
                    "deroga_dpcm_soggetto_aggregatore",
                ),
                key_columns=("cod_deroga_soggetto_aggregatore", "deroga_dpcm_soggetto_aggregatore"),
                code_column="cod_deroga_soggetto_aggregatore",
                label_column="deroga_dpcm_soggetto_aggregatore",
                resolved_fields=(
                    "cod_deroga_soggetto_aggregatore",
                    "deroga_dpcm_soggetto_aggregatore",
                ),
            ),
        ),
    ),
    "categorie-opera": VocabularyDatasetConfig(
        dataset_id="categorie-opera",
        preferred_resource_name="20260401-categorie-opera_csv",
        description="Per-CIG mappings for work-category identifiers, type flags, and import-class variants.",
        tables=(
            VocabularyTableConfig(
                name="categorie_opera",
                description="Unique work-category identifiers and labels observed in the source dataset.",
                source_columns=("id_categoria", "descrizione"),
                key_columns=("id_categoria",),
                code_column="id_categoria",
                label_column="descrizione",
                resolved_fields=("id_categoria", "descrizione"),
            ),
            VocabularyTableConfig(
                name="categorie_opera_varianti",
                description="Unique work-category variants including category type and import class.",
                source_columns=(
                    "id_categoria",
                    "descrizione",
                    "cod_tipo_categoria",
                    "descrizione_tipo_categoria",
                    "classe_importo",
                ),
                key_columns=("id_categoria", "descrizione"),
                code_column="id_categoria",
                label_column="descrizione",
                extra_columns=(
                    "cod_tipo_categoria",
                    "descrizione_tipo_categoria",
                    "classe_importo",
                ),
                resolved_fields=(
                    "id_categoria",
                    "descrizione",
                    "cod_tipo_categoria",
                    "descrizione_tipo_categoria",
                    "classe_importo",
                ),
            ),
            VocabularyTableConfig(
                name="tipi_categoria_opera",
                description="Unique category-type codes reused within the work-category dataset.",
                source_columns=("cod_tipo_categoria", "descrizione_tipo_categoria"),
                key_columns=("cod_tipo_categoria",),
                code_column="cod_tipo_categoria",
                label_column="descrizione_tipo_categoria",
                resolved_fields=("cod_tipo_categoria", "descrizione_tipo_categoria"),
            ),
        ),
    ),
    "smartcig-tipo-fattispecie-contrattuale": VocabularyDatasetConfig(
        dataset_id="smartcig-tipo-fattispecie-contrattuale",
        preferred_resource_name="smartcig-tipo-fattispecie-contrattuale_csv",
        description="Controlled vocabulary for SMARTCIG contract-type identifiers.",
        tables=(
            VocabularyTableConfig(
                name="tipo_fattispecie_contrattuale",
                description="Crosswalk from SMARTCIG contract-type identifier to Italian label.",
                source_columns=(
                    "tipo-fattispecie-contrattuale_id",
                    "tipo-fattispecie-contrattuale_denominazione",
                ),
                key_columns=("tipo-fattispecie-contrattuale_id",),
                code_column="tipo-fattispecie-contrattuale_id",
                label_column="tipo-fattispecie-contrattuale_denominazione",
                resolved_fields=(
                    "tipo_fattispecie_contrattuale_id",
                    "tipo_fattispecie_contrattuale_denominazione",
                ),
            ),
        ),
    ),
}

CURRENT_FIELD_LINKS = [
    {
        "scope": "current_cig_schema",
        "dataset_id": "bandi-cig-tipo-scelta-contraente",
        "table_name": "tipo_scelta_contraente",
        "resolved_fields": ["cod_tipo_scelta_contraente", "tipo_scelta_contraente"],
        "notes": "Directly resolves a code/label pair already present in the January 2025 CIG schema.",
    },
    {
        "scope": "current_cig_schema",
        "dataset_id": "bandi-cig-modalita-realizzazione",
        "table_name": "modalita_realizzazione",
        "resolved_fields": ["cod_modalita_realizzazione", "modalita_realizzazione"],
        "notes": "Directly resolves a code/label pair already present in the January 2025 CIG schema.",
    },
    {
        "scope": "future_or_adjacent_datasets",
        "dataset_id": "categorie-dpcm-aggregazione",
        "table_name": "categorie_dpcm_aggregazione",
        "resolved_fields": [
            "cod_categoria_merceologica_dpcm_aggregazione",
            "categoria_merceologica_dpcm_aggregazione",
        ],
        "notes": "Resolves DPCM aggregation category codes in the dedicated category dataset rather than the current January 2025 CIG schema sample.",
    },
    {
        "scope": "future_or_adjacent_datasets",
        "dataset_id": "categorie-dpcm-aggregazione",
        "table_name": "deroghe_soggetto_aggregatore",
        "resolved_fields": ["cod_deroga_soggetto_aggregatore", "deroga_dpcm_soggetto_aggregatore"],
        "notes": "Resolves derogation codes in the DPCM aggregation dataset.",
    },
    {
        "scope": "future_or_adjacent_datasets",
        "dataset_id": "categorie-opera",
        "table_name": "categorie_opera",
        "resolved_fields": ["id_categoria", "descrizione"],
        "notes": "Resolves work-category identifiers from the category dataset.",
    },
    {
        "scope": "future_or_adjacent_datasets",
        "dataset_id": "categorie-opera",
        "table_name": "categorie_opera_varianti",
        "resolved_fields": [
            "id_categoria",
            "descrizione",
            "cod_tipo_categoria",
            "descrizione_tipo_categoria",
            "classe_importo",
        ],
        "notes": "Preserves category-type and import-class variants attached to work categories.",
    },
    {
        "scope": "future_or_adjacent_datasets",
        "dataset_id": "categorie-opera",
        "table_name": "tipi_categoria_opera",
        "resolved_fields": ["cod_tipo_categoria", "descrizione_tipo_categoria"],
        "notes": "Resolves the category-type codes reused within the work-category dataset.",
    },
    {
        "scope": "future_or_adjacent_datasets",
        "dataset_id": "smartcig-tipo-fattispecie-contrattuale",
        "table_name": "tipo_fattispecie_contrattuale",
        "resolved_fields": [
            "tipo_fattispecie_contrattuale_id",
            "tipo_fattispecie_contrattuale_denominazione",
        ],
        "notes": "Supports SMARTCIG contract-type resolution outside the current CIG schema sample.",
    },
]

CURRENT_CIG_SCHEMA_GAPS = [
    {
        "field": "cod_modalita_indizione_speciali",
        "notes": "No dedicated controlled vocabulary dataset has been wired yet.",
    },
    {
        "field": "cod_modalita_indizione_servizi",
        "notes": "No dedicated controlled vocabulary dataset has been wired yet.",
    },
    {
        "field": "cod_strumento_svolgimento",
        "notes": "No dedicated controlled vocabulary dataset has been wired yet.",
    },
    {
        "field": "cod_motivo_urgenza",
        "notes": "No dedicated controlled vocabulary dataset has been wired yet.",
    },
    {
        "field": "cod_ipotesi_collegamento",
        "notes": "No dedicated controlled vocabulary dataset has been wired yet.",
    },
    {
        "field": "cod_esito",
        "notes": "No dedicated controlled vocabulary dataset has been wired yet.",
    },
]


def build_vocabulary_crosswalks(
    client: CkanClient,
    *,
    dataset_ids: Iterable[str] | None = None,
    data_dir: str | Path = "data/raw",
    schemas_dir: str | Path = "schemas",
    output_dir: str | Path = "vocabularies",
) -> dict[str, object]:
    """@notice Build normalized vocabulary artifacts for the configured datasets."""

    selected_ids = list(dataset_ids) if dataset_ids is not None else list(VOCABULARY_DATASET_CONFIGS)
    schemas_path = Path(schemas_dir)
    vocabularies_path = Path(output_dir)
    schemas_path.mkdir(parents=True, exist_ok=True)
    vocabularies_path.mkdir(parents=True, exist_ok=True)

    artifact_summaries = []
    for dataset_id in selected_ids:
        config = _get_dataset_config(dataset_id)
        downloaded = download_dataset_csv_resource(
            client,
            dataset_id=config.dataset_id,
            preferred_resource_name=config.preferred_resource_name,
            output_dir=Path(data_dir),
        )
        schema = map_csv_schema(downloaded.csv_path, sample_limit=0)
        schema_path = schemas_path / f"{dataset_id}.schema.json"
        schema_path.write_text(json.dumps(schema.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        artifact = _build_dataset_artifact(config, downloaded, schema_path)
        artifact_path = vocabularies_path / f"{dataset_id}.json"
        artifact_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        artifact_summaries.append(
            {
                "dataset_id": dataset_id,
                "resource_name": downloaded.resource_name,
                "csv_path": downloaded.csv_path,
                "schema_path": str(schema_path),
                "artifact_path": str(artifact_path),
                "table_count": len(artifact["tables"]),
            }
        )

    index_payload = {
        "dataset_count": len(artifact_summaries),
        "datasets": artifact_summaries,
        "field_links": CURRENT_FIELD_LINKS,
        "current_cig_schema_gaps": CURRENT_CIG_SCHEMA_GAPS,
    }
    index_path = vocabularies_path / "index.json"
    index_path.write_text(json.dumps(index_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return index_payload


def _build_dataset_artifact(
    config: VocabularyDatasetConfig,
    downloaded: DownloadedCsvResource,
    schema_path: Path,
) -> dict[str, object]:
    """@notice Build the normalized artifact payload for one dataset."""

    tables = _build_tables(config, Path(downloaded.csv_path))
    return {
        "dataset_id": config.dataset_id,
        "description": config.description,
        "resource_name": downloaded.resource_name,
        "resource_url": downloaded.resource_url,
        "csv_path": downloaded.csv_path,
        "archive_path": downloaded.archive_path,
        "schema_path": str(schema_path),
        "tables": tables,
    }


def _build_tables(config: VocabularyDatasetConfig, csv_path: Path) -> list[dict[str, object]]:
    """@notice Build all configured cross-reference tables for one CSV source."""

    counters = {table.name: Counter() for table in config.tables}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            cleaned_row = {key: _clean_cell(value) for key, value in row.items() if key is not None}
            for table in config.tables:
                if not _row_matches_table(cleaned_row, table):
                    continue
                key = tuple(cleaned_row.get(column, "") for column in table.source_columns)
                counters[table.name][key] += 1

    return [_serialize_table(table, counters[table.name]) for table in config.tables]


def _row_matches_table(row: dict[str, str], table: VocabularyTableConfig) -> bool:
    """@notice Return whether a source row contributes to a configured table."""

    return any(row.get(column, "") != "" for column in table.key_columns)


def _serialize_table(
    table: VocabularyTableConfig,
    counter: Counter[tuple[str, ...]],
) -> dict[str, object]:
    """@notice Convert one counter-backed table into a JSON-ready payload."""

    entries = []
    for values, usage_count in sorted(counter.items(), key=lambda item: _entry_sort_key(item[0])):
        raw = dict(zip(table.source_columns, values))
        entries.append(
            {
                "code": raw[table.code_column],
                "label": raw[table.label_column],
                "attributes": {column: raw[column] for column in table.extra_columns},
                "usage_count": usage_count,
                "raw": raw,
            }
        )

    return {
        "name": table.name,
        "description": table.description,
        "resolved_fields": list(table.resolved_fields),
        "source_columns": list(table.source_columns),
        "code_column": table.code_column,
        "label_column": table.label_column,
        "extra_columns": list(table.extra_columns),
        "entry_count": len(entries),
        "entries": entries,
    }


def _entry_sort_key(values: tuple[str, ...]) -> tuple[tuple[int, object], ...]:
    """@notice Produce a stable mixed numeric/string sort key for vocabulary entries."""

    return tuple(_sortable_value(value) for value in values)


def _sortable_value(value: str) -> tuple[int, object]:
    """@notice Convert a string value into a stable sortable representation."""

    if value.isdigit():
        return (0, int(value))
    return (1, value)


def _clean_cell(value: str | None) -> str:
    """@notice Normalize raw CSV cell values while preserving meaningful content."""

    return "" if value is None else value.strip()


def _get_dataset_config(dataset_id: str) -> VocabularyDatasetConfig:
    """@notice Look up a configured vocabulary dataset or fail loudly."""

    try:
        return VOCABULARY_DATASET_CONFIGS[dataset_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported vocabulary dataset {dataset_id!r}.") from exc
