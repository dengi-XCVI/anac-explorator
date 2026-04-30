This project is meant as an interface to facilitate analysis of the ANAC (Autorità Nazionale Anti Corruzione) dataset.
It is part of a wider initiative to increase the legibility of the Italian government.

## Current implementation focus

Phase 1 is centered on a small but reusable slice of the future system:

1. resolve a monthly CIG resource from ANAC metadata
2. download and inspect a sample archive
3. map the complete raw CSV schema

## Current code layout

- `src/anac_explorator/ckan.py` — CKAN metadata client for dataset discovery
- `src/anac_explorator/browser.py` — Playwright-backed access for WAF-protected endpoints
- `src/anac_explorator/sample.py` — monthly CIG sample resolution, download, and ZIP extraction
- `src/anac_explorator/schema.py` — CSV schema inspection and lightweight type inference
- `src/anac_explorator/comparison.py` — schema artifact comparison across files or years
- `src/anac_explorator/cli.py` — CLI entry points for metadata and schema inspection

## Local usage

```bash
python3 -m pip install -e .
anac-explorator inspect-csv-schema ./path/to/file.csv
anac-explorator package-show cig-2025
anac-explorator download-cig-sample --year 2025 --month 1 --transport playwright
anac-explorator compare-schema-files ./schemas/cig_2007_01.schema.json ./schemas/cig_2025_01.schema.json
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
```
