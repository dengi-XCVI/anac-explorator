# ANAC Data — Research Report & Implementation Plan

**Research Date:** 2026-04-26  
**Original Note:** "Procurement data from ANAC → clean it → expose CLI + dataset → publish repo → document schema → show how to query with an LLM"

---

## Mission Context

> "Procurement data from ANAC  
> Can be found on the ANAC Open Data Portal  
> → clean it  
> → expose CLI + dataset
> Then: publish repo, document schema, show how to query with an LLM"

---

## 1. What ANAC Exposes

The **ANAC Open Data Portal** runs on **CKAN 2.6.8** at `https://dati.anticorruzione.it/opendata/` and exposes **70 datasets** via a fully documented REST API at `/api/3/action/`.

### Key Facts
- **License:** CC-BY-SA-4.0 (Creative Commons Attribution-ShareAlike 4.0)
- **API:** Full CKAN REST API — no authentication required
- **CORS:** Fully enabled (`Access-Control-Allow-Origin: *`)
- **Data formats:** CSV (semicolon-delimited), JSON (JSON Lines), TTL (RDF/Turtle)
- **Analytics dashboard:** Superset at `https://dati.anticorruzione.it/superset/dashboard/appalti/`

---

## 2. Complete Data Inventory — 70 Datasets in 11 Categories

### 2.1 CIG (Codice Identificativo Gara) — Core Procurement Data

| Dataset | Description | Timeline | Formats |
|---------|-------------|----------|---------|
| `cig-2007` to `cig-2025` | Full yearly CIG data | 19 years | CSV, JSON, TTL |
| `cig` | Delta/Incremental updates | Ongoing | CSV, JSON |

Monthly files for recent years (e.g. `cig_csv_2025_01` = January 2025), single annual dumps for older years.

**Size estimate:** ~100MB CSV/month, ~270MB JSON/month, ~1.1GB TTL/month → **~1.2GB (CSV) / ~3.2GB (JSON) / ~12GB (TTL) per year**

Estimated **100M+ records** across all 19 years.

### 2.2 SMARTCIG — Simplified Procurement (below-threshold procedures)

`smartcig-2011` to `smartcig-2025` + `smartcig` delta updates  
Similar structure to CIG but for simplified procedures.

### 2.3 OCDS (Open Contracting Data Standard)

`ocds-appalti-ordinari-2018` to `ocds-appalti-ordinari-2026`  
Full OCDS compliance: **planning → tender → award → contract → implementation** lifecycle. Monthly JSON files per year. Internationally recognized standard.

### 2.4 Anagrafiche (Registry)

**`stazioni-appaltanti`** — Contracting authority registry (13MB CSV, 18 columns, verified):
```
codice_fiscale, partita_iva, denominazione, codice_ausa,
natura_giuridica_codice, natura_giuridica_descrizione, soggetto_estero,
provincia_codice, provincia_nome, citta_codice, citta_nome,
indirizzo_odonimo, cap, flag_inHouse, flag_partecipata,
stato, data_inizio, data_fine
```

**`aggiudicatari`** — All awarded contractors (~800MB CSV)

### 2.5 Contract Management (Gestione Contratto)

12 datasets tracking the full contract lifecycle:
- `aggiudicazioni` — Award/assignment details
- `subappalti` — Subcontracting information
- `varianti` — Contract modifications/variants
- `collaudo` — Testing/certification
- `stati-avanzamento` — Progress states
- `lavorazioni` — Works/operations
- `pubblicazioni` — Publications
- `avvio-contratto`, `fine-contratto` — Contract lifecycle
- `partecipanti` — Procedure participants
- `quadro-economico` — Budget breakdown
- `sospensioni` — Contract suspensions

### 2.6 Funding & PNRR

- `cup` — Unique Project Codes (Codice Unico di Progetto)
- `fonti-finanziamento` — Funding sources
- `centri-di-costo` — Cost centers
- `indicatori-pnrrpnc` — PNRR/PNC inclusion quotas and derogations
- `misurepremiali-pnrrpnc` — PNRR premium measures

### 2.7 Controlled Vocabularies (Vocabolari Controllati)

Small reference tables for proper data normalization:
- `bandi-cig-tipo-scelta-contraente` — 39 procurement method types (verified: PROCEDURA APERTA, AFFIDAMENTO DIRETTO, PROCEDURA NEGOZIATA, etc.)
- `bandi-cig-modalita-realizzazione` — Execution modalities
- `categorie-dpcm-aggregazione` — DPCM aggregation categories
- `categorie-opera` — Work categories
- `smartcig-tipo-fattispecie-contrattuale` — Contract types

### 2.8 Certifications
- `attestazioni-soa` — SOA contractor certifications

---

## 3. Technical Infrastructure

### Access Methods

**1. CKAN REST API** (fully authenticated, no API key needed):
```
https://dati.anticorruzione.it/opendata/api/3/action/
/api/3/action/package_list          → Lists all 70 datasets
/api/3/action/package_show?id=cig   → Metadata + download URLs for one dataset
```

**2. Direct Downloads:**
```
https://dati.anticorruzione.it/opendata/download/dataset/<name>/filesystem/<file>.zip
```
All downloads are ZIP archives. CSV uses `;` (semicolon) delimiter.

**3. RDF/Turtle** for semantic web queries

**4. Full catalog metadata** in N3, TTL, RDF/XML, JSON-LD

### CORS & Access
- `Access-Control-Allow-Origin: *` — fully open
- `Access-Control-Allow-Credentials: true`
- **No authentication or paywalls for any endpoint**

### Data Quality Notes
- Some `size` fields show negative numbers (int32 overflow for files >2GB)
- Data may have NULL/mixed encoding issues across years
- Schema consistency across 19 years needs verification (2007 vs 2025 may differ)

---

## 4. Feasibility Assessment: ✅ HIGHLY FEASIBLE

### Why it works:
1. Data is openly licensed (CC-BY-SA-4.0), API-accessible, well-structured
2. Multiple formats (CSV, JSON, TTL) for different use cases
3. Controlled vocabularies enable proper normalization
4. OCDS provides international standard format
5. CKAN API enables full programmatic access
6. **No existing open-source tool wraps this data — greenfield opportunity**
   - GitHub searches returned 0 repos for ANAC open data
   - No relevant packages on PyPI 

### Key Challenges:
1. **Volume:** Multi-year CIG data is hundreds of GB uncompressed
2. **Schema evolution:** Field names/structure may vary across years (2007 vs 2025)
3. **Domain knowledge:** Understanding CIG taxonomy, Italian procurement law, SOA categories
4. **Data quality:** Mixed formats, potential NULL handling issues
5. **Relationship mapping:** CIG ↔ SMARTCIG ↔ stazioni-appaltanti ↔ aggiudicatari requires cross-dataset joins

---

## 5. Proposed Architecture

```
┌─────────────────────────────────────────────┐
│          User (CLI / Python API)            │
└─────────────┬───────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────┐
│           anac CLI / Python API             │
│  • Dataset discovery & listing              │
│  • Download & caching (incremental deltas)  │
│  • Schema validation & documentation        │
│  • DuckDB query engine                      │
|                                             | 
└─────────────┬───────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────┐
│          Local DuckDB Database              │
│  • Parquet files for all datasets           │
│  • Indexed schemas across years             │
│  • Normalized controlled vocabularies       │
│  • Cross-dataset relationships (FKeys)      │
└─────────────┬───────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────┐
│        ANAC Open Data (CKAN 2.6.8)          │
│  dati.anticorruzione.it/opendata           │
│  • REST API for metadata                    │
│  • ZIP downloads (CSV/JSON/TTL)             │
│  • Monthly incremental delta updates        │
└─────────────────────────────────────────────┘
```

---

## 6. Implementation Plan

### Phase 1: Research & Schema Mapping (Week 1–2)
- [x] Discover all 70 datasets and their metadata via CKAN API
- [x] Verify API access and download capability
- [x] Download sample data (1 month of CIG) and map complete column schemas
- [x] Identify schema variations across years (2007 vs 2025)
- [x] Build controlled vocabulary cross-reference tables
- [x] Create comprehensive data dictionary with column descriptions and code meanings

### Phase 2: Data Pipeline (Week 2–3)
- [x] Build CKAN downloader with smart caching and resume support
- [x] Build CSV/JSON parser → Python dataclasses/Pydantic models
- [x] Build data cleaner: handle encoding, NULLs, type coercion
- [x] Build loader: CSV → DuckDB/Parquet with proper indexes
- [x] Handle incremental delta updates (merging into full datasets)
- [ ] Validate data integrity (row counts, referential integrity with vocabularies)

### Phase 3: CLI Tool (Week 3–4)
- [ ] `anac datasets` — List all available datasets
- [ ] `anac download <dataset> --year 2024` — Download specific datasets
- [ ] `anac schema <dataset>` — Show column schema and data types
- [ ] `anac query "SELECT ..."` — SQL query against local DuckDB
- [ ] `anac stats` — Show data statistics and summary
- [ ] `anac update` — Fetch and merge incremental deltas


### Phase 4: Publication (Week 5–6)
- [ ] Create GitHub repo with comprehensive documentation
- [ ] Publish PyPI package
- [ ] Write README with installation + usage examples
- [ ] Create Jupyter notebook demo with interesting analyses
- [ ] Publish data dictionary with full column descriptions
- [ ] Include Italian+English documentation

---

## 7. Key Design Decisions to Resolve

1. **Scope:** Full CIG (2007–present, 100s of GB) or recent years only (2019–2025)?
2. **Storage:** DuckDB embedded (local) or also provide pre-processed Parquet files for download?
3. **Primary format:** OCDS (simpler, international standard) or raw CIG (more detailed, more data)?
4. **Incremental sync:** How often to refresh? Monthly deltas are available.
5. **Schema mapping:** Manual column mapping per year, or automatic discovery?

---

## 8. North Star Analyses — Making Italy Legible

Aligned with the Manifesto: *"Are we making Italy more legible to its citizens?"*

1. **Money flows** — Follow procurement money from State → Region → Municipality → Contractor → Subcontractor
2. **Red flags** — Unusually high direct awards (AFFIDAMENTO DIRETTO), repeated single-contractor patterns, cost overruns via `varianti` data
3. **Geographic analysis** — Procurement distribution across 20 regions, 107 provinces, 8,000+ comuni
4. **PNRR tracking** — Recovery Fund spending by project, region, category (via `indicatori-pnrrpnc`)
5. **Temporal trends** — How procurement patterns changed over 19 years (procedure type distributions, average contract values, etc.)
6. **Contractor network graph** — Connected companies, subcontracting chains, repeated winner patterns
7. **Revolving doors** — Officials appearing in both contracting authority (`stazioni-appaltanti`) and contractor (`aggiudicatari`) lists
8. **SOA certification analysis** — Which certifications are required most? Potential certification concentration patterns

Other example queries: "Top 10 contractors by total value", "PNRR projects in Milan", "How much did Regione Lombardia spend in 2024?"

---

## 9. Reference Links

| Resource | URL |
|----------|-----|
| Open Data Portal | https://dati.anticorruzione.it/opendata/ |
| CKAN REST API | https://dati.anticorruzione.it/opendata/api/3/action/ |
| Download Base | https://dati.anticorruzione.it/opendata/download/dataset/ |
| Analytics Dashboard | https://dati.anticorruzione.it/superset/dashboard/appalti/ |
| Main ANAC Site | https://www.anticorruzione.it |
| License | CC-BY-SA-4.0 |
