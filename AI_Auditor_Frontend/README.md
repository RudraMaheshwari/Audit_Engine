# AI Audit Dashboard — Frontend

A Streamlit-based dashboard for reviewing TPI Due Diligence audit output files produced by the AI Auditor pipeline. Displays per-finding risk analysis in a structured tabular layout with filtering, search, and modal detail views.

---

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Running the App](#running-the-app)
- [Features](#features)
- [Output File Format](#output-file-format)
- [Excel Column Reference](#excel-column-reference)
- [UI Walkthrough](#ui-walkthrough)

---

## Overview

The dashboard reads Excel files produced by the `main.py` AI Auditor pipeline (located separately at `F:\EXL\AI_Auditor`). Each Excel file contains one sheet named **"Output A - Risk Detail"** with one row per audit finding, and an optional **"Output B - Scope Summary"** sheet.

This frontend loads those files, renders each finding as a row with fixed-height cells, and provides:

- File selection across multiple run outputs
- Risk type filtering (Potential Risk / Further Review Required / No Risk Identified)
- Free-text search across questions, descriptions, scope area, and rationale
- "Read more" modal for any truncated cell
- "View Details" modal showing all non-empty fields for a finding

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10 or higher |
| Streamlit | 1.36.0 or higher (`@st.dialog` support) |
| pandas | 2.0.0 or higher |
| openpyxl | 3.1.0 or higher |

---

## Setup

### 1. Clone / copy the frontend folder

```
F:\EXL\AI_Auditor_Frontend\
```

### 2. Create a virtual environment (recommended)

```bash
cd F:\EXL\AI_Auditor_Frontend
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` contents:

```
streamlit>=1.36.0
pandas>=2.0.0
openpyxl>=3.1.0
```

### 4. Set the output directory

Open `config.py` and update `OUTPUT_DIR` to point to the folder where your pipeline writes Excel files:

```python
OUTPUT_DIR = Path(r"C:\Users\RMahesh8\Downloads\AI_Auditor\AI_Auditor\output")
```

This can also be changed at runtime directly in the sidebar without touching the file.

---

## Project Structure

```
F:\EXL\AI_Auditor_Frontend\
│
├── app.py                  # Main Streamlit application entry point
├── config.py               # All configurable constants (paths, colours, columns)
├── requirements.txt        # Python package dependencies
├── README.md               # This file
│
└── utils\
    ├── __init__.py
    └── data_loader.py      # Excel loading, file listing, summary stats
```

### File responsibilities

| File | Responsibility |
|---|---|
| `app.py` | Page config, CSS, sidebar, header, metrics, filter row, finding row rendering, modal dialogs, pagination, main loop |
| `config.py` | Output directory path, risk colour map, filter option list, detail column order, rows-per-page |
| `utils/data_loader.py` | `list_output_files()` — glob for xlsx files newest-first; `load_excel()` — reads "Output A - Risk Detail" sheet; `get_summary_stats()` — counts per risk classification |

---

## Configuration

All tuneable values live in `config.py`.

### Output directory

```python
OUTPUT_DIR = Path(r"C:\Users\RMahesh8\Downloads\AI_Auditor\AI_Auditor\output")
```

Points to the folder where `main.py` (the pipeline) writes `TPI_Due_Diligence_Audit_<timestamp>.xlsx` files. The app globs for files matching that pattern and lists them newest-first in the sidebar.

### Risk colour map

```python
RISK_COLORS = {
    "Potential Risk":          { "border": "#C00000", "badge_bg": "#C00000", ... },
    "Further Review Required": { "border": "#E65100", "badge_bg": "#E65100", ... },
    "No Risk Identified":      { "border": "#2E7D32", "badge_bg": "#2E7D32", ... },
}
```

Controls the left-border colour of each finding card, the badge background, and the filter checkbox icon.

### Detail columns

```python
DETAIL_COLUMNS = [
    ("Scope Area",              "Scope Area"),
    ("Risk Score",              "Risk Score"),
    ("Risk Description",        "Risk Description"),
    ...
]
```

Ordered list of `(display label, exact Excel column header)` pairs shown in the "View Details" modal. Only fields that have a non-empty value for a specific row are displayed.

### Pagination

```python
ROWS_PER_PAGE = 20
```

Number of finding rows shown per page. Increase or decrease as needed.

---

## Running the App

```bash
cd F:\EXL\AI_Auditor_Frontend
streamlit run app.py
```

The app opens at `http://localhost:8501` by default.

To use a different port:

```bash
streamlit run app.py --server.port 8502
```

---

## Features

### Sidebar
- **Output Directory** — editable text input; change at runtime without restarting
- **Select Report** — radio list of all `TPI_Due_Diligence_Audit_*.xlsx` files in the output folder, sorted newest-first

### Header
- Displays phase, task, and status metadata
- Shows the filename of the currently loaded report
- Download and Activity Log placeholder buttons (UI only)

### Summary Metric Cards
Four cards always reflect the **full file** (not the filtered subset):
- Total Findings
- Potential Risk count
- Further Review Required count
- No Risk Identified count

### Filter Bar (top of page)
Three horizontal checkboxes — combinable:
- 🔴 Potential Risk
- 🟠 Further Review Required
- 🟢 No Risk Identified

Unchecking all hides all rows. Combinations show only the selected types.

### Search
Free-text search across: Question, Risk Description, Risk Type, Scope Area, Risk Rationale. Case-insensitive, substring match.

### Finding Rows
Each finding renders as a card with:

| Column | Content |
|---|---|
| **#** | Row number in current view |
| **Scope Area** | From the card header bar |
| **Risk Title** | Coloured badge — Potential Risk / Further Review Required / No Risk Identified |
| **Risk Type** | AI-generated one-liner summary (fixed height 110 px) |
| **Risk Description** | Full AI description (fixed height 110 px) |
| **Risk Rationale** | Policy rationale (fixed height 110 px) |
| **Score** | Risk score 1–10, colour coded (red ≥7, orange ≥4, green <4) |
| **View Details** | Opens full detail modal |

All cells are **fixed height (110 px)**. Content that exceeds the visible area shows a fade gradient at the bottom. A **"↗ Read more"** button appears below cells with long content and opens a modal with the complete text.

### Read More Modal
- Triggered by "↗ Read more" under any truncated cell
- Shows the field label and full untruncated text
- Streamlit native dialog with scrolling

### View Details Modal
- Triggered by "View Details" button on any row
- Risk title badge at the top
- Risk Score displayed prominently (large number, colour-coded)
- Scope Area, Evidence Classification, Processed At in a metadata card
- Each remaining non-empty field in its own bordered container with label + full content
- Fully scrollable
- Only shows fields that have actual data for that specific finding

### Pagination
- 20 rows per page (configurable in `config.py`)
- Previous / Next buttons
- Page resets automatically when filters or search term changes

---

## Output File Format

The dashboard expects Excel files named:

```
TPI_Due_Diligence_Audit_<YYYYMMDD_HHMMSS>.xlsx
```

The file must contain a sheet named exactly:

```
Output A - Risk Detail
```

with a header row in row 1. The pipeline (`main.py` + `run_phase3.py`) in `F:\EXL\AI_Auditor` produces these automatically.

---

## Excel Column Reference

The pipeline writes these columns in this order:

| # | Header | Description |
|---|---|---|
| A | `#` | Serial number |
| B | `Risk Title` | Classification: `Potential Risk` / `Further Review Required` / `No Risk Identified` |
| C | `Risk Type` | AI-generated one-liner summary of the risk (8–15 words) |
| D | `Scope Area` | Audit scope area name |
| E | `Risk Score` | Numeric score 1–10 |
| F | `Risk Score Reasoning` | Explanation of why this score was assigned |
| G | `Risk Rationale` | Policy or rule breached |
| H | `Risk Description` | Full detailed finding |
| I | `Source Details` | Documents utilised by the AI |
| J | `Evidence Classification` | A–E classification of evidence quality |
| K | `Evidence Explanation` | Conflict detection note |
| L | `Calculation Evidence` | Row-level data values (Phase 3 / transactional rows only) |
| M | `Question` | Source questionnaire question text |
| N | `Response` | TPI's response text |
| O | `Follow-up Question` | Follow-up question (if any) |
| P | `Follow-up Response` | Follow-up response (if any) |
| Q | `Processed At` | Timestamp when this row was written |

---

## UI Walkthrough

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Sidebar (navy)                  Main Content                                │
│  ─────────────                   ─────────────────────────────────────────── │
│  Output Directory [editable]     ┌── Risk Analysis (432) ──────────────────┐ │
│                                  │  Phase: TPI DD | Task: RA | Status: OK  │ │
│  Select Report                   │  Home > TPI Audits > Risk Analysis       │ │
│  ○ TPI_Due_Diligence_...xlsx     │  [Download]  [Activity Log]              │ │
│  ● TPI_Due_Diligence_...xlsx     └──────────────────────────────────────────┘ │
│  ○ TPI_Due_Diligence_...xlsx                                                  │
│                                  ┌─432─┐  ┌─11──┐  ┌─45──┐  ┌─376─┐         │
│  Filter by Risk Type             │Total│  │ PR  │  │ FRR │  │  NR │         │
│  ☑ 🔴 Potential Risk             └─────┘  └─────┘  └─────┘  └─────┘         │
│  ☑ 🟠 Further Review Required                                                │
│  ☑ 🟢 No Risk Identified         [Filter checkboxes: PR | FRR | NR]          │
│                                  [Search bar                               ] │
│                                                                               │
│                                  ── Showing 1–20 of 56 ──────────────────── │
│                                                                               │
│                                  ┌── #1  TPI Due Diligence ─────────────────┐│
│                                  │ RISK TITLE │ RISK TYPE │ DESC │ RAT │ SC ││
│                                  │ [Potential]│ one-liner │ ...  │ ... │8.0 ││
│                                  │            │ ↗Read more│↗Read │↗Read│    ││
│                                  │                           [View Details] ││
│                                  └──────────────────────────────────────────┘│
│                                                                               │
│                                  ┌── #2  TPI Due Diligence ─────────────────┐│
│                                  │  ...                                      ││
│                                  └──────────────────────────────────────────┘│
│                                                                               │
│                                     [◀ Previous]  Page 1 of 3  [Next ▶]      │
│                                                                               │
│                                  ── Johnson & Johnson | TPI DD Dashboard ─── │
└─────────────────────────────────────────────────────────────────────────────┘
```
