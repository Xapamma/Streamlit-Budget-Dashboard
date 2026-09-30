"""Session-scoped monthly category budget editor and progress view."""

from datetime import date

import pandas as pd
import streamlit as st

from dashboard_data import (
    income_for_budget_category,
    prepare_analytics_transactions,
    spending_for_budget_category,
    summarize_transactions,
)
from cleaning_logic import category_hierarchy

st.title("Monthly budget")
st.caption("Choose a month, enter income, then plan each spending and income category. Existing transactions prefill missing targets without replacing limits you set.")

current_month = date.today().replace(day=1)
selected_month = st.date_input(
    "Month to budget",
    value=current_month,
    format="MM/DD/YYYY",
    key="budget_month",
)
month_start = pd.Timestamp(selected_month).replace(day=1)
budget_month_key = month_start.strftime("%Y-%m")
month_end = month_start + pd.offsets.MonthBegin(1)

if "budget_editor_revision" not in st.session_state:
    st.session_state.budget_editor_revision = 0
if st.session_state.get("budget_editor_schema_version") != 4:
    st.session_state.budget_editor_revision += 1
    st.session_state.budget_editor_schema_version = 4
previous_budget_month = st.session_state.get("_budget_widget_month_key")
if previous_budget_month is not None and previous_budget_month != budget_month_key:
    st.session_state.budget_editor_revision += 1
    st.session_state.pop("budget_auto_adjust_notice", None)
st.session_state._budget_widget_month_key = budget_month_key

if "monthly_budgets_by_month" not in st.session_state:
    legacy_budgets = st.session_state.get("monthly_budgets", {})
    st.session_state.monthly_budgets_by_month = (
        {budget_month_key: dict(legacy_budgets)} if legacy_budgets else {}
    )
if "monthly_income_by_month" not in st.session_state:
    legacy_income = st.session_state.get("monthly_income")
    st.session_state.monthly_income_by_month = (
        {budget_month_key: float(legacy_income)} if legacy_income is not None else {}
    )
if "removed_budget_categories_by_month" not in st.session_state:
    legacy_removed = st.session_state.get("removed_budget_categories", set())
    st.session_state.removed_budget_categories_by_month = (
        {budget_month_key: set(legacy_removed)} if legacy_removed else {}
    )
st.session_state.monthly_budgets = st.session_state.monthly_budgets_by_month.setdefault(
    budget_month_key, {}
)
st.session_state.monthly_income = st.session_state.monthly_income_by_month.get(budget_month_key)
st.session_state.removed_budget_categories = st.session_state.removed_budget_categories_by_month.setdefault(
    budget_month_key, set()
)

income_sources = list(category_hierarchy["Income"])
source_budget_keys = {source: f"Income :: {source}" for source in income_sources}

transactions = prepare_analytics_transactions(st.session_state.get("editable_transactions"))
month_transactions = transactions.loc[
    (transactions["date"] >= month_start) & (transactions["date"] < month_end)
]
monthly_actual_totals = summarize_transactions(month_transactions)
actual_income_remaining = monthly_actual_totals["income"] - monthly_actual_totals["spending"]
legacy_month_income = st.session_state.monthly_income_by_month.get(budget_month_key)
has_actual_income = (month_transactions["amount"] > 0).any()
if (
    legacy_month_income
    and not has_actual_income
    and not any(key in st.session_state.monthly_budgets for key in source_budget_keys.values())
):
    st.session_state.monthly_budgets[source_budget_keys["Other Income"]] = float(legacy_month_income)

monthly_income = st.session_state.monthly_income_by_month.get(budget_month_key)
if monthly_income is None:
    monthly_income = sum(
        float(st.session_state.monthly_budgets.get(source_budget_keys[source], 0.0))
        for source in income_sources
        if source_budget_keys[source] not in st.session_state.removed_budget_categories
    )

budget_main_categories = ["Income", *[category for category in category_hierarchy if category != "Income"]]
if not month_transactions.empty:
    seeded_any = False
    month_expenses = month_transactions.loc[month_transactions["amount"] < 0]
    for main_category, main_rows in month_expenses.groupby("main_category"):
        if main_category not in budget_main_categories or main_category in {"Income", "Transfer"}:
            continue
        main_spending = float(-main_rows["amount"].sum())
        if (
            main_category not in st.session_state.monthly_budgets
            and main_category not in st.session_state.removed_budget_categories
        ):
            st.session_state.monthly_budgets[main_category] = main_spending
            seeded_any = True
        for subcategory, subcategory_rows in main_rows.groupby("sub_category"):
            if main_category == "Transfer":
                st.caption("N/A · Not budgetable")
                continue
            category_path = f"{main_category} :: {subcategory}"
            if (
                category_path not in st.session_state.monthly_budgets
                and category_path not in st.session_state.removed_budget_categories
            ):
                st.session_state.monthly_budgets[category_path] = float(
                    -subcategory_rows["amount"].sum()
                )
                seeded_any = True

    month_income = month_transactions.loc[
        (month_transactions["amount"] > 0)
    ]
    month_income = month_income.assign(
        budget_source=month_income.apply(
            lambda row: "Dividends"
            if row["sub_category"] == "Dividends"
            else row["sub_category"]
            if row["main_category"] == "Income" and row["sub_category"] in income_sources
            else "Other Income",
            axis=1,
        )
    )
    for source, subcategory_rows in month_income.groupby("budget_source"):
        category_path = f"Income :: {source}"
        if (
            category_path not in st.session_state.monthly_budgets
            and category_path not in st.session_state.removed_budget_categories
        ):
            st.session_state.monthly_budgets[category_path] = float(
                subcategory_rows["amount"].sum()
            )
            seeded_any = True

    if seeded_any:
        st.session_state.budget_editor_revision += 1

subcategory_limits_by_main = {}
category_allocations = {}
auto_adjustments = []

last_adjustment_notice = st.session_state.pop("budget_auto_adjust_notice", None)
if last_adjustment_notice:
    st.warning(last_adjustment_notice)

def current_subcategory_limits(main_category: str) -> dict[str, float]:
    subcategories = category_hierarchy[main_category]
    return {
        subcategory: float(st.session_state.monthly_budgets[f"{main_category} :: {subcategory}"])
        for subcategory in subcategories
        if f"{main_category} :: {subcategory}" in st.session_state.monthly_budgets
        and f"{main_category} :: {subcategory}" not in st.session_state.removed_budget_categories
    }


for main_category in budget_main_categories:
    existing_children = current_subcategory_limits(main_category)
    existing_parent = float(st.session_state.monthly_budgets.get(main_category, 0.0))
    if main_category == "Transfer":
        label_limit = 0.0
        actual_for_label = 0.0
    elif main_category == "Income":
        label_limit = float(monthly_income or sum(existing_children.values()))
        actual_for_label = income_for_budget_category(month_transactions, "Income")
    else:
        label_limit = max(existing_parent, sum(existing_children.values()))
        actual_for_label = spending_for_budget_category(month_transactions, main_category)
    label_balance = label_limit - actual_for_label
    label_amount = (
        "Excluded from budgets"
        if main_category == "Transfer"
        else (
        f"${label_limit:,.0f} planned · ${label_balance:,.0f} left"
        if label_limit > 0
        else "Set a limit"
        )
    )

    with st.expander(f"{main_category} · {label_amount}", expanded=main_category == "Income"):
        if main_category == "Transfer":
            st.caption("Transfers are excluded from spending budgets and reviewed separately.")
            st.caption("N/A · Not budgetable")
        elif main_category == "Income":
            st.caption("Set a monthly amount for each income source below.")
        elif main_category not in st.session_state.removed_budget_categories:
            main_columns = st.columns([5, 1])
            with main_columns[0]:
                monthly_limit = st.number_input(
                    f"Total monthly limit ($) · {main_category}",
                    min_value=0.0,
                    value=float(st.session_state.monthly_budgets.get(main_category, 0.0)),
                    step=25.0,
                    format="%.2f",
                    key=f"budget_limit_{st.session_state.budget_editor_revision}_{main_category}",
                )
                if monthly_limit is not None and monthly_limit > 0:
                    st.session_state.monthly_budgets[main_category] = float(monthly_limit)
                else:
                    st.session_state.monthly_budgets.pop(main_category, None)
            with main_columns[1]:
                if st.button(
                    "Remove",
                    key=f"remove_budget_{st.session_state.budget_editor_revision}_{main_category}",
                    help="Hide this main-category limit. You can add it back below.",
                ):
                    st.session_state.removed_budget_categories.add(main_category)
                    st.session_state.monthly_budgets.pop(main_category, None)
                    st.session_state.budget_editor_revision += 1
                    st.rerun()

        subcategories = category_hierarchy[main_category]
        with st.expander(
            f"Subcategories · {len(subcategories)}",
            expanded=main_category == "Income",
        ):
            for subcategory in subcategories:
                if main_category == "Transfer":
                    st.caption(f"{subcategory} · Not budgetable")
                    continue
                category_path = f"{main_category} :: {subcategory}"
                if category_path in st.session_state.removed_budget_categories:
                    continue
                subcategory_columns = st.columns([5, 1])
                with subcategory_columns[0]:
                    monthly_limit = st.number_input(
                        f"Monthly limit ($) · {subcategory}",
                        min_value=0.0,
                        value=float(st.session_state.monthly_budgets.get(category_path, 0.0)),
                        step=25.0,
                        format="%.2f",
                        key=f"budget_limit_{st.session_state.budget_editor_revision}_{category_path}",
                    )
                    if monthly_limit is not None and monthly_limit > 0:
                        st.session_state.monthly_budgets[category_path] = float(monthly_limit)
                    else:
                        st.session_state.monthly_budgets.pop(category_path, None)
                with subcategory_columns[1]:
                    if st.button(
                        "Remove",
                        key=f"remove_budget_{st.session_state.budget_editor_revision}_{category_path}",
                        help="Hide this subcategory limit. You can add it back below.",
                    ):
                        st.session_state.removed_budget_categories.add(category_path)
                        st.session_state.monthly_budgets.pop(category_path, None)
                        st.session_state.budget_editor_revision += 1
                        st.rerun()

    if main_category == "Transfer":
        subcategory_limits_by_main[main_category] = {}
        continue

    subcategory_limits = current_subcategory_limits(main_category)
    subcategory_limits_by_main[main_category] = subcategory_limits
    subcategory_total = sum(subcategory_limits.values())
    if main_category == "Income":
        monthly_income = subcategory_total
        st.session_state.monthly_income = monthly_income or None
        if monthly_income > 0:
            st.session_state.monthly_income_by_month[budget_month_key] = monthly_income
            category_allocations[main_category] = monthly_income
        else:
            st.session_state.monthly_income_by_month.pop(budget_month_key, None)
        st.metric("Total monthly income", f"${monthly_income:,.2f}")
        continue

    parent_limit = float(st.session_state.monthly_budgets.get(main_category, 0.0))
    effective_limit = max(parent_limit, subcategory_total)
    actual_spending = spending_for_budget_category(month_transactions, main_category)
    if (
        main_category not in st.session_state.removed_budget_categories
        and effective_limit > 0
    ):
        required_limit = max(effective_limit, actual_spending)
        if required_limit > parent_limit:
            st.session_state.monthly_budgets[main_category] = required_limit
            category_allocations[main_category] = required_limit
            auto_adjustments.append((main_category, required_limit))
        else:
            category_allocations[main_category] = effective_limit
    elif effective_limit > 0:
        category_allocations[main_category] = effective_limit

income_source_limits = current_subcategory_limits("Income")
subcategory_limits_by_main["Income"] = income_source_limits
if monthly_income > 0:
    category_allocations["Income"] = monthly_income

if auto_adjustments:
    adjustment_details = ", ".join(
        f"{main}: ${amount:,.2f}" for main, amount in auto_adjustments
    )
    st.session_state.budget_auto_adjust_notice = (
        "Main-category limits were increased to cover their subcategory plans or selected-month spending: "
        + adjustment_details
        + "."
    )
    st.session_state.budget_editor_revision += 1
    st.rerun()

if st.session_state.removed_budget_categories:
    removed_options = sorted(st.session_state.removed_budget_categories)
    restore_category = st.selectbox("Add a removed category back", removed_options)
    if st.button("Add category back"):
        st.session_state.removed_budget_categories.remove(restore_category)
        st.session_state.budget_editor_revision += 1
        st.rerun()

expense_allocation = sum(
    amount for category, amount in category_allocations.items() if category != "Income"
)
income_remaining = None if monthly_income is None else monthly_income - expense_allocation
with st.sidebar:
    st.divider()
    st.subheader("Income allocation")
    st.metric(
        "Monthly income",
        "Set above" if monthly_income is None else f"${monthly_income:,.2f}",
    )
    st.metric("Spending limits", f"${expense_allocation:,.2f}")
    st.metric(
        "Income unallocated",
        "Set monthly income" if income_remaining is None else f"${income_remaining:,.2f}",
    )

st.subheader(f"Budget progress · {month_start:%B %Y}")
if category_allocations:
    for main_category, limit in category_allocations.items():
        is_income = main_category == "Income"
        actual = (
            income_for_budget_category(month_transactions, main_category)
            if is_income
            else spending_for_budget_category(month_transactions, main_category)
        )
        remaining = limit - actual
        heading_columns = st.columns([3, 1])
        with heading_columns[0]:
            st.subheader(main_category)
        with heading_columns[1]:
            balance = actual_income_remaining if is_income else remaining
            st.metric("Income remaining" if is_income else "Amount left", f"${balance:,.2f}")
        if limit > 0:
            st.progress(min(actual / limit, 1.0))
            st.caption(
                f"${actual:,.2f} received of ${limit:,.2f} planned"
                if is_income
                else f"${actual:,.2f} spent of ${limit:,.2f} allocated"
            )

        subcategory_limits = subcategory_limits_by_main[main_category]
        if subcategory_limits:
            for subcategory, subcategory_limit in subcategory_limits.items():
                category_path = f"{main_category} :: {subcategory}"
                subcategory_actual = (
                    income_for_budget_category(month_transactions, category_path)
                    if is_income
                    else spending_for_budget_category(month_transactions, category_path)
                )
                subcategory_remaining = subcategory_limit - subcategory_actual
                with st.expander(
                    f"{subcategory} · ${subcategory_actual:,.2f} "
                    f"{'received' if is_income else 'spent'} of ${subcategory_limit:,.2f}"
                ):
                    st.caption(f"${subcategory_remaining:,.2f} left")
                    if subcategory_remaining < 0:
                        st.warning(
                            f"{subcategory} is ${-subcategory_remaining:,.2f} over its limit."
                        )
                    subcategory_transactions = month_transactions.loc[
                        (month_transactions["main_category"] == main_category)
                        & (month_transactions["sub_category"] == subcategory)
                    ].sort_values("date", ascending=False, kind="stable")
                    if subcategory_transactions.empty:
                        st.caption("No transactions for this subcategory in the selected month.")
                    else:
                        transaction_details = subcategory_transactions[
                            ["date", "description", "merchant", "amount"]
                        ].copy()
                        transaction_details["date"] = transaction_details["date"].dt.strftime(
                            "%m/%d/%Y"
                        )
                        st.dataframe(
                            transaction_details,
                            use_container_width=True,
                            hide_index=True,
                        )
        if remaining < 0 and not is_income:
            st.error(f"{main_category} is ${-remaining:,.2f} over its main-category budget.")
else:
    st.info("Enter a main-category or subcategory limit above to see budget balances here.")

if monthly_income is not None:
    if income_remaining < 0:
        st.error(
            f"Your category plan is ${-income_remaining:,.2f} over monthly income. "
            "Reduce a category limit or reduce the savings allocation."
        )
    elif income_remaining > 0:
        st.warning(
            f"${income_remaining:,.2f} of monthly income is unallocated. "
            "Assign the remainder to Savings & Investments to give every dollar a plan."
        )
        if st.button("Allocate remainder to Savings & Investments"):
            savings_budget = category_allocations.get("Savings & Investments", 0.0)
            st.session_state.monthly_budgets["Savings & Investments"] = (
                savings_budget + income_remaining
            )
            st.session_state.removed_budget_categories.discard("Savings & Investments")
            st.session_state.budget_editor_revision += 1
            st.rerun()
    else:
        st.success("Monthly income is fully allocated across your category plan.")

if transactions.empty:
    st.caption("Import transactions to calculate actual spending against these limits.")
else:
    st.caption("Budgets compare against the current session's edited transactions for the selected month.")

st.caption("Budgets are kept in the current Streamlit session and clear when the session ends.")
