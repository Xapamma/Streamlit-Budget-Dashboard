"""Session-scoped monthly category budget editor and progress view."""

from datetime import date

import pandas as pd
import streamlit as st

from dashboard_data import prepare_analytics_transactions, spending_for_budget_category
from final_export import CATEGORY_LABEL_TO_PAIR, CATEGORY_PAIR_OPTIONS, FINAL_MAIN_CATEGORY_OPTIONS

st.title("Monthly budget")
st.caption("Set category spending limits, then compare them with transactions in the selected month.")

if "monthly_budgets" not in st.session_state:
    st.session_state.monthly_budgets = {}
if "budget_editor_revision" not in st.session_state:
    st.session_state.budget_editor_revision = 0

budget_categories = [
    category
    for category in FINAL_MAIN_CATEGORY_OPTIONS
    if category not in {"Income", "Transfer"}
] + [
    category
    for category in CATEGORY_PAIR_OPTIONS
    if CATEGORY_LABEL_TO_PAIR[category][0] not in {"Income", "Transfer"}
]

budget_rows = pd.DataFrame(
    [
        {"Category": category, "Monthly limit": amount}
        for category, amount in st.session_state.monthly_budgets.items()
    ],
    columns=["Category", "Monthly limit"],
)
edited_budgets = st.data_editor(
    budget_rows,
    key=f"budget_editor_{st.session_state.budget_editor_revision}",
    num_rows="dynamic",
    use_container_width=True,
    hide_index=True,
    column_config={
        "Category": st.column_config.SelectboxColumn(
            "Category", options=budget_categories, required=True
        ),
        "Monthly limit": st.column_config.NumberColumn(
            "Monthly limit", min_value=0.0, format="$%.2f", required=True
        ),
    },
)

if st.button("Save monthly budgets", type="primary"):
    revised_budgets = {}
    budget_error = None
    for _, row in edited_budgets.iterrows():
        category = "" if pd.isna(row["Category"]) else str(row["Category"])
        raw_limit = row["Monthly limit"]
        if not category and pd.isna(raw_limit):
            continue
        if category not in budget_categories or pd.isna(raw_limit):
            budget_error = "Each budget row needs a valid category and monthly limit."
            break
        limit = float(raw_limit)
        if limit < 0:
            budget_error = "Budget limits cannot be negative."
            break
        if category in revised_budgets:
            budget_error = f"{category} appears more than once. Keep one budget row per category."
            break
        revised_budgets[category] = limit

    if budget_error:
        st.error(budget_error)
    else:
        st.session_state.monthly_budgets = revised_budgets
        st.session_state.budget_editor_revision += 1
        st.session_state.pop(f"budget_editor_{st.session_state.budget_editor_revision - 1}", None)
        st.success("Monthly budgets saved for this session.")
        st.rerun()

transactions = prepare_analytics_transactions(st.session_state.get("editable_transactions"))
current_month = date.today().replace(day=1)
selected_month = st.date_input("Compare against month", value=current_month, key="budget_month")
month_start = pd.Timestamp(selected_month).replace(day=1)
month_end = month_start + pd.offsets.MonthBegin(1)
month_transactions = transactions.loc[
    (transactions["date"] >= month_start) & (transactions["date"] < month_end)
]

st.subheader(f"Budget progress · {month_start:%B %Y}")
if not transactions.empty:
    if st.session_state.monthly_budgets:
        for category, limit in st.session_state.monthly_budgets.items():
            spent = spending_for_budget_category(month_transactions, category)
            remaining = limit - spent
            st.markdown(f"**{category}**")
            if limit > 0:
                st.progress(min(spent / limit, 1.0))
                st.caption(
                    f"${spent:,.2f} spent of ${limit:,.2f} · "
                    + (f"${remaining:,.2f} remaining" if remaining >= 0 else f"${-remaining:,.2f} over budget")
                )
            else:
                st.caption(f"${spent:,.2f} spent; set a positive limit to track progress.")
    else:
        st.info("Add categories and monthly limits above to start tracking a budget.")
else:
    st.info("You can set budget targets now. Import transactions to see monthly progress.")

st.caption("Budgets are kept in the current Streamlit session and clear when the session ends.")
