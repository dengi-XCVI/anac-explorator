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
from anac_explorator.comparison import load_schema_mapping
from anac_explorator.sample import DownloadedCsvResource, download_dataset_csv_resource
from anac_explorator.schema import map_csv_schema

DEFAULT_CURRENT_CIG_SCHEMA_PATH = "schemas/cig_2025_01.schema.json"


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
        "source_code_field": "cod_tipo_scelta_contraente",
        "source_label_field": "tipo_scelta_contraente",
        "target_code_field": "code",
        "target_label_field": "label",
        "code_meaning_status": "resolved_external",
        "external_vocabulary_status": "resolved",
        "resolved_fields": ["cod_tipo_scelta_contraente", "tipo_scelta_contraente"],
        "join_contract": {
            "target_dataset": "bandi-cig-tipo-scelta-contraente",
            "target_table": "tipo_scelta_contraente",
            "source_field": "cod_tipo_scelta_contraente",
            "target_field": "code",
            "target_label_field": "label",
            "join_type": "left",
        },
        "notes": "Directly resolves a code/label pair already present in the January 2025 CIG schema.",
    },
    {
        "scope": "current_cig_schema",
        "dataset_id": "bandi-cig-modalita-realizzazione",
        "table_name": "modalita_realizzazione",
        "source_code_field": "cod_modalita_realizzazione",
        "source_label_field": "modalita_realizzazione",
        "target_code_field": "code",
        "target_label_field": "label",
        "code_meaning_status": "resolved_external",
        "external_vocabulary_status": "resolved",
        "resolved_fields": ["cod_modalita_realizzazione", "modalita_realizzazione"],
        "join_contract": {
            "target_dataset": "bandi-cig-modalita-realizzazione",
            "target_table": "modalita_realizzazione",
            "source_field": "cod_modalita_realizzazione",
            "target_field": "code",
            "target_label_field": "label",
            "join_type": "left",
        },
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
        "label_field": "MODALITA_INDIZIONE_SPECIALI",
        "notes": "No dedicated controlled vocabulary dataset has been wired yet.",
    },
    {
        "field": "cod_modalita_indizione_servizi",
        "label_field": "MODALITA_INDIZIONE_SERVIZI",
        "notes": "No dedicated controlled vocabulary dataset has been wired yet.",
    },
    {
        "field": "cod_strumento_svolgimento",
        "label_field": "STRUMENTO_SVOLGIMENTO",
        "notes": "No dedicated controlled vocabulary dataset has been wired yet.",
    },
    {
        "field": "cod_motivo_urgenza",
        "label_field": "MOTIVO_URGENZA",
        "notes": "No dedicated controlled vocabulary dataset has been wired yet.",
    },
    {
        "field": "cod_ipotesi_collegamento",
        "label_field": "IPOTESI_COLLEGAMENTO",
        "notes": "No dedicated controlled vocabulary dataset has been wired yet.",
    },
    {
        "field": "cod_esito",
        "label_field": "ESITO",
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
    current_schema_path: str | Path | None = DEFAULT_CURRENT_CIG_SCHEMA_PATH,
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
        artifact_path = vocabularies_path / f"{dataset_id}.json"
        downloaded = _load_cached_downloaded_resource(config, Path(data_dir), artifact_path)
        if downloaded is None:
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
        "code_meaning_status_taxonomy": {
            "resolved_external": "Field values can be joined to a dedicated external vocabulary artifact.",
            "resolved_inline": "Field values are decoded by a sibling label field already present in the CIG extract.",
            "missing_dataset": "Values look coded, but neither an external vocabulary nor an inline label is available yet.",
            "free_text": "Values behave like natural language and no controlled vocabulary is expected.",
            "not_coded": "The field is structured but not a controlled-vocabulary code.",
            "unknown": "The field could not be classified confidently from the current evidence.",
        },
        "field_links": CURRENT_FIELD_LINKS,
        "current_cig_schema_gaps": _build_current_cig_schema_gap_entries(current_schema_path),
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
        "normalization": _build_table_normalization_summary(entries),
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


def _build_current_cig_schema_gap_entries(current_schema_path: str | Path | None) -> list[dict[str, object]]:
    """@notice Analyze the current unresolved CIG coded fields from the local January 2025 extract."""

    if current_schema_path is None:
        return [_fallback_gap_entry(config, "Current CIG schema path was not configured.") for config in CURRENT_CIG_SCHEMA_GAPS]

    schema_path = Path(current_schema_path)
    if not schema_path.exists():
        return [
            _fallback_gap_entry(config, f"Current CIG schema artifact `{schema_path}` is not available.")
            for config in CURRENT_CIG_SCHEMA_GAPS
        ]

    source_csv_path = Path(load_schema_mapping(schema_path).source_path)
    if not source_csv_path.exists():
        return [
            _fallback_gap_entry(config, f"Current CIG source CSV `{source_csv_path}` is not available.")
            for config in CURRENT_CIG_SCHEMA_GAPS
        ]

    stats = {
        config["field"]: {
            "values": Counter(),
            "labels": Counter(),
            "pairs": Counter(),
        }
        for config in CURRENT_CIG_SCHEMA_GAPS
    }
    with source_csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            normalized_row = {
                key.casefold(): _clean_cell(value)
                for key, value in row.items()
                if key is not None
            }
            for config in CURRENT_CIG_SCHEMA_GAPS:
                field_name = str(config["field"])
                label_field = str(config["label_field"])
                code_value = normalized_row.get(field_name.casefold(), "")
                label_value = normalized_row.get(label_field.casefold(), "")
                if code_value != "":
                    stats[field_name]["values"][code_value] += 1
                if label_value != "":
                    stats[field_name]["labels"][label_value] += 1
                if code_value != "" and label_value != "":
                    stats[field_name]["pairs"][(code_value, label_value)] += 1

    return [_summarize_gap_entry(config, stats[str(config["field"])]) for config in CURRENT_CIG_SCHEMA_GAPS]


def _load_cached_downloaded_resource(
    config: VocabularyDatasetConfig,
    data_dir: Path,
    artifact_path: Path,
) -> DownloadedCsvResource | None:
    """@notice Reuse an already materialized CSV resource before going back to CKAN."""

    base_output_dir = data_dir / config.dataset_id / config.preferred_resource_name
    extracted_csv_path = base_output_dir / "extracted" / f"{config.preferred_resource_name}.csv"
    plain_csv_path = base_output_dir / f"{config.preferred_resource_name}.csv"

    if extracted_csv_path.exists():
        csv_path = extracted_csv_path
        archive_path = base_output_dir / f"{config.preferred_resource_name}.zip"
    elif plain_csv_path.exists():
        csv_path = plain_csv_path
        archive_path = None
    else:
        return None

    resource_url = ""
    if artifact_path.exists():
        artifact_payload = _load_json_object(artifact_path)
        resource_url = str(artifact_payload.get("resource_url", ""))

    return DownloadedCsvResource(
        dataset_id=config.dataset_id,
        resource_name=config.preferred_resource_name,
        resource_url=resource_url,
        archive_path=None if archive_path is None or not archive_path.exists() else str(archive_path),
        csv_path=str(csv_path),
    )


def _summarize_gap_entry(config: dict[str, object], stats: dict[str, Counter[object]]) -> dict[str, object]:
    """@notice Convert one unresolved coded field analysis into a serializable dictionary."""

    field_name = str(config["field"])
    label_field = str(config["label_field"])
    values = Counter({str(key): int(value) for key, value in stats["values"].items()})
    labels = Counter({str(key): int(value) for key, value in stats["labels"].items()})
    pairs = Counter({(str(key[0]), str(key[1])): int(value) for key, value in stats["pairs"].items()})
    observed_pattern = _classify_observed_pattern(values)
    label_alias_groups = _build_label_alias_groups_from_pairs(pairs)

    if values and pairs:
        code_meaning_status = "resolved_inline" if observed_pattern == "code_like" else "unknown"
        hypothesis = "Likely controlled vocabulary already embedded in the CIG extract; no dedicated external dataset is wired yet."
    elif values and observed_pattern == "code_like":
        code_meaning_status = "missing_dataset"
        hypothesis = "Values look code-like, but no inline label field or external dataset currently resolves them."
    elif values and observed_pattern == "natural_language":
        code_meaning_status = "free_text"
        hypothesis = "Values look like natural language rather than a stable code list."
    else:
        code_meaning_status = "unknown"
        hypothesis = "The current data does not provide enough evidence to classify the field confidently."

    notes = [str(config["notes"])]
    if label_alias_groups:
        notes.append("Inline labels show cosmetic variants, so raw labels should be preserved and normalization should stay evidence-based.")

    return {
        "field": field_name,
        "label_field": label_field,
        "semantic_type": "controlled_vocabulary_code",
        "code_meaning_status": code_meaning_status,
        "external_vocabulary_status": "missing_dataset",
        "observed_pattern": observed_pattern,
        "non_empty_row_count": sum(values.values()),
        "unique_value_count": len(values),
        "unique_values_sample": _sorted_counter_keys(values)[:10],
        "unique_label_count": len(labels),
        "unique_labels_sample": _sorted_counter_keys(labels)[:10],
        "paired_values_sample": [
            {"code": code, "label": label}
            for (code, label), _ in pairs.most_common(10)
        ],
        "normalization": {
            "canonicalization_strategy": "preserve_raw",
            "canonicalization_safe": False,
            "unsafe_rules": [
                {
                    "rule": "heuristic_code_merge",
                    "reason": "No external vocabulary dataset is wired, so canonical concepts cannot be proven yet.",
                }
            ],
            "label_alias_groups": label_alias_groups,
        },
        "hypothesis": hypothesis,
        "notes": notes,
    }


def _fallback_gap_entry(config: dict[str, object], reason: str) -> dict[str, object]:
    """@notice Build a best-effort gap entry when the current CIG sample is unavailable."""

    return {
        "field": str(config["field"]),
        "label_field": str(config["label_field"]),
        "semantic_type": "controlled_vocabulary_code",
        "code_meaning_status": "unknown",
        "external_vocabulary_status": "missing_dataset",
        "observed_pattern": "unknown",
        "non_empty_row_count": 0,
        "unique_value_count": 0,
        "unique_values_sample": [],
        "unique_label_count": 0,
        "unique_labels_sample": [],
        "paired_values_sample": [],
        "normalization": {
            "canonicalization_strategy": "preserve_raw",
            "canonicalization_safe": False,
            "unsafe_rules": [{"rule": "heuristic_code_merge", "reason": reason}],
            "label_alias_groups": [],
        },
        "hypothesis": "The field likely needs further inspection once the current CIG extract is available locally.",
        "notes": [str(config["notes"]), reason],
    }


def _build_table_normalization_summary(entries: list[dict[str, object]]) -> dict[str, object]:
    """@notice Summarize whether simple canonicalization would be safe for one vocabulary table."""

    collisions = []
    by_canonical_code: dict[str, list[dict[str, str]]] = {}
    for entry in entries:
        code = str(entry["code"])
        label = str(entry["label"])
        canonical_code = _normalize_code_by_stripping_leading_zeroes(code)
        by_canonical_code.setdefault(canonical_code, []).append({"code": code, "label": label})

    for canonical_code, variants in by_canonical_code.items():
        unique_variants = {(variant["code"], variant["label"]) for variant in variants}
        if len(unique_variants) > 1:
            collisions.append(
                {
                    "rule": "strip_leading_zeroes",
                    "canonical_code": canonical_code,
                    "variants": sorted(
                        ({"code": code, "label": label} for code, label in unique_variants),
                        key=lambda item: (item["code"], item["label"]),
                    ),
                }
            )

    return {
        "canonicalization_strategy": "preserve_raw",
        "canonicalization_safe": len(collisions) == 0,
        "unsafe_rules": collisions,
        "label_alias_groups": _build_table_label_alias_groups(entries),
    }


def _build_table_label_alias_groups(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    """@notice Identify cosmetic label variants for the same raw code inside a table."""

    labels_by_code: dict[str, set[str]] = {}
    for entry in entries:
        code = str(entry["code"])
        label = str(entry["label"])
        labels_by_code.setdefault(code, set()).add(label)

    alias_groups = []
    for code, labels in sorted(labels_by_code.items(), key=lambda item: _sortable_value(item[0])):
        if len(labels) < 2:
            continue
        normalized_labels = {_normalize_label_text(label) for label in labels}
        if len(normalized_labels) == 1:
            alias_groups.append(
                {
                    "code": code,
                    "normalized_label": next(iter(normalized_labels)),
                    "variants": sorted(labels),
                }
            )
    return alias_groups


def _build_label_alias_groups_from_pairs(
    pairs: Counter[tuple[str, str]],
) -> list[dict[str, object]]:
    """@notice Identify cosmetic inline label variants for the same observed code."""

    labels_by_code: dict[str, set[str]] = {}
    for code, label in pairs:
        labels_by_code.setdefault(code, set()).add(label)

    alias_groups = []
    for code, labels in sorted(labels_by_code.items(), key=lambda item: _sortable_value(item[0])):
        if len(labels) < 2:
            continue
        normalized_labels = {_normalize_label_text(label) for label in labels}
        if len(normalized_labels) == 1:
            alias_groups.append(
                {
                    "code": code,
                    "normalized_label": next(iter(normalized_labels)),
                    "variants": sorted(labels),
                }
            )
    return alias_groups


def _classify_observed_pattern(values: Counter[str]) -> str:
    """@notice Classify whether observed values look coded, textual, mixed, or empty."""

    if not values:
        return "empty"
    value_list = list(values)
    code_like_count = sum(1 for value in value_list if _is_code_like_value(value))
    natural_language_count = sum(1 for value in value_list if _looks_like_natural_language(value))
    if code_like_count == len(value_list):
        return "code_like"
    if natural_language_count == len(value_list):
        return "natural_language"
    return "mixed"


def _is_code_like_value(value: str) -> bool:
    """@notice Return whether a value looks like a compact code rather than prose."""

    candidate = value.strip()
    if candidate == "":
        return False
    decimal_candidate = candidate[:-2] if candidate.endswith(".0") else candidate
    if decimal_candidate.isdigit():
        return True
    return all(character.isalnum() or character in {"_", "-", "."} for character in candidate) and any(
        character.isdigit() for character in candidate
    )


def _looks_like_natural_language(value: str) -> bool:
    """@notice Return whether a value looks like descriptive text."""

    candidate = value.strip()
    if candidate == "":
        return False
    return any(character.isalpha() for character in candidate) and not _is_code_like_value(candidate)


def _normalize_code_by_stripping_leading_zeroes(value: str) -> str:
    """@notice Apply the unsafe zero-stripping rule only for analysis and reporting."""

    candidate = value.strip()
    if candidate == "":
        return candidate
    integer_part, dot, fractional_part = candidate.partition(".")
    stripped_integer = integer_part.lstrip("0") or "0"
    if dot == "":
        return stripped_integer
    return stripped_integer + dot + fractional_part


def _normalize_label_text(value: str) -> str:
    """@notice Normalize labels for cosmetic alias detection without changing raw storage."""

    return " ".join(
        value.replace("’", "'").replace("`", "'").casefold().split()
    )


def _sorted_counter_keys(values: Counter[str]) -> list[str]:
    """@notice Return counter keys sorted with the repository's mixed sort semantics."""

    return sorted(values, key=_sortable_value)


def _get_dataset_config(dataset_id: str) -> VocabularyDatasetConfig:
    """@notice Look up a configured vocabulary dataset or fail loudly."""

    try:
        return VOCABULARY_DATASET_CONFIGS[dataset_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported vocabulary dataset {dataset_id!r}.") from exc


def _load_json_object(path: Path) -> dict[str, object]:
    """@notice Load a JSON object from disk and fail loudly on invalid shapes."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object at {path}.")
    return payload
