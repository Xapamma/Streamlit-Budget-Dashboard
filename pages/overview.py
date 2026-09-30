"""At-a-glance summary of the current editable transaction set."""

import pandas as pd
import streamlit as st

from dashboard_data import prepare_analytics_transactions, summarize_transactions, trend_totals

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
st.dataframe(
    recent[["date", "merchant", "amount", "main_category", "sub_category"]],
    use_container_width=True,
    hide_index=True,
)
st.caption("Overview reflects the current session's editable export rows. Excluded transfers and declined transactions are not included.")
