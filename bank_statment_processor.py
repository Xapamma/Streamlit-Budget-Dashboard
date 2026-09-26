import pandas as pd
import numpy as np
import glob
import os
import re
from rapidfuzz import process, fuzz
import json
from cleaning_logic import add_categories, description_merchants, merchants_list,clean_description_for_matching, match_description_map, smart_title

# Get Ollama Working
import ollama

def call_ollama(prompt, model="gemma3:4b"):
    response = ollama.chat(
        model=model,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )

    return response["message"]["content"].strip()

# Add Rapidfuzz logic
def fuzzy_match(desc, choices):
    if not choices:  # handle empty input
        return None, 0

    # make sure choices is a list
    choices = list(choices)

    result = process.extractOne(
        desc,
        choices,
        scorer=fuzz.token_set_ratio
    )

    if result is None:  # handle RapidFuzz returning None
        return None, 0

    match, score, _ = result

    # Reject weak matches
    if score < 90:
        return None, score

    # Reject suspicious short matches
    if len(match.split()) == 1 and score < 97:
        return None, score

    # Require word overlap
    if not any(word in desc for word in match.lower().split()):
        return None, score
    
    # Require **at least 2 token overlaps**
    desc_words = set(desc.lower().split())
    match_words = set(match.lower().split())
    if len(desc_words & match_words) < 2:
        return None, score

    return match, score

# parse robustly
def parse_llm_json(raw_text):
    raw_text = raw_text.replace('```json', '').replace('```', '').strip()
    try:
        data = json.loads(raw_text)
        return data
    except json.JSONDecodeError:
        # fallback: extract manually
        match = re.search(r'"name"\s*:\s*"([^"]+)"', raw_text)
        if match:
            return {"name": match.group(1)}
        return {"name": "unknown"}

# Add the merchant column stuff

# Add merchant cache to automatically update the llm matches
merchant_cache_file = "merchant_cache.json"

if os.path.exists(merchant_cache_file):
    with open(merchant_cache_file, "r") as f:
        merchant_cache = json.load(f)
else:
    merchant_cache = {}

def save_cache():
    with open(merchant_cache_file, "w") as f:
        json.dump(merchant_cache, f, indent=2)

def get_merchant(description):
     # --- Step 1: Clean description for matching ---
    raw_desc = clean_description_for_matching(description)
    
    if raw_desc is None:
        return None

    # --- Step 2: Check regex map FIRST (fastest) ---
    match = match_description_map(raw_desc)
    if match:
        return match

    # --- Step 3: Check cache ---
    if raw_desc in merchant_cache:
        return merchant_cache[raw_desc]
    
    # --- Step 4: RapidFuzz Logic ---
    match, score = fuzzy_match(raw_desc, merchants_list)
    cache_match_key, cache_score = fuzzy_match(raw_desc, merchant_cache.keys())
    desc_match, desc_score = fuzzy_match(raw_desc, description_merchants)

    if score > 96:
        return match
    if desc_score >= 96:
        return desc_match
    if cache_score > 96:
        return cache_match_key
    
    # --- Step 5: LLM fallback ---
    prompt = f"""
You are a strict data extraction function.

Your ONLY job is to extract a merchant name from a bank transaction description.

OUTPUT RULES (MANDATORY):
- Output ONLY valid JSON
- Do NOT include markdown (no ``` or ```json)
- Do NOT include explanations
- Do NOT include any text before or after JSON
- Output must be EXACTLY one JSON object
- Use this exact format: {{"name": "merchant"}}

CRITICAL MATCHING RULE:
- You MUST prefer matching one of the KNOWN MERCHANTS EXACTLY when possible
- If the transaction clearly matches one of them, return it EXACTLY as written
- Do NOT simplify, shorten, or reformat known merchants

KNOWN MERCHANTS:
{merchants_list}
{description_merchants}

EXTRACTION RULES:
- Extract the real merchant (store, company, or service)
- Remove locations, states, phone numbers, and IDs
- Ignore bank names (e.g., credit union, bank)
- If it does NOT match any known merchant, return the cleaned description itself as the value for "name"
- Do NOT invent or shorten merchant names

OUTPUT RULES (MANDATORY):
- Output ONLY valid JSON
- Do NOT include markdown, code blocks, or ```json
- Output must be EXACTLY one JSON object
- Use this exact format: {{"name": "merchant"}}

EXAMPLES:

Input: TST*AMAZON MKTPLACE PMTS SEATTLE WA
Output: {{"name": "Amazon"}}

Input: WALMART SUPERCENTER #1234 UT
Output: {{"name": "Walmart"}}

Input: SQ *JOES PIZZA 435-555-1234 UT
Output: {{"name": "Joes Pizza"}}

Input: ING RADEK LUKAPOD SPEJ UT
Output: {{"name": "ING Radek Lukapod Spej"}}

NOW EXTRACT:

Input: {description}
"""
    
    raw = call_ollama(prompt)


    try:
        data = parse_llm_json(raw)
        merchant = smart_title(data.get("name", "unknown"))

        # Save to cache
        merchant_cache[raw_desc] = merchant
        save_cache()

        return merchant
    
    except Exception:
        # Instead of printing raw, just return raw as fallback
        merchant = smart_title(raw.strip())
        merchant_cache[raw_desc] = merchant
        save_cache()
        return merchant


def add_merchants(df):
    df["merchant"] = df["description"].apply(get_merchant)
    save_cache()
    return df


# Root folder containing bank folders
folder = ".data"

# Known banks and account types
known_banks = ["goldenwest", "sofi", "capital one"]
account_map = {
    "cc": "credit card",
    "credit card": "credit card",
    "checking": "checking",
    "savings": "savings",
    "imm": "money market",
    "gold": "gold account"
}

# List to hold all banks
all_banks = []

# Scan folders in .data
bank_folders = [f.path for f in os.scandir(folder) if f.is_dir()]

for bank_folder in bank_folders:
    raw_bank_name = os.path.basename(bank_folder)

    # Fuzzy match bank name
    match_result = process.extractOne(raw_bank_name, known_banks, scorer=fuzz.ratio)
    bank_name = match_result[0]
    bank_score = match_result[1]
    print(f"Processing bank folder: {raw_bank_name} → matched to {bank_name} (score {bank_score})")

    # Find CSV files in the bank folder
    files = glob.glob(os.path.join(bank_folder, "*.csv"))
    bank_dfs = []

    for file in files:
        df = pd.read_csv(file)
        df.columns = df.columns.str.strip().str.lower()

        # Bank-specific column cleaning
        if bank_name == "goldenwest":
            df = df.rename(columns={
                "posting date": "date",
                "description": "description",
                "amount": "amount",
                "type" : "category type"
            })

        elif bank_name == "capital one":
            df = df.rename(columns={
                "transaction date": "date",        
                "description": "description",           
            })
            # Make into one column
            df = pd.melt(
                df,
                id_vars=[c for c in df.columns if c not in ["debit", "credit"]],
                value_vars=["debit", "credit"],
                var_name="type",
                value_name="amount"
            ).dropna(subset=["amount"])

        elif bank_name == "sofi":
            # Typical SoFi CSV export columns
            df = df.rename(columns={
                "date" : "date",
                "description": "description",     
                "amount": "amount"            
            })

        # Convert date column
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

        # Clean amount column
        df["amount"] = (
            df["amount"]
            .astype(str)
            .str.replace(",", "")
            .str.replace("(", "-")
            .str.replace(")", "")
            .astype(float)
        )

        # Fuzzy match account type from filename
        filename_lower = os.path.basename(file).lower()
        match_result = process.extractOne(filename_lower, account_map.keys(), scorer=fuzz.partial_ratio)
        
        # Get a clean name from the map
        matched_key = match_result[0]
        account_name = account_map[matched_key]

        df["bank"] = bank_name
        df["account"] = account_name

        # Add a "type" column based on the amount
        # If credit card, make debits negative
        if account_name == "credit card":
            df.loc[df["type"] == "debit", "amount"] = -df["amount"].abs()  # Make all debit amounts negative for charges
            df.loc[df["type"] == "credit", "amount"] = df["amount"].abs()  # Make all credit amounts positive for payments or reimbursements
        else:
            df["type"] = np.where(df["amount"] > 0, "credit", "debit")

        bank_dfs.append(df)

    # Combine all accounts for this bank
    bank_combined = pd.concat(bank_dfs, ignore_index=True)
    all_banks.append(bank_combined)

# Combine all banks
combined = pd.concat(all_banks, ignore_index=True)
combined = combined.sort_values("date")

# Placeholder for now
combined["sub_category"] = np.nan
combined["main_category"] = np.nan

# Save the origional descriptions
combined['raw_description'] = combined['description']

# --- 0️⃣ Define noise patterns ---
noise_patterns = [
    "transfer to sofi", "to checking", "angel funding", "roundup",
    "home banking transfer", "pymt", "payment to", "north capital", "internal transfer"
]

# --- 1️⃣ Lowercase descriptions for matching ---
desc_lower = combined["description"].str.lower()

# --- 2️⃣ Create a mask to keep only rows that do NOT match any noise ---
mask = ~desc_lower.apply(lambda x: any(noise in x for noise in noise_patterns))

# --- 3️⃣ Apply the mask BEFORE calling add_merchants ---
combined = combined[mask].copy()

# Add a merchant link
print("Extracting merchants...")
combined = add_merchants(combined)

# Keep only necessary columns
columns_to_keep = ["date", "description", "merchant", "type", "amount", "main_category", "sub_category", "bank", "account"]
combined = combined[columns_to_keep]

# Final Step: Apply the robust categorization logic
print("Applying categorization logic...")
final_df = add_categories(combined)

# Save
final_df.to_csv("all_banks_final_categorized.csv", index=False)

print("✅ Done! Final cleaned and categorized CSV saved as 'all_banks_final_categorized.csv'.")
