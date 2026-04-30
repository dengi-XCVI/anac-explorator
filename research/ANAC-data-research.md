# ANAC Data — Research Notes & Quick Reference

**Date:** 2026-04-26 

---

## Original Project Idea

> Procurement data from ANAC  
> Can be found on the ANAC Open Data Portal  
> → clean it  
> → expose CLI + dataset  
> Then: publish repo, document schema, show how to query with an LLM

---

## Key Findings

### The Portal
- **URL:** `https://dati.anticorruzione.it/opendata/`
- **Platform:** CKAN 2.6.8 (open-source data portal)
- **API:** Standard CKAN REST API at `/api/3/action/` — no auth needed
- **CORS:** `Access-Control-Allow-Origin: *`
- **License:** CC-BY-SA-4.0
- **Analytics:** Superset dashboard at `/superset/dashboard/appalti/`

### The Data — 70 Datasets Total

| Category | Key Datasets | Size Estimate |
|----------|-------------|---------------|
| **CIG** | `cig-2007` → `cig-2025` (19 yearly + delta) | ~1.2GB CSV/year |
| **SMARTCIG** | `smartcig-2011` → `smartcig-2025` + delta | Similar |
| **OCDS** | `ocds-appalti-ordinari-2018` → `2026` | Monthly JSON |
| **Registry** | stazioni-appaltanti (13MB), aggiudicatari (800MB) | — |
| **Contract Mgmt** | aggiudicazioni, subappalti, varianti, collaudo, stati-avanzamento, etc. (12 datasets) | 100MB–1GB each |
| **Funding/PNRR** | cup, fonti-finanziamento, indicatori-pnrrpnc | — |
| **Vocabularies** | tipo-scelta-contraente (39 types), categorie-opera, etc. (5 datasets) | Tiny |
| **Certifications** | attestazioni-soa | — |

### Verified Data Schema — Stazioni Appaltanti (18 columns)

```
codice_fiscale         — Tax code (e.g., "80106870159")
partita_iva            — VAT number
denominazione          — Full name (e.g., "COMUNE DI BELLAGIO")
codice_ausa            — AUSA code (European authority identifier)
natura_giuridica_codice — Legal nature code (e.g., 15 = Enti pubblici)
natura_giuridica_descrizione — Legal nature description
soggetto_estero        — Foreign entity flag (true/false)
provincia_codice       — Province code (e.g., "IT-MI")
provincia_nome         — Province name
citta_codice           — City ISTAT code
citta_nome             — City name
indirizzo_odonimo      — Street address
cap                    — ZIP code
flag_inHouse           — In-house flag
flag_partecipata       — Participated company flag
stato                  — State
data_inizio            — Start date
data_fine              — End date
```

### Verified Controlled Vocabularies — Procurement Methods (39 types)

```
1  → PROCEDURA APERTA
2  → PROCEDURA RISTRETTA 
8  → AFFIDAMENTO IN ECONOMIA - COTTIMO FIDUCIARIO
24 → AFFIDAMENTO DIRETTO
25 → AFFIDAMENTO DIRETTO A SOCIETA' IN HOUSE
27 → AFFIDAMENTO DIRETTO IN ADESIONE AD ACCORDO QUADRO/CONVENZIONE
38 → PROCEDURA COMPETITIVA CON NEGOZIAZIONE
5  → DIALOGO COMPETITIVO
7  → SISTEMA DINAMICO DI ACQUISIZIONE
16 → ACCORDO QUADRO
34 → PROCEDURA NEGOZIATA PER AFFIDAMENTI SOTTO SOGLIA
etc. (39 total)
```

---

## Feasibility: ✅ HIGHLY FEASIBLE

**Greenfield opportunity** — no existing Python packages on PyPI, no GitHub repos found.

### Why it works:
1. Data is openly licensed, API-accessible, well-structured
2. Multiple formats: CSV (semicolon-delimited), JSON (JSON Lines), TTL (RDF)
3. Controlled vocabularies for proper normalization
4. OCDS provides international standard
5. No existing tooling wraps this data

### Key Challenges:
1. **Volume:** Hundreds of GB across all 19 years
2. **Schema evolution:** Field structure may vary year-to-year
3. **Domain knowledge:** Italian procurement law context needed
4. **Relationship mapping:** Cross-dataset joins required

---

## Competitive Landscape

- **PyPI:** No ANAC-related packages
- **GitHub:** Zero public repos for ANAC open data processing
- **Existing:** Superset dashboard on ANAC site (limited interactivity, no programmatic access)
- **→ This is wide open for a well-designed CLI + Python package**

---

## Architecture Decision: DuckDB + Parquet

**Why:**
- DuckDB queries Parquet files directly — no ETL needed
- Parquet compressed = 10x+ smaller than CSV
- Embedded database — zero server setup
- Perfect for CLI usage and LLM-powered natural language queries
- SQL dialect is standard enough for text-to-SQL to work well

---

## Potential Analyses (North Star: Making Italy Legible)

1. **Money flows** — State → Region → Municipality → Contractor → Subcontractor
2. **Red flags** — Unusually high direct awards, repeated single-contractor patterns, cost overruns via `varianti`
3. **Geographic** — Procurement distribution across 8,000+ comuni
4. **PNRR tracking** — Recovery Fund spending by project/region/category
5. **19-year trends** — How procurement evolved from 2007 to now
6. **Contractor network** — Graph of connected companies, subcontracting chains
7. **Revolving doors** — Officials in both contracting authority AND contractor positions
8. **SOA concentration** — Certification requirement patterns

---

## Technical References

```
CKAN API:     https://dati.anticorruzione.it/opendata/api/3/action/
Download:     https://dati.anticorruzione.it/opendata/download/dataset/<name>/filesystem/<file>.zip
API Example:  curl 'https://dati.anticorruzione.it/opendata/api/3/action/package_list'
```
