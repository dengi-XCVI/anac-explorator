# ANAC Explorator Specification

## Project objective

ANAC Explorator is a local CLI- and Python-oriented interface for ANAC open procurement data. Its current goal is to make ANAC datasets:

1. discoverable as logical dataset families
2. downloadable through a manifest-backed cache
3. inspectable through reusable schema and semantic artifacts
4. materializable into a local DuckDB/Parquet warehouse
5. queryable through a stable Phase 3 CLI surface

## Source references

- `research/ANAC-data.md`
- `research/ANAC-data-research.md`
- `cli-specification.md`

## Current implementation scope

The implemented repository now covers:

1. CKAN dataset discovery and WAF-safe resource access
2. manifest-backed CSV and JSON downloads
3. CSV/JSON parsing and schema-aware cleaning
4. schema mapping and cross-year comparison
5. vocabulary crosswalk generation and field-dictionary enrichment
6. manifest-backed CSV loading into partitioned Parquet through DuckDB
7. registered local DuckDB views over Parquet-backed datasets
8. incremental update planning and execution for monthly CIG plus the wired vocabulary snapshot families
9. read-only integrity validation for the local warehouse
10. SQL-queryable metadata discoverability views
11. the stable Phase 3 CLI commands:
   - `datasets`
   - `download`
   - `schema`
   - `query`
   - `stats`
   - `update`
   - `config`
   - `drop`
12. backwards-compatible legacy low-level commands and executable aliasing

## Canonical executable and compatibility

The canonical executable is:

```bash
anacx
```

Compatibility still exists in two places:

1. the legacy executable alias `anac-explorator`
2. the older low-level commands such as `package-show`, `download-cig-sample`, `query-local-data`, and `sync-cig-periods`

Those compatibility paths remain supported so existing scripts do not break abruptly, but new usage should target `anacx` and the eight Phase 3 commands.

## Architectural baseline

The current system is built around five persistent local surfaces:

| Layer | Purpose | Current source |
| --- | --- | --- |
| Raw resource cache | manifest-backed downloaded resources | `data/raw/...` |
| Schema artifacts | reusable schema maps and schema comparisons | `schemas/*.json` |
| Semantic artifacts | vocabulary crosswalks and field dictionaries | `vocabularies/*.json`, `dictionaries/*.json` |
| Warehouse payload | durable analytical data | `data/warehouse/parquet/...` |
| Warehouse catalog | load, view, and period metadata | `data/warehouse/anac.duckdb` |

The warehouse catalog still centers on:

- `loaded_resources`
- `registered_views`
- `dataset_period_manifest`

The Phase 3 metadata discoverability layer builds on top of those tables and local artifacts instead of replacing them.

## Core implementation modules

- `src/anac_explorator/cli.py` — canonical CLI entry point
- `src/anac_explorator/output.py` — shared result-envelope rendering
- `src/anac_explorator/errors.py` — stable error model and exit-code mapping
- `src/anac_explorator/config.py` — persisted config, env merge, and validation
- `src/anac_explorator/paths.py` — shared path resolution
- `src/anac_explorator/selection.py` — temporal selection parser
- `src/anac_explorator/catalog.py` — dataset-family registry plus download/update/drop adapters
- `src/anac_explorator/metadata_views.py` — `anac_*` metadata discoverability views
- `src/anac_explorator/schema_service.py` — artifact-driven schema inspection, diffing, and DDL lookup
- `src/anac_explorator/stats.py` — global, dataset, partition, and profiling stats
- `src/anac_explorator/drop.py` — local pruning planning and execution
- `src/anac_explorator/loader.py` — Parquet loading and local query execution

## Stable Phase 3 command surface

| Command | Current role |
| --- | --- |
| `anacx datasets` | list logical families and inspect local/remote availability |
| `anacx download` | plan or apply raw downloads and optional Parquet loads |
| `anacx schema` | inspect artifact-driven schema, semantic overlays, DDL, and diffs |
| `anacx query` | execute safe SQL against the local warehouse and metadata views |
| `anacx stats` | summarize local storage and optionally profile live values |
| `anacx update` | plan or apply incremental updates for CIG and supported vocabulary families |
| `anacx config` | show, get, set, unset, reset, and validate effective config |
| `anacx drop` | plan or apply safe local pruning while keeping metadata coherent |

## Current family maturity

The project is currently strongest on the **monthly CIG family**.

That means:

- the stable `download`, `schema`, `query`, `stats`, `update`, and `drop` workflows are all implemented for CIG
- `update` also covers the wired vocabulary snapshot families and can explicitly regenerate the current CIG dictionary when their semantic inputs changed
- metadata views describe both the warehouse and the local artifacts around CIG
- update and drop operations for unsupported families fail with explicit stable errors instead of pretending to work

Snapshot-style and vocabulary families are still useful through discovery, schema, querying, and one-shot materialization paths, but they do not all expose the same lifecycle behaviors yet. Update support is now broader for the wired vocabulary families, while drop and the broader warehouse lifecycle remain more limited outside CIG.

## Config and environment model

The canonical config path is:

```bash
~/.config/anacx/config.json
```

If an older `anac-explorator` config directory already exists, the CLI reuses it automatically for compatibility.

The config system prefers the canonical `ANAC_*` environment variables while still accepting the older `ANAC_EXPLORATOR_*` names. Important settings include:

- transport selection
- proxy URL
- request headers
- raw/warehouse/schema/dictionary paths
- query timeout
- default output format

## Access hardening

From this runtime, direct HTTP requests to ANAC are often rejected by the WAF. The validated working path is Playwright transport.

Canonical knobs:

- `ANAC_TRANSPORT`
- `ANAC_PROXY_URL`
- `ANAC_USER_AGENT`
- `ANAC_ACCEPT_LANGUAGE`
- `ANAC_REFERER`
- `ANAC_TIMEOUT`

Compatibility names with the `ANAC_EXPLORATOR_*` prefix remain accepted.

## Current artifact outputs

The repository already publishes and reuses:

- `schemas/cig_2025_01.schema.json`
- `schemas/cig_2007_01.schema.json`
- `schemas/cig_2007_01_vs_cig_2025_01.comparison.json`
- `vocabularies/index.json`
- `vocabularies/*.json`
- `dictionaries/cig_2025_01.dictionary.json`
- `dictionaries/cig_2025_01.dictionary.md`
- `data/warehouse/parquet/...`
- `data/warehouse/anac.duckdb`

## Current semantic and warehouse capabilities

The repository now supports:

- externally resolved vocabulary joins for the wired CIG code fields
- inline code/label resolution when dedicated external vocabularies do not yet exist
- metadata-queryable warehouse lineage through `anac_*` views
- local stats and partition inspection without scanning arbitrary user tables
- read-only query mode by default, with explicit write opt-in and confirmation
- safe drop planning/execution that immediately reconciles local metadata after file deletion

## Current findings carried forward

- January 2025 and January 2007 CIG schemas still expose the same 61 column names in the compared month pair.
- Type and nullability drift exist historically even when column presence stays aligned.
- Vocabulary normalization remains conservative: the repository preserves collisions and source distinctions rather than inventing merged canonical codes.
- The live CIG warehouse is not strictly one-row-per-`cig`, so integrity validation treats that as warning-level behavior rather than assuming a single-row primary key.

## Testing baseline

The repository test suite now covers:

- shared envelope and error semantics
- config precedence, persistence, and validation
- path resolution and temporal selection parsing
- dataset-family registry behavior
- metadata discoverability views
- query policy and timeout enforcement
- stats summaries and profiling
- update planning and execution
- drop planning, execution, and metadata cleanup
- legacy Phase 1 / Phase 2 behaviors that still remain supported

Run the full suite with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Remaining open questions

- How far should update support be generalized beyond the current CIG family?
- Which additional dataset families deserve first-class profile/statistics treatment after publication?
- Should the publication phase keep every low-level legacy command visible by default, or eventually move them behind a clearer compatibility grouping?
