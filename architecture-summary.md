# Architecture Summary

## Current architecture state

The project is currently a **research-complete, pipeline-foundation ANAC ingestion tool**. It already covers:

1. **dataset discovery and access hardening**
2. **raw resource download and local materialization**
3. **schema inspection and cross-year comparison**
4. **controlled-vocabulary and data-dictionary enrichment**
5. **resource parsing and cleaning for later loading**

It does **not** yet implement the planned **DuckDB/Parquet loader**, **incremental merge/update workflow**, or **local analytical query layer**.

## The architecture as it exists today

| Layer | Responsibility | Main files | Current status |
| --- | --- | --- | --- |
| Source system | ANAC CKAN metadata plus downloadable CSV/JSON resources | `research/ANAC-data.md` | External dependency |
| Access layer | Reach CKAN and resource endpoints through HTTP or Playwright | `src/anac_explorator/ckan.py`, `src/anac_explorator/browser.py` | Implemented |
| Download/cache layer | Resolve a resource, download it, materialize it, and persist a manifest-backed cache | `src/anac_explorator/sample.py` | Implemented |
| Schema/research layer | Inspect raw CSVs and compare schemas across years | `src/anac_explorator/schema.py`, `src/anac_explorator/comparison.py` | Implemented |
| Semantic enrichment layer | Build vocabulary crosswalks, join contracts, gap analysis, and the field dictionary | `src/anac_explorator/vocabulary.py`, `src/anac_explorator/dictionary.py` | Implemented |
| Parse/clean layer | Turn CSV/JSON into structured Python objects and cleaned records | `src/anac_explorator/parsing.py`, `src/anac_explorator/cleaner.py`, `src/anac_explorator/models.py` | Implemented |
| Interface layer | Expose all current workflows through the CLI and module entrypoints | `src/anac_explorator/cli.py`, `src/anac_explorator/__main__.py` | Implemented |
| Persistence/query layer | Load curated data into DuckDB/Parquet and expose query surfaces | _not built yet_ | Missing |

## Practical data flow

Today the main flow is:

`ANAC CKAN -> CkanClient/PlaywrightFetcher -> download_dataset_resource -> manifest-backed raw files -> schema/vocabulary/dictionary artifacts -> parse_resource -> clean_resource`

That means the repository already has two strong foundations:

- a **raw lineage foundation**, because downloaded resources are tracked with `manifest.json`
- a **semantic foundation**, because the current CIG surface is documented with vocabulary links, inline-code analysis, and join metadata

## Repository-level architecture notes

- **The project is artifact-driven.** Important outputs are written to disk and reused:
  - `data/raw/...` for downloaded resources
  - `schemas/*.json` for schema maps and comparisons
  - `vocabularies/*.json` plus `vocabularies/index.json` for normalized crosswalks
  - `dictionaries/*.json` and `*.md` for the current field dictionary
- **The internal contract is dataclass-based.** The pipeline uses typed dataclasses instead of Pydantic for CKAN metadata, schemas, manifests, parsed rows, and cleaned records.
- **The current architecture is CLI-first.** The reusable Python functions exist, but the main user-facing surface is the CLI.
- **The system is source-preserving by design.** Raw column names are kept intact, normalization is conservative, and semantic links are added without rewriting source meaning.
- **The network model is environment-aware.** Direct HTTP can fail against the ANAC WAF, so Playwright is treated as the validated transport path from this runtime.

## What is already strong

- **Discovery and download are real, not stubbed.**
- **Schema mapping and cross-year comparison are already operational.**
- **Vocabulary and dictionary artifacts already make the CIG surface more queryable by humans and LLMs.**
- **The Phase 2 parser/cleaner path is in place and integration-tested.**
- **The current cache tree has been brought under manifest tracking for the main resources already in use.**

## What is still missing architecturally

- There is **no storage layer yet** for turning cleaned records into durable analytical tables.
- There is **no incremental-update strategy yet** for merging the ANAC delta datasets into loaded history.
- There is **no integrity-validation layer yet** to enforce row-count checks, expected joins, or vocabulary-linked consistency after loading.
- There is **no query engine/UI layer yet** beyond the current operational CLI commands.

## Current architectural maturity

The project is best described as:

> **Phase 1 complete, Phase 2 foundation complete, analytics layer not yet built.**

In other words, the repository is already past the exploratory stage and now has a credible ingestion foundation, but it has not yet crossed into the final intended architecture of **local relational storage + SQL querying + LLM query support**.

## Next three steps

### 1. Build the loader into DuckDB/Parquet

Convert cleaned CSV/JSON resources into durable local tables with a stable naming scheme and source lineage. This is the missing bridge between the current pipeline and the planned local database architecture.

Concretely, this should add:

- table/materialization conventions
- DuckDB load logic
- Parquet export or backing tables
- links from loaded tables back to source manifests and schemas

### 2. Add incremental delta-update handling

Implement the strategy for the `cig` delta dataset and other update-oriented sources so the local store can be refreshed without reloading everything from scratch.

Concretely, this should add:

- update checkpoints or watermarks
- merge/upsert rules
- duplicate/conflict handling
- repeatable refresh commands

### 3. Add integrity and relationship validation

Once data is loaded, validate that the stored model matches expectations from the schema and vocabulary layers. This is the step that will make the future query layer reliable.

Concretely, this should add:

- row-count and load-completeness checks
- join validation against controlled vocabularies
- nullability/type drift checks on loaded tables
- relationship checks for future cross-dataset joins

## Bottom line

The architecture is currently **well prepared for ingestion**, **well documented semantically**, and **not yet complete as an analytics system**. The next step is no longer research; it is to build the **local storage layer**, then the **update model**, then the **integrity guarantees** that the later query and LLM layers will rely on.
