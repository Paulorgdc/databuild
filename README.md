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
| Structural inventory of the six monthly measurements | `scripts/01_inventory.py` |
| Workbook reader and generic error detectors | `scripts/reader.py`, `scripts/rules.py`, `scripts/02_diagnose.py` |
| Standard data model and versioned rule catalog (foundation of the Stage 02 audit engine, in progress) | `scripts/model.py`, `scripts/catalog.py` |
| Cross-measurement comparison | `scripts/03_compare.py` |
| AI viability experiment (complement, not primary detector) | `scripts/04_ai_viability.py` |
| Local diagnostic report | `scripts/05_report.py` (writes to `outputs/`) |
| Detector tests (unit and random-mutation) | `tests/` (tests only) |

The repository is public, but the code is proprietary. Workbooks (`data/`, ignored entirely), derived results (`outputs/`) and extension records (`reference/`) are kept locally and are never versioned. The company is identified publicly only as **Construtora Horizontes**.

---

## Features

- Row classification by geometry (group, section, leaf item, total) instead of code prefixes.
- Detection of incomplete sums and subtotals, missing or typed-over formulas, formula errors, broken or wrong links, and broken arithmetic identities.
- Root-cause versus cascade attribution of financial divergences.
- Comparison across measurements: removed items, contract drift and accumulated continuity.
- Mutation tests that inject errors into random cells of a temporary copy and check that the detectors flag exactly those cells.

---

## Target Architecture

Defined for the next stages; **none of it is implemented yet**.

| Layer | Role |
|---|---|
| Excel | Original source |
| Python, openpyxl, Pandas | Extraction |
| SQL Server | Storage |
| Bronze | Raw data |
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

---

## Local Development

```bash
pip install -r requirements.txt
# copy the six MED *.xlsx workbooks into data/ (local only, entirely ignored by Git)
python scripts/01_inventory.py
python scripts/02_diagnose.py
python scripts/03_compare.py
python scripts/04_ai_viability.py
python scripts/05_report.py
python -m unittest discover -s tests -v
```

Scripts only read `data/`; every result goes to `outputs/`. Both folders are ignored by Git and must never be committed.

---

## License

Proprietary. All rights reserved (see `LICENSE`). The repository is public for visibility only; the license is a deliberate exception to the academic MIT standard and the code is not open for reuse.
