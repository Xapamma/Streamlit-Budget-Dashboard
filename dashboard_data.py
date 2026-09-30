"""Financial summaries for the session's editable export transactions."""

from __future__ import annotations

import pandas as pd

from cleaning_logic import category_hierarchy, clean_description_for_matching
from merchant_assistance import approve_merchant_match
from final_export import (
    CATEGORY_LABEL_TO_PAIR,
    CATEGORY_PAIR_TO_LABEL,
    CATEGORY_SUBCATEGORIES,
    approve_transaction_category,
)

CATEGORY_EDIT_API_VERSION = 3
ANALYTICS_COLUMNS = [
    "date",
    "description",
    "merchant",
    "amount",
    "main_category",
    "sub_category",
]
CATEGORY_EDIT_API_VERSION = 3


def sort_rows_by_date(rows: pd.DataFrame) -> pd.DataFrame:
    """Sort export-style date rows ascending, stably, placing invalid dates last."""
    if rows.empty or "date" not in rows.columns:
        return rows.copy()
    parsed_dates = pd.to_datetime(rows["date"], format="%m/%d/%Y", errors="coerce")
    order = parsed_dates.sort_values(kind="stable", na_position="last").index
    return rows.loc[order].copy()


def prepare_analytics_transactions(export_rows: pd.DataFrame | None) -> pd.DataFrame:
    """Return valid, typed rows from the current editable export."""
    if export_rows is None or export_rows.empty:
        return pd.DataFrame(columns=ANALYTICS_COLUMNS)

    if not set(ANALYTICS_COLUMNS).issubset(export_rows.columns):
        return pd.DataFrame(columns=ANALYTICS_COLUMNS)

    transactions = export_rows[ANALYTICS_COLUMNS].copy()
    transactions["_source_position"] = range(len(export_rows))
    transactions["date"] = pd.to_datetime(transactions["date"], errors="coerce")
    transactions["amount"] = pd.to_numeric(transactions["amount"], errors="coerce")
    transactions = transactions.dropna(subset=["date", "amount"])
    transactions["main_category"] = transactions["main_category"].fillna("Uncategorized")
    transactions["sub_category"] = transactions["sub_category"].fillna("Other")
    transactions["merchant"] = transactions["merchant"].fillna("Unknown merchant")
    return transactions.reset_index(drop=True)


def category_editor_data(
    transactions: pd.DataFrame,
    columns: list[str],
) -> pd.DataFrame:
    """Prepare a transaction-list view with stable row positions and paired category labels."""
    editor_columns = list(dict.fromkeys([*columns, "description", "_source_position"]))
    editor_rows = transactions[editor_columns].copy()
    editor_rows["sub_category"] = [
        CATEGORY_PAIR_TO_LABEL.get((str(main), str(sub)), "")
        for main, sub in zip(editor_rows["main_category"], editor_rows["sub_category"])
    ]
    return editor_rows


def apply_transaction_category_edits(
    export_rows: pd.DataFrame,
    edited_rows: pd.DataFrame,
    *,
    persist_category_mappings: bool = False,
    persist_merchant_mappings: bool = False,
) -> tuple[pd.DataFrame, bool, bool]:
    """Apply category and optional merchant edits by source position."""
    pairs = edited_rows["sub_category"].map(CATEGORY_LABEL_TO_PAIR)
    positions = pd.to_numeric(edited_rows["_source_position"], errors="coerce")
    invalid_positions = positions.isna() | (positions < 0) | (positions >= len(export_rows))
    valid_pairs = pairs.map(lambda pair: isinstance(pair, tuple))
    matching_mains = pd.Series(
        [
            isinstance(pair, tuple)
            and pair[0] == main_category
            and main_category != "Transfer"
            for pair, main_category in zip(pairs, edited_rows["main_category"])
        ],
        index=edited_rows.index,
    )
    if invalid_positions.any() or not (valid_pairs & matching_mains).all():
        return export_rows, False, True

    updated = export_rows.copy()
    main_column = updated.columns.get_loc("main_category")
    subcategory_column = updated.columns.get_loc("sub_category")
    changed = False
    for row_index, position, main_category, pair in zip(
        edited_rows.index,
        positions.astype(int),
        edited_rows["main_category"],
        pairs,
    ):
        subcategory = pair[1]
        current_main = updated.iat[position, main_column]
        current_subcategory = updated.iat[position, subcategory_column]
        category_changed = (
            str(current_main) != str(main_category)
            or str(current_subcategory) != subcategory
        )
        current_merchant = str(updated.at[position, "merchant"])
        edited_merchant = (
            str(edited_rows.at[row_index, "merchant"]).strip()
            if "merchant" in edited_rows.columns
            else current_merchant
        )
        merchant_changed = bool(edited_merchant) and current_merchant != edited_merchant
        if not category_changed and not merchant_changed:
            continue

        description = str(updated.at[position, "description"])
        normalized_description = clean_description_for_matching(description)
        if normalized_description:
            matching_rows = updated["description"].map(
                lambda value: clean_description_for_matching(str(value))
            ).eq(normalized_description)
        else:
            matching_rows = updated.index == position

        if category_changed:
            updated.loc[matching_rows, "main_category"] = main_category
            updated.loc[matching_rows, "sub_category"] = subcategory
            if "_category_reviewed" in updated.columns:
                updated.loc[matching_rows, "_category_reviewed"] = main_category != "General Spending"
        if merchant_changed:
            updated.loc[matching_rows, "merchant"] = edited_merchant
            if persist_merchant_mappings and normalized_description:
                approve_merchant_match(description, edited_merchant)
        reviewed = (
            bool(updated.at[position, "_category_reviewed"])
            if "_category_reviewed" in updated.columns
            else main_category != "General Spending"
        )
        if (
            persist_category_mappings
            and normalized_description
            and main_category != "Transfer"
            and (main_category != "General Spending" or reviewed)
        ):
            approve_transaction_category(description, main_category, subcategory)
        changed = True
    return updated, changed, False


def analytics_transactions_csv(transactions: pd.DataFrame) -> bytes:
    """Serialize the currently selected analytics rows for CSV download."""
    export_rows = transactions[ANALYTICS_COLUMNS].copy()
    export_rows["date"] = pd.to_datetime(export_rows["date"], errors="coerce").dt.strftime(
        "%m/%d/%Y"
    )
    return export_rows.to_csv(index=False).encode("utf-8-sig")


def summarize_transactions(transactions: pd.DataFrame) -> dict[str, float | int]:
    amounts = transactions["amount"]
    income = float(amounts[amounts > 0].sum())
    spending = float(-amounts[amounts < 0].sum())
    return {
        "transactions": int(len(transactions)),
        "income": income,
        "spending": spending,
        "net_savings": income - spending,
    }


def trend_totals(transactions: pd.DataFrame, frequency: str) -> pd.DataFrame:
    """Aggregate signed transactions into income, spending, and net savings."""
    if transactions.empty:
        return pd.DataFrame(columns=["income", "spending", "net_savings"])

    periodized = transactions.assign(
        period=transactions["date"].dt.to_period(frequency).dt.start_time,
        income=transactions["amount"].clip(lower=0),
        spending=-transactions["amount"].clip(upper=0),
        net_savings=transactions["amount"],
    )
    return (
        periodized.groupby("period")[["income", "spending", "net_savings"]]
        .sum()
        .sort_index()
    )


def spending_for_budget_category(transactions: pd.DataFrame, category: str) -> float:
    """Return outflow for either a main category or a main/subcategory label."""
    spending = transactions.loc[transactions["amount"] < 0]
    if category in CATEGORY_SUBCATEGORIES:
        matched = spending.loc[spending["main_category"] == category]
    else:
        category_pair = CATEGORY_LABEL_TO_PAIR.get(category)
        if category_pair is None:
            return 0.0
        main_category, sub_category = category_pair
        matched = spending.loc[
            (spending["main_category"] == main_category)
            & (spending["sub_category"] == sub_category)
        ]
    total_spending = float(-matched["amount"].sum())
    return 0.0 if total_spending == 0 else total_spending


def income_for_budget_category(transactions: pd.DataFrame, category: str) -> float:
    """Return inflow for an income main category or its subcategory."""
    income = transactions.loc[transactions["amount"] > 0]
    if category == "Income":
        matched = income
    elif category == "Income :: Dividends":
        matched = income.loc[income["sub_category"] == "Dividends"]
    else:
        category_pair = CATEGORY_LABEL_TO_PAIR.get(category)
        if category_pair is None:
            return 0.0
        main_category, sub_category = category_pair
        if main_category != "Income":
            return 0.0
        matched = income.loc[
            (income["main_category"] == main_category)
            & (income["sub_category"] == sub_category)
        ]
    return float(matched["amount"].sum())


def spending_by_main_category(transactions: pd.DataFrame) -> pd.DataFrame:
    """Aggregate outflows for every spending category, including zero-spend categories."""
    spending = transactions.loc[transactions["amount"] < 0].copy()
    spending["spending"] = -spending["amount"]
    totals = (
        spending.groupby("main_category")
        .agg(spending=("spending", "sum"), transactions=("amount", "size"))
        .reset_index()
    )
    excluded_categories = {"Income", "Transfer"}
    main_categories = [
        category
        for category in category_hierarchy
        if category not in excluded_categories
    ]
    additional_categories = [
        category
        for category in totals["main_category"].tolist()
        if category not in main_categories and category not in excluded_categories
    ]
    totals = totals.loc[~totals["main_category"].isin(excluded_categories)]
    totals = (
        totals.set_index("main_category")
        .reindex([*main_categories, *additional_categories], fill_value=0)
        .rename_axis("main_category")
        .reset_index()
    )
    totals["transactions"] = totals["transactions"].astype(int)
    return totals.sort_values("spending", ascending=False, kind="stable").reset_index(drop=True)


def filter_transactions_by_category(
    transactions: pd.DataFrame,
    main_category: str | None = None,
    sub_category: str | None = None,
) -> pd.DataFrame:
    """Filter the selected period's rows by main category and optional subcategory."""
    filtered = transactions
    if main_category:
        filtered = filtered.loc[filtered["main_category"] == main_category]
    if sub_category:
        filtered = filtered.loc[filtered["sub_category"] == sub_category]
    return filtered.sort_values("date", ascending=False, kind="stable").reset_index(drop=True)


def transactions_for_category_review(transactions: pd.DataFrame) -> pd.DataFrame:
    """Return all category-review rows, including provisional General Spending entries."""
    return transactions.copy()