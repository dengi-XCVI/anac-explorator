# Architecture Summary

## Current architecture state

The project now ships a **completed Phase 3 CLI surface** on top of the earlier research, download, schema, and DuckDB/Parquet pipeline work. The system is best described as:

> **artifact-driven, CLI-first, DuckDB-backed, and currently strongest on the monthly CIG family**

What changed in the Phase 3 completion pass is not a new storage engine, but a new **stable command contract** around the existing baseline:

1. a canonical `anacx` executable
2. a shared JSON result envelope and stable error model
3. a shared config and path-resolution layer
4. a dataset-family registry and temporal selection model
5. SQL-queryable metadata discoverability views
6. stable `datasets`, `download`, `schema`, `query`, `stats`, `update`, `config`, and `drop` commands

## Major layers

| Layer | Responsibility | Main files |
| --- | --- | --- |
| Source system | ANAC CKAN metadata plus downloadable CSV/JSON resources | `research/ANAC-data.md` |
| Access layer | Reach CKAN and resource endpoints through HTTP or Playwright | `src/anac_explorator/ckan.py`, `src/anac_explorator/browser.py` |
| Config and paths | Merge defaults, config files, env vars, and CLI overrides into one runtime config | `src/anac_explorator/config.py`, `src/anac_explorator/paths.py` |
| Shared command contract | Normalize success/error payloads and stdout/stderr behavior | `src/anac_explorator/models.py`, `src/anac_explorator/output.py`, `src/anac_explorator/errors.py` |
| Family registry and selection | Resolve logical dataset families, temporal scopes, and adapter capabilities | `src/anac_explorator/catalog.py`, `src/anac_explorator/selection.py` |
| Download/cache layer | Download resources, persist manifests, and reuse local cache state | `src/anac_explorator/sample.py` |
| Schema and semantic artifacts | Inspect schemas, compare history, build vocabulary crosswalks, and publish dictionaries | `src/anac_explorator/schema.py`, `src/anac_explorator/comparison.py`, `src/anac_explorator/vocabulary.py`, `src/anac_explorator/dictionary.py`, `src/anac_explorator/schema_service.py` |
| Warehouse layer | Load manifest-backed resources into Parquet, maintain DuckDB catalog tables, and expose logical views | `src/anac_explorator/loader.py` |
| Metadata discoverability layer | Rebuild the `anac_*` discoverability views from local artifacts and warehouse state | `src/anac_explorator/metadata_views.py` |
| Stats/update/drop orchestration | Summarize local state, plan/apply CIG updates, and safely prune local storage | `src/anac_explorator/stats.py`, `src/anac_explorator/drop.py`, `src/anac_explorator/catalog.py` |
| CLI and compatibility surface | Expose the Phase 3 commands plus retained legacy shims | `src/anac_explorator/cli.py`, `src/anac_explorator/__main__.py` |

## Canonical local state surfaces

The stable CLI contract depends on five persistent local layers:

| Layer | Purpose | Current source |
| --- | --- | --- |
| Raw resource cache | manifest-backed downloaded resources | `data/raw/...` |
| Schema artifacts | reusable schema maps and historical schema comparisons | `schemas/*.json` |
| Semantic artifacts | vocabulary crosswalks and data dictionaries | `vocabularies/*.json`, `dictionaries/*.json` |
| Warehouse payload | durable analytical data | `data/warehouse/parquet/...` |
| Warehouse catalog | metadata for loads, views, and periods | `data/warehouse/anac.duckdb` |

The warehouse catalog still centers on the original storage baseline tables:

- `loaded_resources`
- `registered_views`
- `dataset_period_manifest`

Phase 3 adds a higher-level discoverability layer on top of them rather than replacing them.

## Practical runtime flow

The main end-to-end flow is now:

`ANAC CKAN -> access/config layer -> family registry + temporal selection -> manifest-backed raw download -> schema/dictionary/vocabulary artifacts -> DuckDB/Parquet load -> metadata discoverability views -> stable anacx command envelope`

In practice that means:

- `download` resolves a logical family into raw resources and optional Parquet loads
- `schema` reads local schema artifacts and semantic overlays
- `query` bootstraps the metadata views and runs safe SQL
- `stats` summarizes local metadata or profiles live dataset values
- `update` wraps the existing monthly CIG sync logic behind a normalized plan/apply contract
- `drop` maps local scope selections to exact files, deletes them safely, and reconciles metadata immediately

## Stable command surface

The preferred user and agent interface is:

- `anacx datasets`
- `anacx download`
- `anacx schema`
- `anacx query`
- `anacx stats`
- `anacx update`
- `anacx config`
- `anacx drop`

The repository still ships legacy low-level commands such as `package-show`, `download-cig-sample`, `query-local-data`, and `sync-cig-periods` for backwards compatibility. They remain available through the same parser and executable, but the Phase 3 commands are the canonical surface going forward.

The legacy executable name `anac-explorator` is also still shipped as a compatibility alias. The canonical config path is now `~/.config/anacx/config.json`, with automatic fallback to the older `anac-explorator` config directory when present.

## Current strengths

- **The Phase 3 CLI contract is fully wired.** All eight stable commands are implemented and tested.
- **The metadata layer is queryable.** `anac query` can inspect `anac_*` views without requiring users to manage those views manually.
- **The storage model remains efficient.** Durable payload lives in Parquet while DuckDB stores control-plane metadata and generated views.
- **The mutation commands are guarded.** Destructive and write-enabled flows use explicit confirmation and stable error envelopes.
- **The CIG family is production-ready by repo standards.** Download, schema inspection, querying, stats, update planning/execution, integrity validation, and drop workflows all exist for the current CIG baseline.

## Known boundaries

- The monthly **CIG family remains the most mature** dataset family.
- `update` and `drop` are intentionally strongest for CIG; other families cleanly reject unsupported operations.
- The broader warehouse and integrity strategy is still not generalized to every CKAN dataset family.
- The project remains **CLI-first**. There is still no web UI and no broader Python API ergonomics layer.

## Publication-readiness summary

For publication, the important architectural statement is now:

> The repository no longer exposes only a collection of ingestion utilities. It exposes a coherent local ANAC warehouse interface with a stable top-level CLI, a documented compatibility path, and a metadata layer intended for both humans and automation.
