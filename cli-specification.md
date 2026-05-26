# Phase 3 CLI Specification

## 1. Purpose

This document defines the **stable Phase 3 CLI contract** for the ANAC explorator project. It replaces the earlier scaffold with a detailed specification that is intended to drive implementation, testing, and later agent usage.

Phase 3 is the point where the project stops being only a research and ingestion baseline and becomes a **queryable local procurement interface** with:

1. a coherent human CLI,
2. a stable machine-readable JSON contract,
3. a discoverability layer for agents,
4. predictable semantics for download, schema inspection, querying, and incremental update.

This specification is normative for the new CLI surface:

- `datasets`
- `download`
- `schema`
- `query`
- `stats`
- `update`
- `config`
- `drop`

Everything else is out of the stable Phase 3 contract.

---

## 2. Architecture context

### 2.1 External source system

The source of truth is the ANAC open data portal:

- CKAN-based metadata API
- downloadable CSV / JSON resources
- strong temporal behavior for CIG and related families
- very large fact-like datasets
- browser-like transport requirements from this runtime

The CLI therefore needs to model **both metadata discovery and local materialization**, not just one-shot downloads.

### 2.2 Current repository baseline

The repository already implements the Phase 1 and Phase 2 foundations:

- CKAN metadata discovery
- Playwright-backed transport for WAF-protected access
- manifest-backed downloads
- schema inspection and comparison
- vocabulary crosswalk and dictionary generation
- CSV / JSON parsing and cleaning
- DuckDB / Parquet loading
- local SQL querying
- monthly CIG incremental sync
- warehouse integrity validation

The current system is therefore best described as:

> **artifact-driven, CLI-first, DuckDB-backed, and currently strongest on the monthly CIG family**

Phase 3 must build on that foundation instead of replacing it.

### 2.3 Canonical local state surfaces

The stable CLI contract depends on five local state layers:

| Layer | Purpose | Current source |
| --- | --- | --- |
| Raw resource cache | manifest-backed downloaded resources | `data/raw/...` |
| Schema artifacts | reusable schema maps and schema comparisons | `schemas/*.json` |
| Semantic artifacts | vocabulary crosswalks and field dictionary | `vocabularies/*.json`, `dictionaries/*.json` |
| Warehouse payload | durable analytical data | `data/warehouse/parquet/...` |
| Warehouse catalog | metadata for loads, views, and periods | DuckDB tables in `data/warehouse/anac.duckdb` |

### 2.4 Existing warehouse catalog tables

The current loader already maintains the three core metadata tables that Phase 3 should treat as the storage baseline:

| Table | Role |
| --- | --- |
| `loaded_resources` | one row per manifest-backed warehouse load |
| `registered_views` | one row per registered DuckDB view over Parquet |
| `dataset_period_manifest` | one row per imported period for update-aware families such as monthly CIG |

Phase 3 does **not** replace these tables. It adds a higher-level discoverability layer on top of them.

---

## 3. Phase 3 scope and non-goals

### 3.1 In scope

Phase 3 covers:

1. a new stable command grammar under `anacx`
2. stable output semantics for humans and agents
3. read-only metadata discoverability via SQL-accessible views
4. stable JSON response schemas for all Phase 3 commands
5. explicit temporal slice semantics
6. safe mutation semantics for download, update, and drop
7. local configuration management
8. local storage-pruning workflows

### 3.2 Out of scope

Phase 3 does not standardize:

- Python API ergonomics
- notebook UX
- a web UI
- broader cross-dataset relational modeling beyond the current storage baseline
- full generic incremental update support for all 70 CKAN datasets
- legacy subcommands as part of the preferred user or agent surface

Legacy commands may remain as compatibility shims during migration, but they are not the Phase 3 contract.

---

## 4. Command naming and compatibility

### 4.1 Canonical executable name

The canonical Phase 3 executable name is:

```bash
anacx
```

### 4.2 Compatibility alias

During migration, the project may continue to ship:

```bash
anac-explorator
```

as a compatibility alias. That alias may expose legacy subcommands temporarily, but **agent-oriented automation should target `anacx`**.

### 4.3 Stdout / stderr contract

This is a hard requirement for Phase 3:

- **stdout** carries the command result only
- **stderr** carries progress, logs, warnings rendered for humans, and debug detail
- in `--format json`, stdout must contain **exactly one JSON document**

No human progress text may be mixed into JSON stdout.

---

## 5. Design principles

### 5.1 Agent-safe by default

Every Phase 3 command must support `--format json` with a stable schema.

### 5.2 Read-only by default

Commands that mutate local state are explicit:

- `download`
- `update`
- `config set`
- `config unset`
- `config reset`
- `drop`

`query` is read-only unless the user explicitly opts into writes.

### 5.3 Idempotent local mutations

Re-running a successful `download` or `update` should not duplicate warehouse state or corrupt metadata.

### 5.4 Artifact lineage is preserved

The CLI should preserve the lineage chain:

`remote dataset -> CKAN resource -> local manifest -> schema artifact -> parquet slice -> query view`

### 5.5 Phase-appropriate realism

The CLI contract must reflect the real project:

- CIG is the most mature family
- the ANAC WAF matters
- schemas evolve historically
- not every dataset family supports the same update model yet

---

## 6. Canonical concepts

### 6.1 Dataset family

A **dataset family** is the stable user-facing dataset identifier used by the Phase 3 CLI:

- `cig`
- `smartcig`
- `stazioni-appaltanti`
- `aggiudicatari`
- vocabulary families

This is the identifier accepted by:

- `anacx datasets`
- `anacx download`
- `anacx schema`
- `anacx stats`
- `anacx update`
- `anacx drop`

It is intentionally higher-level than the raw CKAN package slug.

### 6.2 CKAN dataset id

A **CKAN dataset id** is the raw remote package slug, for example:

- `cig-2025`
- `smartcig-2025`
- `stazioni-appaltanti`

Periodized families may map one dataset family to many CKAN dataset ids.

### 6.3 Resource

A **resource** is one downloadable CKAN artifact, such as:

- `cig_csv_2025_01`
- `cig_json_2025_01`

### 6.4 Slice

A **slice** is the normalized temporal selector used by the CLI. The canonical slice format is:

```text
YYYY-MM
```

Examples:

- `2025-01`
- `2025-12`

### 6.5 Periodized family

A **periodized family** is a dataset family whose resources are selected by year or month:

- `cig`
- `smartcig`
- OCDS monthly families

### 6.6 Snapshot family

A **snapshot family** is a dataset family that is effectively one logical dataset without month selection:

- `stazioni-appaltanti`
- some vocabulary datasets

Temporal flags are invalid for snapshot families.

---

## 7. Global semantics

### 7.1 Invocation shape

```bash
anacx [GLOBAL_OPTIONS] COMMAND [ARGS] [COMMAND_OPTIONS]
```

### 7.2 Global options

The following options are shared across the Phase 3 surface:

| Option | Meaning |
| --- | --- |
| `--config PATH` | explicit config file path |
| `--no-config` | ignore config files and use defaults plus CLI flags |
| `-q`, `--quiet` | errors only on stderr |
| `-v`, `--verbose` | progress and informational logs on stderr |
| `--debug` | debug logs and trace detail on stderr |
| `--yes` | suppress confirmations for destructive or write-enabled actions |

`--format` is shared semantically across commands but its allowed values depend on the command.

### 7.3 Configuration precedence

Effective values resolve in this order:

1. explicit CLI flags
2. environment variables
3. config file
4. built-in defaults

### 7.3.1 Environment variable compatibility

Phase 3 should standardize on `ANAC_*` environment variables, but the implementation should read the existing `ANAC_EXPLORATOR_*` names for backward compatibility where a direct mapping exists.

### 7.4 Path defaults

Phase 3 keeps the current artifact-driven repository layout as the default baseline:

| Logical path | Default |
| --- | --- |
| raw resource directory | `data/raw` |
| warehouse directory | `data/warehouse` |
| warehouse database | `data/warehouse/anac.duckdb` |
| schemas directory | `schemas` |
| vocabulary index path | `vocabularies/index.json` |
| dictionaries directory | `dictionaries` |

These values are configurable; the defaults are not normative for other deployments.

### 7.5 Network option semantics

Commands that contact ANAC metadata or download remote resources share these options:

| Option | Meaning |
| --- | --- |
| `--transport auto|playwright|http` | remote access strategy |
| `--timeout SECONDS` | network timeout |
| `--proxy-url URL` | optional proxy |
| `--user-agent VALUE` | request header override |
| `--accept-language VALUE` | request header override |
| `--referer VALUE` | request header override |

Because ANAC access is environment-sensitive, `auto` should prefer the **known-good path for the runtime** rather than pretending all transports are equally viable.

### 7.6 Output format semantics

### 7.6.1 Shared formats

All Phase 3 commands support:

```text
table | json
```

### 7.6.2 Command-specific extensions

| Command | Extra formats |
| --- | --- |
| `query` | `csv`, `parquet` |
| `config show` | `yaml` |

### 7.7 Logging and JSON purity

When `--format json` is used:

- stdout contains only the final JSON document
- stderr may still contain progress or warning text unless `--quiet`
- debug output must stay on stderr

### 7.8 Mutation safety

The following rules are mandatory:

1. `download` and `update` must be idempotent with respect to local manifests and warehouse slices.
2. `query` must reject write SQL unless `--allow-write` is provided.
3. `query --allow-write` must require `--yes` for non-interactive execution.
4. `config reset` must require `--yes`.
5. `drop` must require `--yes` and keep local metadata aligned with physical deletions.

---

## 8. Temporal selection grammar

Temporal selection must mean the same thing across:

- `download`
- `schema`
- `stats`
- `update`
- `drop`

### 8.1 Shared flags

| Flag | Meaning |
| --- | --- |
| `--year YYYY` | one year |
| `--year YYYY-YYYY` | inclusive year range |
| `--month M` | one month inside the selected year |
| `--month M-M` | inclusive month range inside the selected year |
| `--slice YYYY-MM[,YYYY-MM,...]` | explicit slice list |
| `--latest` | newest remote slice for periodized families |

### 8.2 Valid combinations

| Combination | Valid |
| --- | --- |
| `--year` | yes |
| `--year --month` | yes |
| `--slice` | yes |
| `--latest` | yes |
| `--year --slice` | no |
| `--latest` with anything else | no |
| `--month` without `--year` | no |

### 8.3 Normalization rules

The CLI must normalize:

- `--month 1` -> `01`
- `--slice 2025-1` -> `2025-01`
- `--year 2020-2025` -> inclusive year range
- `--month 1-6` -> inclusive month range

### 8.4 Family applicability

Temporal flags are:

- required or meaningful for periodized families
- invalid for snapshot families

Invalid usage must return a structured CLI validation error rather than silently ignoring flags.

---

## 9. Source-format and persistence semantics

### 9.1 Source format

`download` supports:

```text
--source-format auto|csv|json
```

Meaning:

- `auto`: choose the preferred implementation path for the family
- `csv`: require CSV resource selection
- `json`: require JSON resource selection

`auto` should prefer the format that best supports the local warehouse workflow for that family. For the current CIG path, that is CSV.

### 9.2 Output format

`download` supports:

```text
--output-format parquet|raw|both
```

Meaning:

| Value | Behavior |
| --- | --- |
| `parquet` | download and load into the warehouse; extracted working file may be pruned after successful load |
| `raw` | only materialize manifest-backed raw artifacts; do not load into the warehouse |
| `both` | materialize raw artifacts and load into the warehouse without pruning the working file |

This distinction is important because ANAC source format and local storage format are not the same concern.

---

## 10. Phase 3 command contracts

### 10.1 `anacx datasets`

#### Purpose

`datasets` is the catalog and discoverability surface. It answers:

- what logical dataset families exist?
- what remote temporal coverage exists?
- what is already materialized locally?
- which families are queryable or update-capable?

#### Syntax

```bash
anacx datasets [DATASET] [OPTIONS]
```

#### Arguments

| Argument | Meaning |
| --- | --- |
| `DATASET` | optional dataset family identifier for single-dataset detail mode |

#### Options

| Option | Meaning |
| --- | --- |
| `--search TEXT` | search across id, title, description, and aliases |
| `--year YYYY` | filter families that have remote coverage for a year |
| `--downloaded` | show only families with local materialization |
| `--missing` | show only families known remotely but not yet materialized locally |
| `--long` | include full detail fields in table mode |
| `--source-format csv|json` | filter by available remote source format |
| `--format table|json` | result format |

#### Behavior

1. Without `DATASET`, `datasets` returns one row per logical dataset family.
2. With `DATASET`, it returns a single detailed object for that family.
3. The command is allowed to use remote metadata, local metadata, or both.
4. If remote access fails but local metadata is available, the command may return partial local results with warnings.
5. If `DATASET` does not resolve, the command returns `DATASET_NOT_FOUND`.

#### Required fields in detail mode

The dataset detail payload must include:

- `dataset`
- `title`
- `category`
- `description`
- `coverage_kind`
- `remote_dataset_ids`
- `available_source_formats`
- `remote_coverage`
- `local_status`
- `query_view_name`
- `update_supported`
- `dictionary_available`
- `vocabulary_views`

#### Examples

```bash
anacx datasets
anacx datasets cig --format json
anacx datasets --search pnrr
anacx datasets --downloaded
```

---

### 10.2 `anacx download`

#### Purpose

`download` is the local materialization command. It resolves user intent into:

1. dataset-family selection,
2. temporal slice planning,
3. source resource selection,
4. manifest-backed download,
5. optional schema resolution,
6. optional Parquet load and view refresh.

#### Syntax

```bash
anacx download <DATASET> [OPTIONS]
```

#### Options

| Option | Meaning |
| --- | --- |
| temporal flags | shared temporal grammar from Section 8 |
| `--resource-name NAME` | exact CKAN resource override when needed |
| `--source-format auto|csv|json` | required source format |
| `--output-format parquet|raw|both` | local persistence mode |
| `--force-download` | re-fetch raw source even when manifest cache exists |
| `--force-load` | re-run warehouse load even when the load is already registered |
| `--validate` | run integrity validation after a successful load |
| `--dry-run` | emit the resolved plan without downloading or loading |
| `--format table|json` | result format |
| network options | shared network flags from Section 7.5 |

#### Behavior

1. `<DATASET>` is always a logical dataset family.
2. For periodized families, the command resolves the requested slices into one or more CKAN dataset ids and resources.
3. For snapshot families, temporal flags are invalid.
4. In `raw` mode, the command must stop after manifest-backed local materialization.
5. In `parquet` and `both` modes, the command must ensure a warehouse load result exists for every selected resource.
6. `--dry-run` must return the exact planned slices and actions.
7. `--validate` must run only after all selected slices have been loaded successfully.

#### CIG-specific Phase 3 expectation

The initial implementation must fully support:

- `cig`
- year selection
- month selection
- explicit slice selection
- latest selection
- CSV-first acquisition
- Parquet load
- manifest and period-catalog updates

Other families may initially support only a subset of the contract if the dataset registry marks them accordingly.

#### Examples

```bash
anacx download cig --year 2025 --month 1
anacx download cig --slice 2025-01,2025-03 --dry-run --format json
anacx download stazioni-appaltanti --output-format raw
anacx download cig --latest --output-format both --validate
```

---

### 10.3 `anacx schema`

#### Purpose

`schema` is the structural inspection surface. It exposes:

- canonical field lists
- types and nullability
- semantic dictionary detail
- historical drift
- warehouse DDL when relevant

#### Syntax

```bash
anacx schema <DATASET> [OPTIONS]
```

#### Options

| Option | Meaning |
| --- | --- |
| temporal flags | select the schema target for periodized families |
| `--diff LEFT RIGHT` | compare two schema targets |
| `--describe` | include dictionary and vocabulary metadata |
| `--ddl` | output the DuckDB view definition instead of a column listing |
| `--source canonical|warehouse` | choose artifact-driven or warehouse-driven inspection |
| `--format table|json` | result format |

#### Target resolution

`schema` resolves one of three modes:

1. **canonical mode**: default structural schema for the family
2. **targeted mode**: schema for a selected year or slice
3. **diff mode**: comparison between two schema targets

#### Behavior

1. `schema` is read-only and must never download data implicitly.
2. If the requested schema artifact or warehouse view is unavailable locally, the command returns `SCHEMA_NOT_AVAILABLE`.
3. `--describe` enriches column metadata with dictionary fields when available.
4. `--ddl` is mutually exclusive with `--diff`.

#### Schema target token format for `--diff`

Each diff operand must be one of:

- `canonical`
- `YYYY`
- `YYYY-MM`

Examples:

```bash
anacx schema cig
anacx schema cig --year 2025
anacx schema cig --slice 2025-01 --describe
anacx schema cig --diff 2007-01 2025-01 --format json
anacx schema cig --ddl
```

---

### 10.4 `anacx query`

#### Purpose

`query` executes SQL against:

- dataset views
- vocabulary crosswalk views
- metadata discoverability views

#### Syntax

```bash
anacx query "<SQL>" [OPTIONS]
```

#### Options

| Option | Meaning |
| --- | --- |
| `--format table|json|csv|parquet` | output format |
| `--output PATH` | required for `csv` and `parquet`; optional for `json` |
| `--row-limit N` | cap returned rows; `0` means no CLI cap |
| `--timeout SECONDS` | execution timeout |
| `--explain` | return the query plan instead of executing the query |
| `--allow-write` | opt into write SQL |
| `--yes` | required with `--allow-write` in non-interactive mode |
| `--db-path PATH` | override warehouse database path |

#### Read-only default

Without `--allow-write`, the command must reject SQL that mutates state, including:

- `INSERT`
- `UPDATE`
- `DELETE`
- `CREATE`
- `DROP`
- `ALTER`
- `COPY ... TO`

Rejected write attempts return `WRITE_QUERY_BLOCKED`.

#### Output rules

1. `table` and `json` may print directly to stdout.
2. `csv` and `parquet` require `--output`.
3. `json` returns rows as JSON-friendly scalar values.
4. `--explain` returns a structured plan payload instead of result rows.

#### Supported logical relations

At minimum, `query` must expose:

- loaded dataset views such as `cig`
- registered vocabulary views such as `tipo_scelta_contraente`
- metadata views from Section 13

#### Examples

```bash
anacx query "SELECT COUNT(*) AS n FROM cig"
anacx query "SELECT * FROM anac_datasets ORDER BY dataset" --format json
anacx query "SELECT * FROM cig LIMIT 1000" --format csv --output out.csv
anacx query "EXPLAIN SELECT * FROM cig" --explain --format json
```

---

### 10.5 `anacx stats`

#### Purpose

`stats` is for observability and inventory, not free-form analysis. It answers:

- how much data is loaded?
- what is the storage footprint?
- what local coverage exists?
- what does a dataset look like at a summary level?

#### Syntax

```bash
anacx stats [DATASET] [OPTIONS]
```

#### Options

| Option | Meaning |
| --- | --- |
| temporal flags | restrict stats to a selected period scope |
| `--profile` | run a more expensive column-profile pass |
| `--partitions` | show partition completeness / coverage detail |
| `--format table|json` | result format |

#### Scope rules

| Invocation | Scope |
| --- | --- |
| `anacx stats` | global local-storage summary |
| `anacx stats <dataset>` | family-level summary |
| `anacx stats <dataset> --year ...` | subset summary |

#### Behavior

1. `stats` is local-only and read-only.
2. `--profile` is only valid when a dataset family is supplied.
3. `--partitions` is only valid for periodized families.
4. The command should prefer metadata tables and warehouse views before running expensive scans.

#### Examples

```bash
anacx stats
anacx stats cig
anacx stats cig --year 2025 --partitions --format json
anacx stats cig --profile
```

---

### 10.6 `anacx update`

#### Purpose

`update` is the incremental synchronization surface. It is not a synonym for `download`.

`update` must:

1. inspect local state,
2. compare it with remote metadata,
3. plan new or changed slices,
4. apply the plan safely,
5. optionally validate the result.

#### Syntax

```bash
anacx update [DATASET] [OPTIONS]
```

#### Options

| Option | Meaning |
| --- | --- |
| temporal flags | restrict update scope |
| `--refresh-changed` | include already loaded slices whose remote metadata changed |
| `--force-full` | rebuild all selected slices regardless of current state |
| `--validate` | run integrity validation after apply |
| `--dry-run` | emit the resolved update plan only |
| `--format table|json` | result format |
| shared path flags | override raw/schema/dictionary/warehouse locations |
| network options | shared network flags from Section 7.5 |

#### Scope rules

1. `anacx update` without `DATASET` targets all **locally present and update-capable** dataset families.
2. `anacx update <DATASET>` targets one dataset family.
3. Periodized families obey the shared temporal grammar.

#### Behavior

1. `update` may only target families the registry marks as update-capable.
2. `--dry-run` returns the exact plan with `download`, `refresh`, or `skip` actions.
3. `--refresh-changed` means "include already-known slices whose remote metadata indicates drift".
4. `--force-full` bypasses cache reuse for the selected scope.
5. `--validate` runs after all update actions complete successfully.

#### Phase 3 initial support

The current implementation supports:

1. `update cig` end to end for the monthly CIG family
2. `update <vocabulary-family>` for the wired snapshot vocabulary families, including explicit downstream CIG dictionary regeneration when the required local schema/comparison artifacts exist

Other families may still return `DATASET_UPDATE_NOT_SUPPORTED` until an adapter exists.

#### Examples

```bash
anacx update cig
anacx update cig --refresh-changed --dry-run --format json
anacx update cig --year 2025 --validate
anacx update bandi-cig-tipo-scelta-contraente --dry-run --format json
anacx update --dry-run
```

---

### 10.7 `anacx config`

#### Purpose

`config` manages persistent CLI configuration.

#### Syntax

```bash
anacx config <SUBCOMMAND> [ARGS] [OPTIONS]
```

#### Subcommands

| Subcommand | Meaning |
| --- | --- |
| `show` | show effective configuration |
| `get KEY` | read one config value |
| `set KEY VALUE` | persist one config value |
| `unset KEY` | remove one explicitly set key |
| `reset` | reset the full config file |
| `validate` | validate the current effective configuration |

#### Subcommand formats

| Subcommand | Formats |
| --- | --- |
| `show` | `table`, `json`, `yaml` |
| `get` | `table`, `json` |
| `set`, `unset`, `reset`, `validate` | `table`, `json` |

#### Required behavior

1. `show` returns the effective config after applying file + env + defaults.
2. `get` returns one resolved key and its source.
3. `set` persists only to the config file, never to env.
4. `unset` removes the key from the config file.
5. `reset` clears the persisted config file and requires `--yes`.
6. `validate` returns all validation errors, not only the first one.

#### Required config domains

The configuration model must cover:

- paths
- transport defaults
- timeout defaults
- download defaults
- query defaults
- output defaults

#### Examples

```bash
anacx config show
anacx config show --format yaml
anacx config get transport.default --format json
anacx config set transport.default playwright
anacx config validate --format json
```

---

### 10.8 `anacx drop`

#### Purpose

`drop` is the safe local-pruning surface. It removes local raw files, Parquet slices, or both for a selected dataset scope while keeping metadata and manifest state synchronized.

#### Syntax

```bash
anacx drop <DATASET> [OPTIONS]
```

#### Options

| Option | Meaning |
| --- | --- |
| temporal flags | restrict drop scope for periodized families |
| `--resource-id ID[,ID,...]` | target one or more specific local resource identifiers |
| `--layer raw|parquet|all` | choose which local storage layer to remove |
| `--dry-run` | emit the resolved deletion plan only |
| `--yes` | confirm destructive execution |
| `--format table|json` | result format |

#### Scope rules

1. `<DATASET>` is always a logical dataset family.
2. Periodized families must resolve temporal selectors through the shared grammar from Section 8.
3. Snapshot families reject temporal selectors.
4. `--resource-id` may narrow the drop scope further inside the selected dataset family.

#### Required behavior

1. `drop` must be planner-driven: it resolves local targets before deleting anything.
2. `--dry-run` returns the exact targeted paths plus aggregate size to be freed.
3. `--layer raw` may delete manifests, archives, and materialized raw files while leaving Parquet intact.
4. `--layer parquet` may delete warehouse-side Parquet slices while preserving raw artifacts.
5. `--layer all` removes both local layers for the selected scope.
6. Successful execution must immediately prune or refresh the local metadata state so discoverability views such as `anac_loaded_resources` stop advertising deleted data.
7. Non-dry-run execution must require `--yes`.

#### Phase 3 initial support

The initial implementation must support `drop cig` end to end for:

- full-family local deletion
- year- and slice-scoped deletion
- layer-filtered raw-only pruning

Other families may return `DATASET_NOT_SUPPORTED` until their adapters expose a drop planner.

#### Examples

```bash
anacx drop cig --dry-run --format json
anacx drop cig --year 2023 --layer all --yes
anacx drop cig --slice 2025-01 --layer raw --yes
anacx drop cig --resource-id cig_csv_2025_01 --dry-run
```

---

## 11. JSON response contract

This section defines the stable machine-readable contract for `--format json`.

### 11.1 Common success envelope

```json
{
  "type": "object",
  "required": ["ok", "command", "contract_version", "data", "warnings", "meta"],
  "properties": {
    "ok": { "const": true },
    "command": {
      "enum": ["datasets", "download", "schema", "query", "stats", "update", "config", "drop"]
    },
    "contract_version": { "const": "phase3/v1" },
    "data": { "type": "object" },
    "warnings": {
      "type": "array",
      "items": { "$ref": "#/definitions/warning" }
    },
    "meta": { "$ref": "#/definitions/meta" }
  }
}
```

### 11.2 Common error envelope

```json
{
  "type": "object",
  "required": ["ok", "command", "contract_version", "error", "warnings", "meta"],
  "properties": {
    "ok": { "const": false },
    "command": {
      "enum": ["datasets", "download", "schema", "query", "stats", "update", "config", "drop"]
    },
    "contract_version": { "const": "phase3/v1" },
    "error": { "$ref": "#/definitions/error" },
    "warnings": {
      "type": "array",
      "items": { "$ref": "#/definitions/warning" }
    },
    "meta": { "$ref": "#/definitions/meta" }
  }
}
```

### 11.3 Common definitions

```json
{
  "definitions": {
    "warning": {
      "type": "object",
      "required": ["code", "message"],
      "properties": {
        "code": { "type": "string" },
        "message": { "type": "string" },
        "details": { "type": "object" }
      }
    },
    "error": {
      "type": "object",
      "required": ["code", "message", "retryable", "details"],
      "properties": {
        "code": { "type": "string" },
        "message": { "type": "string" },
        "retryable": { "type": "boolean" },
        "details": { "type": "object" }
      }
    },
    "meta": {
      "type": "object",
      "required": ["generated_at", "elapsed_ms"],
      "properties": {
        "generated_at": { "type": "string" },
        "elapsed_ms": { "type": "integer", "minimum": 0 },
        "paths": { "type": "object" },
        "truncated": { "type": "boolean" }
      }
    }
  }
}
```

### 11.4 `datasets` data schema

```json
{
  "type": "object",
  "required": ["items", "item_count"],
  "properties": {
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "dataset",
          "title",
          "coverage_kind",
          "available_source_formats",
          "local_status",
          "update_supported"
        ]
      }
    },
    "item_count": { "type": "integer" },
    "filters": { "type": "object" }
  }
}
```

Each dataset item may additionally include:

- `category`
- `description`
- `remote_dataset_ids`
- `remote_coverage`
- `local_coverage`
- `query_view_name`
- `dictionary_available`
- `vocabulary_views`

### 11.5 `download` data schema

```json
{
  "type": "object",
  "required": ["dataset", "output_format", "selection", "plan", "applied"],
  "properties": {
    "dataset": { "type": "string" },
    "output_format": { "enum": ["parquet", "raw", "both"] },
    "selection": { "type": "object" },
    "plan": { "type": "array" },
    "applied": { "type": "array" },
    "validation": { "type": ["object", "null"] }
  }
}
```

Each `plan` item must include:

- `slice`
- `dataset_id`
- `resource_name`
- `source_format`
- `action`

Each `applied` item must include:

- `manifest_path`
- `download_cache_status`
- `load_status` when applicable
- `parquet_path` when applicable
- `row_count` when applicable

### 11.6 `schema` data schema

```json
{
  "type": "object",
  "required": ["dataset", "mode"],
  "properties": {
    "dataset": { "type": "string" },
    "mode": { "enum": ["canonical", "target", "diff", "ddl"] },
    "target": { "type": ["object", "null"] },
    "columns": { "type": "array" },
    "diff": { "type": ["object", "null"] },
    "ddl": { "type": ["string", "null"] }
  }
}
```

Column entries must include:

- `name`
- `ordinal_position`
- `inferred_type`
- `duckdb_type`
- `nullable`

When `--describe` is active, column entries may additionally include:

- `description`
- `semantic_type`
- `paired_field`
- `code_meaning_status`
- `vocabulary_dataset_id`
- `vocabulary_table`

### 11.7 `query` data schema

```json
{
  "type": "object",
  "required": ["sql", "row_count", "column_names"],
  "properties": {
    "sql": { "type": "string" },
    "row_limit": { "type": "integer" },
    "row_count": { "type": "integer" },
    "column_names": {
      "type": "array",
      "items": { "type": "string" }
    },
    "rows": { "type": "array" },
    "plan": { "type": ["array", "null"] },
    "output_path": { "type": ["string", "null"] }
  }
}
```

### 11.8 `stats` data schema

```json
{
  "type": "object",
  "required": ["scope", "summary"],
  "properties": {
    "scope": { "enum": ["global", "dataset", "slice"] },
    "dataset": { "type": ["string", "null"] },
    "summary": { "type": "object" },
    "partitions": { "type": ["array", "null"] },
    "profile": { "type": ["object", "null"] }
  }
}
```

### 11.9 `update` data schema

```json
{
  "type": "object",
  "required": ["scope", "plan", "applied"],
  "properties": {
    "scope": { "type": "object" },
    "plan": { "type": "array" },
    "applied": { "type": "array" },
    "validation": { "type": ["object", "null"] }
  }
}
```

Each update plan item must include:

- `dataset`
- `slice`
- `action`
- `reason`
- `remote_modified`
- `remote_size`

### 11.10 `config` data schema

```json
{
  "type": "object",
  "required": ["subcommand"],
  "properties": {
    "subcommand": {
      "enum": ["show", "get", "set", "unset", "reset", "validate"]
    },
    "config": { "type": ["object", "null"] },
    "key": { "type": ["string", "null"] },
    "value": {},
    "source": { "type": ["string", "null"] },
    "validation_errors": { "type": ["array", "null"] }
  }
}
```

### 11.11 `drop` data schema

```json
{
  "type": "object",
  "required": ["scope", "layer", "targets", "totals", "applied"],
  "properties": {
    "scope": { "type": "object" },
    "layer": { "enum": ["raw", "parquet", "all"] },
    "targets": { "type": "array" },
    "totals": { "type": "object" },
    "applied": { "type": "array" }
  }
}
```

Each drop target item should include:

- `path`
- `layer`
- `size_bytes`
- `dataset`
- `slice`
- `resource_id`

---

## 12. Error contract

### 12.1 Error-code families

| Exit family | Meaning |
| --- | --- |
| `2` | CLI usage / argument parsing |
| `10-19` | dataset or slice resolution |
| `20-29` | network or transport |
| `30-39` | local artifact or storage |
| `40-49` | schema or validation |
| `50-59` | query execution or SQL policy |
| `60-69` | config |
| `70-79` | update, drop, or integrity |

### 12.2 Required error codes

At minimum, the implementation must standardize these codes:

| Code | Meaning |
| --- | --- |
| `DATASET_NOT_FOUND` | unknown dataset family |
| `DATASET_NOT_SUPPORTED` | known family but unsupported by the current command |
| `TEMPORAL_SLICE_NOT_FOUND` | requested period does not exist remotely or locally |
| `DATASET_UPDATE_NOT_SUPPORTED` | update adapter missing for the family |
| `NETWORK_ERROR` | remote access failure |
| `TRANSPORT_BLOCKED` | likely WAF / blocked transport path |
| `PLAYWRIGHT_UNAVAILABLE` | Playwright runtime missing or unusable |
| `LOCAL_DATASET_NOT_AVAILABLE` | command requires local materialization that is absent |
| `SCHEMA_NOT_AVAILABLE` | required schema artifact or warehouse schema missing |
| `SCHEMA_MISMATCH` | schema incompatibility detected |
| `VALIDATION_FAILED` | post-load or config validation failed |
| `WRITE_QUERY_BLOCKED` | write SQL attempted without explicit opt-in |
| `QUERY_ERROR` | DuckDB execution failure |
| `UNKNOWN_RELATION` | referenced dataset or view does not exist |
| `CONFIG_ERROR` | invalid config state |
| `CONFIG_KEY_NOT_FOUND` | requested config key missing |
| `INTEGRITY_FAILED` | integrity validator reported failure |

### 12.3 Unknown relation recovery

When `query` fails because a relation does not exist, the JSON error should include recoverable context:

```json
{
  "code": "UNKNOWN_RELATION",
  "message": "Relation 'foo' does not exist.",
  "retryable": true,
  "details": {
    "relation": "foo",
    "available_dataset_views": ["cig"],
    "available_metadata_views": ["anac_datasets", "anac_schema_columns"]
  }
}
```

---

## 13. Metadata discoverability layer

This is the internal SQL-facing layer that makes the local store legible to:

- `query`
- `schema`
- `datasets`
- `stats`
- agents exploring the warehouse

The discoverability layer is defined as a set of **stable logical metadata views**.

### 13.1 Design rules

1. Metadata views are read-only.
2. They may be derived from catalog tables and artifact files.
3. They must be queryable through `anacx query`.
4. They must exist even when empty.
5. Their schemas must be stable across runs.

### 13.2 Required metadata views

#### `anac_datasets`

One row per logical dataset family.

| Column | Type | Meaning |
| --- | --- | --- |
| `dataset` | `VARCHAR` | stable family id |
| `title` | `VARCHAR` | display title |
| `category` | `VARCHAR` | high-level category |
| `description` | `VARCHAR` | human summary |
| `coverage_kind` | `VARCHAR` | `periodic_monthly`, `periodic_yearly`, `snapshot`, or `delta` |
| `available_source_formats` | `VARCHAR[]` or JSON text | known remote source formats |
| `remote_dataset_ids` | `VARCHAR[]` or JSON text | mapped CKAN package ids |
| `remote_first_year` | `INTEGER` | earliest known year when applicable |
| `remote_last_year` | `INTEGER` | latest known year when applicable |
| `local_slice_count` | `BIGINT` | number of locally materialized slices |
| `local_first_slice` | `VARCHAR` | earliest local slice |
| `local_last_slice` | `VARCHAR` | latest local slice |
| `query_view_name` | `VARCHAR` | main query relation if available |
| `update_supported` | `BOOLEAN` | whether `update` is supported |
| `dictionary_available` | `BOOLEAN` | whether a field dictionary exists |

#### `anac_dataset_resources`

One row per resolved remote or local resource record.

| Column | Type | Meaning |
| --- | --- | --- |
| `dataset` | `VARCHAR` | logical family |
| `dataset_id` | `VARCHAR` | CKAN dataset id |
| `resource_name` | `VARCHAR` | CKAN resource name |
| `source_format` | `VARCHAR` | CSV / JSON |
| `slice` | `VARCHAR` | normalized `YYYY-MM` when applicable |
| `remote_size_bytes` | `BIGINT` | CKAN size metadata |
| `remote_modified` | `VARCHAR` or timestamp-cast text | CKAN last-modified |
| `manifest_path` | `VARCHAR` | local manifest path when present |
| `materialized_path` | `VARCHAR` | local raw working file |
| `parquet_path` | `VARCHAR` | local Parquet slice when loaded |
| `local_status` | `VARCHAR` | `missing`, `raw`, or `loaded` |
| `row_count` | `BIGINT` | warehouse row count when loaded |

#### `anac_partitions`

One row per locally cataloged period slice.

| Column | Type | Meaning |
| --- | --- | --- |
| `dataset` | `VARCHAR` | logical family |
| `slice` | `VARCHAR` | normalized `YYYY-MM` |
| `year` | `INTEGER` | extracted year |
| `month` | `INTEGER` | extracted month |
| `dataset_id` | `VARCHAR` | CKAN dataset id |
| `resource_name` | `VARCHAR` | CKAN resource name |
| `manifest_path` | `VARCHAR` | raw manifest |
| `parquet_path` | `VARCHAR` | loaded Parquet slice |
| `row_count` | `BIGINT` | row count |
| `remote_size_bytes` | `BIGINT` | size used for drift detection |
| `remote_modified` | `VARCHAR` or timestamp-cast text | remote modified timestamp |
| `content_checksum` | `VARCHAR` | stored checksum |
| `imported_at` | `VARCHAR` or timestamp-cast text | first import |
| `refreshed_at` | `VARCHAR` or timestamp-cast text | latest refresh |

#### `anac_registered_views`

One row per registered user-queryable DuckDB view.

| Column | Type | Meaning |
| --- | --- | --- |
| `view_name` | `VARCHAR` | DuckDB relation name |
| `table_name` | `VARCHAR` | logical storage table |
| `parquet_root` | `VARCHAR` | root directory |
| `parquet_file_count` | `BIGINT` | number of backing files |
| `updated_at` | `VARCHAR` or timestamp-cast text | last refresh |

#### `anac_loaded_resources`

One row per manifest-backed warehouse load.

| Column | Type | Meaning |
| --- | --- | --- |
| `manifest_path` | `VARCHAR` | manifest key |
| `dataset_id` | `VARCHAR` | CKAN dataset id |
| `dataset` | `VARCHAR` | logical family, usually derived from `table_name` |
| `resource_name` | `VARCHAR` | resource name |
| `view_name` | `VARCHAR` | exposed query view |
| `source_path` | `VARCHAR` | local raw source |
| `schema_path` | `VARCHAR` | schema artifact used |
| `parquet_path` | `VARCHAR` | specific output file |
| `row_count` | `BIGINT` | row count |
| `partition_values_json` | `VARCHAR` | partition metadata |
| `loaded_at` | `VARCHAR` or timestamp-cast text | load timestamp |

#### `anac_schema_columns`

One row per dataset column exposed to users.

| Column | Type | Meaning |
| --- | --- | --- |
| `dataset` | `VARCHAR` | logical family |
| `target` | `VARCHAR` | `canonical`, `YYYY`, or `YYYY-MM` |
| `source_kind` | `VARCHAR` | `schema_artifact` or `warehouse_view` |
| `ordinal_position` | `BIGINT` | column order |
| `column_name` | `VARCHAR` | source column name |
| `inferred_type` | `VARCHAR` | schema artifact type |
| `duckdb_type` | `VARCHAR` | warehouse type |
| `nullable` | `BOOLEAN` | nullability |
| `description` | `VARCHAR` | dictionary description when available |
| `semantic_type` | `VARCHAR` | semantic category |
| `paired_field` | `VARCHAR` | code/label sibling |
| `code_meaning_status` | `VARCHAR` | vocabulary status |
| `vocabulary_dataset_id` | `VARCHAR` | linked vocabulary dataset |
| `vocabulary_table` | `VARCHAR` | linked vocabulary table |

#### `anac_dictionary_fields`

One row per dictionary entry.

| Column | Type | Meaning |
| --- | --- | --- |
| `dataset` | `VARCHAR` | logical family |
| `field_name` | `VARCHAR` | raw field name |
| `section` | `VARCHAR` | dictionary section |
| `description` | `VARCHAR` | human description |
| `semantic_type` | `VARCHAR` | semantic category |
| `value_pattern` | `VARCHAR` | observed pattern summary |
| `inferred_type` | `VARCHAR` | schema-derived type |
| `nullable` | `BOOLEAN` | nullability |
| `paired_field` | `VARCHAR` | code/label sibling |
| `code_meaning_status` | `VARCHAR` | code resolution status |
| `external_vocabulary_status` | `VARCHAR` | external vocabulary availability |
| `vocabulary_dataset_id` | `VARCHAR` | linked vocabulary dataset |
| `vocabulary_table` | `VARCHAR` | linked vocabulary table |
| `join_key` | `VARCHAR` | source join field when available |
| `label_field` | `VARCHAR` | target label field when available |

#### `anac_crosswalks`

One row per registered vocabulary crosswalk view.

| Column | Type | Meaning |
| --- | --- | --- |
| `dataset_id` | `VARCHAR` | source vocabulary dataset id |
| `view_name` | `VARCHAR` | DuckDB relation name |
| `table_name` | `VARCHAR` | logical table name |
| `parquet_path` | `VARCHAR` | backing Parquet file |
| `row_count` | `BIGINT` | rows in the view |

#### `anac_update_status`

One row per dataset family summarizing local update state.

| Column | Type | Meaning |
| --- | --- | --- |
| `dataset` | `VARCHAR` | logical family |
| `update_supported` | `BOOLEAN` | whether update is implemented |
| `local_slice_count` | `BIGINT` | number of local slices |
| `latest_local_slice` | `VARCHAR` | newest local slice |
| `latest_imported_at` | `VARCHAR` or timestamp-cast text | newest import |
| `latest_refreshed_at` | `VARCHAR` or timestamp-cast text | newest refresh |

### 13.3 Minimum query examples for agents

```sql
SELECT * FROM anac_datasets ORDER BY dataset;
SELECT * FROM anac_schema_columns WHERE dataset = 'cig' ORDER BY ordinal_position;
SELECT * FROM anac_partitions WHERE dataset = 'cig' ORDER BY slice DESC;
SELECT * FROM anac_dictionary_fields WHERE dataset = 'cig' AND code_meaning_status != 'not_coded';
```

---

## 14. Phase 3 consistency rules

These are hard invariants worth preserving across commands.

### 14.1 Temporal flags mean the same thing everywhere

`--year`, `--month`, `--slice`, and `--latest` must not change meaning by command.

### 14.2 JSON shape is stable across commands

All JSON responses use:

- the common envelope
- a command-specific `data` object
- stable error and warning objects

### 14.3 Dataset arguments are logical families

Commands must not force users or agents to reason directly in raw CKAN package ids except for explicit debug overrides.

### 14.4 Schema inspection is read-only

`schema` must never perform implicit downloads.

### 14.5 Update is not generic re-download

`update` is planner-driven synchronization over local state plus remote drift detection.

### 14.6 Drop must reconcile metadata immediately

`drop` is not a thin wrapper around file deletion.

After removing local files, it must also prune or refresh the corresponding local metadata so discoverability views and local inventory commands reflect the new state without requiring manual repair.

---

## 15. Bottom line

Phase 3 standardizes the ANAC CLI as:

1. **logical-family oriented**
2. **period-aware**
3. **DuckDB discoverable**
4. **JSON-contract stable**
5. **safe for agent execution**

The implementation plan in `cli-implementation-plan.md` is the execution roadmap for delivering this contract on top of the current argparse, dataclass, manifest, and DuckDB/Parquet baseline.
