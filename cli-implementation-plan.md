# Phase 3 CLI Implementation Plan

## 1. Goal

Implement the Phase 3 CLI defined in `cli-specification.md` on top of the current repository baseline without discarding the existing working Phase 1 / Phase 2 foundation.

This plan is intentionally written as the **execution roadmap** for the implementation phase. The intended delivery outcome is:

1. a stable `anac` command surface,
2. a shared JSON contract for agents,
3. metadata discoverability views for SQL and schema exploration,
4. command parity for `datasets`, `download`, `schema`, `query`, `stats`, `update`, and `config`,
5. backward-compatible migration from the current legacy CLI surface.

---

## 2. Implementation constraints

### 2.1 Do not rewrite the project around a new CLI framework

The current codebase already uses:

- `argparse`
- dataclass-backed output models
- JSON serialization via `.to_dict()`

The Phase 3 implementation should **extend that architecture**, not replace it with Typer, Click, Pydantic, or a broad CLI rewrite.

### 2.2 Reuse the current storage baseline

Do not replace:

- manifest-backed raw downloads
- `loaded_resources`
- `registered_views`
- `dataset_period_manifest`
- current DuckDB / Parquet loading

Phase 3 should layer on top of these components.

### 2.3 Preserve current artifact-driven repository behavior

The implementation should continue to work with the current repository layout by default:

- `data/raw`
- `data/warehouse`
- `schemas`
- `vocabularies`
- `dictionaries`

Configuration support should make these overridable, not make repo-local operation a second-class path.

### 2.4 Build generic interfaces, but deliver CIG-first functionality

The system should be structured so multiple dataset families can plug in later, but the first fully implemented update-aware family remains:

- `cig`

Other families can start as discovery-only or raw-download-only if needed.

---

## 3. Delivery strategy

The implementation should be split into three layers:

| Layer | Why it comes first |
| --- | --- |
| Shared foundations | everything else depends on them |
| Discoverability and catalog layer | multiple commands depend on the same metadata |
| Command implementations | built on stable shared primitives |

The recommended order is:

1. shared contracts and helpers
2. config system
3. dataset family registry
4. metadata discoverability views
5. `datasets`
6. `download`
7. `schema`
8. `query`
9. `stats`
10. `update`
11. migration / compatibility polish

---

## 4. Target module plan

The implementation does not need to land in exactly these files, but this split is the most coherent one for the current codebase.

| File | Role |
| --- | --- |
| `src/anac_explorator/cli.py` | top-level parser, command wiring, legacy command shims |
| `src/anac_explorator/models.py` | shared output dataclasses and config models |
| `src/anac_explorator/config.py` | config loading, merging, validation, persistence |
| `src/anac_explorator/errors.py` | stable error codes, exceptions, exit-code mapping |
| `src/anac_explorator/output.py` | JSON envelopes and table rendering helpers |
| `src/anac_explorator/selection.py` | shared temporal selection parsing and normalization |
| `src/anac_explorator/catalog.py` | dataset-family registry and local/remote resolution |
| `src/anac_explorator/metadata_views.py` | discoverability-view materialization / registration |
| `src/anac_explorator/stats.py` | stats aggregation and profiling helpers |
| `src/anac_explorator/loader.py` | reuse and extend for Phase 3 download/update flows |
| `src/anac_explorator/integrity.py` | reuse validation entrypoints for `download --validate` and `update --validate` |
| `tests/test_cli.py` | parser and top-level CLI regression coverage |
| `tests/test_pipeline_integration.py` | end-to-end command workflows |
| new focused tests | config, selection, metadata views, stats, update behavior |

If `cli.py` becomes unwieldy, extract command-specific helper functions first; do not start by splitting into many subpackages before the contracts are stable.

---

## 5. Shared foundations

This is the prerequisite work. No Phase 3 command should be finalized before these pieces exist.

### 5.1 Shared command result envelope

#### Deliverables

1. New dataclasses for:
   - success envelope
   - error envelope
   - warning object
   - shared meta object
2. One rendering path for JSON output
3. One rendering path for table output

#### Required behavior

1. `--format json` must always emit one JSON document on stdout.
2. Existing dataclass-style payloads should be reusable inside the new envelope.
3. Warnings must be structured instead of only textual.
4. Output code should not duplicate envelope logic in each command handler.

#### Implementation notes

- Extend `models.py` with new envelope models.
- Add `output.py` to centralize `print_json_result(...)`, `print_table_result(...)`, and error emission.
- Keep serialization dataclass-based to match the existing project style.

#### Acceptance criteria

- A command handler can return a command-specific payload plus warnings and have output formatting handled centrally.
- JSON output stays stable regardless of verbosity flags.

### 5.2 Stable error model and exit-code mapping

#### Deliverables

1. Central error-code catalog
2. Exception types or structured error values mapped to the Phase 3 contract
3. Consistent exit-code families

#### Required behavior

1. Parser errors remain usage errors.
2. Command logic errors map to structured JSON error objects.
3. `query` can distinguish `WRITE_QUERY_BLOCKED`, `UNKNOWN_RELATION`, and generic `QUERY_ERROR`.
4. Remote-access failures can distinguish network failure from blocked transport.

#### Implementation notes

- Add `errors.py`.
- Convert current raw exceptions into domain-specific command errors near the CLI boundary.
- Preserve original exception detail in debug mode, but do not leak raw tracebacks into normal JSON responses.

#### Acceptance criteria

- Every Phase 3 command exits with a documented error code family.
- The same failure produces the same JSON `error.code` regardless of rendering mode.

### 5.3 Shared path resolution

#### Deliverables

1. Central path-resolution helper
2. Effective paths object returned to each command

#### Required behavior

1. Resolve raw, warehouse, schemas, vocabulary, and dictionaries paths from config + env + CLI.
2. Preserve current repository defaults.
3. Include resolved paths in the JSON `meta.paths` block.

#### Acceptance criteria

- Commands no longer hardcode path defaults in multiple places.
- Tests can override storage locations cleanly with temp directories.

### 5.4 Config system

#### Deliverables

1. Config dataclass model
2. config file loader and saver
3. env-variable merge layer
4. validation function

#### Required config domains

- `paths.*`
- `transport.*`
- `download.*`
- `query.*`
- `output.*`

#### Required behavior

1. `ANAC_*` env names are primary.
2. Existing `ANAC_EXPLORATOR_*` env names are accepted as compatibility fallbacks.
3. `show` reports effective values and value sources.
4. `set` and `unset` only change the config file.
5. `validate` returns all detected validation errors.

#### Implementation notes

- Add `config.py`.
- Use JSON for the persisted config file at first; YAML output can be a rendered view of the same config object.
- Avoid adding a YAML parser dependency unless the codebase already needs one; YAML output can be optional and generated manually if necessary.

#### Acceptance criteria

- Config precedence is deterministic.
- Commands can depend on resolved config without each command reimplementing merge logic.

### 5.5 Temporal selection parser

#### Deliverables

1. Shared parser for:
   - year
   - year range
   - month
   - month range
   - slice list
   - latest
2. Normalized selection object

#### Required behavior

1. The same selection grammar works across `download`, `schema`, `stats`, and `update`.
2. Invalid combinations are rejected consistently.
3. The normalized representation is easy to pass into dataset-family adapters.

#### Implementation notes

- Add `selection.py`.
- Represent normalized slices canonically as `YYYY-MM`.
- Include helper functions for converting to the current `YYYY_MM` period form used in the warehouse catalog.

#### Acceptance criteria

- No command parses temporal flags ad hoc.
- Tests cover all valid and invalid flag combinations.

### 5.6 Dataset family registry

#### Deliverables

1. One registry object describing logical dataset families
2. Metadata per family:
   - display title
   - category
   - coverage kind
   - source-format support
   - query view naming
   - update support
   - resolver / adapter class

#### Required initial families

At minimum:

- `cig`
- `smartcig`
- `stazioni-appaltanti`
- `aggiudicatari`
- vocabulary families already wired in the repository

Not every family needs full implementation on day one, but they should exist in the registry if Phase 3 intends to expose them through `datasets`.

#### Implementation notes

- Add `catalog.py`.
- Keep the family registry explicit rather than discovering families dynamically from CKAN every time.
- The registry is where the CLI reconciles logical families with raw CKAN package ids.

#### Acceptance criteria

- `datasets` can operate over logical families.
- `download` and `update` can dispatch by family adapter rather than by special-casing CIG in the CLI layer.

---

## 6. Metadata discoverability layer

This is the next critical dependency because `datasets`, `schema`, `query`, and `stats` all rely on it.

### 6.1 Deliverables

1. A metadata-view builder that exposes the views defined in the spec:
   - `anac_datasets`
   - `anac_dataset_resources`
   - `anac_partitions`
   - `anac_registered_views`
   - `anac_loaded_resources`
   - `anac_schema_columns`
   - `anac_dictionary_fields`
   - `anac_crosswalks`
   - `anac_update_status`
2. A consistent way to register or refresh those views when needed

### 6.2 Recommended implementation approach

Use a **view materialization helper** that:

1. opens the DuckDB database,
2. ensures the base warehouse catalog tables exist,
3. loads local JSON artifacts through Python,
4. writes small temporary or persistent metadata tables with explicit schemas,
5. creates stable SQL views over those tables.

This is preferable to relying on `read_json_auto(...)` inference for core metadata because:

- the project already has typed dataclass loaders,
- it keeps metadata types predictable,
- it reduces hidden schema drift in the discoverability layer.

### 6.3 Source mapping by view

| View | Primary source |
| --- | --- |
| `anac_registered_views` | `registered_views` |
| `anac_loaded_resources` | `loaded_resources` |
| `anac_partitions` | `dataset_period_manifest` |
| `anac_datasets` | dataset registry + local metadata aggregates |
| `anac_dataset_resources` | dataset registry + local manifests + warehouse loads |
| `anac_schema_columns` | schema artifacts + optional dictionary overlay + warehouse introspection |
| `anac_dictionary_fields` | dictionary artifact |
| `anac_crosswalks` | registered crosswalk views and vocabulary artifacts |
| `anac_update_status` | registry + `dataset_period_manifest` aggregates |

### 6.4 Work breakdown

#### Step 1: Build metadata models

Add small internal row models for each metadata view or the minimum rows that need explicit typing before insert into DuckDB.

#### Step 2: Implement loaders for local artifacts

Reuse the current JSON/dataclass artifact readers where possible:

- schema mapping loader
- dictionary artifact loader
- vocabulary index loader

#### Step 3: Create registration helpers

Create helper functions that:

- clear and repopulate temporary metadata tables, or
- upsert persistent metadata helper tables if persistence proves more useful

The first implementation should prefer **recomputed-on-demand** metadata tables because it is simpler and avoids new state drift.

#### Step 4: Add a single "ensure metadata views" entrypoint

This function should be reusable by:

- `datasets`
- `schema`
- `query`
- `stats`

#### Acceptance criteria

- Querying `SELECT * FROM anac_datasets` works even when no user dataset is loaded.
- Querying `SELECT * FROM anac_schema_columns WHERE dataset = 'cig'` works when the relevant local schema and dictionary artifacts exist.
- View schemas remain stable even when the underlying local store is sparse.

---

## 7. Command implementation workstreams

## 7.1 `datasets`

### Goals

Implement logical dataset discovery with local and remote awareness.

### Dependencies

- config system
- dataset registry
- metadata discoverability layer
- shared result envelope

### Implementation tasks

1. Add the `datasets` parser to `cli.py`.
2. Implement search and filter normalization.
3. Build a service that merges:
   - static registry fields
   - remote coverage metadata
   - local warehouse / manifest status
4. Implement list mode.
5. Implement single-dataset detail mode.
6. Add `--downloaded`, `--missing`, `--year`, and `--source-format` filtering.
7. Make JSON output use the stable envelope.

### Recommended internal structure

- `catalog.py` exposes a `list_dataset_families(...)` and `get_dataset_family(...)` API.
- The CLI handler should remain thin and avoid containing merge logic.

### Tests

1. Parser acceptance tests
2. Search/filter behavior tests
3. Single-detail JSON schema tests
4. Missing dataset error test
5. Partial-local / remote-warning behavior test

### Done criteria

- A user or agent can discover what exists locally and remotely without knowing CKAN package ids.

---

## 7.2 `download`

### Goals

Implement the Phase 3 download contract while reusing the current downloader and loader.

### Dependencies

- shared path/config resolution
- temporal selection parser
- dataset registry and family adapters
- current downloader and loader helpers
- optional integrity validation hook

### Implementation tasks

#### Step 1: Introduce download planning

Create a plan object that contains:

- dataset family
- requested scope
- normalized slices
- resolved CKAN dataset ids
- resolved resource names
- planned action per slice

This is needed so `--dry-run` and real execution share the same logic.

#### Step 2: Add family adapters

Implement at least two adapter types:

1. **periodized adapter** for `cig`
2. **snapshot adapter** for one-shot datasets

The adapter owns:

- remote resource resolution
- whether temporal flags are valid
- whether warehouse loading is supported
- whether update is supported

#### Step 3: Implement raw-only execution

Wire `--output-format raw` to the existing `download_dataset_resource(...)` path without forcing a warehouse load.

#### Step 4: Implement Parquet and both modes

Wire `--output-format parquet|both` to:

- manifest-backed download
- schema resolution
- Parquet load
- optional crosswalk registration
- optional post-load validation

#### Step 5: Implement `--force-download` and `--force-load`

These must feed through to the existing lower-level helpers rather than being emulated in the CLI.

#### Step 6: Normalize the final result

The handler must return:

- requested selection
- resolved plan
- applied actions
- validation result when requested

### Tests

1. dry-run plan on monthly CIG
2. raw-only snapshot family download
3. parquet mode reuses cache
4. both mode preserves working file
5. invalid temporal flags on snapshot family
6. source-format unavailable error

### Done criteria

- `download` fully replaces the user-facing role of the current low-level resource download commands for the Phase 3 surface.

---

## 7.3 `schema`

### Goals

Implement local structural inspection over schema artifacts and warehouse state.

### Dependencies

- metadata discoverability layer
- schema artifact loaders
- dictionary overlay data

### Implementation tasks

1. Add the `schema` parser and mutually exclusive mode handling.
2. Implement canonical target resolution by family.
3. Implement targeted resolution via year / slice.
4. Implement `--describe` by joining schema fields with dictionary metadata.
5. Implement `--ddl` using registered view SQL from `registered_views`.
6. Implement `--diff LEFT RIGHT` using the existing comparison logic where possible.
7. Standardize `SCHEMA_NOT_AVAILABLE` handling.

### Recommended behavior split

- artifact-driven schema output should be the default
- warehouse DDL should be a separate mode
- schema diff should reuse existing comparison utilities rather than reimplementing drift detection in SQL

### Tests

1. canonical schema JSON output
2. describe mode includes semantic fields
3. DDL mode returns view SQL
4. diff mode between two targets
5. missing schema artifact error

### Done criteria

- A user can discover column structure and semantic context without opening artifact files manually.

---

## 7.4 `query`

### Goals

Turn the current raw SQL façade into a stable, agent-safe Phase 3 command.

### Dependencies

- shared output and error models
- metadata discoverability layer
- current `run_local_query(...)`

### Implementation tasks

#### Step 1: Register metadata views before execution

Every `query` invocation should ensure the metadata discoverability layer is available in the target DuckDB session.

#### Step 2: Enforce read-only by default

Add SQL policy checking before execution. The first implementation can be conservative:

- detect leading write verbs after stripping whitespace/comments
- reject known mutating statements unless `--allow-write`

This does not need a full SQL parser on day one, but it must be safe.

#### Step 3: Add explain mode

Support `--explain` with a structured JSON payload.

#### Step 4: Add output-path flows

Support:

- stdout table
- stdout JSON
- file output for CSV
- file output for Parquet

#### Step 5: Improve error translation

Translate common DuckDB failures into:

- `UNKNOWN_RELATION`
- `QUERY_ERROR`

### Tests

1. read-only select succeeds
2. write blocked without opt-in
3. write allowed only with `--allow-write --yes`
4. metadata view query works
5. CSV output writes file
6. unknown relation returns structured recovery payload

### Done criteria

- `query` becomes safe and predictable enough for agents to use as the main exploration loop.

---

## 7.5 `stats`

### Goals

Provide local observability and inventory without requiring ad hoc SQL from users.

### Dependencies

- metadata discoverability layer
- query helpers
- possibly new stats helper module

### Implementation tasks

1. Add the `stats` parser and scope resolution.
2. Implement global storage summary from metadata tables.
3. Implement dataset summary from registered views and partition metadata.
4. Implement `--partitions` from `anac_partitions`.
5. Implement `--profile` using DuckDB aggregate queries.
6. Ensure the command prefers metadata over full scans unless profiling requires data access.

### Suggested metric groups

#### Global summary

- dataset family count
- loaded resource count
- registered view count
- partition count
- total on-disk Parquet size when practical
- latest import / refresh timestamps

#### Dataset summary

- row count
- slice coverage
- Parquet file count
- schema column count
- presence of dictionary / crosswalk metadata

#### Profile mode

- null counts or null ratio
- min / max where meaningful
- approximate distinct count when feasible
- top values for selected categorical fields

### Tests

1. global stats with empty warehouse
2. dataset stats with loaded test data
3. partition mode on a periodized family
4. profile mode on a small fixture dataset
5. invalid `--partitions` on snapshot family

### Done criteria

- Users can answer "what do I have locally?" without writing SQL.

---

## 7.6 `update`

### Goals

Implement planner-driven incremental synchronization over locally present data.

### Dependencies

- dataset registry and family adapters
- temporal selection parser
- current CIG period-sync logic
- validation hook

### Implementation tasks

#### Step 1: Abstract update planning behind the family adapter

The CLI should not embed CIG-specific rules. Instead:

- `cig` adapter exposes a planning API
- unsupported families report `DATASET_UPDATE_NOT_SUPPORTED`

#### Step 2: Implement dry-run first

Before mutating anything, make `--dry-run` return:

- scope
- latest local state
- plan items
- action reasoning

#### Step 3: Implement apply mode

Reuse the current `sync_cig_periods_to_parquet(...)` behavior where possible, but wrap it in the Phase 3 envelope and adapter contract.

#### Step 4: Implement global update mode

`anac update` without `DATASET` should:

1. inspect locally present dataset families,
2. filter to update-capable families,
3. execute each family plan in sequence,
4. aggregate the result.

Do not attempt generic remote scanning over all 70 datasets in the first implementation.

#### Step 5: Integrate validation

`--validate` should run the existing integrity validator for families that support it. For the initial implementation, this is primarily `cig`.

### Tests

1. `update cig --dry-run`
2. `update cig` applies only newer periods by default
3. `update cig --refresh-changed` includes changed existing periods
4. `update` without `DATASET` only targets local update-capable families
5. unsupported family returns `DATASET_UPDATE_NOT_SUPPORTED`

### Done criteria

- `update` is a stable orchestration command, not just a thin alias over the current CIG sync helper.

---

## 7.7 `config`

### Goals

Implement persistent configuration management for Phase 3.

### Dependencies

- config system
- shared output and error models

### Implementation tasks

1. Add `config` parser and subparsers.
2. Implement:
   - `show`
   - `get`
   - `set`
   - `unset`
   - `reset`
   - `validate`
3. Return key source information for `show` and `get`.
4. Implement config-file persistence.
5. Add confirmation guard for `reset`.

### Tests

1. show effective config
2. get missing key error
3. set then show reflects persisted value
4. unset removes key
5. reset requires `--yes`
6. validate returns multiple errors

### Done criteria

- Users can manage defaults without editing code or env manually.

---

## 8. Migration and backwards compatibility

The Phase 3 surface should not break existing users abruptly.

### 8.1 Compatibility strategy

Implement migration in three steps:

1. introduce new `anac` commands
2. keep existing legacy commands working
3. progressively rewire legacy handlers to call the new shared internals

### 8.2 Legacy command handling

Do not remove the current low-level commands until the new Phase 3 commands have coverage and parity. Instead:

- keep them available
- mark them as legacy in help text if appropriate
- reuse the new shared helpers where possible

### 8.3 Alias behavior

If `anac-explorator` remains installed, it should either:

- invoke the new parser with legacy aliases, or
- keep the old parser but share the same backend helpers

The implementation should avoid maintaining two divergent code paths.

---

## 9. Testing plan

The implementation must expand tests in lockstep with the command work.

### 9.1 New or expanded test areas

| Area | Test focus |
| --- | --- |
| CLI parsing | new parsers and flag exclusivity |
| Config | precedence, persistence, validation |
| Selection | temporal grammar normalization |
| Catalog | dataset family registry and filters |
| Metadata views | stable schemas and empty-state behavior |
| Query policy | read-only enforcement and error translation |
| Stats | summary and profile outputs |
| Update | dry-run planning and applied sync behavior |

### 9.2 Existing tests to preserve

Do not regress the current test-covered capabilities:

- parse-resource
- clean-resource
- query-local-data
- download-to-Parquet workflows
- sync-cig-periods behavior
- integrity validation
- direct module invocation

### 9.3 Recommended file split

In addition to updating `tests/test_cli.py`, add focused test modules:

- `tests/test_config.py`
- `tests/test_selection.py`
- `tests/test_metadata_views.py`
- `tests/test_stats.py`
- `tests/test_phase3_query.py`
- `tests/test_phase3_update.py`

### 9.4 End-to-end scenarios to add

1. `download cig --year 2025 --month 1 --format json`
2. `schema cig --describe --format json`
3. `query "SELECT * FROM anac_datasets" --format json`
4. `stats cig --partitions --format json`
5. `update cig --dry-run --format json`
6. `config show --format json`

---

## 10. Implementation checkpoints

Use these as the actual delivery milestones.

### Checkpoint 1 - shared substrate ready

Must be complete before command work proceeds:

- result envelope
- error model
- config system
- path resolution
- temporal parser
- dataset registry

### Checkpoint 2 - metadata layer ready

Must be complete before `schema`, `query`, and `stats` are considered stable:

- metadata views implemented
- empty-state behavior handled
- metadata views queryable from DuckDB

### Checkpoint 3 - discovery and materialization ready

- `datasets` stable
- `download` stable

### Checkpoint 4 - introspection and SQL ready

- `schema` stable
- `query` stable
- `stats` stable

### Checkpoint 5 - synchronization ready

- `update` stable for `cig`
- validation hook integrated

### Checkpoint 6 - config and migration ready

- `config` stable
- compatibility path documented
- legacy commands routed through shared helpers where practical

---

## 11. Recommended execution order

This is the step-by-step build order to follow during implementation.

1. Add shared result-envelope and error models.
2. Add config loading, persistence, and precedence resolution.
3. Add shared path-resolution helpers.
4. Add temporal selection parsing and normalization.
5. Add the dataset family registry plus family-adapter interface.
6. Add metadata-view registration and query-session bootstrap helpers.
7. Implement `datasets`.
8. Implement `download`, starting with `cig` and one snapshot-family path.
9. Implement `schema`.
10. Implement `query`, including read-only enforcement and metadata views.
11. Implement `stats`.
12. Wrap the current CIG sync logic behind the new `update` contract.
13. Implement `config` subcommands.
14. Rewire legacy commands or aliases to the new shared helpers where feasible.
15. Expand tests and then update README/help text once the implementation is stable.

---

## 12. Definition of done

Phase 3 CLI implementation is done when all of the following are true:

1. `anac` exposes the seven commands from the specification.
2. `--format json` returns the stable envelope and command-specific payload for each command.
3. metadata discoverability views are queryable through `anac query`.
4. `download`, `schema`, `query`, `stats`, and `update` all work for the current CIG baseline.
5. `config` persists and validates effective defaults.
6. the test suite covers the new command contracts and does not regress existing Phase 1 / Phase 2 behavior.

This plan should be treated as the implementation sequence for the next phase, not as a loose recommendation list.
