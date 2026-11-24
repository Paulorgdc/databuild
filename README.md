# 🏗️ DATABUILD: business intelligence for detecting and preventing errors in construction measurements

A diagnostic toolkit that finds formula, aggregation and financial-consistency errors in monthly construction measurement workbooks, built as the foundation for a validated BI pipeline.

[![Category](https://img.shields.io/badge/CATEGORY-ACADEMIC%20PROJECT-e0004d?style=for-the-badge)](#) [![UNIVAG](https://img.shields.io/badge/UNIVAG-PROJETO%20INTEGRADOR%20EXTENSIONISTA-00c8ff?style=for-the-badge)](#) [![License](https://img.shields.io/badge/LICENSE-PROPRIETARY-76b900?style=for-the-badge)](#)

---

## Overview

Monthly measurements of a construction job are kept in Excel workbooks full of hierarchical items, formulas and accumulated balances. A single missing term in a sum, a typed-over formula or a broken link silently distorts what is measured and invoiced. DataBuild detects these errors with generic, structure-based rules (not per-cell checks and not by trusting highlighted cells) and classifies each finding as a proven error, a suspicion or a legitimate exception.

**DataBuild** is the group, project and future application name. It was developed for the *Projeto Integrador e Extensionista* (6th semester of Software Engineering, UNIVAG), theme *Implementação de Inteligência de Negócios*, under the title **Aplicação de Inteligência de Negócios na Detecção e Prevenção de Erros em Medições de Obras**. The case study is a floor-laying job of the fictitious company **Construtora Horizontes**.

---

## Deliverables

| What | Where |
|---|---|
| Structural inventory of the six monthly measurements (Stage 01) | `scripts/01_inventory.py` |
| Workbook reader | `scripts/reader.py` |
| Standard data model and versioned rule catalog (34 rules, v2.0.0) | `scripts/model.py`, `scripts/catalog.py` |
| Spreadsheet-to-model builder | `scripts/build.py` |
| Structural, formula, reference and control detectors | `scripts/rules.py` |
| Independent recalculation, primary errors versus cascades | `scripts/recalc.py` |
| Cross-measurement consistency rules | `scripts/compare.py` |
| Validation engine (severity by impact, financial impact counted once) | `scripts/engine.py` |
| Structured export (CSV and JSON, schema, catalog) | `scripts/export.py` |
| Command-line interface (`audit`, `catalog`, `baseline`) | `scripts/audit.py` |
| Comparison against the Stage 01 baseline | `scripts/baseline.py` |
| AI viability experiment (complement, not primary detector) | `scripts/04_ai_viability.py` |
| SQL Server schema: versioned migrations (`bronze`, `silver`, `gold`, roles) | `database/migrations/` |
| Local SQL Server installer (admin, one time) | `database/setup/install_local_sqlserver.ps1` |
| Connection layer (pyodbc, SQLAlchemy/Pandas, JDBC/Spark, Airflow URIs) | `scripts/db.py` |
| Database administration CLI (`init-env`, `migrate`, `provision`, `check`, `status`) | `scripts/dbadmin.py` |
| Bronze workbook ingestion, load history and integrity verification (Stage 04) | `database/migrations/009_bronze_ingestao.sql`, `scripts/ingest.py` |
| Unit, integration, mutation and live SQL Server tests | `tests/` (tests only) |

The repository is public, but the code is proprietary. Workbooks (`data/`, ignored entirely), derived results (`outputs/`) and extension records (`reference/`) are kept locally and are never versioned. The company is identified publicly only as **Construtora Horizontes**.

---

## Features

- Row classification by geometry (group, section, leaf item, total) instead of code prefixes.
- **Independent recalculation** from the primary inputs (quantity, unit price, measured quantity, previous accumulated), compared with both the stored values and the local identities.
- **Primary errors versus cascades**: a cell that breaks its own identity is a primary error; a cell that only inherits an upstream error is a cascade, linked to its root cause. Only primary errors carry financial impact, so the same money is never counted twice.
- Detection of incomplete sums and subtotals, missing or typed-over formulas, formula errors, broken or wrong links.
- **Cross-measurement rules**: removed or added services, changed registration data, accumulated-value continuity.
- Four classes: proven error, suspicion, legitimate exception and *depends on contract validation* (scope changes are flagged, never asserted as errors).
- Structured export (CSV and JSON) and a Bronze loader for the underlying workbooks.
- Unit, integration and mutation tests (random error injection, row shifting, service removal and insertion).

### Stage 02 results on the six measurements

306 occurrences: 202 proven (111 primary errors and 91 cascades), 47 suspicions, 40 legitimate exceptions and 17 that depend on contract validation. All 15 reviewer red marks are covered. Compared with the Stage 01 baseline (306 findings, 149 proven), the engine drops 75 zero-balance truncation residues and adds 42 cascades, 5 continuity breaks and 12 removed-service flags. Details stay in local, ignored outputs.

---

## Stage 03: SQL Server infrastructure and modeling

SQL Server 2022 Express (free, local, fixed port 1433) hosts the `databuild` database with three schemas:

- **bronze**: raw text landing tables plus `carga` (load batch, the lineage root).
- **silver**: typed, validated tables (works, measurements, items, values, formulas, rules, occurrences, primary-cause links) with composite foreign keys, so an item always belongs to its measurement, a cascade always points to a primary error, and only primary errors carry financial impact. Money is `DECIMAL(18,2)` or `(18,4)`, never floating point.
- **gold**: star schema for Power BI: dimensions (date, work, measurement, item, rule, class, severity, cause), facts (item value, measurement, occurrence) and a cascade-to-primary bridge. Unknown members use key -1. `gold.usp_carregar_gold` loads it from Silver, transactionally and idempotently.

Two database roles: `rl_databuild_pipeline` (reads and writes the three schemas) and `rl_databuild_powerbi_reader` (reads `gold` only; `bronze` and `silver` are denied). Migrations are idempotent, tracked by SHA-256 in `dbo.schema_migration`, and an altered applied migration is rejected. Credentials live only in the git-ignored `.env.local`. `.env.example` documents the variable names. SQL Server integration tests use synthetic data in disposable databases and skip when the server is unavailable.

```bash
# once, from an elevated PowerShell: installs SQL Server 2022 Express and ODBC Driver 18
powershell -ExecutionPolicy Bypass -File database/setup/install_local_sqlserver.ps1
python scripts/dbadmin.py init-env      # creates .env.local with generated passwords
python scripts/dbadmin.py migrate       # applies database/migrations/*.sql
python scripts/dbadmin.py provision     # creates the pipeline and Power BI logins
python scripts/dbadmin.py check         # connects with each role and verifies permissions
```

---

## Stage 04: Bronze ingestion

The loader opens each workbook read-only. Bronze records the file's SHA-256, sheet metadata, populated cells, formulas, cached Excel values and error markers, along with extracted measurements, categories, services and values. Each file is loaded transactionally. A repeat with the same SHA-256 records a duplicate attempt without inserting another copy of the workbook data.

After applying the migrations and provisioning the SQL Server roles, use the ingestion CLI locally.

```bash
python scripts/ingest.py batch --dry-run     # extract and count without database writes
python scripts/ingest.py batch               # load MED *.xlsx from data/
python scripts/ingest.py file "data/MED filename.xlsx"
python scripts/ingest.py status
python scripts/ingest.py log
python scripts/ingest.py verify --json
```

Verification checks Bronze's internal consistency and compares cell and structured data with the local workbooks whose SHA-256 matches. The JSON field `confrontado_com_excel` confirms whether each file was compared with its source. Keep the workbooks, database contents and CLI output local.

The local run loaded six workbooks, 23 sheets and 23,129 cells, including 12,268 formulas and 7,076 cells with Excel errors. It also loaded 240 categories, 480 services and 3,600 value records. Verification matched all six sources. The recorded batch replay identified all six workbooks as duplicates and inserted no workbook data.

Excel error markers retain their source meaning. Empty cells contribute to sheet counts. Recalculated values, rule definitions and validation findings are deferred to the validation stage.

---

## Target Architecture

Stages 01 through 04 deliver extraction diagnostics, a workbook validation engine, the SQL Server schema, and Bronze ingestion. Planned work includes Silver processing with PySpark, Airflow orchestration, Power BI and the web application.

| Layer | Role |
|---|---|
| Excel | Original source |
| Python, openpyxl, Pandas | Extraction |
| SQL Server | Storage with schemas and roles from Stage 03 |
| Bronze | Raw workbook data and load history from Stage 04 |
| Silver | Treatment and validation |
| PySpark | Processing and business rules |
| Gold | Indicators and validated data |
| Apache Airflow | Full pipeline orchestration |
| Power BI | Direct consumption of Gold through SQL (`dashboard/`) |
| Web application | Batch import, audit and validation of measurements |

---

## Stack

- **Language:** Python
- **Workbook reading:** openpyxl
- **Consolidation:** Pandas
- **AI viability experiment:** scikit-learn
- **Database:** SQL Server 2022 Express, pyodbc, SQLAlchemy

---

## Local Development

```bash
pip install -r requirements.txt
# copy the six MED *.xlsx workbooks into data/ (local only, entirely ignored by Git)
python scripts/audit.py audit                 # audits data/MED *.xlsx -> outputs/etapa02/
python scripts/audit.py audit MED_A.xlsx MED_B.xlsx --out my_folder --format csv,json
python scripts/audit.py catalog --out catalogo.json
python scripts/audit.py baseline              # compares with the Stage 01 baseline
python -m unittest discover -s tests -v
```

Scripts open `data/` for reading. Local audit exports go to `outputs/` and ingestion writes to SQL Server. Git ignores both folders. The Stage 01 scripts (`02_diagnose`, `03_compare`, `05_report`) were replaced by the engine and remain in the `etapa-01` git tag.

---

## License

Proprietary. All rights reserved (see `LICENSE`). The repository is public for visibility only; the license is a deliberate exception to the academic MIT standard and the code is not open for reuse.
