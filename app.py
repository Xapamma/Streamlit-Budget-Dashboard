"""Guided Streamlit interface for standardizing bank statement CSVs."""

from __future__ import annotations

import csv
import hashlib
import importlib
import io
from html import escape
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from bank_profiles import load_bank_profiles, save_bank_profile
from dashboard_data import sort_rows_by_date
from cleaning_logic import add_categories, clean_description_for_matching

import final_export

if not hasattr(final_export, "approve_transaction_category"):
    importlib.reload(final_export)

from final_export import (
    CATEGORY_SUBCATEGORIES,
    CATEGORY_LABEL_TO_PAIR,
    CATEGORY_PAIR_OPTIONS,
    CATEGORY_PAIR_TO_LABEL,
    FINAL_EXPORT_COLUMNS,
    FINAL_MAIN_CATEGORY_OPTIONS,
    approve_transaction_category,
    complete_missing_categories,
    format_final_export,
    get_final_export_row_issues,
    load_category_cache,
    load_merchant_cache,
    merchant_needs_review,
    normalize_account_type,
    normalize_bank_name,
    normalize_export_filename,
    make_transfer_review_rows,
    restored_rows_to_standardized,
    split_filtered_transactions,
    validate_final_export_rows,
)
import merchant_assistance

if (
    not all(
        hasattr(merchant_assistance, helper_name)
        for helper_name in (
            "merge_merchant_cache_edits",
            "suggest_merchant_and_category_with_ollama",
            "ask_ollama_app_help",
        )
    )
    or getattr(merchant_assistance, "MAX_OLLAMA_SUGGESTION_ATTEMPTS", 0) < 5
):
    merchant_assistance = importlib.reload(merchant_assistance)

from merchant_assistance import (
    ask_ollama_app_help,
    approve_merchant_match,
    merge_merchant_cache_edits,
    save_merchant_cache,
    suggest_merchant_and_category_with_ollama,
)
from fuzzy_search import fuzzy_match_indices
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
EDITABLE_SOURCE_COLUMNS = ["_source_file", "_source_row", "_category_reviewed"]
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
    default_value: str | None = None,
) -> str | None:
    suggested = suggested_header(headers, field)
    placeholder = "Not used" if optional else "Choose a column"
    options = [placeholder, *headers]
    selected_default = default_value if default_value in headers else suggested
    index = headers.index(selected_default) + 1 if selected_default else 0
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


st.title("Bank Statement Import")
st.caption("Turn bank CSV exports into one consistent transaction table.")
try:
    bank_profiles = load_bank_profiles()
except (OSError, ValueError) as error:
    bank_profiles = {}
    st.warning(f"Could not load saved bank formats: {error}")

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
if "dropped_transactions" not in st.session_state:
    st.session_state.dropped_transactions = pd.DataFrame(columns=DROPPED_REVIEW_COLUMNS)
if "dropped_editor_revision" not in st.session_state:
    st.session_state.dropped_editor_revision = 0
if "export_editor_revision" not in st.session_state:
    st.session_state.export_editor_revision = 0
if "merchant_cache_editor_revision" not in st.session_state:
    st.session_state.merchant_cache_editor_revision = 0
if "ai_review_expanded" not in st.session_state:
    st.session_state.ai_review_expanded = False
if "app_help_chat_messages" not in st.session_state:
    st.session_state.app_help_chat_messages = []
if "app_help_chat_expanded" not in st.session_state:
    st.session_state.app_help_chat_expanded = False
if "editable_transactions" not in st.session_state:
    current_transactions = st.session_state.standardized_transactions
    initial_export = (
        format_final_export(current_transactions)
        if not current_transactions.empty
        else pd.DataFrame(columns=FINAL_EXPORT_COLUMNS)
    )
    initial_export["_source_file"] = ""
    initial_export["_source_row"] = pd.NA
    approved_category_cache = load_category_cache()
    initial_export["_category_reviewed"] = [
        main_category != "General Spending"
        or clean_description_for_matching(str(description)) in approved_category_cache
        for main_category, description in zip(
            initial_export["main_category"], initial_export["description"]
        )
    ]
    st.session_state.editable_transactions = initial_export[EDITABLE_COLUMNS]
elif not set(EDITABLE_SOURCE_COLUMNS).issubset(st.session_state.editable_transactions.columns):
    st.session_state.editable_transactions = st.session_state.editable_transactions.copy()
    if "_source_file" not in st.session_state.editable_transactions.columns:
        st.session_state.editable_transactions["_source_file"] = ""
    if "_source_row" not in st.session_state.editable_transactions.columns:
        st.session_state.editable_transactions["_source_row"] = pd.NA
    if "_category_reviewed" not in st.session_state.editable_transactions.columns:
        approved_category_cache = load_category_cache()
        st.session_state.editable_transactions["_category_reviewed"] = (
            st.session_state.editable_transactions.apply(
                lambda row: row["main_category"] != "General Spending"
                or clean_description_for_matching(str(row["description"]))
                in approved_category_cache,
                axis=1,
            )
        )
    st.session_state.editable_transactions = st.session_state.editable_transactions[EDITABLE_COLUMNS]

st.session_state.editable_transactions["_category_reviewed"] = st.session_state.editable_transactions[
    "_category_reviewed"
].map(
    lambda value: False
    if pd.isna(value)
    else value.strip().casefold() in {"true", "1", "yes"}
    if isinstance(value, str)
    else bool(value)
).astype(bool)

if st.session_state.get("export_editor_schema_version") != 5:
    st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
    st.session_state.export_editor_revision += 1
    st.session_state.export_editor_schema_version = 5

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
        st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
        st.session_state.pop(f"dropped_editor_{st.session_state.dropped_editor_revision}", None)
        st.session_state.export_editor_revision += 1
        st.session_state.dropped_editor_revision += 1
        st.rerun()

    source_names = set()
    for frame, column in (
        (current_data, "source_file"),
        (st.session_state.editable_transactions, "_source_file"),
        (st.session_state.dropped_transactions, "source_file"),
    ):
        if column in frame.columns:
            source_names.update(frame[column].dropna().astype(str).loc[lambda values: values != ""])
    if source_names:
        with st.expander("Remove one statement's data", expanded=False):
            statement_to_remove = st.selectbox(
                "Statement",
                sorted(source_names),
                key="statement_to_remove",
            )
            if st.button("Remove selected statement data"):
                st.session_state.standardized_transactions = remove_source_rows(
                    st.session_state.standardized_transactions,
                    source_column="source_file",
                    source_names={statement_to_remove},
                )
                st.session_state.editable_transactions = remove_source_rows(
                    st.session_state.editable_transactions,
                    source_column="_source_file",
                    source_names={statement_to_remove},
                )
                st.session_state.dropped_transactions = remove_source_rows(
                    st.session_state.dropped_transactions,
                    source_column="source_file",
                    source_names={statement_to_remove},
                )
                st.session_state.file_import_signatures.pop(statement_to_remove, None)
                st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
                st.session_state.pop(f"dropped_editor_{st.session_state.dropped_editor_revision}", None)
                st.session_state.export_editor_revision += 1
                st.session_state.dropped_editor_revision += 1
                st.session_state.pop("statement_to_remove", None)
                st.rerun()

    with st.expander(
        "Ask the app (local Ollama)",
        expanded=st.session_state.app_help_chat_expanded,
    ):
        st.caption(
            "Ask how to import, review, categorize, or export. This chat only sends your "
            "question and recent chat messages to local Ollama. Do not include financial details."
        )
        with st.expander("Ollama setup", expanded=False):
            st.markdown(
                "**1. Install Ollama for Windows.** Download and run the installer. Ollama "
                "then runs in the background. After installation, close and reopen your terminal "
                "so the `ollama` command is available."
            )
            st.link_button("Download Ollama for Windows", "https://ollama.com/download/windows")
            st.link_button("Ollama Windows installation guide", "https://docs.ollama.com/windows")
            st.markdown(
                "**2. Open a terminal in this project.** In VS Code, choose **Terminal → New Terminal**. "
                "PowerShell, Command Prompt, and Git Bash all work. Make sure the terminal is in the "
                "project folder before running these commands:"
            )
            st.code(
                "uv sync --extra ai\n"
                "ollama --version\n"
                "ollama list",
                language="bash",
            )
            st.markdown(
                "**3. Download a model if needed.** The app currently defaults to `gemma3:4b`; "
                "`qwen3:8b` is an optional recommendation if your computer can run it. Check "
                "`ollama list` first and pull only the model you want if it is not already listed. "
                "Both model fields accept any installed Ollama model tag."
            )
            st.code(
                "ollama pull gemma3:4b  # current app default, only if missing\n"
                "ollama pull qwen3:8b   # optional alternative, only if missing",
                language="bash",
            )
            st.caption(
                "The sidebar chat history is temporary and is cleared when this app session ends."
            )

        for chat_message in st.session_state.app_help_chat_messages[-12:]:
            with st.chat_message(chat_message["role"]):
                st.markdown(chat_message["content"])

        help_model = st.text_input(
            "Local help model",
            value="gemma3:4b",
            key="app_help_model",
        ).strip()
        with st.form("app_help_chat_form", clear_on_submit=True):
            help_question = st.text_input(
                "Ask a question",
                placeholder="How do I save a bank format?",
            )
            ask_submitted = st.form_submit_button("Send question", use_container_width=True)

        if ask_submitted:
            question = help_question.strip()
            if not question:
                st.warning("Enter a question about using the app.")
            elif not help_model:
                st.warning("Enter the name of a local Ollama model.")
            else:
                st.session_state.app_help_chat_expanded = True
                messages = st.session_state.app_help_chat_messages
                messages.append({"role": "user", "content": question})
                try:
                    with st.spinner("Ollama is answering..."):
                        answer = ask_ollama_app_help(
                            question,
                            messages[:-1],
                            model=help_model,
                        )
                except Exception as error:
                    error_text = str(error)
                    if "Ollama support is optional" in error_text:
                        answer = (
                            "Ollama support is not installed yet. Run `uv sync --extra ai`, "
                            "then retry your question."
                        )
                    else:
                        answer = (
                            "I couldn't reach that Ollama model. Check that Ollama is running "
                            "and the model is installed with `ollama list`, then try again. "
                            f"Details: {error_text}"
                        )
                messages.append({"role": "assistant", "content": answer})
                st.session_state.app_help_chat_messages = messages[-12:]
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
    detected_bank = detect_bank(selected_file.name)
    custom_bank_names = sorted(
        name for name in bank_profiles if name not in BANK_OPTIONS
    )
    bank_options = [*BANK_OPTIONS[:4], *custom_bank_names, BANK_OPTIONS[-1]]
    detected_bank_choice = detected_bank if detected_bank in bank_options else BANK_OPTIONS[-1]
    bank_key = f"bank_{file_digest[:12]}"
    bank_index = bank_options.index(detected_bank_choice)
    bank_choice = st.selectbox(
        "Bank or credit union",
        bank_options,
        index=bank_index,
        key=bank_key,
    )
    needs_custom_bank = bank_choice == "Other / new bank"
    bank_name = (
        st.text_input(
            "Enter the bank name",
            key=f"custom_bank_{file_digest[:12]}",
            help="Save its format after mapping this CSV to add it to the bank menu.",
        ).strip()
        if needs_custom_bank
        else detected_bank
        if bank_choice == "Auto-detect from filename"
        else bank_choice
    )
    bank_profile = bank_profiles.get(bank_name, {})
    profile_scope = normalize_name(bank_name or bank_choice) or "newbank"
    detected_delimiter = detect_delimiter(content)
    delimiter_names = list(DELIMITER_OPTIONS)
    detected_label = next(
        (label for label, value in DELIMITER_OPTIONS.items() if value == detected_delimiter),
        "Comma (,)",
    )
    delimiter_label = st.selectbox(
        "Values are separated by",
        delimiter_names,
        index=delimiter_names.index(
            bank_profile.get("delimiter")
            if bank_profile.get("delimiter") in delimiter_names
            else "Auto-detect"
        ),
        key=f"delimiter_{file_digest[:12]}_{profile_scope}",
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
    account_default = str(bank_profile.get("account", ""))
    normalized_account_default = normalize_account_type(account_default) if account_default else ""
    account_default_index = next(
        (
            index
            for index, option in enumerate(ACCOUNT_OPTIONS)
            if option != "Other" and normalize_account_type(option) == normalized_account_default
        ),
        ACCOUNT_OPTIONS.index("Other") if account_default and normalized_account_default else 0,
    )
    account_choice = st.selectbox(
        "Account type",
        ACCOUNT_OPTIONS,
        index=account_default_index,
        key=f"account_{file_digest[:12]}_{profile_scope}",
    )
    account_name = (
        st.text_input(
            "Enter the account type",
            value=account_default,
            key=f"custom_account_{file_digest[:12]}_{profile_scope}",
        ).strip()
        if account_choice == "Other"
        else None
        if account_choice == "Select an account type"
        else normalize_account_type(account_choice)
    )

    st.subheader("3. Match the columns")
    st.caption("Choose which column in this statement matches each field. Suggested matches are preselected when possible.")
    key_prefix = f"{file_digest[:12]}_{profile_scope}"
    profile_mapping = bank_profile.get("mapping", {})
    if not isinstance(profile_mapping, dict):
        profile_mapping = {}
    date_column = column_picker(
        "Transaction date",
        headers,
        field="date",
        widget_key=f"date_{key_prefix}",
        default_value=profile_mapping.get("date"),
    )
    description_column = column_picker(
        "Description",
        headers,
        field="description",
        widget_key=f"description_{key_prefix}",
        default_value=profile_mapping.get("description"),
    )
    amount_mode_options = ["One signed amount column", "Separate debit and credit columns"]
    default_amount_mode = bank_profile.get("amount_mode", amount_mode_options[0])
    amount_mode = st.radio(
        "How are transaction amounts shown?",
        amount_mode_options,
        index=amount_mode_options.index(default_amount_mode)
        if default_amount_mode in amount_mode_options
        else 0,
        horizontal=True,
        key=f"amount_mode_{key_prefix}",
    )
    positive_amounts_are_debits = bool(bank_profile.get("positive_amounts_are_debits", False))
    if amount_mode == "One signed amount column":
        sign_convention = st.radio(
            "Amount sign convention",
            [
                "Negative = money out; positive = money in",
                "Positive = money out; negative = money in",
            ],
            index=1 if positive_amounts_are_debits else 0,
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
            "Amount",
            headers,
            field="amount",
            widget_key=f"amount_{key_prefix}",
            default_value=profile_mapping.get("amount"),
        )
        if amount_column:
            mapping["amount"] = amount_column
    else:
        left, right = st.columns(2)
        with left:
            debit_column = column_picker(
                "Debit / money out",
                headers,
                field="debit",
                widget_key=f"debit_{key_prefix}",
                default_value=profile_mapping.get("debit"),
            )
        with right:
            credit_column = column_picker(
                "Credit / money in",
                headers,
                field="credit",
                widget_key=f"credit_{key_prefix}",
                default_value=profile_mapping.get("credit"),
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
                default_value=profile_mapping.get(field),
            )
            if source_column:
                mapping[field] = source_column

    number_format_label = st.selectbox(
        "Number format",
        list(NUMBER_FORMATS),
        index=list(NUMBER_FORMATS).index(bank_profile["number_format"])
        if bank_profile.get("number_format") in NUMBER_FORMATS
        else 0,
        key=f"number_format_{key_prefix}",
        help="Choose the decimal and thousands separators used in the amount columns.",
    )
    decimal_separator, thousands_separator = NUMBER_FORMATS[number_format_label]

    with st.expander("Save this bank's CSV format as the default", expanded=False):
        st.caption(
            "Save the delimiter, amount rules, account type, and column mappings for this bank. "
            "The saved format stays on this computer and is selected automatically next time. "
            "You can save defaults for Capital One, Goldenwest Credit Union, and SoFi too."
        )
        if st.button(
            f"Save format for {bank_name or 'this bank'}",
            key=f"save_bank_profile_{file_digest[:12]}_{profile_scope}",
            disabled=(
                not bank_name
                or not account_name
                or not date_column
                or not description_column
                or (
                    "amount" not in mapping
                    and not {"debit", "credit"}.issubset(mapping)
                )
            ),
        ):
            try:
                save_bank_profile(
                    bank_name,
                    {
                        "delimiter": delimiter_label,
                        "amount_mode": amount_mode,
                        "positive_amounts_are_debits": positive_amounts_are_debits,
                        "number_format": number_format_label,
                        "account": account_name or "",
                        "mapping": mapping,
                    },
                )
                st.success(f"Saved the default CSV format for {bank_name}.")
                st.rerun()
            except (OSError, ValueError) as error:
                st.error(f"Could not save this bank format: {error}")

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
                new_export = format_final_export(exportable).reset_index(drop=True)
                new_export["_source_file"] = selected_file.name
                new_export["_source_row"] = (
                    exportable.sort_values("date", kind="stable")["source_row"].reset_index(drop=True)
                    if "source_row" in exportable
                    else pd.Series([pd.NA] * len(new_export), dtype="object")
                )
                new_export["_category_reviewed"] = new_export["main_category"].ne(
                    "General Spending"
                )
                approved_category_cache = load_category_cache()
                if approved_category_cache:
                    cached_descriptions = new_export["description"].map(
                        lambda value: clean_description_for_matching(str(value))
                    )
                    new_export["_category_reviewed"] |= cached_descriptions.isin(
                        approved_category_cache
                    )
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
    st.info(
        "Before downloading, check dates, signs and amounts, and confirm merchants and categories. "
        "Merchant names and categories can be changed in the table or with the optional AI tool below. "
        "Unmatched spending starts unchecked in Category reviewed; assign a category or explicitly "
        "check it to keep General Spending before downloading. "
        "Review transfers carefully: if a transfer was not caught by the rules, set its main category "
        "to Transfer. The app fills N/A and moves it to the excluded transaction review list."
    )
    st.caption("Transactions are shown oldest to newest. Editing a date reorders the table; type, account, bank and category are selection-only except for category choices.")
    editable = sort_rows_by_date(
        complete_missing_categories(st.session_state.editable_transactions)
    ).reset_index(drop=True)
    st.session_state.editable_transactions = editable
    editor_data = editable.copy()
    editor_data.insert(0, "row_number", range(1, len(editor_data) + 1))
    editor_data["sub_category"] = [
        CATEGORY_PAIR_TO_LABEL.get((str(main), str(sub)), "")
        for main, sub in zip(editable["main_category"], editable["sub_category"])
    ]
    search_query = st.text_input(
        "Search transactions",
        placeholder="Description, merchant, category, amount...",
        key="transaction_search",
    ).strip()
    previous_search_query = st.session_state.get("_previous_transaction_search")
    if previous_search_query != search_query:
        if previous_search_query is not None:
            st.session_state.export_editor_revision += 1
        st.session_state._previous_transaction_search = search_query
    if search_query:
        searchable_rows = [
            " ".join(row)
            for row in editor_data.astype("string").fillna("").to_numpy(dtype=str)
        ]
        matching_rows = fuzzy_match_indices(search_query, searchable_rows)
        visible_editor_data = editor_data.iloc[matching_rows].copy()
        st.caption(f"Showing {len(visible_editor_data):,} of {len(editor_data):,} transactions.")
    else:
        visible_editor_data = editor_data
    account_options = sorted(
        set(editable["account"].dropna().astype(str))
        | {"checking", "savings", "credit card", "money market", "gold account"}
    )
    bank_options = sorted(
        set(editable["bank"].dropna().astype(str))
        | {normalize_bank_name(bank) for bank in transactions["bank"].dropna().astype(str)}
    )
    edited_editor_data = st.data_editor(
        visible_editor_data,
        key=f"export_editor_{st.session_state.export_editor_revision}",
        num_rows="fixed" if search_query else "dynamic",
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
            "_category_reviewed": st.column_config.CheckboxColumn(
                "Category reviewed",
                default=False,
                help="Uncheck to prevent download until this transaction's category is reviewed.",
            ),
            "_source_file": None,
            "_source_row": None,
        },
    )
    if search_query and not edited_editor_data.empty:
        all_editor_data = editor_data.set_index("row_number")
        all_editor_data.update(edited_editor_data.set_index("row_number"))
        edited_editor_data = all_editor_data.reset_index()
    elif search_query:
        edited_editor_data = editor_data
    original_editor_data = editor_data.set_index("row_number")
    editor_row_numbers = edited_editor_data["row_number"].reset_index(drop=True)
    edited_source_metadata = edited_editor_data[EDITABLE_SOURCE_COLUMNS].reset_index(drop=True)
    edited_transactions = edited_editor_data.drop(
        columns=["row_number", *EDITABLE_SOURCE_COLUMNS]
    ).copy()
    reviewed_categories = edited_source_metadata["_category_reviewed"].fillna(False).astype(bool)
    reviewed_categories.loc[
        edited_transactions["main_category"].astype(str) != "General Spending"
    ] = True
    edited_source_metadata["_category_reviewed"] = reviewed_categories
    transfer_mask = (
        edited_transactions["main_category"].astype("string").str.strip().eq("Transfer").fillna(False)
    )
    transfers_moved = bool(transfer_mask.any())
    edited_transactions.loc[transfer_mask, "sub_category"] = CATEGORY_PAIR_TO_LABEL[("Transfer", "N/A")]
    category_pairs = edited_transactions["sub_category"].map(CATEGORY_LABEL_TO_PAIR)
    pair_mismatch = category_pairs.map(
        lambda pair: isinstance(pair, tuple)
    ) & category_pairs.map(
        lambda pair: pair[0] if isinstance(pair, tuple) else None
    ).ne(edited_transactions["main_category"])
    edited_transactions["sub_category"] = category_pairs.map(
        lambda pair: pair[1] if isinstance(pair, tuple) else pd.NA
    )
    for row_position, row in edited_transactions.iterrows():
        if pair_mismatch.iloc[row_position] or row["main_category"] == "Transfer":
            continue
        editor_row_number = editor_row_numbers.iloc[row_position]
        if pd.isna(editor_row_number) or editor_row_number not in original_editor_data.index:
            continue
        original_row = original_editor_data.loc[editor_row_number]
        old_pair = CATEGORY_LABEL_TO_PAIR.get(str(original_row["sub_category"]))
        new_pair = (str(row["main_category"]), str(row["sub_category"]))
        was_reviewed = original_row["_category_reviewed"]
        is_reviewed = edited_source_metadata.at[row_position, "_category_reviewed"]
        was_reviewed = False if pd.isna(was_reviewed) else bool(was_reviewed)
        is_reviewed = False if pd.isna(is_reviewed) else bool(is_reviewed)
        if not is_reviewed or (old_pair == new_pair and was_reviewed):
            continue
        description = str(row["description"])
        normalized_description = clean_description_for_matching(description)
        if not normalized_description:
            continue
        try:
            approve_transaction_category(description, new_pair[0], new_pair[1])
        except (OSError, ValueError) as error:
            st.warning(
                "The category was applied to this session but could not be saved "
                f"for future imports: {error}"
            )
        matching_rows = edited_transactions["description"].map(
            lambda value: clean_description_for_matching(str(value))
        ).eq(normalized_description)
        edited_transactions.loc[matching_rows, "main_category"] = new_pair[0]
        edited_transactions.loc[matching_rows, "sub_category"] = new_pair[1]
        edited_source_metadata.loc[matching_rows, "_category_reviewed"] = True
        pair_mismatch.loc[matching_rows] = False
    edited_transactions = edited_transactions[FINAL_EXPORT_COLUMNS]
    edited_transactions = edited_transactions.reset_index(drop=True)
    if transfers_moved:
        transfer_rows = make_transfer_review_rows(
            edited_transactions.loc[transfer_mask].reset_index(drop=True),
            source_files=edited_source_metadata.loc[transfer_mask, "_source_file"],
            source_rows=edited_source_metadata.loc[transfer_mask, "_source_row"],
        )
        st.session_state.dropped_transactions = pd.concat(
            [st.session_state.dropped_transactions, transfer_rows], ignore_index=True
        )
        st.session_state.pop(
            f"dropped_editor_{st.session_state.dropped_editor_revision}", None
        )
        st.session_state.dropped_editor_revision += 1
        edited_transactions = edited_transactions.loc[~transfer_mask].reset_index(drop=True)
        edited_source_metadata = edited_source_metadata.loc[~transfer_mask].reset_index(drop=True)
        editor_row_numbers = editor_row_numbers.loc[~transfer_mask].reset_index(drop=True)
        pair_mismatch = pair_mismatch.loc[~transfer_mask].reset_index(drop=True)
        st.info(f"Moved {int(transfer_mask.sum())} manually marked transfer(s) to the excluded transaction review list.")

    date_order = sort_rows_by_date(edited_transactions).index
    date_order_changed = not date_order.equals(edited_transactions.index)
    if date_order_changed:
        edited_transactions = edited_transactions.loc[date_order].reset_index(drop=True)
        edited_source_metadata = edited_source_metadata.loc[date_order].reset_index(drop=True)
        editor_row_numbers = editor_row_numbers.loc[date_order].reset_index(drop=True)
        pair_mismatch = pair_mismatch.loc[date_order].reset_index(drop=True)

    st.session_state.editable_transactions = pd.concat(
        [edited_transactions, edited_source_metadata], axis=1
    )[EDITABLE_COLUMNS]
    if transfers_moved or date_order_changed:
        st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
        st.session_state.export_editor_revision += 1
        st.rerun()

    merchant_cache = load_merchant_cache()
    candidate_rows = [
        index
        for index, row in st.session_state.editable_transactions.iterrows()
        if not bool(row["_category_reviewed"])
        or merchant_needs_review(str(row["description"]), str(row["merchant"]), merchant_cache)
    ]
    with st.expander(
        f"Needs category review and optional AI help · {len(candidate_rows)} unresolved",
        expanded=st.session_state.ai_review_expanded,
    ):
        st.caption(
            "Unreviewed transactions appear here for manual categorization or an optional local AI suggestion. "
            "AI suggestions are never saved until you approve them."
        )
        st.caption("Ollama runs locally. When requested, it receives only the selected description and amount plus allowed categories. Nothing is saved until you approve.")
        with st.expander("Set up Ollama (optional)", expanded=False):
            st.markdown(
                "For Windows installation and terminal instructions, open **Ask the app → Ollama setup** "
                "in the sidebar. In short: install Ollama, open a terminal in this project, run "
                "`uv sync --extra ai`, and make sure the model shown in the model field is installed. "
                "Both model fields accept any installed model tag. The current default is `gemma3:4b`; "
                "`qwen3:8b` is an optional alternative. Leave Ollama running, then press Suggest merchant "
                "with Ollama. Review and edit the merchant and categories before approving."
            )
            st.link_button("Download Ollama for Windows", "https://ollama.com/download/windows")
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
                st.session_state.ai_review_expanded = True
                if not ai_model:
                    st.error("Enter a local Ollama model name.")
                else:
                    try:
                        with st.spinner("Asking local Ollama for a merchant and category suggestion..."):
                            suggestion = suggest_merchant_and_category_with_ollama(
                                suggestion_description,
                                float(suggestion_transaction["amount"]),
                                CATEGORY_SUBCATEGORIES,
                                model=ai_model,
                            )
                        st.session_state[suggestion_key] = {
                            "description": suggestion_description,
                            **suggestion,
                        }
                    except Exception as error:
                        st.error(f"Ollama suggestion failed: {error}")

            pending_suggestion = st.session_state.get(suggestion_key)
            if pending_suggestion and pending_suggestion.get("description") == suggestion_description:
                if pending_suggestion.get("category_warning"):
                    st.warning(pending_suggestion["category_warning"])
                edited_suggestion = st.text_input(
                    "Merchant suggestion (edit before approval)",
                    value=pending_suggestion["merchant"],
                    key=f"ai_merchant_edit_{suggestion_fingerprint}",
                ).strip()

                suggestion_category = add_categories(
                    pd.DataFrame(
                        [{
                            "merchant": edited_suggestion or suggestion_transaction["merchant"],
                            "amount": suggestion_transaction["amount"],
                            "description": suggestion_description,
                        }]
                    )
                ).iloc[0]
                rule_main = suggestion_category["main_category"]
                rule_sub = suggestion_category["sub_category"]
                if rule_sub in {"Other", "Other Income"}:
                    proposed_main = pending_suggestion.get("main_category", rule_main)
                    proposed_sub = pending_suggestion.get("sub_category", rule_sub)
                else:
                    proposed_main = rule_main
                    proposed_sub = rule_sub
                if proposed_main not in FINAL_MAIN_CATEGORY_OPTIONS:
                    proposed_main = str(suggestion_transaction["main_category"])
                current_main = str(suggestion_transaction["main_category"])
                main_index = (
                    FINAL_MAIN_CATEGORY_OPTIONS.index(proposed_main)
                    if proposed_main in FINAL_MAIN_CATEGORY_OPTIONS
                    else FINAL_MAIN_CATEGORY_OPTIONS.index(current_main)
                    if current_main in FINAL_MAIN_CATEGORY_OPTIONS
                    else 0
                )
                category_fingerprint = hashlib.sha256(
                    f"{suggestion_fingerprint}|{edited_suggestion}".encode("utf-8")
                ).hexdigest()[:12]
                selected_main = st.selectbox(
                    "Suggested main category",
                    FINAL_MAIN_CATEGORY_OPTIONS,
                    index=main_index,
                    key=f"ai_main_{category_fingerprint}",
                )
                subcategory_options = CATEGORY_SUBCATEGORIES[selected_main]
                if selected_main != proposed_main:
                    proposed_sub = str(suggestion_transaction["sub_category"])
                if proposed_sub not in subcategory_options:
                    proposed_sub = subcategory_options[0]
                selected_sub = st.selectbox(
                    "Suggested sub-category",
                    subcategory_options,
                    index=subcategory_options.index(proposed_sub),
                    key=f"ai_sub_{category_fingerprint}_{normalize_name(selected_main)}",
                )

                approve_col, dismiss_col = st.columns(2)
                with approve_col:
                    approve_clicked = st.button(
                        "Mark as transfer and exclude"
                        if selected_main == "Transfer"
                        else "Approve and add to cache",
                        type="primary",
                        key=f"approve_{suggestion_fingerprint}",
                    )
                with dismiss_col:
                    dismiss_clicked = st.button("Dismiss", key=f"dismiss_{suggestion_fingerprint}")

                if approve_clicked:
                    try:
                        if not edited_suggestion:
                            st.error("Enter a merchant name before approving this suggestion.")
                            st.stop()
                        st.session_state.ai_review_expanded = True
                        approved_merchant = edited_suggestion
                        if selected_main != "Transfer":
                            approve_merchant_match(suggestion_description, approved_merchant)
                        updated = st.session_state.editable_transactions.copy()
                        normalized_description = clean_description_for_matching(
                            suggestion_description
                        )
                        matching_rows = updated["description"].map(
                            lambda value: clean_description_for_matching(str(value))
                        ).eq(normalized_description)
                        updated.loc[matching_rows, "merchant"] = approved_merchant
                        updated.loc[matching_rows, "main_category"] = selected_main
                        updated.loc[matching_rows, "sub_category"] = selected_sub
                        updated.loc[matching_rows, "_category_reviewed"] = True
                        if selected_main != "Transfer":
                            approve_transaction_category(
                                suggestion_description,
                                selected_main,
                                selected_sub,
                            )
                        st.session_state.editable_transactions = updated
                        st.session_state.pop(suggestion_key, None)
                        st.session_state.merchant_cache_editor_revision += 1
                        st.session_state.pop(
                            f"merchant_cache_editor_{st.session_state.merchant_cache_editor_revision - 1}",
                            None,
                        )
                        st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
                        st.session_state.export_editor_revision += 1
                        st.success(
                            "Marked as Transfer and moved to the excluded review list."
                            if selected_main == "Transfer"
                            else "Approved merchant saved to merchant_cache.json."
                        )
                        st.rerun()
                    except (OSError, ValueError) as error:
                        st.error(f"Could not save the approved merchant: {error}")
                elif dismiss_clicked:
                    st.session_state.ai_review_expanded = True
                    st.session_state.pop(suggestion_key, None)
                    st.rerun()

    with st.expander(f"Personal merchant cache · {len(merchant_cache):,} entries", expanded=False):
        st.caption("Edit a merchant mapping directly, or delete its row and save. Cache keys are normalized transaction descriptions.")
        cache_table = pd.DataFrame(
            [
                {"Description key": key, "Merchant": merchant, "_original_key": key}
                for key, merchant in merchant_cache.items()
            ],
            columns=["Description key", "Merchant", "_original_key"],
        )
        cache_search = st.text_input(
            "Search merchant cache",
            placeholder="Merchant or transaction description",
            key="merchant_cache_search",
        ).strip()
        previous_cache_search = st.session_state.get("_previous_merchant_cache_search")
        if previous_cache_search != cache_search:
            if previous_cache_search is not None:
                st.session_state.merchant_cache_editor_revision += 1
                st.session_state.pop(
                    f"merchant_cache_editor_{st.session_state.merchant_cache_editor_revision - 1}",
                    None,
                )
            st.session_state._previous_merchant_cache_search = cache_search
        if cache_search:
            cache_search_values = (
                cache_table["Description key"].astype(str)
                + " "
                + cache_table["Merchant"].astype(str)
            ).tolist()
            cache_matches = fuzzy_match_indices(cache_search, cache_search_values)
            visible_cache_table = cache_table.iloc[cache_matches].copy()
            st.caption(f"Showing {len(visible_cache_table):,} of {len(cache_table):,} cache entries.")
        else:
            visible_cache_table = cache_table
        edited_cache = st.data_editor(
            visible_cache_table,
            key=f"merchant_cache_editor_{st.session_state.merchant_cache_editor_revision}",
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "Description key": st.column_config.TextColumn("Normalized description", required=True),
                "Merchant": st.column_config.TextColumn("Merchant", required=True),
                "_original_key": None,
            },
        )
        if st.button("Save merchant cache changes", type="primary"):
            visible_original_keys = set(visible_cache_table["_original_key"].dropna().astype(str))
            edited_entries = []
            cache_error = None
            for _, cache_row in edited_cache.iterrows():
                raw_description_key = cache_row["Description key"]
                raw_merchant_name = cache_row["Merchant"]
                description_key = clean_description_for_matching(
                    "" if pd.isna(raw_description_key) else str(raw_description_key)
                )
                merchant_name = "" if pd.isna(raw_merchant_name) else str(raw_merchant_name).strip()
                if not description_key and not merchant_name:
                    continue
                if not description_key or not merchant_name:
                    cache_error = "Each cache row needs both a description key and a merchant."
                    break
                edited_entries.append((description_key, merchant_name))

            if cache_error:
                st.error(cache_error)
            else:
                try:
                    revised_cache = merge_merchant_cache_edits(
                        merchant_cache, visible_original_keys, edited_entries
                    )
                    save_merchant_cache(revised_cache)
                    st.session_state.merchant_cache_editor_revision += 1
                    st.session_state.pop(
                        f"merchant_cache_editor_{st.session_state.merchant_cache_editor_revision - 1}",
                        None,
                    )
                    st.success("Merchant cache saved. Unresolved count has been refreshed.")
                    st.rerun()
                except (OSError, ValueError) as error:
                    st.error(f"Could not save the merchant cache: {error}")

    download_data, validation_error = validate_final_export_rows(edited_transactions)
    download_data = sort_rows_by_date(download_data).reset_index(drop=True)
    if pair_mismatch.any():
        validation_error = "Choose a sub-category whose displayed main category matches the Main category column."
    unreviewed_mask = ~edited_source_metadata["_category_reviewed"].fillna(False).astype(bool)
    if unreviewed_mask.any():
        validation_error = (
            f"Review the category for {int(unreviewed_mask.sum())} transaction(s) before downloading. "
            "Choose a category or explicitly confirm General Spending."
        )
    row_issues = get_final_export_row_issues(edited_transactions)
    for row_index in pair_mismatch[pair_mismatch].index:
        row_issues.setdefault(int(row_index), []).append(
            "Sub-category does not match the selected Main category."
        )
    for row_index in unreviewed_mask[unreviewed_mask].index:
        row_issues.setdefault(int(row_index), []).append(
            "Category needs review before download."
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
                                        pd.DataFrame(
                                            {
                                                "_category_reviewed": selected_rows[
                                                    "main_category"
                                                ].ne("General Spending").to_numpy()
                                            }
                                        ),
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