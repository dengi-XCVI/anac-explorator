# ANAC Explorator Specification

## Project objective
Create a CLI- and Python-oriented interface for ANAC open procurement data that can discover datasets, download samples or full resources, document schemas, and eventually support local analytical querying.

## Source references
- `research/ANAC-data.md`
- `research/ANAC-data-research.md`

## In-scope slice
The current implementation slice now covers:
1. identifying one monthly CIG resource
2. downloading a sample archive
3. extracting the source data
4. mapping the complete column schema
5. comparing schema variations across years
6. building controlled vocabulary cross-reference tables
7. building a comprehensive field dictionary for the January 2025 CIG schema
8. documenting the result for later ingestion and querying phases

## Architectural baseline
- ANAC CKAN metadata is the source of truth for dataset and resource discovery
- sample data is downloaded as a ZIP archive
- raw source files are inspected before any normalization
- schema outputs should be reusable by later DuckDB/Parquet pipeline work

## Implemented baseline
- Python package skeleton under `src/anac_explorator/`
- `anac-explorator package-show <dataset>` for CKAN metadata lookup
- `anac-explorator download-dataset-csv <dataset>` for generic CKAN CSV acquisition
- `anac-explorator download-cig-sample --year <year> --month <month>` for live CIG sample acquisition
- `anac-explorator inspect-csv-schema <path>` for local CSV schema mapping
- `anac-explorator compare-schema-files <left> <right>` for schema-diff reporting
- `anac-explorator build-vocabulary-crosswalks` for normalized vocabulary artifact generation
- `anac-explorator build-data-dictionary` for January 2025 CIG field-dictionary generation
- configurable proxy and request-header support for CKAN access hardening
- Playwright transport for WAF-protected ANAC access
- unit coverage for CKAN response parsing, schema inference, and CLI JSON output

## Access hardening knobs
- `ANAC_EXPLORATOR_PROXY_URL` or `--proxy-url`
- `ANAC_EXPLORATOR_USER_AGENT` or `--user-agent`
- `ANAC_EXPLORATOR_ACCEPT_LANGUAGE` or `--accept-language`
- `ANAC_EXPLORATOR_REFERER` or `--referer`
- `ANAC_EXPLORATOR_TRANSPORT` or `--transport`

From this runtime, direct HTTP requests are rejected by the ANAC WAF. The
validated working path is `--transport playwright`.

## Initial constraints
- Preserve ANAC source column names exactly in the first schema map
- Prefer reusable components over one-off scripts
- Treat connectivity to the ANAC portal as a runtime dependency that must be validated explicitly
- Use Python docstrings/comments that mirror NatSpec intent

## Phase 1 findings: CIG January 2025 sample
- Dataset resolved live from CKAN: `cig-2025`
- Monthly CSV resource selected: `cig_csv_2025_01`
- Resource URL: `https://dati.anticorruzione.it/opendata/download/dataset/cig-2025/filesystem/cig_csv_2025_01.zip`
- Reported ZIP size from CKAN metadata: `91,942,157` bytes
- Extracted CSV path: `data/raw/cig-2025/cig_csv_2025_01/extracted/cig_csv_2025_01.csv`
- Schema artifact: `schemas/cig_2025_01.schema.json`
- Parsing characteristics:
  - delimiter: `;`
  - encoding: `utf-8-sig`
  - full-file rows scanned: `112,879`
  - row-length mismatches in full scan: `0`
  - total discovered columns: `61`

## January 2025 CIG columns
`cig`, `cig_accordo_quadro`, `numero_gara`, `oggetto_gara`, `importo_complessivo_gara`, `n_lotti_componenti`, `oggetto_lotto`, `importo_lotto`, `oggetto_principale_contratto`, `stato`, `settore`, `luogo_istat`, `provincia`, `data_pubblicazione`, `data_scadenza_offerta`, `cod_tipo_scelta_contraente`, `tipo_scelta_contraente`, `cod_modalita_realizzazione`, `modalita_realizzazione`, `codice_ausa`, `cf_amministrazione_appaltante`, `denominazione_amministrazione_appaltante`, `sezione_regionale`, `id_centro_costo`, `denominazione_centro_costo`, `anno_pubblicazione`, `mese_pubblicazione`, `cod_cpv`, `descrizione_cpv`, `flag_prevalente`, `COD_MOTIVO_CANCELLAZIONE`, `MOTIVO_CANCELLAZIONE`, `DATA_CANCELLAZIONE`, `DATA_ULTIMO_PERFEZIONAMENTO`, `COD_MODALITA_INDIZIONE_SPECIALI`, `MODALITA_INDIZIONE_SPECIALI`, `COD_MODALITA_INDIZIONE_SERVIZI`, `MODALITA_INDIZIONE_SERVIZI`, `DURATA_PREVISTA`, `COD_STRUMENTO_SVOLGIMENTO`, `STRUMENTO_SVOLGIMENTO`, `FLAG_URGENZA`, `COD_MOTIVO_URGENZA`, `MOTIVO_URGENZA`, `FLAG_DELEGA`, `FUNZIONI_DELEGATE`, `CF_SA_DELEGANTE`, `DENOMINAZIONE_SA_DELEGANTE`, `CF_SA_DELEGATA`, `DENOMINAZIONE_SA_DELEGATA`, `IMPORTO_SICUREZZA`, `TIPO_APPALTO_RISERVATO`, `CUI_PROGRAMMA`, `FLAG_PREV_RIPETIZIONI`, `COD_IPOTESI_COLLEGAMENTO`, `IPOTESI_COLLEGAMENTO`, `CIG_COLLEGAMENTO`, `COD_ESITO`, `ESITO`, `DATA_COMUNICAZIONE_ESITO`, `FLAG_PNRR_PNC`

## January 2025 inferred type observations
- Stable identifier/text fields include `cig`, `numero_gara`, `oggetto_gara`, `oggetto_lotto`, `cod_cpv`, and most descriptive labels.
- Monetary fields infer cleanly as decimals in the current sample, including `importo_complessivo_gara`, `importo_lotto`, and `IMPORTO_SICUREZZA`.
- Date fields infer cleanly as ISO dates, including `data_pubblicazione`, `data_scadenza_offerta`, `DATA_ULTIMO_PERFEZIONAMENTO`, and `DATA_COMUNICAZIONE_ESITO`.
- Boolean-style flags are present and now separated from numeric code columns using column-name hints, for example `flag_prevalente`, `FLAG_URGENZA`, `FLAG_DELEGA`, and `FLAG_PNRR_PNC`.
- Several nullable late-stage outcome or linkage fields remain empty even in the full January 2025 scan, notably `COD_MOTIVO_CANCELLAZIONE`, `MOTIVO_CANCELLAZIONE`, and `DATA_CANCELLAZIONE`.

## Cross-year findings: January 2007 vs January 2025
- January 2007 resource selected: `cig_csv_2007_01`
- January 2007 resource URL: `https://dati.anticorruzione.it/opendata/download/dataset/cig-2007/filesystem/cig_csv_2007_01.zip`
- January 2007 schema artifact: `schemas/cig_2007_01.schema.json`
- Cross-year comparison artifact: `schemas/cig_2007_01_vs_cig_2025_01.comparison.json`
- January 2007 full-file rows scanned: `1,126`
- January 2007 row-length mismatches in full scan: `0`
- Column-presence result:
  - `61` columns in January 2007
  - `61` columns in January 2025
  - `61` shared column names
  - no columns exclusive to either file in the January-to-January comparison
- Type-difference result:
  - `numero_gara` shifts from integer-like values in January 2007 to text-like mixed identifiers in January 2025
  - `CUI_PROGRAMMA` shifts from integer-like values in January 2007 to text in January 2025
  - several delegation/linkage fields are `unknown` in January 2007 because they are entirely empty there, but populated and typed in January 2025: `FLAG_DELEGA`, `FUNZIONI_DELEGATE`, `CF_SA_DELEGANTE`, `DENOMINAZIONE_SA_DELEGANTE`, `CF_SA_DELEGATA`, `DENOMINAZIONE_SA_DELEGATA`, `COD_IPOTESI_COLLEGAMENTO`, `IPOTESI_COLLEGAMENTO`, `CIG_COLLEGAMENTO`, `TIPO_APPALTO_RISERVATO`
- Nullability-difference result:
  - `id_centro_costo`, `denominazione_centro_costo`, `descrizione_cpv`, and `sezione_regionale` are consistently filled in the January 2007 file but nullable in January 2025
  - `n_lotti_componenti` is nullable in January 2007 but consistently filled in January 2025
  - `FLAG_PNRR_PNC` is nullable in January 2007 and consistently present in January 2025

## Controlled vocabulary findings
- Artifact index: `vocabularies/index.json`
- All five planned vocabulary datasets were downloaded and normalized using live CKAN metadata plus Playwright-backed resource access.
- Selected resources:
  - `bandi-cig-tipo-scelta-contraente_csv`
  - `bandi-cig-modalita-realizzazione_csv`
  - `20260401-categorie-dpcm-aggregazione_csv`
  - `20260401-categorie-opera_csv`
  - `smartcig-tipo-fattispecie-contrattuale_csv`
- Raw schema artifacts:
  - `schemas/bandi-cig-tipo-scelta-contraente.schema.json` → `46` rows / `2` columns
  - `schemas/bandi-cig-modalita-realizzazione.schema.json` → `28` rows / `2` columns
  - `schemas/categorie-dpcm-aggregazione.schema.json` → `134,904` rows / `5` columns
  - `schemas/categorie-opera.schema.json` → `576,518` rows / `6` columns
  - `schemas/smartcig-tipo-fattispecie-contrattuale.schema.json` → `78` rows / `2` columns

## Generated cross-reference tables
- `vocabularies/bandi-cig-tipo-scelta-contraente.json`
  - table: `tipo_scelta_contraente`
  - entry count: `46`
  - directly resolves `cod_tipo_scelta_contraente` ↔ `tipo_scelta_contraente`
- `vocabularies/bandi-cig-modalita-realizzazione.json`
  - table: `modalita_realizzazione`
  - entry count: `28`
  - directly resolves `cod_modalita_realizzazione` ↔ `modalita_realizzazione`
- `vocabularies/categorie-dpcm-aggregazione.json`
  - table: `categorie_dpcm_aggregazione` → `27` entries
  - table: `deroghe_soggetto_aggregatore` → `7` entries
  - resolves DPCM aggregation category and derogation codes
- `vocabularies/categorie-opera.json`
  - table: `categorie_opera` → `97` unique code/label pairs
  - table: `categorie_opera_varianti` → `176` unique code/label/type/class variants
  - table: `tipi_categoria_opera` → `2` type entries
  - preserves the work-category mappings without collapsing code/label variants prematurely
- `vocabularies/smartcig-tipo-fattispecie-contrattuale.json`
  - table: `tipo_fattispecie_contrattuale` → `78` entries
  - resolves SMARTCIG contract-type identifiers

## Current CIG field coverage and gaps
- Covered directly in the current January 2025 CIG schema:
  - `cod_tipo_scelta_contraente` ↔ `tipo_scelta_contraente`
  - `cod_modalita_realizzazione` ↔ `modalita_realizzazione`
- Covered for adjacent or future datasets:
  - DPCM aggregation category and derogation fields
  - work-category identifiers, type codes, and import-class variants
  - SMARTCIG contract-type identifiers
- Still unresolved for the current January 2025 CIG schema:
  - `cod_modalita_indizione_speciali`
  - `cod_modalita_indizione_servizi`
  - `cod_strumento_svolgimento`
  - `cod_motivo_urgenza`
  - `cod_ipotesi_collegamento`
  - `cod_esito`

## Data dictionary outputs
- Machine-readable artifact: `dictionaries/cig_2025_01.dictionary.json`
- Human-readable artifact: `dictionaries/cig_2025_01.dictionary.md`
- Dictionary scope:
  - all `61` columns from `schemas/cig_2025_01.schema.json`
  - sectioned into `9` logical groups to keep procurement, authority, lifecycle, and outcome fields readable
  - enriched with explicit field descriptions rather than generated placeholder text
  - linked to controlled vocabularies where the repository currently has confirmed cross-reference tables
  - annotated with cross-year notes where `schemas/cig_2007_01_vs_cig_2025_01.comparison.json` already shows differences
- Current code-meaning resolution status:
  - resolved through live vocabulary artifacts:
    - `cod_tipo_scelta_contraente` ↔ `tipo_scelta_contraente`
    - `cod_modalita_realizzazione` ↔ `modalita_realizzazione`
  - surfaced as explicit unresolved gaps inside the dictionary:
    - `COD_MODALITA_INDIZIONE_SPECIALI`
    - `COD_MODALITA_INDIZIONE_SERVIZI`
    - `COD_STRUMENTO_SVOLGIMENTO`
    - `COD_MOTIVO_URGENZA`
    - `COD_IPOTESI_COLLEGAMENTO`
    - `COD_ESITO`
- Cross-year notes currently embedded for:
  - type shifts such as `numero_gara` and `CUI_PROGRAMMA`
  - delegation/linkage fields that were empty in January 2007 but typed in January 2025
  - nullability shifts such as `FLAG_PNRR_PNC`, `n_lotti_componenti`, `descrizione_cpv`, `id_centro_costo`, and `sezione_regionale`

## Open questions
- Compare additional months and older years to see whether the January 2007 vs January 2025 alignment holds outside this month pair.
- Resolve the remaining current CIG coded fields not yet covered by a controlled vocabulary dataset.
