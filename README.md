# Bank Statement Processor

This repository contains a Python-based data pipeline that compiles, cleans, and categorizes bank transaction data from multiple financial institutions.

The project transforms raw CSV exports into a structured dataset suitable for financial analysis by combining rule-based logic, fuzzy matching, and a local large language model (LLM).

---

## Table of Contents
- [Overview](#overview)
- [Motivation](#motivation)
- [Key Features](#key-features)
- [Ethical Considerations](#ethical-considerations)
- [Project Structure](#project-structure)
- [Requirements](#requirements)
- [Usage](#usage)
- [Data Pipeline](#data-pipeline)
- [Output Dataset](#output-dataset)

---

## Overview

Bank transaction data is often messy, inconsistent, and difficult to analyze across multiple accounts. This project automates the process of:

- Combining multiple bank CSV files  
- Cleaning and standardizing transaction data  
- Extracting merchant names  
- Categorizing transactions into meaningful groups  

The final output is a clean dataset that can be used for budgeting, analysis, or visualization.

---

## Motivation

This project was built to answer questions such as:

- How much am I spending in each category?
- Where can I reduce unnecessary expenses?
- How can I automate financial tracking instead of doing it manually?

Manually organizing bank data in spreadsheets is time-consuming and error-prone. This system reduces that effort significantly.

---

## Key Features

### Multi-Bank Integration

- Supports multiple institutions (e.g., Goldenwest, SoFi, Capital One)
- Automatically standardizes different CSV formats

### Rule-Based Cleaning

- Regex-based normalization of transaction descriptions
- Handles bank-specific noise and formatting inconsistencies

### Fuzzy Matching (RapidFuzz)

- Matches similar or misspelled merchant names
- Reduces duplicate merchant entries

### LLM-Based Merchant Extraction (Ollama)

- Uses a local large language model to extract merchant names when rules fail
- Enforces structured JSON outputs for reliability

### Merchant Caching System

- Stores extracted merchants in `merchant_cache.json`
- Prevents repeated LLM calls
- Improves speed and consistency over time

### Categorization Engine

- Assigns transactions to sub-categories and main categories
- Uses a predefined mapping system

### Noise Filtering

- Removes non-spending transactions such as:
  - Internal transfers  
  - Account payments  
  - Round-ups  

---

## Ethical Considerations

This project uses personal financial data and follows responsible data practices:

- All processing is done locally  
- No private financial data is shared  
- No APIs or scraping are used  
- No sensitive information (e.g., account numbers) is included in outputs  

If sharing this repository publicly, it is recommended to exclude raw financial data files.

---

## Project Structure
- .data/ # Folder containing bank CSV files

    - goldenwest/ # Bank-specific folders

    - chase/

    - ...

- cleaning_logic.py # Cleaning + categorization logic

- bank_statement_processor.py # Main pipeline script

- bank_statement.py # Python script for cleaning and categorizing

- merchant_cache.json # Cached merchant results (auto-generated)

- all_banks_final_categorized.csv # Final output dataset

- README.md

---

## Requirements
- Python 3.10+
- Libraries:
  - `pandas`
  - `numpy`
  - `rapidfuzz`
  - `ollama`
  - `json` (standard library)
  - `re` (standard library)

### Additional Setup

Install and run Ollama locally:

👉 [Ollama Link](https://ollama.com)

Then pull your model:
```bash
ollama pull gemma3:4b
```

---

## Usage

1. Place CSV files from your banks into the `.data` folder, separated by bank.
2. Adjust the `known_banks` and `account_types` lists in `bank_statement_processor.py` if needed.
3. Run the script:

```bash
python bank_statement_processor.py
```
4. Output will be saved as:

```
all_banks_final_categorized.csv
```

The script outputs all_banks_final_categorized.csv with the following columns:

- date
- description
- type
- amount
- main_category
- sub_category
- bank
- account

---

## Data Pipeline

The processing workflow follows a structured pipeline:

1. **Load Data**
   - Read CSV files from multiple banks
   - Normalize column names

2. **Standardize Data**
   - Convert dates into consistent datetime format  
   - Clean and normalize transaction amounts  

3. **Clean Descriptions**
   - Remove symbols, numbers, and bank-specific noise  
   - Normalize text for matching  

4. **Merchant Detection**
   - Regex-based matching (fast, high-confidence)
   - Fuzzy matching using RapidFuzz
   - LLM fallback (Ollama) for unknown merchants  

5. **Caching**
   - Store results in `merchant_cache.json`
   - Avoid repeated LLM calls  

6. **Noise Filtering**
   - Remove internal transfers and non-spending transactions  

7. **Categorization**
   - Assign sub-categories and main categories  

8. **Export**
   - Save final cleaned dataset  

---

## Output Dataset

The final dataset contains:

- **500+ transactions (varies depending on input data)**
- **9 features (columns):**

| Column           | Description |
|------------------|------------|
| date             | Transaction date |
| description      | Cleaned transaction description |
| merchant         | Extracted merchant name |
| type             | Debit or credit |
| amount           | Transaction value |
| main_category    | High-level category |
| sub_category     | Detailed category |
| bank             | Source bank |
| account          | Account type |

---

## Notes & Limitations

- LLM outputs may occasionally be inconsistent  
- New merchants must be processed once before being cached  
- Some transactions may fall into "Other" categories  
- Results depend on the quality and consistency of input data  
- Dataset reflects personal spending, which introduces bias  

---

## Future Improvements

- Add embedding-based merchant clustering  
- Improve categorization with machine learning models  
- Build a dashboard for visualization  
- Add user feedback loop for correcting classifications  

---