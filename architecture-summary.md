# Architecture Summary

## Current architecture state

The project is currently a **research-complete, ingestion-and-storage baseline ANAC tool**. It already covers:

1. **dataset discovery and access hardening**
2. **raw resource download and local materialization**
3. **schema inspection and cross-year comparison**
4. **controlled-vocabulary and data-dictionary enrichment**
5. **resource parsing and cleaning for later loading**
6. **DuckDB/Parquet loading plus local SQL view registration**
7. **incremental monthly CIG period sync with slice replacement**

It does **not** yet implement the broader planned **integrity-validation layer** or a richer end-user analytical interface beyond the local SQL facade, and the incremental workflow currently targets the monthly CIG family only.

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
| Persistence/query layer | Load manifest-backed resources into partitioned Parquet and expose DuckDB views over them | `src/anac_explorator/loader.py`, `src/anac_explorator/cli.py`, `src/anac_explorator/models.py` | Implemented baseline |

## Practical data flow

Today the main flow is:

`ANAC CKAN -> CkanClient/PlaywrightFetcher -> download_dataset_resource/download_dataset_to_parquet -> manifest-backed raw files and schemas -> vocabulary/dictionary artifacts -> load_downloaded_resource -> partitioned parquet -> query_local_data`

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
- **The storage model is now view-first.** DuckDB stores metadata and generated views, while the durable analytical payload lives in Parquet.
- **The orchestration path is now partially integrated.** One CLI command can reuse the download cache, ensure a schema artifact exists, load to Parquet, prune the extracted CSV, and register vocabulary cross-reference views for DuckDB joins.
- **The network model is environment-aware.** Direct HTTP can fail against the ANAC WAF, so Playwright is treated as the validated transport path from this runtime.

## What is already strong

- **Discovery and download are real, not stubbed.**
- **Schema mapping and cross-year comparison are already operational.**
- **Vocabulary and dictionary artifacts already make the CIG surface more queryable by humans and LLMs.**
- **The Phase 2 parser/cleaner path is in place and integration-tested.**
- **The current cache tree has been brought under manifest tracking for the main resources already in use.**
- **A first DuckDB/Parquet loader exists and keeps large scans inside DuckDB instead of Python memory.**
- **The semantic crosswalk artifacts can now be surfaced directly inside DuckDB for joinable querying.**

## What is still missing architecturally

- The incremental-update strategy is now implemented only for the **monthly CIG family**; other dataset families still use the one-shot load path.
- There is **no integrity-validation layer yet** to enforce row-count checks, expected joins, or vocabulary-linked consistency after loading.
- There is **no broader query engine/UI layer yet** beyond the current raw SQL CLI facade.
- The current loader scope is still intentionally narrow: monthly CIG resources and the already-wired vocabulary datasets.

## Current architectural maturity

The project is best described as:

> **Phase 1 complete, Phase 2 storage baseline complete, first incremental CIG slice built, integrity layer still pending.**

In other words, the repository is already past the exploratory stage and now has a credible ingestion foundation plus a local storage baseline, but it has not yet crossed into the final intended architecture of **incremental relational refresh + integrity guarantees + richer query and LLM support**.

## Next three steps

### 1. Extend incremental delta-update handling beyond monthly CIG

Generalize the new period-catalog workflow to other update-oriented sources so the local store can be refreshed without reloading everything from scratch.

Concretely, this should add:

- update checkpoints or watermarks
- merge/upsert rules
- duplicate/conflict handling
- repeatable refresh commands

### 2. Add integrity and relationship validation

Once data is loaded, validate that the stored model matches expectations from the schema and vocabulary layers. This is the step that will make the future query layer reliable.

Concretely, this should add:

- row-count and load-completeness checks
- join validation against controlled vocabularies
- nullability/type drift checks on loaded tables
- relationship checks for future cross-dataset joins

### 3. Expand the query surface carefully

Build on the current raw SQL facade with a slightly richer local analytical interface, but keep the system view-first and avoid duplicating Parquet-backed data unless reuse clearly justifies it.

Concretely, this should add:

- small dataset/view discovery commands
- reusable example analytical queries
- query ergonomics for bounded-memory output
- later LLM-oriented schema/context helpers

## Bottom line

The architecture is currently **well prepared for ingestion**, **semantically documented**, and now **equipped with a first local storage/query baseline**. The next steps are to add the **update model**, then the **integrity guarantees**, then the broader **query and LLM surfaces** that will rely on that storage layer.
