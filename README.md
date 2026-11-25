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
| Silver promotion, validation and Bronze lineage (Stage 05) | `database/migrations/010_silver_processamento.sql`, `011_silver_indices.sql`, `scripts/silver.py` |
| Gold publication, measurement indicators and reconciliation (Stage 06) | `database/migrations/012_gold_publicacao.sql`, `scripts/gold.py` |
| Airflow DAG, Ubuntu setup and incremental intake (Stage 07) | `airflow/`, `scripts/orquestrar.py` |
| Editable Power BI report, Gold semantic model and reconciliation (Stage 08) | `dashboard/build.py`, `dashboard/reconcile.py` |
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

## Stage 05: Silver treatment and validation

On Windows x64, the project runtime uses Python 3.14, Amazon Corretto JDK 17, Spark/PySpark 4.3.0, Pandas 2.3.3 and Microsoft JDBC 13.6.0 (jre11). The installer keeps Java and JDBC under `.runtime/` and Python packages in `.venv/`. Use the SQL Server administrator configured in `.env.local` for the migration. The promotion uses the pipeline login.

```powershell
python database/setup/install_silver.py
.venv/Scripts/python.exe scripts/dbadmin.py migrate
.venv/Scripts/python.exe scripts/silver.py run --json
.venv/Scripts/python.exe scripts/silver.py status --json
.venv/Scripts/python.exe scripts/silver.py verify --json
# Compare with the existing Stage 02 exports and reviewer marks in outputs/.
.venv/Scripts/python.exe scripts/silver.py verify --comparar-etapa02 --json
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

Spark reads Bronze sheets and cells via JDBC and writes typed DECIMAL values to execution-scoped staging. Pandas organizes the cell data and normalizes item text and validation flags. The Stage 02 engine validates the reconstructed sheets and compares consecutive measurements. A separate Decimal pass applies its recalculation identities and propagates missing inputs. Silver keeps cached, recalculated and pending values distinct, and stores findings with their root causes and source coordinates.

SQL Server publishes measurements, items, values, formulas and findings in one transaction. The CLI reports failed runs and returns a nonzero exit code. Replaying a published manifest returns the existing execution ID. A different manifest requires an explicit revision decision before replacing the published sequence. Review references, including the red marks, are used only by the optional comparison command.

The local run published six measurements, 7,920 typed values and 306 findings. All 306 finding IDs matched the Stage 02 baseline and all 15 reviewer red marks were covered. In the separate monetary projection, 1,367 results that Stage 02 computed with incomplete inputs remain NULL. Decimal truncation changed four service measurement values, which flowed through to the section totals, accumulated values and balances for 15 changes in total.

---

## Stage 06: Gold publication

Apply the migrations with the SQL Server administrator and publish the completed Silver execution with the pipeline login.

```powershell
.venv/Scripts/python.exe scripts/dbadmin.py migrate
.venv/Scripts/python.exe scripts/gold.py run --json
.venv/Scripts/python.exe scripts/gold.py status --json
.venv/Scripts/python.exe scripts/gold.py verify --json
```

Use `run --execucao <id>` to select a completed Silver execution explicitly. A replay returns the existing publication and preserves its fact keys. The loader checks the source execution and reconciles fact counts and booked impact inside the publication transaction. Gold rows without a publication record require operator review.

`gold.fact_medicao` holds one row per measurement. `gold.fact_valor_item` keeps stored and recalculated monetary values at measurement × item grain. Both preserve SQL NULLs. `gold.fact_pendencia_valor` keeps missing-data and contractual statuses at measurement × item × field grain. `gold.fact_ocorrencia` stores findings and their booked impact. Only primaries marked in Silver contribute to that amount. The accumulated amount and balance describe a measurement at a point in time. Use a single measurement for those indicators.

`gold.vw_qualidade_medicao` aggregates findings, cascades and pending values by measurement. Gold retains the Silver measurement and finding identifiers for lineage. The existing reporting login can read the Gold view and facts.

The local publication reconciled six measurements, 720 item facts, 306 findings, 143 cascade-to-primary links and 2,570 pending fields. Verification reported zero differences against Silver and a repeat left the publication unchanged.

---

## Stage 07: Airflow orchestration

`databuild_pipeline` runs discovery, incremental Bronze ingestion, Silver promotion, Silver audit, Gold publication and Gold reconciliation in that order. The Gold tasks use the exact Silver execution from the same run. Temporary failures get two automatic retries with backoff. A conflicting revision fails discovery and leaves downstream work blocked. Airflow allows one run and one task at a time for this DAG. SQL Server locks coordinate changes to the Bronze manifest with Silver promotion and Gold publication.

### Run locally

Use WSL 2 with Ubuntu 24.04, Python 3.12, Java 17 and ODBC 18. The installer sets up separate virtual environments for Airflow 3.4.0 and PySpark 4.3.0. PostgreSQL 16 keeps Airflow metadata in the dedicated `databuild_airflow` database. Airflow accesses it through a local Unix socket using peer authentication. The pipeline uses the existing `.env.local` through the Windows mount and Microsoft JDBC 13.6.0 for Spark. Run these commands from the project folder in PowerShell.

```powershell
wsl --list --verbose
# Install Ubuntu if it is absent. Restart Windows if the WSL installer requests it.
wsl --install -d Ubuntu-24.04 --no-launch --web-download
wsl -d Ubuntu-24.04 -u root --exec bash ./airflow/install-ubuntu.sh
wsl -d Ubuntu-24.04 -u databuild_airflow --exec bash ./airflow/install.sh
wsl -d Ubuntu-24.04 -u databuild_airflow
```

In Ubuntu, use the Windows host address from the default route when WSL is in NAT mode and SQL Server listens on TCP 1433. With mirrored networking, use IPv4 loopback or the address already set in `.env.local`. If PostgreSQL is stopped after a WSL restart, run `wsl -d Ubuntu-24.04 -u root --exec /usr/sbin/service postgresql start` from PowerShell.

```bash
# In NAT mode, set this before starting the Airflow services.
export DATABUILD_SQL_HOST="$(ip -4 route show default | awk 'NR==1 { print $3 }')"
source ./airflow/env.sh
airflow db check
airflow standalone >>"$AIRFLOW_HOME/standalone.log" 2>&1
```

Airflow listens on `127.0.0.1:8080`. The local auth manager stores its generated password, and Airflow writes task logs and configuration, under the Linux `AIRFLOW_HOME` outside the repository. Start a second Ubuntu shell as the same account to operate the DAG.

```bash
source ./airflow/env.sh
airflow dags list-import-errors
airflow dags unpause databuild_pipeline
airflow dags trigger databuild_pipeline --run-id manual-verification
airflow tasks states-for-dag-run databuild_pipeline manual-verification
```

The DAG defaults to manual runs. Set `DATABUILD_SCHEDULE` to a cron expression, for example `0 6 * * *`, before starting Airflow to enable scheduling in `America/Cuiaba`. Past schedules are skipped. Monitor task states, retries and verification counts in Airflow. For a failed load, consult the existing Bronze or Silver status CLI and the local SQL Server logs, then let the scheduler retry or trigger a new run after resolving the cause.

### Incremental ingestion and sequence changes

- Deliver complete workbooks as `MED *.xlsx` through an atomic rename into `data/` or `DATABUILD_INPUT_DIR`. Matching content is counted and skipped by SHA-256 without opening a new load. In a candidate database, unseen measurement numbers are added to Bronze. A batch interrupted after some files commit resumes from the missing hashes. A file and its load receipt commit together.
- A new measurement against a sealed sequence, a changed workbook for an existing measurement number or the same content assigned to a different number stops discovery before ingestion. Keep these files in a separate local intake folder for review. An aborted run remains visible in Airflow. Review the full sequence with the data owner, choose one approved workbook per measurement number and provision a separate SQL Server database and pipeline login with the existing migrations. Run the DAG against that candidate using a separate Airflow instance or with the current instance stopped and reconfigured.
- Verify the candidate end to end and obtain approval before redirecting consumers. Keep the previous database and its publication available. The DAG never switches the consumer database, deletes a published sequence or selects a revision automatically.

The two local verification runs completed all six tasks. Both recognized six duplicates, kept the existing Silver and Gold publication, and reported zero discrepancies. Table fingerprints across all three layers and hashes of the six original workbooks matched before and after the runs.

---

## Stage 08 — Power BI

Generate a new editable project under the ignored `dashboard/local/` directory. The generator uses the existing SQL Server endpoint configuration and refuses to overwrite a project. Use `--name <folder>` to create a separate copy.

```powershell
.venv/Scripts/python.exe -m dashboard.build
Invoke-Item -LiteralPath dashboard/local/DataBuild/DataBuild.pbip
```

In Power BI Desktop, select **Home → Refresh**. On the first connection, choose **Database** authentication and enter the Stage 03 reporting login using `DATABUILD_POWERBI_USER` and `DATABUILD_POWERBI_PASSWORD` from `.env.local`. Desktop manages these credentials for the current Windows user. For the existing self-signed instance on this workstation, set `$env:PBI_SQL_TRUSTED_SERVERS = 'localhost'` in the PowerShell session before opening a fresh Desktop process. Use a trusted SQL Server certificate on shared infrastructure. [Microsoft SQL Server connector](https://learn.microsoft.com/en-us/power-query/connectors/sqlserver#sql-server-certificate-isnt-trusted-on-the-client-power-bi-desktop-or-on-premises-data-gateway)

Power Query imports selected columns from the SQL Server `gold` schema. The model reuses the date, work, measurement, item, rule, class, severity and cause dimensions. Four facts preserve the measurement, measurement × item, finding and measurement × item × field grains. Filters propagate in one direction. The report uses the public case name **Construtora Horizontes**.

| Page | Use |
|---|---|
| Visão geral | Review official measurement totals, finding counts, primary errors and cascades. |
| Análise financeira | Filter by measurement, category and service to compare original and recalculated measured, contract, accumulated and balance amounts. |
| Inconsistências | Filter by class, severity and rule. Inspect finding IDs, cells, primary errors, cascades, suspicions and legitimate exceptions. |
| Auditoria e decisão | Review pending fields, contractual checks and the suggested review priority by measurement. |

The model contains 32 DAX measures and 13 active, single-direction relationships. The 17 financial measures retain fixed-decimal precision and display Brazilian reais to two decimal places. Period flow is additive across measurements. Contract, accumulated amounts and balances use the last measurement in the active scope. Financial detail aggregates leaf services to avoid counting parent subtotals twice. Totals stay blank when a required amount is missing, and deltas require both amounts. Booked impact includes only flagged, proven primary findings. Contractual findings and contract-dependent fields are separate indicators.

Each page includes buttons for the four report destinations. The overview chart uses blue for primary errors and amber for cascades, with a labeled legend. The inconsistency charts use separate colors for severity and rule. Financial tables show missing values as blanks and cards use the Desktop blank indicator.

Run the read-only verification after refresh. Pass the port of the specific open project, available to Power BI external tools or in its `msmdsrv.port.txt` under `%LOCALAPPDATA%\Microsoft\Power BI Desktop\AnalysisServicesWorkspaces`.

```powershell
.venv/Scripts/python.exe -m dashboard.reconcile --baseline
.venv/Scripts/python.exe -m dashboard.reconcile --baseline --port 12345
```

Replace `12345` with the current project port. Use `--baseline` for the audited six-measurement sequence. The SQL check compares the counts to the Gold quality view. With `--port`, the reconciler compares DAX to SQL for measurement, date, category, service, finding class and an empty selection. It reports measure names and counts without printing amounts or business identifiers.

The authenticated Desktop import matched all 416 DAX results across 13 contexts, including blanks and numeric zeroes. Gold confirmed 306 findings across six measurements with 111 primary errors, 91 cascades, 47 suspicions, 40 legitimate exceptions and 17 contractual checks. It also confirmed 62 booked impacts and 2,570 pending fields, including two that depend on contract validation.

Only the generators and verifier are versioned. Keep generated PBIP/PBIR/TMDL, PBIX, caches and screenshots in the ignored project directory with local access controls. Edit the generated project in Desktop, or change the generators and produce a new copy. PBIP supports text-based authoring and the Desktop Bridge can open and reload definitions. This workflow uses Desktop for authentication and refresh and the local Analysis Services endpoint for DAX. [Microsoft PBIP documentation](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-overview) · [Desktop Bridge](https://learn.microsoft.com/en-us/power-bi/developer/agentic/power-bi-desktop-bridge-overview)

## Target Architecture

Stages 01 through 08 cover extraction diagnostics, validation, the SQL Server schema, Bronze ingestion, Silver processing, Gold publication, Airflow orchestration and the Power BI report. The web application is planned.

| Layer | Role |
|---|---|
| Excel | Original source |
| Python, openpyxl, Pandas | Extraction |
| SQL Server | Storage with schemas and roles from Stage 03 |
| Bronze | Raw workbook data and load history from Stage 04 |
| Silver | Typed measurements, calculated values and validation with Bronze lineage |
| PySpark | JDBC extraction and typed staging for Silver |
| Gold | Published dimensional facts, quality indicators and pending values |
| Apache Airflow | Full pipeline orchestration |
| Power BI | Power BI Desktop project using Import from Gold (`dashboard/`) |
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
