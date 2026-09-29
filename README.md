# Bank Statement Processor

This project imports personal bank CSVs in a local Streamlit app, normalizes them to one transaction schema, applies merchant/category rules, and exports a categorized CSV. Optional local Ollama suggestions can help resolve unfamiliar merchants after the deterministic rules and cache.

---

## Features

- Upload multiple bank CSVs and map each file's columns with dropdowns.
- Select account type manually and choose the sign convention for single signed-amount columns.
- Re-import a file with changed settings to replace that filename's rows; removing an upload removes its imported, edited, and excluded rows.
- Review and edit dates, descriptions, merchants, amounts, categories, and transaction types before export.
- Review declined, payment, and transfer rows with reasons; restore selected rows after validation.
- Optionally request a local Ollama merchant suggestion for unresolved transactions and explicitly approve it into the local merchant cache.
- Export the established nine-column categorized CSV format.

## Privacy

Bank data is sensitive. `.data/`, the generated categorized CSV, and `merchant_cache.json` are ignored and excluded from version control for new commits. The app processes CSV data locally. Ollama runs locally as well; it is not called unless the user requests a suggestion. Files previously pushed to a remote remain in Git history unless that history is separately rewritten.

## Project Structure
- `app.py`: Streamlit CSV import, review, and export interface.
- `transaction_import.py`: canonical transaction normalization and validation.
- `final_export.py`: merchant resolution, exclusions, categories, validation, and export formatting.
- `cleaning_logic.py`: merchant rules and category hierarchy.
- `merchant_assistance.py`: optional Ollama suggestions and approved cache writes.
- `tests/test_transaction_import.py`: importer/exporter regression tests.
- `.data/`, `merchant_cache.json`, and `all_banks_final_categorized.csv`: local data/artifacts; ignored by Git.

---

## Setup

Requires Python 3.13 or later. Install the base app dependencies and launch:

```bash
uv sync
uv run streamlit run app.py
```

Ollama is optional. To enable local merchant suggestions, install Ollama, then install the extra and pull the default model:

```bash
uv sync --extra ai
ollama pull gemma3:4b
```

---

## Usage

### Guided CSV Import App

From the project folder, start the app with:

```bash
uv run streamlit run app.py
```

Upload one or more bank statement CSVs, choose each file, select its account, and map the required columns. For a single signed amount column, choose whether positive values mean money in or money out. Map a Status column under Optional columns to exclude declined rows. Excluded transfers/payments have a review panel with a reason and a restore option. Positive descriptions containing refund/return/reimbursement wording are categorized as Income / Refunds.

Removing an uploaded file removes all imported, edited, and excluded-review rows from that filename. Re-submitting the same filename with changed columns, sign convention, delimiter, or number format replaces its previous rows. Submitting an unchanged file with unchanged settings is blocked to avoid duplicates. Filenames must be unique among the currently uploaded statements.

The download uses the same nine columns as the example file: `date`, `description`, `merchant`, `type`, `amount`, `main_category`, `sub_category`, `bank`, and `account`. Export dates use `MM/DD/YYYY`. Known descriptions use the existing merchant rules and cache; unrecognized descriptions are exported for later review. The app recognizes Capital One, Goldenwest Credit Union, and SoFi layouts. For a new bank, enter its name and select the matching columns; the app will validate the file and explain any missing or invalid values.

The download columns are `date`, `description`, `merchant`, `type`, `amount`, `main_category`, `sub_category`, `bank`, and `account`. Dates export as `MM/DD/YYYY`.

## Current Scope

This is an importer and categorized CSV exporter, not yet a persistent Mint-like budget dashboard. Imported data is held in the Streamlit session; restarting the app clears that session. A local database, spending trends, budgets, and account balances are future work.

---