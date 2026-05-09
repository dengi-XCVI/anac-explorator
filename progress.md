# Progress

## Current phase
- Phase 2: DuckDB/Parquet incremental CIG sync

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
  - inline-resolved current code fields now surfaced directly in the dictionary: `COD_MODALITA_INDIZIONE_SPECIALI`, `COD_MODALITA_INDIZIONE_SERVIZI`, `COD_STRUMENTO_SVOLGIMENTO`, `COD_MOTIVO_URGENZA`, `COD_IPOTESI_COLLEGAMENTO`, `COD_ESITO`
  - cross-year notes now embedded where January 2007 vs January 2025 differences already exist, including `numero_gara`, `CUI_PROGRAMMA`, delegation fields, and current nullability shifts
- Semantic metadata refinement completed:
  - `vocabularies/index.json` now publishes a machine-readable `code_meaning_status` taxonomy
  - external code fields now expose explicit join contracts through both `field_links` and dictionary `code_reference` entries
  - the previously opaque coded-field gaps are now classified as `resolved_inline` plus `missing_dataset`
  - dictionary entries now include `semantic_type`, `value_pattern`, `paired_field`, and `external_vocabulary_status`
  - vocabulary tables now publish conservative normalization metadata instead of guessed canonical codes
  - `bandi-cig-modalita-realizzazione` explicitly records unsafe leading-zero collisions such as `01 -> APPALTO` versus `1 -> CONTRATTO D'APPALTO`
  - `build-vocabulary-crosswalks` now reuses cached local CSVs before falling back to CKAN resolution
- Phase 2 baseline completed for the first three planned items:
  - smart downloader support:
    - generic `download-dataset-resource` command added for CSV and JSON resources
    - `manifest.json` persisted next to materialized resources
    - manifest-backed cache reuse implemented
    - legacy Phase 1 cache layouts can now be adopted without re-downloading
    - HTTP transport now supports partial-download resume
    - Playwright transport now uses restart-safe temporary files
  - parser support:
    - `src/anac_explorator/parsing.py` added
    - `parse-resource` command added for CSV and JSON resources
    - parsed row/document dataclasses added for structured downstream handling
  - cleaner support:
    - `src/anac_explorator/cleaner.py` added
    - `clean-resource` command added
    - central cleaning rules added for BOM/whitespace normalization, conservative NULL handling, and scalar coercion
    - CSV cleaning can now use schema artifacts to drive typed coercion
  - hardening pass:
    - key local CIG and vocabulary resources now have manifest files written by the Phase 2 downloader path
    - integration tests now cover download -> parse -> clean flows for both CSV and JSON resources
    - direct `python -m anac_explorator.cli ...` execution now works and is tested
    - the January 2025 CIG sample was re-parsed and re-cleaned through the CLI as a smoke pass
- Phase 2 storage baseline completed for the next planned item:
  - DuckDB/Parquet loader support:
    - `src/anac_explorator/loader.py` added for SQL-native warehouse loading and local query execution
    - `load-downloaded-resource` command added for manifest-backed CSV loading into `data/warehouse/`
    - `query-local-data` command added for SQL execution against the local DuckDB warehouse
    - `download-dataset-to-parquet` command added for direct dataset download into Parquet-backed DuckDB views
    - `duckdb` added as a project dependency
  - view-first storage model:
    - durable analytical data now lives in `data/warehouse/parquet/...`
    - DuckDB stores `loaded_resources` lineage metadata and `registered_views` definitions
    - monthly CIG resources now map to the logical `cig` view and partition by `year` / `month`
    - smaller reference datasets can be loaded without forcing unnecessary partitions
  - loader safeguards:
    - the heavy load path now stays inside DuckDB instead of retaining full tables in Python memory
    - repeated loads of the same manifest can now reuse an existing Parquet slice instead of rewriting it
    - typed SQL projections are validated before Parquet writes so invalid coercions fail fast
    - DuckDB views are refreshed from the current Parquet file inventory after each load
    - archive-backed manifests can now rematerialize a pruned extracted CSV without re-downloading the source ZIP
  - integrated query workflow:
    - the direct-to-Parquet command can reuse or generate schema artifacts automatically
    - extracted CSVs can be pruned after a successful load when the ZIP archive remains cached
    - local vocabulary crosswalk artifacts can now be registered as joinable DuckDB views for semantic querying
  - test coverage:
    - new loader tests now cover partitioned CIG loading, Parquet registration, and local SQL querying
    - loader tests now also cover archive-only reloads, crosswalk-view registration, and direct download-to-Parquet orchestration
    - CLI coverage now includes the local DuckDB query command plus the loader and direct-to-Parquet parser surfaces
- Research references reviewed: `research/ANAC-data.md`, `research/ANAC-data-research.md`
- Live network access is no longer blocked when using Playwright transport with the default browser-like request headers
- Live direct-to-Parquet smoke pass now succeeds against ANAC for `cig-2025` / `cig_csv_2025_01`:
  - fresh manifest-backed download completed through Playwright
  - January 2025 CIG data loaded into `data/warehouse/parquet/cig/year=2025/month=01/`
  - registered `cig` row count validated at `112,879`
  - vocabulary crosswalk joins now query correctly against the live-loaded warehouse
- First incremental-update slice completed for monthly CIG resources:
  - `dataset_period_manifest` added to DuckDB for period-level tracking with remote metadata and content checksums
  - `sync-cig-periods` command added for forward-only updates, explicit period sync, and correction-aware refreshes
  - existing one-shot monthly CIG loads can now be backfilled into the period catalog from `loaded_resources`
  - corrected periods now replace the existing Parquet slice in place and refresh the merged `cig` view without building a second master fact table
  - repeated incremental runs now stay idempotent when the remote period metadata is unchanged
  - real smoke pass confirmed that `sync-cig-periods cig-2025 --period 2025_01 --transport playwright` recognizes the already-loaded live January 2025 slice and skips it instead of redownloading it

## Planned milestones
1. Validate data integrity and vocabulary-linked referential expectations
2. Expand the local query surface carefully without over-materializing data
3. Extend the incremental-update model beyond the monthly CIG family
4. Resolve the remaining coded fields through dedicated external vocabularies where available

## Known risks
- Direct HTTP access to the ANAC API and portal is rejected from this runtime; Playwright is the validated access path
