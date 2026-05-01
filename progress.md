# Progress

## Current phase
- Phase 1: comprehensive data dictionary

## Current status
- Bootstrap implementation complete:
  - Python package skeleton added under `src/anac_explorator/`
  - CLI entry points added for CKAN metadata inspection and local CSV schema mapping
  - unit tests added for CKAN parsing, CLI output, and schema inference
- CKAN access hardening implemented:
  - configurable proxy support
  - browser-like `User-Agent`, `Accept-Language`, and `Referer` headers
- Playwright transport implemented and validated against the live ANAC portal
- Monthly sample resolved and downloaded successfully:
  - dataset: `cig-2025`
  - resource: `cig_csv_2025_01`
  - ZIP URL resolved from live CKAN metadata
  - local outputs written under `data/raw/cig-2025/cig_csv_2025_01/`
- Schema mapping completed for the extracted January 2025 CSV:
  - machine-readable artifact: `schemas/cig_2025_01.schema.json`
  - full-file rows scanned: 112,879
  - row-length mismatches observed: 0
  - columns discovered: 61
- Cross-year comparison completed for January 2007 vs January 2025:
  - January 2007 artifact: `schemas/cig_2007_01.schema.json`
  - comparison artifact: `schemas/cig_2007_01_vs_cig_2025_01.comparison.json`
  - both files expose the same 61 column names
  - observed differences are in inferred type sparsity and nullability, not column presence
- Controlled vocabulary cross-reference build completed for five datasets:
  - artifact index: `vocabularies/index.json`
  - per-dataset artifacts: `vocabularies/*.json`
  - raw schema artifacts: `schemas/bandi-cig-tipo-scelta-contraente.schema.json`, `schemas/bandi-cig-modalita-realizzazione.schema.json`, `schemas/categorie-dpcm-aggregazione.schema.json`, `schemas/categorie-opera.schema.json`, `schemas/smartcig-tipo-fattispecie-contrattuale.schema.json`
- Generated cross-reference tables:
  - `bandi-cig-tipo-scelta-contraente` → 1 table / 46 entries
  - `bandi-cig-modalita-realizzazione` → 1 table / 28 entries
  - `categorie-dpcm-aggregazione` → 2 tables / 27 category entries + 7 derogation entries
  - `categorie-opera` → 3 tables / 97 category entries + 176 variant entries + 2 type entries
  - `smartcig-tipo-fattispecie-contrattuale` → 1 table / 78 entries
- Current-field coverage now documented:
  - direct CIG coverage for `cod_tipo_scelta_contraente` and `cod_modalita_realizzazione`
  - adjacent/future coverage for DPCM aggregation, work-category, and SMARTCIG contract-type fields
  - unresolved current January 2025 CIG coded fields tracked in `vocabularies/index.json`
- Comprehensive January 2025 CIG data dictionary completed:
  - machine-readable artifact: `dictionaries/cig_2025_01.dictionary.json`
  - human-readable artifact: `dictionaries/cig_2025_01.dictionary.md`
  - field entries published: `61`
  - logical sections published: `9`
  - resolved code-meaning links: `cod_tipo_scelta_contraente`, `tipo_scelta_contraente`, `cod_modalita_realizzazione`, `modalita_realizzazione`
  - unresolved current code fields now surfaced directly in dictionary notes: `COD_MODALITA_INDIZIONE_SPECIALI`, `COD_MODALITA_INDIZIONE_SERVIZI`, `COD_STRUMENTO_SVOLGIMENTO`, `COD_MOTIVO_URGENZA`, `COD_IPOTESI_COLLEGAMENTO`, `COD_ESITO`
  - cross-year notes now embedded where January 2007 vs January 2025 differences already exist, including `numero_gara`, `CUI_PROGRAMMA`, delegation fields, and current nullability shifts
- Research references reviewed: `research/ANAC-data.md`, `research/ANAC-data-research.md`
- Live network access is no longer blocked when using Playwright transport

## Planned milestones
1. Expand the cross-year comparison beyond January 2007 vs January 2025
2. Resolve the remaining coded fields not yet covered by controlled vocabularies
3. Extend the loader path toward DuckDB/Parquet-ready ingestion
4. Reuse the generated dictionary artifacts in later CLI and query surfaces

## Known risks
- Direct HTTP access to the ANAC API and portal is rejected from this runtime; Playwright is the validated access path
