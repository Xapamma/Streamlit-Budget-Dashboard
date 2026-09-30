"""At-a-glance summary of the current editable transaction set."""

import importlib

import pandas as pd
import streamlit as st

import dashboard_data

if getattr(dashboard_data, "CATEGORY_EDIT_API_VERSION", 0) < 3:
    importlib.reload(dashboard_data)

from dashboard_data import (
    apply_transaction_category_edits,
    category_editor_data,
    prepare_analytics_transactions,
    summarize_transactions,
    trend_totals,
)
from final_export import CATEGORY_PAIR_OPTIONS, FINAL_MAIN_CATEGORY_OPTIONS

st.title("Overview")
transactions = prepare_analytics_transactions(st.session_state.get("editable_transactions"))

if transactions.empty:
    st.info("Import a statement to see your overview.")
    st.page_link(
        st.session_state["budget_dashboard_pages"]["process"],
        label="Process bank statements",
        icon=":material/upload_file:",
    )
    st.stop()

summary = summarize_transactions(transactions)
metric_columns = st.columns(4)
metric_columns[0].metric("Transactions", f"{summary['transactions']:,}")
metric_columns[1].metric("Income", f"${summary['income']:,.2f}")
metric_columns[2].metric("Spending", f"${summary['spending']:,.2f}")
metric_columns[3].metric("Net savings", f"${summary['net_savings']:,.2f}")

left, right = st.columns([1.5, 1])
with left:
    st.subheader("Monthly trend")
    monthly = trend_totals(transactions, "M").tail(12)
    if not monthly.empty:
        st.line_chart(monthly, y=["income", "spending", "net_savings"], x_label="Month", y_label="Amount")
with right:
    st.subheader("Spending by category")
    spending = transactions.loc[transactions["amount"] < 0].copy()
    if spending.empty:
        st.caption("No expenses in the imported transactions.")
    else:
        spending["spending"] = -spending["amount"]
        category_totals = spending.groupby("main_category")["spending"].sum().sort_values(ascending=False).head(8)
        st.bar_chart(category_totals)

st.subheader("Recent transactions")
recent = transactions.sort_values("date", ascending=False).head(10).copy()
recent["date"] = recent["date"].dt.strftime("%m/%d/%Y")
if "overview_recent_editor_revision" not in st.session_state:
    st.session_state.overview_recent_editor_revision = 0
recent_editor_data = category_editor_data(
    recent,
    ["date", "merchant", "amount", "main_category", "sub_category"],
)
edited_recent = st.data_editor(
    recent_editor_data,
    key=f"overview_recent_transactions_{st.session_state.overview_recent_editor_revision}",
    num_rows="fixed",
    use_container_width=True,
    hide_index=True,
    column_config={
        "date": st.column_config.TextColumn("Date", disabled=True),
        "merchant": st.column_config.TextColumn("Merchant", disabled=True),
        "amount": st.column_config.NumberColumn("Amount", format="$%.2f", disabled=True),
        "main_category": st.column_config.SelectboxColumn(
            "Main category", options=FINAL_MAIN_CATEGORY_OPTIONS, required=True
        ),
        "sub_category": st.column_config.SelectboxColumn(
            "Sub-category (Main :: Sub)", options=CATEGORY_PAIR_OPTIONS, required=True
        ),
        "description": None,
        "_source_position": None,
    },
)
updated_transactions, categories_changed, category_mismatch = apply_transaction_category_edits(
    st.session_state.editable_transactions,
    edited_recent,
    persist_category_mappings=True,
)
if category_mismatch:
    st.error("Choose a sub-category matching its main category. Use Process statements to mark transfers.")
elif categories_changed:
    st.session_state.editable_transactions = updated_transactions
    st.session_state.overview_recent_editor_revision += 1
    if "export_editor_revision" in st.session_state:
        export_revision = st.session_state.export_editor_revision
        st.session_state.pop(f"export_editor_{export_revision}", None)
        st.session_state.export_editor_revision += 1
    st.rerun()
st.caption("Overview reflects the current session's editable export rows. Excluded transfers and declined transactions are not included.")
