"""Financial summaries for the session's editable export transactions."""

from __future__ import annotations

import pandas as pd

from final_export import CATEGORY_LABEL_TO_PAIR, CATEGORY_SUBCATEGORIES


ANALYTICS_COLUMNS = ["date", "merchant", "amount", "main_category", "sub_category"]


def prepare_analytics_transactions(export_rows: pd.DataFrame | None) -> pd.DataFrame:
    """Return valid, typed rows from the current editable export."""
    if export_rows is None or export_rows.empty:
        return pd.DataFrame(columns=ANALYTICS_COLUMNS)

    if not set(ANALYTICS_COLUMNS).issubset(export_rows.columns):
        return pd.DataFrame(columns=ANALYTICS_COLUMNS)

    transactions = export_rows[ANALYTICS_COLUMNS].copy()
    transactions["date"] = pd.to_datetime(transactions["date"], errors="coerce")
    transactions["amount"] = pd.to_numeric(transactions["amount"], errors="coerce")
    transactions = transactions.dropna(subset=["date", "amount"])
    transactions["main_category"] = transactions["main_category"].fillna("Uncategorized")
    transactions["sub_category"] = transactions["sub_category"].fillna("Other")
    transactions["merchant"] = transactions["merchant"].fillna("Unknown merchant")
    return transactions.reset_index(drop=True)


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
    return float(-matched["amount"].sum())