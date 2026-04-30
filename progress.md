# Progress

## Current phase
- Phase 1: complete schema mapping and cross-year schema comparison

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
- Research references reviewed: `research/ANAC-data.md`, `research/ANAC-data-research.md`
- Live network access is no longer blocked when using Playwright transport

## Planned milestones
1. Expand the cross-year comparison beyond January 2007 vs January 2025
2. Promote inferred types into explicit field semantics/documentation
3. Extend the loader path toward DuckDB/Parquet-ready ingestion

## Known risks
- Direct HTTP access to the ANAC API and portal is rejected from this runtime; Playwright is the validated access path
