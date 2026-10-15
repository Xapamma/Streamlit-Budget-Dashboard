# Streamlit Budget Dashboard — AI Agent Context

## Project Identity
A multipage **Streamlit** app for normalizing bank-statement CSVs, categorizing transactions, providing analytics/budgeting, and allowing manual review of merchants and categories with automatic persistence of reviewed mappings. Hosted online at **https://budget-dashboard-4u.streamlit.app/**. Python ≥ 3.13. Package manager: `uv`.

## Quick Commands
```bash
uv sync                    # Install dependencies
uv run streamlit run dashboard.py  # Launch app
uv run python -m unittest discover tests  # Run all tests
```

## Architecture Overview

### Entrypoint & Pages
- **`dashboard.py`** — Multipage entrypoint. `st.navigation(pages, position="sidebar")`.
- **`app.py`** (~1400 lines) — Main "Process statements" page. CSV import, column mapping, transaction editor, category review, exclusion, export.
- **`pages/home.py`** — Landing page with workflow instructions.
- **`pages/overview.py`** — Metrics, charts, recent transaction editor.
- **`pages/analytics.py`** — Date range selector, charts, searchable editor, CSV download.
- **`pages/budget.py`** — Month selector, income entry, limits, PDF/Excel download.

### Data Pipeline Modules
- **`transaction_import.py`** — CSV → standardized 13-col DataFrame. `_ADAPTERS` dict for known banks. `load_transactions()` handles delimiters, column mapping, amount parsing, sign reversal, number formats, date parsing, declined rows.
- **`final_export.py`** — Standardized → categorized 9-col export. Merchant resolution (rules → persistent merchant mapping → fallback). Category resolution (rules → persistent category mapping → user edits). Map persistence (`save_approved_mappings()`). Exclusion logic. Composite label system (`CATEGORY_LABEL_TO_PAIR`/`CATEGORY_PAIR_TO_LABEL`).
- **`cleaning_logic.py`** — Deterministic rules. `description_map` (~90 regex→merchant patterns), `sub_category_map`, `category_hierarchy` (12 categories), `clean_description_for_matching()`.
- **`dashboard_data.py`** — Financial calculations for overview/analytics/budget pages.

### Utility Modules
- **`bank_profiles.py`** — loader for the committed `bank_profiles.json` CSV format seeds.
- **`fuzzy_search.py`** — `fuzzy_match_indices()` using RapidFuzz.
- **`budget_export.py`** — `build_excel()` (OpenPyXL) and `build_pdf()` (ReportLab).

### Data Files
Static seed data, loaded read-only each session and written at runtime when mapping edits are applied:
- **`bank_profiles.py`** — read-only loader for the committed bank CSV format seeds
- **`bank_profiles.json`** — `{bank: {delimiter, amount_mode, ...}}` (committed)
- **`category_cache.json`** — `{normalized_description: {main_category, sub_category}}`
- **`merchant_cache.json`** — `{normalized_description: merchant_name}`

`bank_profiles.json`, `category_cache.json`, and `merchant_cache.json` are all committed, so the hosted app lists every bank and mapping.

## Critical Design Patterns

### 1. Session State Only — No Database
Key `st.session_state` DataFrames: `standardized_transactions` (13-col), `editable_transactions` (9-col + `_source_file`, `_source_row`, `_category_reviewed`), `dropped_transactions` (excluded rows), `uploaded_file_infos`.

### 2. Widget Revision Counters
When programmatically changing a DataFrame in `st.data_editor`:
```python
st.session_state.editor_revision += 1
st.session_state.pop(f"editor_key_{revision}", None)
st.rerun()
```
Counters: `export_editor_revision`, `dropped_editor_revision`, `overview_recent_editor_revision`, `analytics_transaction_editor_revision`, `budget_editor_revision`.

### 3. Category Pair System
Composite labels `"Main :: Sub"`. Use the mappings in `final_export.py`:
- `CATEGORY_PAIR_TO_LABEL[("Main", "Sub")]` → `"Main :: Sub"`
- `CATEGORY_LABEL_TO_PAIR["Main :: Sub"]` → `("Main", "Sub")`
- Never set a sub-category not belonging to its main category.

### 4. Description Normalization
Always call `clean_description_for_matching()` from `cleaning_logic.py` before using a description as a mapping key or rule match target. Strips bank noise, removes non-alpha chars (except `& ' -`), lowercases, normalizes whitespace.

### 5. Multi-Layer Resolution (priority order)
**Merchant:** rules (`match_description_map`) → `merchant_cache.json` (read-only master) → `smart_title(description)`  
**Category:** `add_categories()` → `category_cache.json` (read-only master) → user edits (session-only)

### 6. Import Signature
`build_import_signature()` hashes file content + all import options. Changed signature = replace rows. Same signature + same filename = no-op.

### 7. Transfer/Declined Pipeline
- Import: unparseable rows → `_declined_transactions` attribute on the standardized frame
- Export: `split_filtered_transactions()` → transfers excluded
- Editor: main_category="Transfer" → moved to excluded (sub_category="N/A")
- Restore: `restored_rows_to_standardized()` → back to standardized pool

### 8. Review Persistence

## Conventions
- **Imports:** Standard library → third-party → local.
- **Python 3.13+:** Use `from __future__ import annotations` in new modules.
- **Type hints:** Use `typing` module annotations and `| None` unions.
- **Docstrings:** All public functions have triple-quoted docstrings.

## Testing Patterns
```python


# File I/O tests
import tempfile
with tempfile.TemporaryDirectory() as tmpdir:
    path = Path(tmpdir) / "test.csv"
    path.write_text("...")

# Streamlit integration tests
from streamlit.testing.v1 import AppTest
app = AppTest.from_file("../dashboard.py")
app.session_state["key"] = value
app.switch_page("pages/budget.py").run()
```

## When to Look Where
| Need | Look In |
|------|---------|
| Add/modify bank adapter | `transaction_import.py` `_ADAPTERS` + `app.py` `BANK_OPTIONS` |
| Add merchant rules | `cleaning_logic.py` `description_map`/`sub_category_map` |
| Change category hierarchy | `cleaning_logic.py` `category_hierarchy` |
| Save reviewed merchant/category mapping | `final_export.py` `save_approved_mappings()` |
| Change export columns | `final_export.py` `FINAL_EXPORT_COLUMNS` + `transaction_import.py` `STANDARD_COLUMNS` |
| Fix widget not updating | Check revision counter pattern |
| Add new page | Create in `pages/`, register in `dashboard.py` |
| New financial metric | `dashboard_data.py` |
| Modify budget export | `budget_export.py` |

## Common Pitfalls
1. **Revision counters** — Programmatic DF changes must bump counter, pop old key, and `st.rerun()`.
2. **Category pairs** — Always validate via `CATEGORY_LABEL_TO_PAIR`.
3. **Description normalization** — Cache/rule lookups fail without `clean_description_for_matching()`.
4. **DataFrame immutability** — Most functions return new DFs; capture return values.
5. **`st.rerun()`** — Restarts the script from top; ensure termination conditions.

## Data Schema Reference

### STANDARD_COLUMNS (13 cols)
`transaction_id`, `date`, `posted_date`, `description`, `amount`, `transaction_type`, `bank_category`, `status`, `bank`, `account`, `currency`, `source_file`, `source_row`

### FINAL_EXPORT_COLUMNS (9 cols)
`date`, `description`, `merchant`, `type`, `amount`, `main_category`, `sub_category`, `bank`, `account`

### Category Hierarchy
```
Food & Dining: Groceries, Restaurants/Dining, Fast Food, Snacks
Transportation: Fuel, Auto Insurance, Maintenance, Repairs
Shopping & Supplies: General Retail, Home Improvement, Hobbies, Clothing
Housing & Bills: Mortgage/Rent, Phone, Cable/Internet, Service Fees, Utilities, Maintenance/Repairs
Health & Wellness: Doctor/Dentist, Pharmacy, Insurance, Personal Care
Gifts & Donations: Tithing, Fast Offerings, Hum Aid, Gift
Education: Education, Tuition, Books
Entertainment: Gaming, Subscriptions, Recreation, Tickets
Savings & Investments: Dividends, Investments, Emergency Fund, Retirement, House Down Payment
Income: Paychecks, Refunds, CC Rewards, Other Income, Savings/Other Withdrawals
Miscellaneous: Fees & Charges, Other Services
Vacations/Travel: Airfare, Travel, Lodging, Souvenirs
General Spending: (all unassigned subcategories)
Transfer: N/A
```