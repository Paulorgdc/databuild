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
| Generic error detectors and diagnosis | `scripts/detectors.py`, `scripts/02_diagnose.py` |
| Cross-measurement comparison | `scripts/03_compare.py` |
| AI viability experiment (complement, not primary detector) | `scripts/04_ai_viability.py` |
| Local diagnostic report | `scripts/05_report.py` (writes to `outputs/`) |
| Detector tests (unit and random-mutation) | `tests/` |

Workbooks, derived results and extension records (partnership annex, field logbook and photos in `reference/`) are kept locally and are not versioned.

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
# copy the six MED *.xlsx workbooks into data/ (not versioned)
python scripts/01_inventory.py
python scripts/02_diagnose.py
python scripts/03_compare.py
python scripts/04_ai_viability.py
python scripts/05_report.py
python -m unittest discover -s tests -v
```

Scripts only read `data/`; every result goes to `outputs/`, which Git ignores. See `data/README.md` for the data policy.

---

## License

Proprietary. All rights reserved (see `LICENSE`). This is a deliberate exception to the academic MIT standard: the repository is private and its code is not open for reuse.
