"""Date-range analytics for income, spending, and net savings."""

from datetime import date

import pandas as pd
import streamlit as st

from dashboard_data import prepare_analytics_transactions, summarize_transactions, trend_totals

st.title("Analytics")
transactions = prepare_analytics_transactions(st.session_state.get("editable_transactions"))

if transactions.empty:
    st.info("Import a statement to explore spending and savings trends.")
    st.page_link(
        st.session_state["budget_dashboard_pages"]["process"],
        label="Process bank statements",
        icon=":material/upload_file:",
    )
    st.stop()

period_mode = st.radio(
    "Date range",
    ["Monthly", "Year to date", "Year", "Custom"],
    horizontal=True,
    key="analytics_period_mode",
)
available_months = sorted(transactions["date"].dt.to_period("M").unique())
available_years = sorted(transactions["date"].dt.year.unique().tolist())

if period_mode == "Monthly":
    selected_month = st.selectbox(
        "Month",
        available_months,
        index=len(available_months) - 1,
        format_func=lambda period: period.strftime("%B %Y"),
    )
    start_date = selected_month.start_time.normalize()
    end_date = (selected_month + 1).start_time.normalize() - pd.Timedelta(days=1)
elif period_mode in {"Year to date", "Year"}:
    selected_year = st.selectbox(
        "Year",
        available_years,
        index=len(available_years) - 1,
    )
    start_date = pd.Timestamp(year=int(selected_year), month=1, day=1)
    year_rows = transactions.loc[transactions["date"].dt.year == selected_year, "date"]
    if period_mode == "Year to date":
        latest_available = year_rows.max().normalize()
        current_date = pd.Timestamp(date.today())
        end_date = min(latest_available, current_date) if selected_year == date.today().year else latest_available
        st.caption(f"Year to date through {end_date:%B %d, %Y}, the latest available date in this selection.")
    else:
        end_date = pd.Timestamp(year=int(selected_year), month=12, day=31)
else:
    default_start = transactions["date"].min().date()
    default_end = transactions["date"].max().date()
    date_range = st.date_input("Custom date range", value=(default_start, default_end))
    if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        start_date, end_date = (pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1]))
    else:
        start_date = end_date = pd.Timestamp(date_range)

if start_date > end_date:
    st.error("The start date must be on or before the end date.")
    st.stop()

selected = transactions.loc[
    (transactions["date"] >= start_date)
    & (transactions["date"] < end_date + pd.Timedelta(days=1))
].copy()

if selected.empty:
    st.info("There are no transactions in this date range.")
    st.stop()

summary = summarize_transactions(selected)
savings_rate = summary["net_savings"] / summary["income"] if summary["income"] else None
metric_columns = st.columns(4)
metric_columns[0].metric("Income", f"${summary['income']:,.2f}")
metric_columns[1].metric("Spending", f"${summary['spending']:,.2f}")
metric_columns[2].metric("Net savings", f"${summary['net_savings']:,.2f}")
metric_columns[3].metric("Savings rate", f"{savings_rate:.1%}" if savings_rate is not None else "N/A")

interval_options = {"Daily": "D", "Weekly": "W", "Monthly": "M"}
default_interval = "Daily" if period_mode == "Monthly" else "Monthly"
interval = st.selectbox(
    "Trend interval",
    list(interval_options),
    index=list(interval_options).index(default_interval),
)
trend = trend_totals(selected, interval_options[interval])
st.line_chart(trend, y=["income", "spending", "net_savings"], x_label=interval, y_label="Amount")

spending = selected.loc[selected["amount"] < 0].copy()
if not spending.empty:
    spending["spending"] = -spending["amount"]
    category_totals = spending.groupby(["main_category", "sub_category"])["spending"].sum().sort_values(ascending=False)
    category_totals.index = [f"{main} :: {sub}" for main, sub in category_totals.index]
    st.subheader("Spending by category")
    st.bar_chart(category_totals)

st.caption("Spending is displayed as positive outflow; net savings is income plus signed expenses. Transfers removed by the import rules are not included.")
