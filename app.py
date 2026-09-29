"""Guided Streamlit interface for standardizing bank statement CSVs."""

from __future__ import annotations

import csv
import hashlib
import io
from html import escape
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from cleaning_logic import add_categories, sub_category_map
from final_export import (
    CATEGORY_LABEL_TO_PAIR,
    CATEGORY_PAIR_OPTIONS,
    CATEGORY_PAIR_TO_LABEL,
    FINAL_EXPORT_COLUMNS,
    FINAL_MAIN_CATEGORY_OPTIONS,
    complete_missing_categories,
    format_final_export,
    get_final_export_row_issues,
    load_merchant_cache,
    merchant_needs_review,
    normalize_account_type,
    normalize_bank_name,
    normalize_export_filename,
    restored_rows_to_standardized,
    split_filtered_transactions,
    validate_final_export_rows,
)
from merchant_assistance import approve_merchant_match, suggest_merchant_with_ollama
from transaction_import import (
    ImportFormatError,
    STANDARD_COLUMNS,
    build_import_signature,
    load_transactions,
    remove_source_rows,
    replace_source_rows,
)


BANK_OPTIONS = [
    "Auto-detect from filename",
    "Capital One",
    "Goldenwest Credit Union",
    "SoFi",
    "Other / new bank",
]
ACCOUNT_OPTIONS = [
    "Select an account type",
    "Checking",
    "Savings",
    "Credit card",
    "Money market",
    "Gold account",
    "Other",
]
DELIMITER_OPTIONS = {
    "Auto-detect": None,
    "Comma (,)": ",",
    "Semicolon (;)": ";",
    "Tab": "\t",
    "Pipe (|)": "|",
}
NUMBER_FORMATS = {
    "1,234.56 (US/UK)": (".", ","),
    "1.234,56 (many European countries)": (",", "."),
    "1 234,56 (space thousands)": (",", " "),
    "1,234 (decimal comma, no thousands separator)": (",", None),
}
DROPPED_REVIEW_COLUMNS = FINAL_EXPORT_COLUMNS + [
    "removal_reason",
    "status",
    "source_file",
    "source_row",
    "restore",
]
EDITABLE_SOURCE_COLUMNS = ["_source_file", "_source_row"]
EDITABLE_COLUMNS = FINAL_EXPORT_COLUMNS + EDITABLE_SOURCE_COLUMNS
HEADER_SUGGESTIONS = {
    "date": ("date", "transaction date", "posting date", "effective date", "activity date"),
    "posted_date": ("posted date", "effective date", "date posted", "posting date"),
    "description": ("description", "transaction description", "details", "memo", "name"),
    "amount": ("amount", "transaction amount", "value"),
    "debit": ("debit", "withdrawal", "outflow"),
    "credit": ("credit", "deposit", "inflow"),
    "transaction_id": ("transaction id", "id", "reference number", "reference"),
    "bank_category": ("category", "transaction category", "type"),
    "status": ("status", "transaction status"),
    "currency": ("currency", "currency code"),
}


def normalize_name(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def detect_bank(filename: str) -> str:
    normalized = normalize_name(filename)
    for token, bank in (
        ("capitalone", "Capital One"),
        ("gwcu", "Goldenwest Credit Union"),
        ("goldenwest", "Goldenwest Credit Union"),
        ("sofi", "SoFi"),
    ):
        if token in normalized:
            return bank
    return "Other / new bank"


def detect_delimiter(content: bytes) -> str:
    sample = content[:8192].decode("utf-8-sig", errors="replace")
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def suggested_header(headers: list[str], field: str) -> str | None:
    normalized_headers = {normalize_name(header): header for header in headers}
    for suggestion in HEADER_SUGGESTIONS[field]:
        match = normalized_headers.get(normalize_name(suggestion))
        if match is not None:
            return match
    return None


def column_picker(
    label: str,
    headers: list[str],
    *,
    field: str,
    widget_key: str,
    optional: bool = False,
) -> str | None:
    suggested = suggested_header(headers, field)
    placeholder = "Not used" if optional else "Choose a column"
    options = [placeholder, *headers]
    index = headers.index(suggested) + 1 if suggested else 0
    selection = st.selectbox(label, options, index=index, key=widget_key)
    return None if selection == placeholder else selection


def uploaded_csv_preview(content: bytes, delimiter: str) -> pd.DataFrame:
    return pd.read_csv(
        io.BytesIO(content),
        sep=delimiter,
        dtype="string",
        keep_default_na=False,
        nrows=8,
        encoding="utf-8-sig",
    )


def make_dropped_review_rows(
    transactions: pd.DataFrame,
    reasons: list[str] | pd.Series,
) -> pd.DataFrame:
    if transactions.empty:
        return pd.DataFrame(columns=DROPPED_REVIEW_COLUMNS)

    source = transactions.copy().sort_values("date", kind="stable")
    reason_values = pd.Series(reasons, index=transactions.index).loc[source.index].tolist()
    source_files = source["source_file"].tolist() if "source_file" in source else [""] * len(source)
    source_rows = source["source_row"].tolist() if "source_row" in source else [pd.NA] * len(source)
    statuses = source["status"].tolist() if "status" in source else [pd.NA] * len(source)
    review = format_final_export(source, apply_exclusions=False)
    review["removal_reason"] = reason_values
    review["status"] = statuses
    review["source_file"] = source_files
    review["source_row"] = source_rows
    review["restore"] = False
    return review[DROPPED_REVIEW_COLUMNS]


st.set_page_config(page_title="Statement Import", page_icon="📄", layout="wide")
st.title("Bank Statement Import")
st.caption("Turn bank CSV exports into one consistent transaction table.")

uploaded_files = st.file_uploader(
    "Add bank statement CSV files",
    type=["csv"],
    accept_multiple_files=True,
    help="Files are processed in this app session and are not sent to an external service.",
)

if "standardized_transactions" not in st.session_state:
    st.session_state.standardized_transactions = pd.DataFrame(columns=STANDARD_COLUMNS)
if "file_import_signatures" not in st.session_state:
    st.session_state.file_import_signatures = {}
if "active_upload_names" not in st.session_state:
    st.session_state.active_upload_names = set()
if "dropped_transactions" not in st.session_state:
    st.session_state.dropped_transactions = pd.DataFrame(columns=DROPPED_REVIEW_COLUMNS)
if "dropped_editor_revision" not in st.session_state:
    st.session_state.dropped_editor_revision = 0
if "export_editor_revision" not in st.session_state:
    st.session_state.export_editor_revision = 0
if "editable_transactions" not in st.session_state:
    current_transactions = st.session_state.standardized_transactions
    initial_export = (
        format_final_export(current_transactions)
        if not current_transactions.empty
        else pd.DataFrame(columns=FINAL_EXPORT_COLUMNS)
    )
    initial_export["_source_file"] = ""
    initial_export["_source_row"] = pd.NA
    st.session_state.editable_transactions = initial_export[EDITABLE_COLUMNS]
elif not set(EDITABLE_SOURCE_COLUMNS).issubset(st.session_state.editable_transactions.columns):
    st.session_state.editable_transactions = st.session_state.editable_transactions.copy()
    st.session_state.editable_transactions["_source_file"] = ""
    st.session_state.editable_transactions["_source_row"] = pd.NA
    st.session_state.editable_transactions = st.session_state.editable_transactions[EDITABLE_COLUMNS]

active_upload_names = {uploaded.name for uploaded in (uploaded_files or [])}
removed_upload_names = st.session_state.active_upload_names - active_upload_names
if removed_upload_names:
    st.session_state.standardized_transactions = remove_source_rows(
        st.session_state.standardized_transactions,
        source_column="source_file",
        source_names=removed_upload_names,
    )
    st.session_state.editable_transactions = remove_source_rows(
        st.session_state.editable_transactions,
        source_column="_source_file",
        source_names=removed_upload_names,
    )
    st.session_state.dropped_transactions = remove_source_rows(
        st.session_state.dropped_transactions,
        source_column="source_file",
        source_names=removed_upload_names,
    )
    for removed_name in removed_upload_names:
        st.session_state.file_import_signatures.pop(removed_name, None)
    st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
    st.session_state.pop(f"dropped_editor_{st.session_state.dropped_editor_revision}", None)
    st.session_state.export_editor_revision += 1
    st.session_state.dropped_editor_revision += 1
st.session_state.active_upload_names = active_upload_names
if st.session_state.get("export_editor_schema_version") != 4:
    st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
    st.session_state.export_editor_revision += 1
    st.session_state.export_editor_schema_version = 4

with st.sidebar:
    st.header("Imported data")
    current_data = st.session_state.standardized_transactions
    st.metric("Imported rows", len(current_data))
    if not current_data.empty:
        st.metric("Banks", current_data["bank"].nunique())
    if st.button("Clear imported data", use_container_width=True):
        st.session_state.standardized_transactions = pd.DataFrame(columns=STANDARD_COLUMNS)
        st.session_state.editable_transactions = pd.DataFrame(columns=EDITABLE_COLUMNS)
        st.session_state.dropped_transactions = pd.DataFrame(columns=DROPPED_REVIEW_COLUMNS)
        st.session_state.file_import_signatures = {}
        st.session_state.active_upload_names = set()
        st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
        st.session_state.pop(f"dropped_editor_{st.session_state.dropped_editor_revision}", None)
        st.session_state.export_editor_revision += 1
        st.session_state.dropped_editor_revision += 1
        st.rerun()

if uploaded_files:
    st.subheader("1. Choose a statement")
    selected_file = st.selectbox(
        "Statement file",
        uploaded_files,
        format_func=lambda uploaded: uploaded.name,
        key="selected_upload",
    )
    content = selected_file.getvalue()
    uploaded_name_counts = pd.Series([uploaded.name for uploaded in uploaded_files]).value_counts()
    duplicate_name = uploaded_name_counts.get(selected_file.name, 0) > 1
    if duplicate_name:
        st.error("Two uploaded files have the same name. Remove or rename one so each statement can be tracked safely.")
    file_digest = hashlib.sha256(content).hexdigest()
    detected_delimiter = detect_delimiter(content)
    delimiter_names = list(DELIMITER_OPTIONS)
    detected_label = next(
        (label for label, value in DELIMITER_OPTIONS.items() if value == detected_delimiter),
        "Comma (,)",
    )
    delimiter_label = st.selectbox(
        "Values are separated by",
        delimiter_names,
        index=delimiter_names.index("Auto-detect"),
        key=f"delimiter_{file_digest[:12]}",
        help=f"The detected separator is {detected_label}.",
    )
    delimiter = DELIMITER_OPTIONS[delimiter_label] or detected_delimiter

    try:
        preview = uploaded_csv_preview(content, delimiter)
        headers = [str(header).strip() for header in preview.columns]
        preview.columns = headers
        if not headers:
            st.error("This file does not appear to have a CSV header row.")
            st.stop()
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as error:
        st.error(f"Could not read this CSV: {error}")
        st.stop()

    with st.expander("Preview the first rows", expanded=False):
        st.dataframe(preview, use_container_width=True, hide_index=True)

    st.subheader("2. Identify the account")
    bank_index = BANK_OPTIONS.index(detect_bank(selected_file.name))
    bank_choice = st.selectbox(
        "Bank or credit union",
        BANK_OPTIONS,
        index=bank_index,
        key=f"bank_{file_digest[:12]}",
    )
    detected_bank = detect_bank(selected_file.name)
    needs_custom_bank = bank_choice == "Other / new bank" or (
        bank_choice == "Auto-detect from filename" and detected_bank == "Other / new bank"
    )
    bank_name = (
        st.text_input("Enter the bank name", key=f"custom_bank_{file_digest[:12]}").strip()
        if needs_custom_bank
        else detected_bank
        if bank_choice == "Auto-detect from filename"
        else bank_choice
    )

    account_choice = st.selectbox(
        "Account type",
        ACCOUNT_OPTIONS,
        index=0,
        key=f"account_{file_digest[:12]}",
    )
    account_name = (
        st.text_input("Enter the account type", key=f"custom_account_{file_digest[:12]}").strip()
        if account_choice == "Other"
        else None
        if account_choice == "Select an account type"
        else normalize_account_type(account_choice)
    )

    st.subheader("3. Match the columns")
    st.caption("Choose which column in this statement matches each field. Suggested matches are preselected when possible.")
    key_prefix = file_digest[:12]
    date_column = column_picker("Transaction date", headers, field="date", widget_key=f"date_{key_prefix}")
    description_column = column_picker(
        "Description", headers, field="description", widget_key=f"description_{key_prefix}"
    )
    amount_mode = st.radio(
        "How are transaction amounts shown?",
        ["One signed amount column", "Separate debit and credit columns"],
        horizontal=True,
        key=f"amount_mode_{key_prefix}",
    )
    positive_amounts_are_debits = False
    if amount_mode == "One signed amount column":
        sign_convention = st.radio(
            "Amount sign convention",
            [
                "Negative = money out; positive = money in",
                "Positive = money out; negative = money in",
            ],
            key=f"amount_sign_{key_prefix}",
            horizontal=True,
        )
        positive_amounts_are_debits = sign_convention.startswith("Positive")
    else:
        st.caption("Debit values become negative expenses; credit values become positive deposits.")

    mapping: dict[str, str] = {}
    if date_column:
        mapping["date"] = date_column
    if description_column:
        mapping["description"] = description_column
    if amount_mode == "One signed amount column":
        amount_column = column_picker(
            "Amount", headers, field="amount", widget_key=f"amount_{key_prefix}"
        )
        if amount_column:
            mapping["amount"] = amount_column
    else:
        left, right = st.columns(2)
        with left:
            debit_column = column_picker(
                "Debit / money out", headers, field="debit", widget_key=f"debit_{key_prefix}"
            )
        with right:
            credit_column = column_picker(
                "Credit / money in", headers, field="credit", widget_key=f"credit_{key_prefix}"
            )
        if debit_column:
            mapping["debit"] = debit_column
        if credit_column:
            mapping["credit"] = credit_column

    with st.expander("Optional columns", expanded=False):
        optional_fields = (
            ("posted_date", "Posted date"),
            ("transaction_id", "Transaction ID"),
            ("bank_category", "Bank's category or transaction type"),
            ("status", "Transaction status"),
            ("currency", "Currency"),
        )
        for field, label in optional_fields:
            source_column = column_picker(
                label,
                headers,
                field=field,
                widget_key=f"{field}_{key_prefix}",
                optional=True,
            )
            if source_column:
                mapping[field] = source_column

    number_format_label = st.selectbox(
        "Number format",
        list(NUMBER_FORMATS),
        key=f"number_format_{key_prefix}",
        help="Choose the decimal and thousands separators used in the amount columns.",
    )
    decimal_separator, thousands_separator = NUMBER_FORMATS[number_format_label]

    current_signature = build_import_signature(
        file_digest,
        bank=bank_name,
        account=account_name or "",
        column_mapping=mapping,
        delimiter=delimiter,
        decimal=decimal_separator,
        thousands=thousands_separator,
        positive_amounts_are_debits=positive_amounts_are_debits,
    )
    previous_signature = st.session_state.file_import_signatures.get(selected_file.name)
    already_current = previous_signature == current_signature
    replacing_existing = previous_signature is not None and not already_current
    if already_current:
        st.info("This file is already imported with these settings.")
    elif replacing_existing:
        st.info("Settings changed. Submitting will replace this file's existing transactions.")

    if st.button(
        "Update this file's import" if replacing_existing else "Standardize and add statement",
        type="primary",
        disabled=already_current or duplicate_name,
        use_container_width=True,
    ):
        if not bank_name:
            st.error("Enter a bank name before importing this statement.")
        elif not account_name:
            st.error("Select or enter the account type before importing this statement.")
        elif not date_column or not description_column:
            st.error("Select both a transaction date column and a description column.")
        elif "amount" not in mapping and not {"debit", "credit"}.issubset(mapping):
            st.error("Select an amount column, or select both debit and credit columns.")
        else:
            suffix = Path(selected_file.name).suffix or ".csv"
            temp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
                    temp_file.write(content)
                    temp_path = Path(temp_file.name)
                standardized = load_transactions(
                    temp_path,
                    bank=bank_name,
                    account=account_name,
                    column_mapping=mapping,
                    decimal=decimal_separator,
                    thousands=thousands_separator,
                    delimiter=delimiter,
                    positive_amounts_are_debits=positive_amounts_are_debits,
                )
                standardized["source_file"] = selected_file.name
                declined = standardized.attrs.get("declined_transactions", pd.DataFrame(columns=STANDARD_COLUMNS))
                declined = declined.copy()
                if not declined.empty:
                    declined["source_file"] = selected_file.name
                exportable, filtered = split_filtered_transactions(standardized)
                new_export = format_final_export(exportable)
                new_export["_source_file"] = selected_file.name
                new_export["_source_row"] = pd.NA
                review_parts = []
                if not filtered.empty:
                    filter_reasons = filtered["removal_reason"].tolist()
                    review_parts.append(
                        make_dropped_review_rows(
                            filtered.drop(columns="removal_reason"), filter_reasons
                        )
                    )
                if not declined.empty:
                    review_parts.append(
                        make_dropped_review_rows(
                            declined,
                            ["Bank status is Declined."] * len(declined),
                        )
                    )
                declined_rows = standardized.attrs.get("declined_rows_dropped", 0)
                new_review = (
                    pd.concat(review_parts, ignore_index=True)
                    if review_parts
                    else pd.DataFrame(columns=DROPPED_REVIEW_COLUMNS)
                )
                st.session_state.standardized_transactions = replace_source_rows(
                    st.session_state.standardized_transactions,
                    standardized,
                    source_column="source_file",
                    source_name=selected_file.name,
                )
                st.session_state.editable_transactions = replace_source_rows(
                    st.session_state.editable_transactions,
                    new_export,
                    source_column="_source_file",
                    source_name=selected_file.name,
                )
                st.session_state.dropped_transactions = replace_source_rows(
                    st.session_state.dropped_transactions,
                    new_review,
                    source_column="source_file",
                    source_name=selected_file.name,
                )
                if review_parts:
                    st.session_state.pop(
                        f"dropped_editor_{st.session_state.dropped_editor_revision}", None
                    )
                    st.session_state.dropped_editor_revision += 1
                st.session_state.file_import_signatures[selected_file.name] = current_signature
                st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
                st.session_state.export_editor_revision += 1
                excluded_count = declined_rows + len(filtered)
                declined_message = (
                    f" Excluded {excluded_count:,} declined, transfer, or payment transaction(s)."
                    if excluded_count
                    else ""
                )
                st.success(
                    f"Processed {len(standardized):,} transactions from {selected_file.name}."
                    f"{declined_message}"
                )
            except (ImportFormatError, OSError, ValueError) as error:
                st.error(f"This statement could not be standardized: {error}")
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

if (
    not st.session_state.standardized_transactions.empty
    or not st.session_state.dropped_transactions.empty
):
    transactions = st.session_state.standardized_transactions
    st.subheader("4. Review, edit and download")
    st.caption("Edit descriptions, merchants, dates and amounts directly in the table. Type, account, bank and category are selection-only.")
    editable = complete_missing_categories(st.session_state.editable_transactions)
    st.session_state.editable_transactions = editable
    editor_data = editable.copy()
    editor_data.insert(0, "row_number", range(1, len(editor_data) + 1))
    editor_data["sub_category"] = [
        CATEGORY_PAIR_TO_LABEL.get((str(main), str(sub)), "")
        for main, sub in zip(editable["main_category"], editable["sub_category"])
    ]
    account_options = sorted(
        set(editable["account"].dropna().astype(str))
        | {"checking", "savings", "credit card", "money market", "gold account"}
    )
    bank_options = sorted(
        set(editable["bank"].dropna().astype(str))
        | {normalize_bank_name(bank) for bank in transactions["bank"].dropna().astype(str)}
    )
    edited_editor_data = st.data_editor(
        editor_data,
        key=f"export_editor_{st.session_state.export_editor_revision}",
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "row_number": st.column_config.NumberColumn("Row", disabled=True, width="small"),
            "date": st.column_config.TextColumn("Date (MM/DD/YYYY)", required=True),
            "description": st.column_config.TextColumn("Description", required=True),
            "merchant": st.column_config.TextColumn("Merchant", required=True),
            "type": st.column_config.SelectboxColumn(
                "Type", options=["debit", "credit"], required=True
            ),
            "amount": st.column_config.NumberColumn("Amount", format="$%.2f", required=True),
            "main_category": st.column_config.SelectboxColumn(
                "Main category", options=FINAL_MAIN_CATEGORY_OPTIONS, required=True
            ),
            "sub_category": st.column_config.SelectboxColumn(
                "Sub-category (Main :: Sub)", options=CATEGORY_PAIR_OPTIONS, required=True
            ),
            "bank": st.column_config.SelectboxColumn("Bank", options=bank_options, required=True),
            "account": st.column_config.SelectboxColumn("Account", options=account_options, required=True),
            "_source_file": None,
            "_source_row": None,
        },
    )
    editor_row_numbers = edited_editor_data["row_number"].reset_index(drop=True)
    edited_source_metadata = edited_editor_data[EDITABLE_SOURCE_COLUMNS].reset_index(drop=True)
    edited_transactions = edited_editor_data.drop(
        columns=["row_number", *EDITABLE_SOURCE_COLUMNS]
    ).copy()
    category_pairs = edited_transactions["sub_category"].map(CATEGORY_LABEL_TO_PAIR)
    pair_mismatch = category_pairs.map(
        lambda pair: isinstance(pair, tuple)
    ) & category_pairs.map(
        lambda pair: pair[0] if isinstance(pair, tuple) else None
    ).ne(edited_transactions["main_category"])
    edited_transactions["sub_category"] = category_pairs.map(
        lambda pair: pair[1] if isinstance(pair, tuple) else pd.NA
    )
    edited_transactions = edited_transactions[FINAL_EXPORT_COLUMNS]
    edited_transactions = edited_transactions.reset_index(drop=True)
    st.session_state.editable_transactions = pd.concat(
        [edited_transactions, edited_source_metadata], axis=1
    )[EDITABLE_COLUMNS]

    with st.expander("Optional AI merchant help", expanded=False):
        st.caption("Ollama runs locally. It only receives a description when you request a suggestion; nothing is cached until you approve it.")
        merchant_cache = load_merchant_cache()
        candidate_rows = [
            index
            for index, row in st.session_state.editable_transactions.iterrows()
            if merchant_needs_review(
                str(row["description"]), str(row["merchant"]), merchant_cache
            )
        ]
        if not candidate_rows:
            st.info("No unresolved merchant matches need suggestions.")
        else:
            def suggestion_label(index: int) -> str:
                row = st.session_state.editable_transactions.loc[index]
                return f"{row['date']} | {row['merchant']} | {row['description']}"

            suggestion_row = st.selectbox(
                "Unmatched transaction",
                candidate_rows,
                format_func=suggestion_label,
                key=f"ai_candidate_{st.session_state.export_editor_revision}",
            )
            suggestion_transaction = st.session_state.editable_transactions.loc[suggestion_row]
            suggestion_description = str(suggestion_transaction["description"])
            suggestion_fingerprint = hashlib.sha256(
                f"{suggestion_row}|{suggestion_description}".encode("utf-8")
            ).hexdigest()[:12]
            suggestion_key = f"ai_merchant_suggestion_{suggestion_fingerprint}"
            ai_model = st.text_input(
                "Local Ollama model",
                value="gemma3:4b",
                key=f"ai_model_{st.session_state.export_editor_revision}",
            ).strip()
            if st.button("Suggest merchant with Ollama", key=f"suggest_{suggestion_fingerprint}"):
                if not ai_model:
                    st.error("Enter a local Ollama model name.")
                else:
                    try:
                        with st.spinner("Asking local Ollama for a merchant suggestion..."):
                            suggestion = suggest_merchant_with_ollama(
                                suggestion_description, model=ai_model
                            )
                        st.session_state[suggestion_key] = {
                            "description": suggestion_description,
                            "merchant": suggestion,
                        }
                    except Exception as error:
                        st.error(f"Ollama suggestion failed: {error}")

            pending_suggestion = st.session_state.get(suggestion_key)
            if pending_suggestion and pending_suggestion.get("description") == suggestion_description:
                st.info(f"Suggested merchant: {pending_suggestion['merchant']}")
                approve_col, dismiss_col = st.columns(2)
                with approve_col:
                    approve_clicked = st.button(
                        "Approve and add to cache", type="primary", key=f"approve_{suggestion_fingerprint}"
                    )
                with dismiss_col:
                    dismiss_clicked = st.button("Dismiss", key=f"dismiss_{suggestion_fingerprint}")

                if approve_clicked:
                    try:
                        approved_merchant = pending_suggestion["merchant"]
                        approve_merchant_match(suggestion_description, approved_merchant)
                        updated = st.session_state.editable_transactions.copy()
                        updated.at[suggestion_row, "merchant"] = approved_merchant
                        if approved_merchant in sub_category_map:
                            categories = add_categories(
                                pd.DataFrame(
                                    [{
                                        "merchant": approved_merchant,
                                        "amount": updated.at[suggestion_row, "amount"],
                                        "description": updated.at[suggestion_row, "description"],
                                    }]
                                )
                            ).iloc[0]
                            updated.at[suggestion_row, "main_category"] = categories["main_category"]
                            updated.at[suggestion_row, "sub_category"] = categories["sub_category"]
                        st.session_state.editable_transactions = updated
                        st.session_state.pop(suggestion_key, None)
                        st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
                        st.session_state.export_editor_revision += 1
                        st.success("Approved merchant saved to merchant_cache.json.")
                        st.rerun()
                    except (OSError, ValueError) as error:
                        st.error(f"Could not save the approved merchant: {error}")
                elif dismiss_clicked:
                    st.session_state.pop(suggestion_key, None)
                    st.rerun()

    download_data, validation_error = validate_final_export_rows(edited_transactions)
    if pair_mismatch.any():
        validation_error = "Choose a sub-category whose displayed main category matches the Main category column."
    row_issues = get_final_export_row_issues(edited_transactions)
    for row_index in pair_mismatch[pair_mismatch].index:
        row_issues.setdefault(int(row_index), []).append(
            "Sub-category does not match the selected Main category."
        )

    if row_issues:
        issue_links = []
        for row_index in row_issues:
            row_number = editor_row_numbers.iloc[row_index]
            row_number = int(row_number) if pd.notna(row_number) else row_index + 1
            issue_links.append(f"[Row {row_number}](#transaction-issue-{row_index})")
        st.error("Fix invalid rows before downloading: " + " | ".join(issue_links))
        st.markdown("#### Rows needing attention")
        for row_index, issues in row_issues.items():
            row = edited_transactions.iloc[row_index]
            row_number = editor_row_numbers.iloc[row_index]
            row_number = int(row_number) if pd.notna(row_number) else row_index + 1
            details = [
                f"<strong>Row {row_number}</strong>",
                f"Date: {escape(str(row['date']))}",
                f"Description: {escape(str(row['description']))}",
                f"Merchant: {escape(str(row['merchant']))}",
                f"Issue: {escape(' '.join(issues))}",
            ]
            st.markdown(
                f"<div id=\"transaction-issue-{row_index}\" "
                "style=\"background-color:#FDECEC;color:#7F1D1D;"
                "border-left:4px solid #D93025;padding:10px 12px;margin:6px 0;\">"
                + "<br>".join(details)
                + "</div>",
                unsafe_allow_html=True,
            )
    amounts = pd.to_numeric(download_data["amount"], errors="coerce")
    metric_columns = st.columns(3)
    metric_columns[0].metric("Transactions", f"{len(download_data):,}")
    metric_columns[1].metric("Money in", f"${amounts[amounts > 0].sum():,.2f}")
    metric_columns[2].metric("Money out", f"${abs(amounts[amounts < 0].sum()):,.2f}")
    export_filename = normalize_export_filename(
        st.text_input(
            "Export file name",
            value="all_banks_final_categorized.csv",
            help="The .csv extension is added automatically if needed.",
        )
    )
    if validation_error and not row_issues:
        if "Date must use MM/DD/YYYY" in validation_error:
            st.error(validation_error)
        else:
            st.warning(validation_error)
    st.download_button(
        "Download categorized transactions",
        data=download_data.to_csv(index=False).encode("utf-8-sig"),
        file_name=export_filename,
        mime="text/csv",
        type="primary",
        disabled=validation_error is not None,
    )
    dropped_transactions = st.session_state.dropped_transactions
    if not dropped_transactions.empty:
        with st.expander(
            f"Review {len(dropped_transactions):,} excluded transactions", expanded=False
        ):
            st.caption("Review the reason each row was excluded. Select Restore and add selected rows back to the export.")
            review_editor = st.data_editor(
                dropped_transactions,
                key=f"dropped_editor_{st.session_state.dropped_editor_revision}",
                num_rows="fixed",
                use_container_width=True,
                hide_index=True,
                column_config={
                    "date": st.column_config.TextColumn("Date (MM/DD/YYYY)"),
                    "description": st.column_config.TextColumn("Description"),
                    "merchant": st.column_config.TextColumn("Merchant"),
                    "amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
                    "removal_reason": st.column_config.TextColumn("Why it was removed", disabled=True),
                    "status": st.column_config.TextColumn("Status", disabled=True),
                    "source_file": st.column_config.TextColumn("Source file", disabled=True),
                    "source_row": st.column_config.NumberColumn("Source row", disabled=True),
                    "restore": st.column_config.CheckboxColumn("Restore", default=False),
                },
            )
            if st.button("Add selected transactions back", type="primary"):
                selected_indices = review_editor.index[review_editor["restore"].fillna(False).astype(bool)]
                if len(selected_indices) == 0:
                    st.info("Select at least one transaction to restore.")
                else:
                    selected_rows = review_editor.loc[selected_indices]
                    restored, restore_error = restored_rows_to_standardized(
                        selected_rows[FINAL_EXPORT_COLUMNS],
                        statuses=selected_rows["status"],
                        source_files=selected_rows["source_file"],
                        source_rows=selected_rows["source_row"],
                    )
                    if restore_error:
                        st.error(f"Cannot restore selected transactions yet: {restore_error}")
                    else:
                        st.session_state.standardized_transactions = pd.concat(
                            [st.session_state.standardized_transactions, restored], ignore_index=True
                        )
                        st.session_state.editable_transactions = pd.concat(
                            [
                                st.session_state.editable_transactions,
                                pd.concat(
                                    [
                                        selected_rows[FINAL_EXPORT_COLUMNS].reset_index(drop=True),
                                        selected_rows[["source_file", "source_row"]]
                                        .rename(columns={"source_file": "_source_file", "source_row": "_source_row"})
                                        .reset_index(drop=True),
                                    ],
                                    axis=1,
                                ),
                            ],
                            ignore_index=True,
                        )
                        st.session_state.dropped_transactions = review_editor.drop(
                            index=selected_indices
                        ).reset_index(drop=True)
                        st.session_state.pop(
                            f"dropped_editor_{st.session_state.dropped_editor_revision}", None
                        )
                        st.session_state.dropped_editor_revision += 1
                        st.session_state.pop(
                            f"export_editor_{st.session_state.export_editor_revision}", None
                        )
                        st.session_state.export_editor_revision += 1
                        st.success(f"Restored {len(restored):,} transaction(s).")
                        st.rerun()
else:
    st.info("Add a statement above to see standardized transactions here.")