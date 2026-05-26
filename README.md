# ANAC Explorator

This project provides a local CLI and Python toolkit for working with the ANAC (Autorità Nazionale Anticorruzione) open-data ecosystem. It now ships a **completed Phase 3 CLI contract** centered on a queryable local DuckDB/Parquet warehouse and a stable automation-friendly command surface.

## Current status

The preferred executable is:

```bash
anacx
```

The stable Phase 3 commands are:

| Command | Purpose |
| --- | --- |
| `anacx datasets` | discover logical dataset families and local/remote coverage |
| `anacx download` | plan or apply raw downloads and optional warehouse loads |
| `anacx schema` | inspect local schema artifacts, semantic overlays, diffs, and DDL |
| `anacx query` | execute safe SQL against DuckDB plus `anac_*` metadata views |
| `anacx stats` | summarize local storage and optionally profile live dataset values |
| `anacx update` | plan or apply incremental updates for CIG and locally materialized vocabulary families |
| `anacx config` | inspect, persist, reset, and validate CLI configuration |
| `anacx drop` | safely prune local raw files, Parquet slices, or both |

The project still ships the legacy executable alias `anac-explorator` and the earlier low-level subcommands for backwards compatibility, but new usage should target `anacx` and the Phase 3 commands above.

## Quick start

Install the package in editable mode:

```bash
python3 -m pip install -e .
```

Inspect the current config:

```bash
anacx config show --format yaml
```

Discover the local and logical dataset families:

```bash
anacx datasets --format table
anacx datasets cig --format json
```

Plan or execute a local CIG materialization:

```bash
anacx download cig --year 2025 --month 1 --dry-run --format json
anacx download cig --year 2025 --month 1 --output-format parquet --format json
```

Inspect schema, metadata, and local stats:

```bash
anacx schema cig --describe --format json
anacx query "SELECT dataset, category FROM anac_datasets ORDER BY dataset LIMIT 5" --format json
anacx stats cig --partitions --format json
```

Plan safe maintenance operations:

```bash
anacx update cig --dry-run --format json
anacx update bandi-cig-tipo-scelta-contraente --dry-run --format json
anacx drop cig --dry-run --format json
```

## Current architecture

The repository is now:

> **artifact-driven, CLI-first, DuckDB-backed, and currently strongest on the monthly CIG family**

The main layers are:

1. CKAN discovery plus Playwright-backed ANAC access
2. manifest-backed raw downloads under `data/raw/...`
3. reusable schema, vocabulary, and dictionary artifacts under `schemas/`, `vocabularies/`, and `dictionaries/`
4. Parquet-backed analytical storage under `data/warehouse/parquet/...`
5. DuckDB catalog state in `data/warehouse/anac.duckdb`
6. SQL-queryable metadata discoverability views such as `anac_datasets`, `anac_loaded_resources`, and `anac_partitions`
7. a stable command envelope, error model, config layer, and path-resolution layer for the top-level CLI

For a broader overview, see `architecture-summary.md` and `cli-specification.md`.

## Local state and artifacts

Important generated state lives in:

- `data/raw/...` — manifest-backed downloaded resources
- `schemas/*.json` — schema mappings and historical comparisons
- `vocabularies/index.json` and `vocabularies/*.json` — vocabulary crosswalks and join metadata
- `dictionaries/*.json` and `dictionaries/*.md` — field dictionaries
- `data/warehouse/parquet/...` — durable analytical payload
- `data/warehouse/anac.duckdb` — DuckDB metadata catalog and registered views

The metadata layer exposed through `anacx query` is rebuilt from these artifact and catalog surfaces instead of being maintained as a second permanent warehouse.

## Configuration and environment

The canonical persisted config path is:

```bash
~/.config/anacx/config.json
```

If an older `anac-explorator` config directory already exists, the CLI will reuse it automatically for compatibility.

The config system prefers the canonical `ANAC_*` environment variable names, while still accepting `ANAC_EXPLORATOR_*` compatibility names. Common knobs include:

- `ANAC_TRANSPORT`
- `ANAC_PROXY_URL`
- `ANAC_USER_AGENT`
- `ANAC_ACCEPT_LANGUAGE`
- `ANAC_REFERER`
- `ANAC_TIMEOUT`

## Network note

From this environment, direct HTTP requests to the ANAC portal are often blocked by the WAF. The validated working path is Playwright transport:

```bash
anacx datasets cig --transport playwright --format json
anacx download cig --year 2025 --month 1 --transport playwright --dry-run --format json
```

The default browser-like headers are already configured, so a manual user-agent override is usually no longer required.

## Legacy compatibility

The older low-level commands remain available, including:

- `package-show`
- `download-dataset-csv`
- `download-dataset-resource`
- `download-dataset-to-parquet`
- `sync-cig-periods`
- `download-cig-sample`
- `inspect-csv-schema`
- `parse-resource`
- `clean-resource`
- `load-downloaded-resource`
- `query-local-data`
- `validate-local-data-integrity`

They exist to preserve older workflows and scripts, but they are no longer the preferred interface for users or automation. The Phase 3 commands should be considered canonical for new integrations and publication material.

## Testing

Run the full test suite with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Project references

- `cli-specification.md` — normative Phase 3 CLI contract
- `cli-implementation-plan.md` — implementation sequence and completion criteria
- `architecture-summary.md` — current architectural overview
- `specification.md` — broader project and artifact summary
