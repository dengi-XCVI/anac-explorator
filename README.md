This project is meant as an interface to facilitate analysis of the ANAC (Autorità Nazionale Anti Corruzione) dataset.
It is part of a wider initiative to increase the legibility of the Italian government.

## Current implementation focus

Phase 1 now covers seven reusable slices of the future system:

1. resolve a monthly CIG resource from ANAC metadata
2. download and inspect a sample archive
3. map the complete raw CSV schema
4. compare schema variations across years
5. build controlled vocabulary cross-reference tables
6. build a comprehensive field dictionary for the January 2025 CIG surface
7. publish semantic join and normalization metadata for LLM- and DuckDB-oriented querying

## Current code layout

- `src/anac_explorator/ckan.py` — CKAN metadata client for dataset discovery
- `src/anac_explorator/browser.py` — Playwright-backed access for WAF-protected endpoints
- `src/anac_explorator/sample.py` — generic CKAN CSV download plus monthly CIG sample helpers
- `src/anac_explorator/schema.py` — CSV schema inspection and lightweight type inference
- `src/anac_explorator/comparison.py` — schema artifact comparison across files or years
- `src/anac_explorator/vocabulary.py` — controlled vocabulary normalization and cross-reference generation
- `src/anac_explorator/dictionary.py` — data-dictionary generation from schema, comparison, and vocabulary artifacts
- `src/anac_explorator/cli.py` — CLI entry points for metadata, schema, vocabulary, and dictionary workflows

## Local usage

```bash
python3 -m pip install -e .
anac-explorator inspect-csv-schema ./path/to/file.csv
anac-explorator package-show cig-2025
anac-explorator download-dataset-csv bandi-cig-tipo-scelta-contraente --transport playwright
anac-explorator download-cig-sample --year 2025 --month 1 --transport playwright
anac-explorator compare-schema-files ./schemas/cig_2007_01.schema.json ./schemas/cig_2025_01.schema.json
anac-explorator build-vocabulary-crosswalks --transport playwright
anac-explorator build-data-dictionary
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
