"""@notice Data-dictionary builders for the current CIG schema artifacts.

@dev This module implements the current Phase 1 slice: turn the January 2025
CIG schema, cross-year comparison, and vocabulary artifacts into a structured
field dictionary with descriptions, code meanings, and documented gaps.
"""

from __future__ import annotations

import json
from pathlib import Path

from anac_explorator.comparison import load_schema_mapping
from anac_explorator.models import (
    DataDictionaryArtifact,
    DataDictionaryCodeReference,
    DataDictionaryEntry,
    JoinContract,
)


DEFAULT_DICTIONARY_NAME = "cig_2025_01"
DEFAULT_DATASET_ID = "cig-2025"
DEFAULT_SCHEMA_PATH = "schemas/cig_2025_01.schema.json"
DEFAULT_COMPARISON_PATH = "schemas/cig_2007_01_vs_cig_2025_01.comparison.json"
DEFAULT_VOCABULARY_INDEX_PATH = "vocabularies/index.json"
DEFAULT_VOCABULARY_DIR = "vocabularies"
DEFAULT_OUTPUT_DIR = "dictionaries"

FIELD_SECTIONS = {
    "cig": "Identifiers and lot structure",
    "cig_accordo_quadro": "Identifiers and lot structure",
    "numero_gara": "Identifiers and lot structure",
    "oggetto_gara": "Identifiers and lot structure",
    "n_lotti_componenti": "Identifiers and lot structure",
    "oggetto_lotto": "Identifiers and lot structure",
    "importo_complessivo_gara": "Amounts and contract scope",
    "importo_lotto": "Amounts and contract scope",
    "oggetto_principale_contratto": "Amounts and contract scope",
    "settore": "Amounts and contract scope",
    "DURATA_PREVISTA": "Amounts and contract scope",
    "IMPORTO_SICUREZZA": "Amounts and contract scope",
    "TIPO_APPALTO_RISERVATO": "Amounts and contract scope",
    "luogo_istat": "Location and classification",
    "provincia": "Location and classification",
    "cod_cpv": "Location and classification",
    "descrizione_cpv": "Location and classification",
    "flag_prevalente": "Location and classification",
    "data_pubblicazione": "Publication and procedure",
    "data_scadenza_offerta": "Publication and procedure",
    "cod_tipo_scelta_contraente": "Publication and procedure",
    "tipo_scelta_contraente": "Publication and procedure",
    "cod_modalita_realizzazione": "Publication and procedure",
    "modalita_realizzazione": "Publication and procedure",
    "stato": "Publication and procedure",
    "anno_pubblicazione": "Publication and procedure",
    "mese_pubblicazione": "Publication and procedure",
    "codice_ausa": "Contracting authority and cost center",
    "cf_amministrazione_appaltante": "Contracting authority and cost center",
    "denominazione_amministrazione_appaltante": "Contracting authority and cost center",
    "sezione_regionale": "Contracting authority and cost center",
    "id_centro_costo": "Contracting authority and cost center",
    "denominazione_centro_costo": "Contracting authority and cost center",
    "COD_MOTIVO_CANCELLAZIONE": "Cancellation and lifecycle maintenance",
    "MOTIVO_CANCELLAZIONE": "Cancellation and lifecycle maintenance",
    "DATA_CANCELLAZIONE": "Cancellation and lifecycle maintenance",
    "DATA_ULTIMO_PERFEZIONAMENTO": "Cancellation and lifecycle maintenance",
    "COD_MODALITA_INDIZIONE_SPECIALI": "Indiction and execution instruments",
    "MODALITA_INDIZIONE_SPECIALI": "Indiction and execution instruments",
    "COD_MODALITA_INDIZIONE_SERVIZI": "Indiction and execution instruments",
    "MODALITA_INDIZIONE_SERVIZI": "Indiction and execution instruments",
    "COD_STRUMENTO_SVOLGIMENTO": "Indiction and execution instruments",
    "STRUMENTO_SVOLGIMENTO": "Indiction and execution instruments",
    "FLAG_URGENZA": "Urgency and delegated execution",
    "COD_MOTIVO_URGENZA": "Urgency and delegated execution",
    "MOTIVO_URGENZA": "Urgency and delegated execution",
    "FLAG_DELEGA": "Urgency and delegated execution",
    "FUNZIONI_DELEGATE": "Urgency and delegated execution",
    "CF_SA_DELEGANTE": "Urgency and delegated execution",
    "DENOMINAZIONE_SA_DELEGANTE": "Urgency and delegated execution",
    "CF_SA_DELEGATA": "Urgency and delegated execution",
    "DENOMINAZIONE_SA_DELEGATA": "Urgency and delegated execution",
    "CUI_PROGRAMMA": "Planning, linking, and outcome",
    "FLAG_PREV_RIPETIZIONI": "Planning, linking, and outcome",
    "COD_IPOTESI_COLLEGAMENTO": "Planning, linking, and outcome",
    "IPOTESI_COLLEGAMENTO": "Planning, linking, and outcome",
    "CIG_COLLEGAMENTO": "Planning, linking, and outcome",
    "COD_ESITO": "Planning, linking, and outcome",
    "ESITO": "Planning, linking, and outcome",
    "DATA_COMUNICAZIONE_ESITO": "Planning, linking, and outcome",
    "FLAG_PNRR_PNC": "Planning, linking, and outcome",
}

FIELD_DESCRIPTIONS = {
    "cig": "Unique ANAC tender-lot identifier for the current procurement record.",
    "cig_accordo_quadro": "Reference CIG for the parent framework agreement or convention when the current lot derives from one.",
    "numero_gara": "Source procedure identifier for the broader procurement, not necessarily a purely numeric value.",
    "oggetto_gara": "Overall title or subject of the procurement procedure.",
    "importo_complessivo_gara": "Total amount declared for the broader procurement procedure.",
    "n_lotti_componenti": "Number of lots or components declared for the broader procurement.",
    "oggetto_lotto": "Title or subject of the specific lot represented by the current row.",
    "importo_lotto": "Declared amount for the specific lot represented by the current row.",
    "oggetto_principale_contratto": "Main contract object category, such as works, supplies, or services.",
    "stato": "Current record or procedure status reported by the source dataset.",
    "settore": "Regulatory sector of the procurement, such as ordinary or special sectors.",
    "luogo_istat": "ISTAT territorial code for the procurement location.",
    "provincia": "Province name associated with the procurement location.",
    "data_pubblicazione": "Publication date of the ANAC record.",
    "data_scadenza_offerta": "Deadline for submitting offers when the procedure exposes one.",
    "cod_tipo_scelta_contraente": "Code for the contractor-selection procedure used for the lot.",
    "tipo_scelta_contraente": "Italian label for the contractor-selection procedure code.",
    "cod_modalita_realizzazione": "Code for the execution or realization modality of the lot.",
    "modalita_realizzazione": "Italian label for the execution or realization modality code.",
    "codice_ausa": "AUSA identifier of the contracting authority.",
    "cf_amministrazione_appaltante": "Tax code or source identifier of the contracting authority.",
    "denominazione_amministrazione_appaltante": "Name of the contracting authority.",
    "sezione_regionale": "Regional ANAC section associated with the record or authority.",
    "id_centro_costo": "Internal cost-center identifier linked to the procurement.",
    "denominazione_centro_costo": "Name of the cost center linked to the procurement.",
    "anno_pubblicazione": "Publication year extracted from the record.",
    "mese_pubblicazione": "Publication month number extracted from the record.",
    "cod_cpv": "Common Procurement Vocabulary code attached to the lot.",
    "descrizione_cpv": "Italian label for the CPV classification.",
    "flag_prevalente": "Flag indicating whether the listed CPV classification is the principal one.",
    "COD_MOTIVO_CANCELLAZIONE": "Code describing why the procurement record was cancelled, when cancellation applies.",
    "MOTIVO_CANCELLAZIONE": "Italian label for the cancellation reason.",
    "DATA_CANCELLAZIONE": "Date on which the procurement record was cancelled.",
    "DATA_ULTIMO_PERFEZIONAMENTO": "Last date on which the ANAC record was finalized or updated to a perfected state.",
    "COD_MODALITA_INDIZIONE_SPECIALI": "Code for the indiction mode used in special-sector procedures.",
    "MODALITA_INDIZIONE_SPECIALI": "Italian label for the special-sector indiction mode code.",
    "COD_MODALITA_INDIZIONE_SERVIZI": "Code for the indiction mode used in service-related procedure cases.",
    "MODALITA_INDIZIONE_SERVIZI": "Italian label for the service-related indiction mode code.",
    "DURATA_PREVISTA": "Planned contract duration value reported by the source; the exact unit should be confirmed against ANAC domain rules.",
    "COD_STRUMENTO_SVOLGIMENTO": "Code for the instrument or platform used to conduct the procedure.",
    "STRUMENTO_SVOLGIMENTO": "Italian label for the procedure-conduct instrument code.",
    "FLAG_URGENZA": "Flag indicating whether the lot is treated as urgent.",
    "COD_MOTIVO_URGENZA": "Code for the urgency rationale when urgency handling applies.",
    "MOTIVO_URGENZA": "Italian label for the urgency rationale code.",
    "FLAG_DELEGA": "Flag indicating whether the procedure involves delegated procurement functions.",
    "FUNZIONI_DELEGATE": "Description of the procurement functions delegated to another authority or body.",
    "CF_SA_DELEGANTE": "Tax code or identifier of the delegating contracting authority.",
    "DENOMINAZIONE_SA_DELEGANTE": "Name of the delegating contracting authority.",
    "CF_SA_DELEGATA": "Tax code or identifier of the delegated authority or purchasing body.",
    "DENOMINAZIONE_SA_DELEGATA": "Name of the delegated authority or purchasing body.",
    "IMPORTO_SICUREZZA": "Safety-related amount reported separately from the main lot amount.",
    "TIPO_APPALTO_RISERVATO": "Text describing whether participation in the procurement is reserved to specific categories of operators.",
    "CUI_PROGRAMMA": "Program-level CUI identifier linked to planning documents or scheduled interventions.",
    "FLAG_PREV_RIPETIZIONI": "Flag indicating whether repeat or renewal options are foreseen.",
    "COD_IPOTESI_COLLEGAMENTO": "Code for the linkage scenario that connects the lot to another procurement record.",
    "IPOTESI_COLLEGAMENTO": "Italian label for the linkage scenario code.",
    "CIG_COLLEGAMENTO": "Linked CIG referenced by the current procurement when a linkage scenario applies.",
    "COD_ESITO": "Outcome code for the procedure or lot.",
    "ESITO": "Italian label for the recorded procedure outcome.",
    "DATA_COMUNICAZIONE_ESITO": "Date on which the procedure outcome was communicated.",
    "FLAG_PNRR_PNC": "Flag indicating whether the procurement is linked to PNRR or PNC funding or reporting.",
}

RELATED_FIELDS = {
    "cig": ["cig_accordo_quadro", "numero_gara"],
    "cig_accordo_quadro": ["cig"],
    "numero_gara": ["cig", "oggetto_gara", "n_lotti_componenti"],
    "oggetto_gara": ["numero_gara", "oggetto_lotto"],
    "importo_complessivo_gara": ["importo_lotto", "n_lotti_componenti"],
    "n_lotti_componenti": ["numero_gara", "importo_complessivo_gara"],
    "oggetto_lotto": ["oggetto_gara", "importo_lotto"],
    "importo_lotto": ["importo_complessivo_gara", "oggetto_lotto", "IMPORTO_SICUREZZA"],
    "luogo_istat": ["provincia"],
    "provincia": ["luogo_istat"],
    "data_pubblicazione": ["anno_pubblicazione", "mese_pubblicazione", "data_scadenza_offerta"],
    "data_scadenza_offerta": ["data_pubblicazione"],
    "cod_tipo_scelta_contraente": ["tipo_scelta_contraente"],
    "tipo_scelta_contraente": ["cod_tipo_scelta_contraente"],
    "cod_modalita_realizzazione": ["modalita_realizzazione"],
    "modalita_realizzazione": ["cod_modalita_realizzazione"],
    "codice_ausa": ["cf_amministrazione_appaltante", "denominazione_amministrazione_appaltante"],
    "cf_amministrazione_appaltante": ["codice_ausa", "denominazione_amministrazione_appaltante"],
    "denominazione_amministrazione_appaltante": ["cf_amministrazione_appaltante", "codice_ausa"],
    "id_centro_costo": ["denominazione_centro_costo"],
    "denominazione_centro_costo": ["id_centro_costo"],
    "anno_pubblicazione": ["mese_pubblicazione", "data_pubblicazione"],
    "mese_pubblicazione": ["anno_pubblicazione", "data_pubblicazione"],
    "cod_cpv": ["descrizione_cpv", "flag_prevalente"],
    "descrizione_cpv": ["cod_cpv", "flag_prevalente"],
    "flag_prevalente": ["cod_cpv", "descrizione_cpv"],
    "COD_MOTIVO_CANCELLAZIONE": ["MOTIVO_CANCELLAZIONE", "DATA_CANCELLAZIONE"],
    "MOTIVO_CANCELLAZIONE": ["COD_MOTIVO_CANCELLAZIONE", "DATA_CANCELLAZIONE"],
    "DATA_CANCELLAZIONE": ["COD_MOTIVO_CANCELLAZIONE", "MOTIVO_CANCELLAZIONE"],
    "COD_MODALITA_INDIZIONE_SPECIALI": ["MODALITA_INDIZIONE_SPECIALI"],
    "MODALITA_INDIZIONE_SPECIALI": ["COD_MODALITA_INDIZIONE_SPECIALI"],
    "COD_MODALITA_INDIZIONE_SERVIZI": ["MODALITA_INDIZIONE_SERVIZI"],
    "MODALITA_INDIZIONE_SERVIZI": ["COD_MODALITA_INDIZIONE_SERVIZI"],
    "COD_STRUMENTO_SVOLGIMENTO": ["STRUMENTO_SVOLGIMENTO"],
    "STRUMENTO_SVOLGIMENTO": ["COD_STRUMENTO_SVOLGIMENTO"],
    "FLAG_URGENZA": ["COD_MOTIVO_URGENZA", "MOTIVO_URGENZA"],
    "COD_MOTIVO_URGENZA": ["FLAG_URGENZA", "MOTIVO_URGENZA"],
    "MOTIVO_URGENZA": ["FLAG_URGENZA", "COD_MOTIVO_URGENZA"],
    "FLAG_DELEGA": ["FUNZIONI_DELEGATE", "CF_SA_DELEGANTE", "CF_SA_DELEGATA"],
    "FUNZIONI_DELEGATE": ["FLAG_DELEGA", "CF_SA_DELEGANTE", "CF_SA_DELEGATA"],
    "CF_SA_DELEGANTE": ["DENOMINAZIONE_SA_DELEGANTE", "FLAG_DELEGA"],
    "DENOMINAZIONE_SA_DELEGANTE": ["CF_SA_DELEGANTE", "FLAG_DELEGA"],
    "CF_SA_DELEGATA": ["DENOMINAZIONE_SA_DELEGATA", "FLAG_DELEGA"],
    "DENOMINAZIONE_SA_DELEGATA": ["CF_SA_DELEGATA", "FLAG_DELEGA"],
    "CUI_PROGRAMMA": ["FLAG_PREV_RIPETIZIONI"],
    "FLAG_PREV_RIPETIZIONI": ["CUI_PROGRAMMA"],
    "COD_IPOTESI_COLLEGAMENTO": ["IPOTESI_COLLEGAMENTO", "CIG_COLLEGAMENTO"],
    "IPOTESI_COLLEGAMENTO": ["COD_IPOTESI_COLLEGAMENTO", "CIG_COLLEGAMENTO"],
    "CIG_COLLEGAMENTO": ["COD_IPOTESI_COLLEGAMENTO", "IPOTESI_COLLEGAMENTO"],
    "COD_ESITO": ["ESITO", "DATA_COMUNICAZIONE_ESITO"],
    "ESITO": ["COD_ESITO", "DATA_COMUNICAZIONE_ESITO"],
    "DATA_COMUNICAZIONE_ESITO": ["COD_ESITO", "ESITO"],
}


def build_cig_data_dictionary(
    *,
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
    comparison_path: str | Path = DEFAULT_COMPARISON_PATH,
    vocabulary_index_path: str | Path = DEFAULT_VOCABULARY_INDEX_PATH,
    vocabulary_dir: str | Path = DEFAULT_VOCABULARY_DIR,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    dictionary_name: str = DEFAULT_DICTIONARY_NAME,
    dataset_id: str = DEFAULT_DATASET_ID,
) -> dict[str, object]:
    """@notice Build the structured data dictionary for the current CIG schema surface."""

    schema = load_schema_mapping(schema_path)
    comparison_payload = _load_json_object(comparison_path)
    vocabulary_index = _load_json_object(vocabulary_index_path)
    vocabulary_path = Path(vocabulary_dir)

    direct_links = _build_field_links(vocabulary_index, vocabulary_path)
    gap_analyses = _build_gap_analysis_map(vocabulary_index)
    field_semantics = _build_field_semantics(vocabulary_index, direct_links, gap_analyses)
    cross_year_notes = _build_cross_year_notes(comparison_payload)

    entries = []
    seen_sections = []
    for column in schema.columns:
        section = _lookup_field_metadata(FIELD_SECTIONS, column.name, default="Other")
        if section not in seen_sections:
            seen_sections.append(section)
        entries.append(
            _build_entry(
                column_name=column.name,
                inferred_type=column.inferred_type,
                nullable=column.nullable,
                samples=column.non_empty_samples,
                section=section,
                field_semantics=field_semantics,
                cross_year_notes=cross_year_notes,
            )
        )

    artifact = DataDictionaryArtifact(
        dictionary_name=dictionary_name,
        dataset_id=dataset_id,
        source_schema_path=str(schema_path),
        comparison_path=str(comparison_path),
        vocabulary_index_path=str(vocabulary_index_path),
        sections=seen_sections,
        entries=entries,
    )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / f"{dictionary_name}.dictionary.json"
    markdown_path = output_path / f"{dictionary_name}.dictionary.md"
    json_path.write_text(json.dumps(artifact.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_data_dictionary_markdown(artifact), encoding="utf-8")

    return {
        "dictionary_name": dictionary_name,
        "dataset_id": dataset_id,
        "entry_count": len(entries),
        "section_count": len(seen_sections),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "resolved_code_fields": sorted(
            entry.name
            for entry in entries
            if entry.code_meaning_status in {"resolved_external", "resolved_inline"}
        ),
        "unresolved_code_fields": sorted(
            entry.name for entry in entries if entry.code_meaning_status in {"missing_dataset", "unknown"}
        ),
        "missing_external_vocabulary_fields": sorted(
            entry.name
            for entry in entries
            if entry.semantic_type == "controlled_vocabulary_code"
            and entry.external_vocabulary_status == "missing_dataset"
        ),
    }


def render_data_dictionary_markdown(artifact: DataDictionaryArtifact) -> str:
    """@notice Render the generated dictionary as grouped Markdown."""

    lines = [
        f"# Data dictionary: {artifact.dictionary_name}",
        "",
        f"- Dataset: `{artifact.dataset_id}`",
        f"- Source schema: `{artifact.source_schema_path}`",
        f"- Comparison artifact: `{artifact.comparison_path}`" if artifact.comparison_path else "- Comparison artifact: none",
        f"- Vocabulary index: `{artifact.vocabulary_index_path}`" if artifact.vocabulary_index_path else "- Vocabulary index: none",
        f"- Entry count: `{len(artifact.entries)}`",
        "",
    ]

    entries_by_section = {section: [] for section in artifact.sections}
    for entry in artifact.entries:
        entries_by_section.setdefault(entry.section, []).append(entry)

    for section in artifact.sections:
        lines.append(f"## {section}")
        lines.append("")
        for entry in entries_by_section.get(section, []):
            lines.append(f"### `{entry.name}`")
            lines.append(f"- Description: {entry.description}")
            lines.append(f"- Semantic type: `{entry.semantic_type}`")
            lines.append(f"- Value pattern: `{entry.value_pattern}`")
            lines.append(f"- Type: `{entry.inferred_type}`")
            lines.append(f"- Nullable: `{entry.nullable}`")
            if entry.related_fields:
                lines.append(f"- Related fields: {', '.join(f'`{field}`' for field in entry.related_fields)}")
            if entry.paired_field:
                lines.append(f"- Paired field: `{entry.paired_field}`")
            lines.append(f"- Code meaning status: `{entry.code_meaning_status}`")
            lines.append(f"- External vocabulary status: `{entry.external_vocabulary_status}`")
            if entry.code_reference is not None:
                lines.append(
                    f"- Code reference kind: `{entry.code_reference.reference_kind}`"
                )
                if entry.code_reference.dataset_id and entry.code_reference.table_name:
                    lines.append(
                        "- Code meanings: "
                        f"`{entry.code_reference.dataset_id}` / `{entry.code_reference.table_name}` "
                        f"({entry.code_reference.entry_count} entries)"
                    )
                if entry.code_reference.join_contract is not None:
                    lines.append(
                        "- Join contract: "
                        f"`{entry.code_reference.join_contract.source_field}` -> "
                        f"`{entry.code_reference.join_contract.target_dataset}` / "
                        f"`{entry.code_reference.join_contract.target_table}` "
                        f"on `{entry.code_reference.join_contract.target_field}` "
                        f"({entry.code_reference.join_contract.join_type} join)"
                    )
            else:
                lines.append("- Code meanings: none")
            if entry.cross_year_notes:
                lines.append("- Cross-year notes:")
                for note in entry.cross_year_notes:
                    lines.append(f"  - {note}")
            if entry.notes:
                lines.append("- Notes:")
                for note in entry.notes:
                    lines.append(f"  - {note}")
            if entry.non_empty_samples:
                lines.append(
                    "- Samples: "
                    + ", ".join(f"`{value}`" for value in entry.non_empty_samples[:3])
                )
            lines.append("")
    return "\n".join(lines)


def _build_entry(
    *,
    column_name: str,
    inferred_type: str,
    nullable: bool,
    samples: list[str],
    section: str,
    field_semantics: dict[str, dict[str, object]],
    cross_year_notes: dict[str, list[str]],
) -> DataDictionaryEntry:
    """@notice Build one dictionary entry from schema plus enrichment metadata."""

    normalized_name = _normalize_field_name(column_name)
    semantics = field_semantics.get(
        normalized_name,
        _derive_default_field_semantics(column_name, inferred_type, samples),
    )
    code_reference = semantics.get("code_reference")
    notes = list(semantics.get("notes", []))
    if inferred_type == "unknown":
        notes.append("The current full-file scan did not observe any non-empty values for this field.")
    if column_name == "DURATA_PREVISTA":
        notes.append("The source values look numeric, but the duration unit is not yet confirmed in repository documentation.")
    if column_name == "numero_gara":
        notes.append("The January 2025 file contains mixed identifier formats, so this field should not be treated as strictly numeric.")

    return DataDictionaryEntry(
        name=column_name,
        section=section,
        description=str(
            _lookup_field_metadata(
                FIELD_DESCRIPTIONS,
                column_name,
                default=f"Field `{column_name}` from the current CIG schema artifact.",
            )
        ),
        semantic_type=str(semantics["semantic_type"]),
        value_pattern=str(semantics["value_pattern"]),
        inferred_type=inferred_type,
        nullable=nullable,
        non_empty_samples=samples,
        related_fields=list(_lookup_field_metadata(RELATED_FIELDS, column_name, default=[])),
        paired_field=None if semantics.get("paired_field") is None else str(semantics["paired_field"]),
        code_meaning_status=str(semantics["code_meaning_status"]),
        external_vocabulary_status=str(semantics["external_vocabulary_status"]),
        code_reference=code_reference if isinstance(code_reference, DataDictionaryCodeReference) else None,
        cross_year_notes=cross_year_notes.get(normalized_name, []),
        notes=notes,
    )


def _build_field_semantics(
    vocabulary_index: dict[str, object],
    direct_links: dict[str, DataDictionaryCodeReference],
    gap_analyses: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    """@notice Build per-field semantic metadata from external links and inline gap analyses."""

    semantics: dict[str, dict[str, object]] = {}
    for link in vocabulary_index.get("field_links", []):
        if not isinstance(link, dict) or link.get("scope") != "current_cig_schema":
            continue
        code_field = str(link["source_code_field"])
        label_field = str(link["source_label_field"])
        reference = direct_links[_normalize_field_name(code_field)]
        shared_notes = [str(link.get("notes", ""))] if str(link.get("notes", "")) else []
        semantics[_normalize_field_name(code_field)] = {
            "semantic_type": "controlled_vocabulary_code",
            "value_pattern": "code_like",
            "paired_field": label_field,
            "code_meaning_status": str(link.get("code_meaning_status", "resolved_external")),
            "external_vocabulary_status": str(link.get("external_vocabulary_status", "resolved")),
            "code_reference": reference,
            "notes": shared_notes,
        }
        semantics[_normalize_field_name(label_field)] = {
            "semantic_type": "controlled_vocabulary_label",
            "value_pattern": "natural_language",
            "paired_field": code_field,
            "code_meaning_status": str(link.get("code_meaning_status", "resolved_external")),
            "external_vocabulary_status": str(link.get("external_vocabulary_status", "resolved")),
            "code_reference": reference,
            "notes": shared_notes,
        }

    for analysis in gap_analyses.values():
        code_field = str(analysis["field"])
        label_field = str(analysis["label_field"])
        reference = _build_inline_reference(analysis)
        raw_notes = analysis.get("notes", [])
        if isinstance(raw_notes, str):
            note_list = [raw_notes]
        else:
            note_list = [str(note) for note in raw_notes]
        notes = [str(analysis["hypothesis"])] + note_list
        semantics[_normalize_field_name(code_field)] = {
            "semantic_type": "controlled_vocabulary_code",
            "value_pattern": str(analysis["observed_pattern"]),
            "paired_field": label_field,
            "code_meaning_status": str(analysis["code_meaning_status"]),
            "external_vocabulary_status": str(analysis.get("external_vocabulary_status", "missing_dataset")),
            "code_reference": reference,
            "notes": notes,
        }
        semantics[_normalize_field_name(label_field)] = {
            "semantic_type": "controlled_vocabulary_label",
            "value_pattern": "natural_language",
            "paired_field": code_field,
            "code_meaning_status": str(analysis["code_meaning_status"]),
            "external_vocabulary_status": str(analysis.get("external_vocabulary_status", "missing_dataset")),
            "code_reference": reference,
            "notes": [
                f"Inline label field paired with `{code_field}`.",
                *notes,
            ],
        }
    return semantics


def _build_field_links(
    vocabulary_index: dict[str, object],
    vocabulary_dir: Path,
) -> dict[str, DataDictionaryCodeReference]:
    """@notice Build per-field code references from the current vocabulary index."""

    references = {}
    for link in vocabulary_index.get("field_links", []):
        if not isinstance(link, dict) or link.get("scope") != "current_cig_schema":
            continue
        dataset_id = str(link["dataset_id"])
        table_name = str(link["table_name"])
        code_field = str(link["source_code_field"])
        label_field = str(link["source_label_field"])
        artifact_payload = _load_json_object(vocabulary_dir / f"{dataset_id}.json")
        table = _find_table(artifact_payload, table_name)
        join_contract = None
        if isinstance(link.get("join_contract"), dict):
            join_contract = JoinContract(
                target_dataset=str(link["join_contract"]["target_dataset"]),
                target_table=str(link["join_contract"]["target_table"]),
                source_field=str(link["join_contract"]["source_field"]),
                target_field=str(link["join_contract"]["target_field"]),
                target_label_field=str(link["join_contract"]["target_label_field"]),
                join_type=str(link["join_contract"].get("join_type", "left")),
            )
        reference = DataDictionaryCodeReference(
            reference_kind="external_vocabulary",
            dataset_id=dataset_id,
            table_name=table_name,
            source_code_field=code_field,
            source_label_field=label_field,
            target_code_field=str(link.get("target_code_field", "code")),
            target_label_field=str(link.get("target_label_field", "label")),
            table_code_column=None if table.get("code_column") is None else str(table["code_column"]),
            table_label_column=None if table.get("label_column") is None else str(table["label_column"]),
            resolved_fields=[str(field) for field in link.get("resolved_fields", [])],
            external_vocabulary_status=str(link.get("external_vocabulary_status", "resolved")),
            join_contract=join_contract,
            notes=str(link.get("notes", "")),
            artifact_path=str(vocabulary_dir / f"{dataset_id}.json"),
            entry_count=int(table.get("entry_count", 0)),
            preview_entries=[
                {
                    "code": str(entry["code"]),
                    "label": str(entry["label"]),
                }
                for entry in table.get("entries", [])[:5]
                if isinstance(entry, dict)
            ],
        )
        for field_name in reference.resolved_fields:
            references[_normalize_field_name(field_name)] = reference
    return references


def _build_gap_analysis_map(vocabulary_index: dict[str, object]) -> dict[str, dict[str, object]]:
    """@notice Index the enriched current-gap analysis entries by field name."""

    analyses = {}
    for item in vocabulary_index.get("current_cig_schema_gaps", []):
        if not isinstance(item, dict) or "field" not in item:
            continue
        analyses[_normalize_field_name(str(item["field"]))] = item
    return analyses


def _build_inline_reference(analysis: dict[str, object]) -> DataDictionaryCodeReference:
    """@notice Build a code reference for fields resolved by an inline label pair."""

    return DataDictionaryCodeReference(
        reference_kind="inline_label_field",
        source_code_field=str(analysis["field"]),
        source_label_field=str(analysis["label_field"]),
        resolved_fields=[str(analysis["field"]), str(analysis["label_field"])],
        external_vocabulary_status=str(analysis.get("external_vocabulary_status", "missing_dataset")),
        notes=str(analysis["hypothesis"]),
        preview_entries=[
            {
                "code": str(item["code"]),
                "label": str(item["label"]),
            }
            for item in analysis.get("paired_values_sample", [])[:5]
            if isinstance(item, dict)
        ],
    )


def _derive_default_field_semantics(
    column_name: str,
    inferred_type: str,
    samples: list[str],
) -> dict[str, object]:
    """@notice Infer conservative semantics for fields without explicit vocabulary metadata."""

    semantic_type = _infer_semantic_type(column_name, inferred_type)
    if semantic_type == "free_text":
        code_meaning_status = "free_text"
    else:
        code_meaning_status = "not_coded"
    return {
        "semantic_type": semantic_type,
        "value_pattern": _infer_value_pattern(column_name, semantic_type, inferred_type, samples),
        "paired_field": None,
        "code_meaning_status": code_meaning_status,
        "external_vocabulary_status": "not_applicable",
        "code_reference": None,
        "notes": [],
    }


def _infer_semantic_type(column_name: str, inferred_type: str) -> str:
    """@notice Derive a high-level semantic type for fields without explicit mappings."""

    normalized_name = _normalize_field_name(column_name)
    if normalized_name.startswith("flag_"):
        return "boolean_flag"
    if normalized_name.startswith("data_"):
        return "date"
    if normalized_name.startswith("importo_"):
        return "monetary_amount"
    if normalized_name in {"durata_prevista", "n_lotti_componenti", "anno_pubblicazione", "mese_pubblicazione"}:
        return "quantity"
    if normalized_name in {
        "cig",
        "cig_accordo_quadro",
        "numero_gara",
        "luogo_istat",
        "codice_ausa",
        "cf_amministrazione_appaltante",
        "id_centro_costo",
        "cf_sa_delegante",
        "cf_sa_delegata",
        "cui_programma",
        "cig_collegamento",
        "cod_cpv",
    }:
        return "identifier"
    if inferred_type == "text":
        return "free_text"
    return "quantity"


def _infer_value_pattern(
    column_name: str,
    semantic_type: str,
    inferred_type: str,
    samples: list[str],
) -> str:
    """@notice Derive a pragmatic pattern summary from semantic type and samples."""

    if semantic_type == "controlled_vocabulary_code":
        return "code_like"
    if semantic_type in {"controlled_vocabulary_label", "free_text"}:
        return "natural_language"
    if semantic_type == "date":
        return "date_iso"
    if semantic_type == "boolean_flag":
        return "boolean_flag"
    if semantic_type in {"monetary_amount", "quantity"}:
        return "decimal_number" if inferred_type == "decimal" else "integer_number"
    if semantic_type == "identifier":
        if any(any(character in sample for character in "/:_-") for sample in samples):
            return "mixed_identifier"
        if inferred_type == "integer":
            return "numeric_identifier"
        return "identifier_text"
    return "unknown"



def _build_cross_year_notes(comparison_payload: dict[str, object]) -> dict[str, list[str]]:
    """@notice Build per-field cross-year notes from the comparison artifact."""

    notes: dict[str, list[str]] = {}
    for item in comparison_payload.get("type_changes", []):
        if not isinstance(item, dict):
            continue
        field_name = _normalize_field_name(str(item["name"]))
        notes.setdefault(field_name, []).append(
            f"Compared with January 2007, the inferred type changed from `{item['left_type']}` to `{item['right_type']}`."
        )
    for item in comparison_payload.get("nullable_changes", []):
        if not isinstance(item, dict):
            continue
        field_name = _normalize_field_name(str(item["name"]))
        notes.setdefault(field_name, []).append(
            f"Compared with January 2007, nullability changed from `{item['left_nullable']}` to `{item['right_nullable']}`."
        )
    return notes


def _find_table(artifact_payload: dict[str, object], table_name: str) -> dict[str, object]:
    """@notice Find one table inside a vocabulary artifact."""

    for table in artifact_payload.get("tables", []):
        if isinstance(table, dict) and table.get("name") == table_name:
            return table
    raise ValueError(f"Could not find vocabulary table {table_name!r}.")


def _load_json_object(path: str | Path) -> dict[str, object]:
    """@notice Load a JSON object from disk and fail loudly on shape mismatches."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object at {path}.")
    return payload


def _lookup_field_metadata(mapping: dict[str, object], column_name: str, *, default: object | None = None) -> object:
    """@notice Resolve field metadata while tolerating source case inconsistencies."""

    if column_name in mapping:
        return mapping[column_name]
    normalized_name = _normalize_field_name(column_name)
    for key, value in mapping.items():
        if _normalize_field_name(key) == normalized_name:
            return value
    return default


def _normalize_field_name(column_name: str) -> str:
    """@notice Normalize field names so schema and vocabulary artifacts can be joined safely."""

    return column_name.casefold()
