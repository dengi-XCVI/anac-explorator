This project is meant as an interface to facilitate analysis of the ANAC (Autorità Nazionale Anti Corruzione) dataset.
It is part of a wider initiative to increase the legibility of the Italian government.

## Current implementation focus

Phase 1 is complete, and the current Phase 2 storage slice now covers thirteen
reusable parts of the future system:

1. resolve a monthly CIG resource from ANAC metadata
2. download and inspect a sample archive
3. map the complete raw CSV schema
4. compare schema variations across years
5. build controlled vocabulary cross-reference tables
6. build a comprehensive field dictionary for the January 2025 CIG surface
7. publish semantic join and normalization metadata for LLM- and DuckDB-oriented querying
8. download CKAN CSV and JSON resources with manifest-backed caching
9. parse local CSV and JSON resources into structured Python-friendly payloads
10. clean parsed records for later database loading
11. load manifest-backed CSV resources into partitioned Parquet through DuckDB
12. register lightweight DuckDB views over the Parquet files for local querying
13. validate the pipeline end to end with integration coverage and direct module CLI execution

## Current code layout

- `src/anac_explorator/ckan.py` — CKAN metadata client for dataset discovery
- `src/anac_explorator/browser.py` — Playwright-backed access for WAF-protected endpoints
- `src/anac_explorator/sample.py` — generic CKAN CSV download plus monthly CIG sample helpers
- `src/anac_explorator/parsing.py` — reusable CSV and JSON parser entry points
- `src/anac_explorator/cleaner.py` — reusable cleaning and type-coercion helpers
- `src/anac_explorator/schema.py` — CSV schema inspection and lightweight type inference
- `src/anac_explorator/comparison.py` — schema artifact comparison across files or years
- `src/anac_explorator/vocabulary.py` — controlled vocabulary normalization and cross-reference generation
- `src/anac_explorator/dictionary.py` — data-dictionary generation from schema, comparison, and vocabulary artifacts
- `src/anac_explorator/loader.py` — DuckDB/Parquet loader plus local SQL query helpers
- `src/anac_explorator/cli.py` — CLI entry points for metadata, schema, vocabulary, and dictionary workflows

## Local usage

```bash
python3 -m pip install -e .
anac-explorator inspect-csv-schema ./path/to/file.csv
anac-explorator package-show cig-2025
anac-explorator download-dataset-csv bandi-cig-tipo-scelta-contraente --transport playwright
anac-explorator download-dataset-resource bandi-cig-modalita-realizzazione --resource-name bandi-cig-modalita-realizzazione_csv --resource-format csv
anac-explorator download-cig-sample --year 2025 --month 1 --transport playwright
anac-explorator compare-schema-files ./schemas/cig_2007_01.schema.json ./schemas/cig_2025_01.schema.json
anac-explorator build-vocabulary-crosswalks --transport playwright
anac-explorator build-data-dictionary
anac-explorator parse-resource ./data/raw/cig-2025/cig_csv_2025_01/extracted/cig_csv_2025_01.csv --format csv --record-limit 2
anac-explorator clean-resource ./data/raw/cig-2025/cig_csv_2025_01/extracted/cig_csv_2025_01.csv --format csv --schema-path ./schemas/cig_2025_01.schema.json --record-limit 2
anac-explorator load-downloaded-resource ./data/raw/cig-2025/cig_csv_2025_01/manifest.json --schema-path ./schemas/cig_2025_01.schema.json
anac-explorator query-local-data "SELECT cig, importo_lotto FROM cig ORDER BY cig LIMIT 5"
python3 -m anac_explorator.cli parse-resource ./data/raw/cig-2025/cig_csv_2025_01/extracted/cig_csv_2025_01.csv --format csv --record-limit 1
```

If ANAC blocks the default runtime IP, the CKAN command also supports alternate
network settings:

```bash
ANAC_EXPLORATOR_PROXY_URL=http://proxy.example:8080 \
anac-explorator package-show cig-2025 \
  --user-agent 'Mozilla/5.0' \
  --accept-language 'it-IT,it;q=0.9' \
  --referer 'https://dati.anticorruzione.it/opendata/'
```

For the current ANAC portal, Playwright transport is the working path from this
environment:

```bash
anac-explorator package-show cig-2025 --transport playwright
anac-explorator download-cig-sample --year 2025 --month 1 --transport playwright
anac-explorator inspect-csv-schema ./data/raw/cig-2025/cig_csv_2025_01/extracted/cig_csv_2025_01.csv --sample-limit 0
anac-explorator build-vocabulary-crosswalks --transport playwright
anac-explorator build-data-dictionary
```

## Current generated artifacts

- `schemas/cig_2025_01.schema.json` — full-file January 2025 CIG schema
- `schemas/cig_2007_01.schema.json` — full-file January 2007 CIG schema
- `schemas/cig_2007_01_vs_cig_2025_01.comparison.json` — January 2007 vs January 2025 diff
- `schemas/*.schema.json` for the five controlled-vocabulary datasets
- `vocabularies/index.json` — generated inventory, field links, and current CIG coverage gaps
- `vocabularies/*.json` — per-dataset normalized cross-reference tables
- `dictionaries/cig_2025_01.dictionary.json` — machine-readable field dictionary for the current January 2025 CIG schema
- `dictionaries/cig_2025_01.dictionary.md` — grouped human-readable version of the same dictionary
- `data/warehouse/anac.duckdb` — local DuckDB catalog for registered views and load metadata
- `data/warehouse/parquet/...` — partitioned Parquet files written by the local loader

## Current local analytical storage

- `load-downloaded-resource` now turns one manifest-backed CSV resource into Parquet through DuckDB instead of materializing a second large in-memory Python copy.
- Large fact-like datasets are partitioned when year/month can be derived safely from the manifest naming convention.
  - Current example: monthly CIG resources register under the logical `cig` view and write files under `data/warehouse/parquet/cig/year=YYYY/month=MM/`.
- DuckDB now acts as a lightweight control/query plane:
  - `registered_views` stores the current generated view SQL
  - `loaded_resources` stores manifest/schema lineage for each loaded Parquet slice
  - `query-local-data` executes SQL against those registered views without duplicating the Parquet data into a second fact table
- The current storage scope is intentionally narrow: monthly CIG resources plus the already-wired vocabulary datasets.

## Current semantic metadata

- `vocabularies/index.json` now publishes a `code_meaning_status` taxonomy:
  - `resolved_external`
  - `resolved_inline`
  - `missing_dataset`
  - `free_text`
  - `not_coded`
  - `unknown`
- The current "missing vocabulary" fields are no longer represented as a single opaque gap.
  - `cod_motivo_urgenza`
  - `cod_esito`
  - `cod_modalita_indizione_speciali`
  - `cod_modalita_indizione_servizi`
  - `cod_strumento_svolgimento`
  - `cod_ipotesi_collegamento`
  - These are now classified as `resolved_inline` plus `missing_dataset`, because the January 2025 CIG extract already carries sibling label fields such as `MOTIVO_URGENZA`, `ESITO`, and `IPOTESI_COLLEGAMENTO`, even though no separate CKAN vocabulary dataset has been wired yet.
- `dictionaries/cig_2025_01.dictionary.json` now embeds:
  - `semantic_type`
  - `value_pattern`
  - `paired_field`
  - richer `code_reference` objects
  - explicit `join_contract` metadata for externally resolved vocabularies
- Vocabulary artifacts now publish conservative normalization metadata rather than guessed canonical merges.
  - Example: `vocabularies/bandi-cig-modalita-realizzazione.json` explicitly marks leading-zero stripping as unsafe because `01 -> APPALTO` and `1 -> CONTRATTO D'APPALTO` are different meanings, not safe aliases.

## Phase 2 pipeline baseline

- `download-dataset-resource` now persists a `manifest.json` next to materialized resources.
- The downloader can:
  - reuse manifest-backed cache hits without re-downloading
  - adopt the older Phase 1 cache layout and wrap it in a new manifest
  - resume partial downloads when using plain HTTP transport
  - restart safely through Playwright transport when resume is not available
- The main Phase 1 cached resources have now been passed through the manifest-aware downloader path, so the active local resource tree is manifest-backed for the key CIG and vocabulary artifacts already in use.
- `parse-resource` now supports:
  - semicolon-delimited CSV resources
  - JSON resources
- `clean-resource` now supports:
  - whitespace and BOM normalization
  - conservative NULL-marker normalization
  - scalar coercion for booleans, integers, decimals, dates, and datetimes
  - schema-driven cleaning for CSV resources when a schema artifact is supplied
- `load-downloaded-resource` now supports:
  - manifest-backed CSV loading into DuckDB-written Parquet
  - data-aware partitioning for monthly CIG resources
  - fail-fast validation when typed SQL projections would silently coerce invalid values
  - dynamic view refresh so one logical DuckDB view can span all loaded Parquet slices for a dataset
- `query-local-data` now supports:
  - direct SQL execution against the local DuckDB warehouse
  - JSON-friendly result emission for downstream tooling
- The automated test suite now includes end-to-end Phase 2 coverage for:
  - download -> manifest -> parse -> clean on CSV resources
  - download -> parse -> clean on JSON resources
  - manifest -> partitioned Parquet -> DuckDB view registration/query on CSV resources
  - direct `python -m anac_explorator.cli ...` execution
