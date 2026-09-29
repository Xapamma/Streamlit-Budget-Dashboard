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
- Save a reusable CSV format for each bank, including custom banks, so future imports reuse its delimiter, amount rules, account type, and column mapping.
- Search transactions and the merchant cache with case-insensitive fuzzy matching.
- Export the established nine-column categorized CSV format.

## Privacy

Bank data is sensitive. `.data/`, the generated categorized CSV, `merchant_cache.json`, and `bank_profiles.json` are ignored and excluded from version control for new commits. The app processes CSV data locally. Ollama runs locally as well; it is not called unless the user requests a suggestion. Files previously pushed to a remote remain in Git history unless that history is separately rewritten.

## Project Structure
- `app.py`: Streamlit CSV import, review, and export interface.
- `transaction_import.py`: canonical transaction normalization and validation.
- `final_export.py`: merchant resolution, exclusions, categories, validation, and export formatting.
- `cleaning_logic.py`: merchant rules and category hierarchy.
- `merchant_assistance.py`: optional Ollama suggestions and approved cache writes.
- `bank_profiles.py`: local saved CSV formats for built-in and custom banks.
- `fuzzy_search.py`: shared fuzzy matching for local search fields.
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

Upload one or more bank statement CSVs, choose each file, select its bank and account, and map the required columns. For a single signed amount column, choose whether positive values mean money in or money out. Use “Save this bank's CSV format as the default” after mapping to remember settings for a built-in or new bank; saved local profiles are selected from the bank menu on later imports. Map a Status column under Optional columns to exclude declined rows. Excluded transfers/payments have a review panel with a reason and a restore option. Positive descriptions containing refund/return/reimbursement wording are categorized as Income / Refunds.

Removing an uploaded file removes all imported, edited, and excluded-review rows from that filename. Re-submitting the same filename with changed columns, sign convention, delimiter, or number format replaces its previous rows. Submitting an unchanged file with unchanged settings is blocked to avoid duplicates. Filenames must be unique among the currently uploaded statements.

The download uses the same nine columns as the example file: `date`, `description`, `merchant`, `type`, `amount`, `main_category`, `sub_category`, `bank`, and `account`. Export dates use `MM/DD/YYYY`. Before downloading, review dates, signs, amounts, merchants, and categories. Transfers not caught by the rules should be manually deleted from the preview. Search the editable transaction preview and merchant cache by text; searches ignore case and tolerate typos, and filtering does not remove hidden transactions or cache entries. Known descriptions use the existing merchant rules and cache; unrecognized descriptions are exported for later review. In the optional Ollama panel, edit the suggested merchant and choose its main/subcategory before approving it into the cache and preview. The unresolved count updates as matches are approved. The Personal merchant cache panel lets you add, edit, and delete cached mappings, then save them locally.

To enable Ollama suggestions, install Ollama if needed and run `uv sync --extra ai`. Check available local models with `ollama list`; only run `ollama pull gemma3:4b` if that model is not already listed. Leave Ollama running, then request a suggestion in the app. The optional setup instructions and Ollama download link are also available in the app. Suggestions are requested only when you press the button; approved matches are saved locally.

The download columns are `date`, `description`, `merchant`, `type`, `amount`, `main_category`, `sub_category`, `bank`, and `account`. Dates export as `MM/DD/YYYY`.

## Current Scope

This is an importer and categorized CSV exporter, not yet a persistent Mint-like budget dashboard. Imported data is held in the Streamlit session; restarting the app clears that session. A local database, spending trends, budgets, and account balances are future work.

---