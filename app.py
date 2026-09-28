"""Guided Streamlit interface for standardizing bank statement CSVs."""

from __future__ import annotations

import csv
import hashlib
import io
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from final_export import format_final_export, normalize_account_type
from transaction_import import ImportFormatError, STANDARD_COLUMNS, load_transactions


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
HEADER_SUGGESTIONS = {
    "date": ("date", "transaction date", "posting date", "effective date", "activity date"),
    "posted_date": ("posted date", "effective date", "date posted", "posting date"),
    "description": ("description", "transaction description", "details", "memo", "name"),
    "amount": ("amount", "transaction amount", "value"),
    "debit": ("debit", "withdrawal", "outflow"),
    "credit": ("credit", "deposit", "inflow"),
    "transaction_id": ("transaction id", "id", "reference number", "reference"),
    "bank_category": ("category", "transaction category", "type"),
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
if "imported_file_ids" not in st.session_state:
    st.session_state.imported_file_ids = set()

with st.sidebar:
    st.header("Imported data")
    current_data = st.session_state.standardized_transactions
    st.metric("Transactions", len(current_data))
    if not current_data.empty:
        st.metric("Banks", current_data["bank"].nunique())
    if st.button("Clear imported data", use_container_width=True):
        st.session_state.standardized_transactions = pd.DataFrame(columns=STANDARD_COLUMNS)
        st.session_state.imported_file_ids = set()
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
    if amount_mode == "One signed amount column":
        st.caption("For this option, expenses should be negative and deposits or refunds positive.")
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

    already_imported = file_digest in st.session_state.imported_file_ids
    if already_imported:
        st.info("This exact file has already been added to the current session.")

    if st.button(
        "Standardize and add statement",
        type="primary",
        disabled=already_imported,
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
                )
                st.session_state.standardized_transactions = pd.concat(
                    [st.session_state.standardized_transactions, standardized],
                    ignore_index=True,
                )
                st.session_state.imported_file_ids.add(file_digest)
                st.success(f"Added {len(standardized):,} transactions from {selected_file.name}.")
            except (ImportFormatError, OSError, ValueError) as error:
                st.error(f"This statement could not be standardized: {error}")
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

if not st.session_state.standardized_transactions.empty:
    transactions = st.session_state.standardized_transactions
    final_export = format_final_export(transactions)
    st.subheader("4. Review and download")
    metric_columns = st.columns(3)
    metric_columns[0].metric("Transactions", f"{len(transactions):,}")
    metric_columns[1].metric("Money in", f"${transactions.loc[transactions.amount > 0, 'amount'].sum():,.2f}")
    metric_columns[2].metric("Money out", f"${abs(transactions.loc[transactions.amount < 0, 'amount'].sum()):,.2f}")
    st.dataframe(final_export, use_container_width=True, hide_index=True)
    st.download_button(
        "Download categorized transactions",
        data=final_export.to_csv(index=False).encode("utf-8-sig"),
        file_name="all_banks_final_categorized.csv",
        mime="text/csv",
        type="primary",
    )
else:
    st.info("Add a statement above to see standardized transactions here.")