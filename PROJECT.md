# Streamlit Budget Dashboard — Project Overview

> **A multipage Streamlit app that normalizes bank-statement CSVs into a categorized transaction list, provides dashboards, analytics, and monthly budget planning. Unresolved merchants and categories are reviewed manually; approved mappings are saved so future statements follow them automatically.**

---

## Quick Start

```bash
uv sync
uv run streamlit run dashboard.py
```

**Requirements:** Python ≥ 3.13 (enforced by `.python-version` and `pyproject.toml`).  
**Dependencies:** streamlit, pandas, altair, openpyxl, reportlab, rapidfuzz.

---

## Project Architecture

```
Streamlit-Budget-Dashboard/
├── dashboard.py                  # Multipage entrypoint (st.navigation)
├── app.py                        # Main CSV import, review, category editing, export
├── cleaning_logic.py             # Deterministic merchant rules + category hierarchy
├── transaction_import.py         # CSV loading → standardized 13-column schema
├── final_export.py               # Standardized → categorized 9-col export + mapping persistence
├── bank_profiles.py              # Read-only bank CSV format seeds (JSON)
├── fuzzy_search.py               # RapidFuzz-based case-insensitive matching
├── dashboard_data.py             # Financial summaries for Overview, Analytics, Budget
├── budget_export.py              # PDF & Excel budget report generation
├── pyproject.toml                # Project metadata + dependencies
├── README.md                     # User-facing documentation
├── pages/
│   ├── home.py                   # Home page with workflow instructions
│   ├── overview.py               # At-a-glance summary + category editor
│   ├── analytics.py              # Date-range income/spending/net-savings analytics
│   └── budget.py                 # Monthly budget editor with PDF/Excel export
├── tests/
│   └── test_transaction_import.py # Integration + unit tests (1100+ lines)
├── .data/                        # [Git-ignored] local data directory
├── bank_profiles.json            # [Git-ignored] seed bank CSV formats
├── category_cache.json           # Master seed: normalized description → category pair
├── merchant_cache.json           # Master seed: normalized description → merchant name
└── all_banks_final_categorized.csv # [Git-ignored] exported download
```

### Page Navigation

| Route | File | Purpose |
|--------|------|---------|
| Home | `pages/home.py` | Workflow instructions, links to Process statements |
| Process statements | `app.py` | Upload CSVs, map columns, review/export transactions |
| Overview | `pages/overview.py` | Transaction count, income, spending, net savings, recent transactions |
| Analytics | `pages/analytics.py` | Monthly/YTD/yearly/custom date range breakdown, charts |
| Budget | `pages/budget.py` | Income entry, category limits, spending comparison, PDF/Excel export |

All pages read from `st.session_state.editable_transactions` (the categorized export rows).

---

## Data Flow

```
User uploads CSV
    │
    ▼
app.py — load_transactions(file, bank, account, column_mapping, ...)
    │
    ├── Known bank? → use built-in _ADAPTERS mapping
    └── Unknown bank? → use user-supplied column_mapping
    │
    ▼
transaction_import.py — STANDARD_COLUMNS (13-col DataFrame)
    │
    ▼
app.py — format_final_export(standardized)
    │
    ├── Resolve merchant via cleaning_logic rules
    ├── Resolve merchant via merchant_cache.json
    │
    ▼
app.py — FINAL_EXPORT_COLUMNS (9-col DataFrame) + source metadata
    │
    ├── User reviews in data editor
    │   └── Edits saved as persistent mappings (merchant_cache.json + category_cache.json)
    │
    ▼
app.py — Download categorized CSV / Transfer to excluded
    │
    ▼
pages/overview.py, analytics.py, budget.py — read editable_transactions
```

### Key Transformation Functions

| Step | Function | Location |
|------|----------|----------|
| CSV → Raw DataFrame | `load_transactions()` | `transaction_import.py` |
| Raw → Standardized | `_date_values()`, `_parse_amounts()` | `transaction_import.py` |
| Standardized → Categorized | `format_final_export()` | `final_export.py` |
| Add categories | `add_categories()` | `cleaning_logic.py` |
| Split transfers | `split_filtered_transactions()` | `final_export.py` |
| Validate rows | `validate_final_export_rows()` | `final_export.py` |
| Restore excluded | `restored_rows_to_standardized()` | `final_export.py` |
| Apply user edits | `apply_transaction_category_edits()` | `dashboard_data.py` |

---

## Module Deep Dive

### `dashboard.py` — Multipage Entrypoint
- Calls `st.set_page_config()` with wide layout.
- Defines 5 pages via `st.Page()`, navigated with `st.navigation()` in the sidebar.
- Stores page references in `st.session_state.budget_dashboard_pages`.

### `app.py` — CSV Import, Review & Export (~1511 lines)
The largest module. Handles:
1. **File upload** — Accepts `.csv` files via `st.file_uploader(multiple=True)`.
2. **Bank selection** — Auto-detect from filename or manual pick (Capital One, Goldenwest, SoFi, Other).
3. **Delimiter & number format** — Comma, semicolon, tab, pipe; US/UK vs European formats.
4. **Column mapping** — Dropdowns to map CSV columns to canonical fields.
5. **Sign convention** — Single signed amount vs split debit/credit columns.
6. **Import execution** — `load_transactions()` → `format_final_export()`.
7. **Transaction editor** — `st.data_editor` with selectable categories; searchable.
8. **Category review panel** — Unresolved transactions listed with an editable table; apply saves the mapping.
9. **Transfer/excluded review** — Removed rows with restore support.
10. **Export download** — CSV, 9 columns, `MM/DD/YYYY` dates.

### `transaction_import.py` — CSV Loading & Standardization
- `STANDARD_COLUMNS` (13 cols): transaction_id, date, posted_date, description, amount, transaction_type, bank_category, status, bank, account, currency, source_file, source_row.
- `_ADAPTERS` dict maps known banks (capitalone, gwcu, goldenwest, sofi) to column layouts.
- `load_transactions()`: delimiter detection, column mapping, amount parsing, sign reversal, number format conversion, date parsing, declined row separation, duplicate detection.
- `remove_source_rows()` / `replace_source_rows()` — Manage session rows on upload changes.
- `build_import_signature()` — Hash of file content + all import options.

### `final_export.py` — Categorization & Export Formatting
- `FINAL_EXPORT_COLUMNS` (9 cols): date, description, merchant, type, amount, main_category, sub_category, bank, account.
- **Merchant resolution hierarchy:**
  1. `cleaning_logic` rules (`match_description_map`)
  2. `merchant_cache.json` (read-only master seed)
  3. Fallback: `smart_title(description)`
- **Category resolution:**
  1. `add_categories()` from `cleaning_logic.py`
  2. `category_cache.json` (read-only master seed) override
  3. User edits (session-only) supersede everything
- **Exclusion:** `EXCLUDED_DESCRIPTION_FRAGMENTS`, `PAYMENT_LABEL_PATTERN`, `get_exclusion_reason()`, `split_filtered_transactions()`.
- **Validation:** `validate_final_export_rows()`.

### `cleaning_logic.py` — Deterministic Rules
- **`description_map`** — ~90 regex→merchant patterns (retail, dining, travel, health).
- **`sub_category_map`** — Merchant → sub-category mapping.
- **`category_hierarchy`** — 12 main categories with their sub-categories.
- **`add_categories(df)`** — Adds main_category/sub_category columns.
- **`clean_description_for_matching()`** — Strips bank noise, removes non-alpha chars (except `& ' -`), lowercases, normalizes whitespace.

### `final_export.py` — Mapping Persistence
Category and merchant review is manual: unresolved transactions appear in a one-by-one review flow; applying changes saves them as persistent mappings (via `save_approved_mappings()`) so matching descriptions skip the review list in future imports.

### Supporting Modules
- **`bank_profiles.py`** — `load_bank_profiles()` (read-only seed formats).
- **`fuzzy_search.py`** — `fuzzy_match_indices()` using RapidFuzz WRatio + partial_ratio.
- **`dashboard_data.py`** — Financial calculations: `sort_rows_by_date()`, `prepare_analytics_transactions()`, `summarize_transactions()`, `trend_totals()`, `spending_by_main_category()`, `apply_transaction_category_edits()`.
- **`budget_export.py`** — `build_excel()` (OpenPyXL) and `build_pdf()` (ReportLab) from `build_budget_rows()`.

### Pages
- **`pages/home.py`** — Landing page with 4 workflow steps and page links.
- **`pages/overview.py`** — Metrics, monthly line chart, top-8 categories bar chart, recent transactions editor.
- **`pages/analytics.py`** — Date range selector, summary metrics, income/spending chart, category breakdown, searchable/filterable transaction editor, CSV download.
- **`pages/budget.py`** — Month selector, income entry, per-category limits, sub-category breakdowns, savings allocation, over-budget warnings, PDF/Excel download.

---

## Key Design Decisions

### 1. Session-Only State
All transactions, budgets, and AI keys live in `st.session_state` only. Ending the session clears everything.

### 2. Revision Counters for Widget Refresh
When editable data changes programmatically, increment a revision counter, pop the old widget key, and `st.rerun()`. This keeps `st.data_editor` in sync.

### 3. Import Signature for Re-Import Detection
`build_import_signature()` hashes file content + all options. Changed signature → replace rows. Same signature + same filename → no-op.

### 4. Multi-Layer Category Resolution
```
User edits  ← apply to current transactions; reviewed rows are saved as mappings
    ↑
Persistent saved mapping (merchant_cache.json + category_cache.json)
    ↑
Deterministic rules (cleaning_logic.py: add_categories)
    ↑
Fallback (General Spending/Other or Income/Other Income)
```

### 5. Transfer / Declined Row Pipeline
- Import separates declined rows into `_declined_transactions` (a plain attribute on the standardized frame; `.attrs` holds only `declined_rows_dropped` so pandas 3.x `pd.concat` finalization can compare attrs safely).
- Export separates transfers via `split_filtered_transactions()`.
- User can mark rows as Transfer → moved to excluded with sub_category "N/A".
- Excluded rows can be restored via `restored_rows_to_standardized()`.

### 6. AI Modules Removed
The AI suggestion and help-chat features (`ai_providers.py`, `ai_settings.py`, `merchant_assistance.py`) were removed in favor of a simpler manual review flow. See `final_export.save_approved_mappings()` for the persistence layer that replaced session-only edits.

### 7. Normalized Descriptions for the Master Mapping
`clean_description_for_matching()` strips bank noise, removes non-alpha characters, lowercases, and normalizes whitespace. This makes mapping keys stable despite bank formatting variations.

---

## Category System

### Main Categories (12)
```
Food & Dining, Transportation, Shopping & Supplies, Housing & Bills,
Health & Wellness, Gifts & Donations, Education, Entertainment,
Savings & Investments, Income, Miscellaneous, Vacations / Travel
```

Plus two special system categories:
- **Transfer** — Moves rows to excluded list with sub_category "N/A"
- **General Spending** — Fallback for spending not matching any specific category

### Category Pair Labels
Displayed as `"Main :: Sub"` composite labels:
- `CATEGORY_PAIR_TO_LABEL`: `{(main, sub): "Main :: Sub"}`
- `CATEGORY_LABEL_TO_PAIR`: `{"Main :: Sub": (main, sub)}`
- `CATEGORY_PAIR_OPTIONS`: Sorted list of all composite labels
- `FINAL_MAIN_CATEGORY_OPTIONS`: Sorted list of main category names

---



---

## Session State Model

### Core DataFrames
| Key | Type | Description |
|-----|------|-------------|
| `standardized_transactions` | pd.DataFrame | Raw 13-col standardized rows |
| `editable_transactions` | pd.DataFrame | 9-col export + `_source_file`, `_source_row`, `_category_reviewed` |
| `dropped_transactions` | pd.DataFrame | Excluded rows + `removal_reason`, `status`, `restore` |
| `uploaded_file_infos` | list[dict] | Metadata about uploaded files |

### Budget
| Key | Description |
|-----|-------------|
| `monthly_budgets_by_month` | `{YYYY-MM: {category: limit}}` |
| `monthly_income_by_month` | `{YYYY-MM: float}` |

### Widget Revision Counters
`export_editor_revision`, `dropped_editor_revision`, `overview_recent_editor_revision`, `analytics_transaction_editor_revision`, `budget_editor_revision`, `budget_editor_schema_version`

---

## Testing

### Test Files
- **`tests/test_transaction_import.py`** (~1330 lines) — Capital One/Goldenwest/SoFi imports, custom banks, delimiters, number formats, declined rows, caches, validation, integration flows with `AppTest`.


### Running Tests
```bash
uv run python -m unittest discover tests
```

### Test Patterns
- Use `unittest.mock.patch` for mocking network calls.
- Use `tempfile.TemporaryDirectory` for file I/O tests.
- Use `streamlit.testing.v1.AppTest` for end-to-end page flow tests.
- CSV content constructed inline as strings.

---

## AI Agent Instructions

### Conventions
- **Python 3.13+** — Use `from __future__ import annotations` in new modules.
- **Type hints** — Use `typing` module annotations and `| None` unions.
- **Docstrings** — All public functions have docstrings.
- **Imports** — Standard library first, then third-party, then local.
- **Session state** — Always use `st.session_state` for cross-page data.
- **Widget keys** — Use unique, descriptive keys. Use revision counters to force refresh.

### Key Files for Navigation
| Need | Look In |
|------|---------|
| Modify CSV import format | `transaction_import.py` |
| Add/modify merchant rules | `cleaning_logic.py` |
| Change category hierarchy | `cleaning_logic.py` (category_hierarchy) |
| Change export columns | `final_export.py` + `transaction_import.py` |
| Save reviewed mapping | `final_export.py` (`save_approved_mappings`) |
| Edit master mapping seeds | `merchant_cache.json` + `category_cache.json` (persisted at runtime) |
| Add new page | Create in `pages/`, register in `dashboard.py` |
| Add new financial metric | `dashboard_data.py` |
| Modify budget export | `budget_export.py` |

### Common Pitfalls
1. **Widget revision counters** — If you programmatically change a DataFrame in `st.data_editor`, increment the revision counter, pop the old widget key, and call `st.rerun()`.
2. **Category pair validation** — Always use `CATEGORY_LABEL_TO_PAIR` / `CATEGORY_PAIR_TO_LABEL` to ensure main/sub consistency.
3. **Description normalization** — Always use `clean_description_for_matching()` before using a description as a mapping key.
4. **DataFrame immutability** — Many functions return new DataFrames. Check return values.
5. **`st.rerun()`** — Restarts the script from the top. Use sparingly.

### Adding a New Bank Adapter
1. Add entry to `transaction_import.py` `_ADAPTERS` dict.
2. Add to `BANK_OPTIONS` list in `app.py`.
3. Add normalization to `final_export.py` `_normalize_label()` if needed.
4. Add test cases.

### Adding a New Category
1. Add to `category_hierarchy` in `cleaning_logic.py`.
2. Add entries to `sub_category_map` for relevant merchants.
3. Verify derived structures (`CATEGORY_SUBCATEGORIES`, `FINAL_MAIN_CATEGORY_OPTIONS`, `CATEGORY_PAIR_OPTIONS`).
4. Add test.