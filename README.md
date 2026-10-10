# Streamlit Budget Dashboard

This project imports personal bank CSVs in a local Streamlit app, normalizes them to one transaction schema, applies merchant/category rules, and exports a categorized CSV. Optional AI suggestions (bring your own API key) can help resolve unfamiliar merchants after the deterministic rules and cache.

---

## Features

- Upload multiple bank CSVs and map each file's columns with dropdowns.
- Select account type manually and choose the sign convention for single signed-amount columns.
- Re-import a file with changed settings to replace that filename's rows; removing an upload removes its imported, edited, and excluded rows.
- Review and edit dates, descriptions, merchants, amounts, categories, and transaction types before export.
- Review declined, payment, and transfer rows with reasons; restore selected rows after validation.
- Optionally request an AI merchant suggestion (Gemini, OpenRouter, Ollama Cloud, or local Ollama) for unresolved transactions and explicitly approve it into the local merchant cache.
- Ask app-usage questions in the sidebar's AI help chat; it only receives the question and a short help conversation, not imported transaction data.
- Save a reusable CSV format for each bank, including custom banks, so future imports reuse its delimiter, amount rules, account type, and column mapping.
- Search transactions and the merchant cache with case-insensitive fuzzy matching.
- Navigate between Home, statement processing, Overview, Analytics, and Budget pages.
- Compare income, spending, and net savings by month, year to date, full year, or a custom date range.
- Set session-only monthly spending limits by main category or subcategory and review progress.
- Export the established nine-column categorized CSV format.

## Privacy

Bank data is sensitive. `.data/`, the generated categorized CSV, `merchant_cache.json`, and `bank_profiles.json` are ignored and excluded from version control for new commits. The app processes CSV data locally. AI providers are not called unless the user configures one and requests a suggestion or help answer; then only the selected description, amount, and allowed categories (or the help question) are sent. Files previously pushed to a remote remain in Git history unless that history is separately rewritten.

## Project Structure
- `dashboard.py`: multipage Streamlit entrypoint and navigation.
- `pages/`: Home instructions, transaction Overview, date-range Analytics, and session-scoped Budget pages.
- `app.py`: CSV import, review, and export page.
- `dashboard_data.py`: safe normalization and calculations for analytics and budgets.
- `transaction_import.py`: canonical transaction normalization and validation.
- `final_export.py`: merchant resolution, exclusions, categories, validation, and export formatting.
- `cleaning_logic.py`: merchant rules and category hierarchy.
- `merchant_assistance.py`: AI merchant suggestions and approved cache writes.
- `ai_providers.py`: provider calls (Gemini, OpenRouter, Ollama Cloud/local) using user-supplied keys.
- `ai_settings.py`: sidebar AI provider, model, and key settings.
- `bank_profiles.py`: local saved CSV formats for built-in and custom banks.
- `fuzzy_search.py`: shared fuzzy matching for local search fields.
- `tests/test_transaction_import.py`: importer/exporter regression tests.
- `.data/`, `merchant_cache.json`, and `all_banks_final_categorized.csv`: local data/artifacts; ignored by Git.

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

Upload one or more bank statement CSVs, choose each file, select its bank and account, and map the required columns. For a single signed amount column, choose whether positive values mean money in or money out. Use “Save this bank's CSV format as the default” after mapping to remember settings for a built-in or new bank; saved local profiles are selected from the bank menu on later imports. Map a Status column under Optional columns to exclude declined rows. Exact duplicate transactions with the same date, description, signed amount, bank, and account are excluded from the export and listed in the review panel with a duplicate reason and restore option. Excluded transfers/payments also have a review panel with a reason and restore option. Positive descriptions containing refund/return/reimbursement wording are categorized as Income / Refunds.

Imported, edited, and excluded-review rows remain in the session when you leave Process statements or remove a file from the uploader. Use **Remove one statement's data** or **Clear imported data** to delete them explicitly. Re-submitting the same filename with changed columns, sign convention, delimiter, or number format replaces its previous rows. Submitting an unchanged file with unchanged settings is blocked to avoid duplicates. Filenames must be unique among the currently uploaded statements.

The download uses the same nine columns as the example file: `date`, `description`, `merchant`, `type`, `amount`, `main_category`, `sub_category`, `bank`, and `account`. Export dates use `MM/DD/YYYY`. The editable preview and download are sorted oldest to newest by transaction date, including after date edits; invalid dates remain at the bottom for correction. Before downloading, review dates, signs, amounts, merchants, and categories. Unmatched spending is flagged for category review and blocks download until you assign a category or explicitly confirm General Spending. Approved category pairs are saved by normalized transaction description and reused for matching rows and future imports. For a transfer not caught by the rules, set its main category to `Transfer`; the subcategory becomes `N/A`, and the row moves to the excluded review list instead of the export. Search the editable transaction preview and merchant cache by text; searches ignore case and tolerate typos, and filtering does not remove hidden transactions or cache entries. Known descriptions use the existing merchant rules and cache; unrecognized descriptions appear in the review queue. For an unresolved transaction, the optional AI tool suggests a merchant and a main/subcategory from the app's allowed choices. Known merchant rules take precedence; for a new merchant, the AI category pair is preselected. Review and edit suggestions before approval updates the preview and local cache. The unresolved count updates as matches are approved. The Personal merchant cache panel lets you add, edit, and delete cached mappings, then save them locally.

For an unresolved transaction, choose a provider under **AI settings** in the sidebar first (see [AI providers](#ai-providers)).

The download columns are `date`, `description`, `merchant`, `type`, `amount`, `main_category`, `sub_category`, `bank`, and `account`. Dates export as `MM/DD/YYYY`.

## AI providers

Open **AI settings** in the sidebar, choose a provider, paste your own API key, pick a model, confirm the privacy notice, and optionally press **Test connection**. You pay for and are responsible for your own usage, quotas, and the provider's terms; free tiers and model names change, so check each provider's pricing page.

| Provider | Get a key | Privacy / pricing |
| --- | --- | --- |
| Google Gemini | [AI Studio](https://aistudio.google.com/apikey) | [Terms](https://ai.google.dev/gemini-api/terms), [pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| OpenRouter | [Keys](https://openrouter.ai/keys) | [Privacy](https://openrouter.ai/privacy), [models](https://openrouter.ai/models) |
| Ollama Cloud | [Keys](https://ollama.com/settings/keys) | [Privacy](https://ollama.com/privacy), [docs](https://docs.ollama.com/cloud) |
| Ollama (local) | no key; [install Ollama](https://ollama.com/download) and `ollama pull <model>` | runs on your computer; only works when the app also runs locally |

- Keys live only in your Streamlit session memory; they are never written to disk, the database, or logs. **Clear API key** removes it. A password field only hides the key on screen, and the hosting server still handles it, so use a spend-limited key and revoke it afterward.
- Free tiers may use your prompts for training or human review. The app shows a notice per provider and requires confirmation for cloud providers. Never include sensitive details in the help chat; suggestions send only a transaction description and amount.
- Nothing is sent to a provider other than the one you selected, and there is no automatic fallback. Model names listed are suggestions that are not verified; type any model your provider supports.
- Deploying to Streamlit Community Cloud needs no app-level AI secret. Don't put user keys in the app's shared secrets. Local files such as the merchant cache are not guaranteed to persist there.
- To refresh dependencies after this change run `uv lock` (the old optional `ai` extra was removed).

## Current Scope

Imported transactions and budgets are held in the Streamlit session; ending the session clears them. Export categorized transactions to keep a copy. Budget targets are not yet stored in a persistent database or separated by user account, so persistent multi-user hosting will require user-specific storage and authentication.

---
