# Streamlit Budget Dashboard

This project imports personal bank CSVs in a local Streamlit app, normalizes them to one transaction schema, applies merchant/category rules, and exports a categorized CSV. Unresolved transactions are reviewed manually; approved merchant and category choices are saved so matching descriptions in future statements follow them automatically.

---

## Features

- Upload multiple bank CSVs and map each file's columns with dropdowns.
- Select account type manually and choose the sign convention for single signed-amount columns.
- Re-import a file with changed settings to replace that filename's rows; removing an upload removes its imported, edited, and excluded rows.
- Review and edit dates, descriptions, merchants, amounts, categories, and transaction types before export.
- Review declined, payment, and transfer rows with reasons; restore selected rows after validation.
- Review unresolved transactions in one editable list; assign or confirm each merchant and category, and the mapping is saved so the same description is auto-filled and skipped next time.
- Descriptions that match a saved mapping are already categorized and never appear in the review list.
- Import defaults for each built-in or custom bank come from the read-only seed formats supplied with the app; nothing is saved from the UI.
- Search transactions with case-insensitive fuzzy matching.
- Navigate between Home, statement processing, Overview, Analytics, and Budget pages.
- Compare income, spending, and net savings by month, year to date, full year, or a custom date range.
- Set session-only monthly spending limits by main category or subcategory and review progress.
- Export the established nine-column categorized CSV format.

## Privacy

Bank data is sensitive. `.data/`, the generated categorized CSV, and `bank_profiles.json` are ignored and excluded from version control for new commits. The app processes CSV data entirely locally — no data is sent to any external service. The merchant and category mappings (`merchant_cache.json`, `category_cache.json`) are updated in place when you apply reviewed transactions, so keep them in version control if you want your mapping history tracked. Files previously pushed to a remote remain in Git history unless that history is separately rewritten.

## Project Structure
- `dashboard.py`: multipage Streamlit entrypoint and navigation.
- `pages/`: Home instructions, transaction Overview, date-range Analytics, and session-scoped Budget pages.
- `app.py`: CSV import, review, and export page.
- `dashboard_data.py`: safe normalization and calculations for analytics and budgets.
- `transaction_import.py`: canonical transaction normalization and validation.
- `final_export.py`: merchant resolution, exclusions, categories, validation, and export formatting.
- `cleaning_logic.py`: merchant rules and category hierarchy.
- `bank_profiles.py`: read-only bank CSV format seeds for built-in and custom banks.
- `fuzzy_search.py`: shared fuzzy matching for local search fields.
- `tests/test_transaction_import.py`: importer/exporter regression tests.
- `.data/`, `bank_profiles.json`, and `all_banks_final_categorized.csv`: local data/artifacts; ignored by Git.
- `merchant_cache.json`, `category_cache.json`: master merchant/category mapping seeds (read-only).

---

## Setup

Requires Python 3.13 or later. Install the base app dependencies and launch:

```bash
uv sync
uv run streamlit run dashboard.py
```

AI is optional. See [AI providers](#ai-providers) below.

---

## Usage

### Multipage Streamlit App

From the project folder, start the app with:

```bash
uv run streamlit run dashboard.py
```

Start on Home for the workflow guide. Use Process statements to upload, map, review, and export bank CSVs; Overview for a session-wide snapshot; Analytics for monthly, year-to-date, yearly, or custom trends; and Budget to set monthly category limits.

Analytics groups income, positive spending outflow, and net savings. A selected month defaults to daily detail; longer ranges default to monthly detail, with daily, weekly, or monthly intervals available. Year-to-date ends on the latest imported date in that year. Click a positive spending bar to drill into that main category's subcategory totals. Use the Transactions by category filters to inspect rows for a main category or an exact main/subcategory pair. The Budget page uses the main/subcategory hierarchy from `cleaning_logic.py` but omits its export-only General Spending fallback and Transfer. Budget totals recalculate from the current edited transaction amounts in Process statements, excluding transfers. Choose a month and enter income line by line under Paychecks, Dividends, Refunds, CC Rewards, Other Income, and Savings / Other Withdrawals; the total and unallocated amount update automatically. Existing transactions for that month prefill missing source and spending targets without replacing limits you set. A main-category limit automatically rises to cover larger subcategory plans or selected-month spending. If planned categories exceed income, the page warns; if income remains unallocated, use the savings action to balance the plan. Remove individual budget lines or add removed lines back. The bottom summary shows main-category balances with optional subcategory details for the selected month.

Upload one or more bank statement CSVs, choose each file, select its bank and account, and map the required columns. For a single signed amount column, choose whether positive values mean money in or money out. Import defaults for built-in and custom banks come from read-only seed formats; there is no save button in the app. Map a Status column under Optional columns to exclude declined rows. Exact duplicate transactions with the same date, description, signed amount, bank, and account are excluded from the export and listed in the review panel with a duplicate reason and restore option. Excluded transfers/payments also have a review panel with a reason and restore option. Positive descriptions containing refund/return/reimbursement wording are categorized as Income / Refunds.

Imported, edited, and excluded-review rows remain in the session when you leave Process statements or remove a file from the uploader. Use **Remove one statement's data** or **Clear imported data** to delete them explicitly. Re-submitting the same filename with changed columns, sign convention, delimiter, or number format replaces its previous rows. Submitting an unchanged file with unchanged settings is blocked to avoid duplicates. Filenames must be unique among the currently uploaded statements.

The download uses the same nine columns as the example file: `date`, `description`, `merchant`, `type`, `amount`, `main_category`, `sub_category`, `bank`, and `account`. Export dates use `MM/DD/YYYY`. The editable preview and download are sorted oldest to newest by transaction date, including after date edits; invalid dates remain at the bottom for correction. Before downloading, review dates, signs, amounts, merchants, and categories. Unmatched spending is flagged for category review and blocks download until you assign a category or explicitly confirm General Spending. Category and merchant edits apply only to the current session and are never written to the master merchant/category mapping; download your CSV to keep them. For a transfer not caught by the rules, set its main category to `Transfer`; the subcategory becomes `N/A`, and the row moves to the excluded review list instead of the export. Search the editable transaction preview by text; searches ignore case and tolerate typos, and filtering does not remove hidden transactions. Known descriptions use the existing merchant rules and the read-only master mapping; unrecognized descriptions appear in the review queue. For an unresolved transaction, the optional AI tool suggests a merchant and a main/subcategory from the app's allowed choices. Known merchant rules take precedence; for a new merchant, the AI category pair is preselected. Applying a suggestion only updates the session's transaction, never the master mapping. The unresolved count updates as matches are applied.



The download columns are `date`, `description`, `merchant`, `type`, `amount`, `main_category`, `sub_category`, `bank`, and `account`. Dates export as `MM/DD/YYYY`.

## Current Scope

Imported transactions and budgets are held in the Streamlit session; ending the session clears them. Export categorized transactions to keep a copy. Budget targets are not yet stored in a persistent database or separated by user account, so persistent multi-user hosting will require user-specific storage and authentication.

---
