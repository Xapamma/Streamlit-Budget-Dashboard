"""Date-range analytics for income, spending, and net savings."""

import importlib
from datetime import date

import pandas as pd
import streamlit as st
import altair as alt

import dashboard_data

if getattr(dashboard_data, "CATEGORY_EDIT_API_VERSION", 0) < 3:
    importlib.reload(dashboard_data)

from dashboard_data import (
    apply_transaction_category_edits,
    analytics_transactions_csv,
    category_editor_data,
    filter_transactions_by_category,
    prepare_analytics_transactions,
    spending_by_main_category,
    summarize_transactions,
    transactions_for_category_review,
    trend_totals,
)
from final_export import CATEGORY_PAIR_OPTIONS, FINAL_MAIN_CATEGORY_OPTIONS
from fuzzy_search import fuzzy_match_indices

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

category_totals = spending_by_main_category(selected)
st.subheader("Spending by main category")
selected_main = None
if category_totals.empty:
    st.info("No spending categories have a positive total in this date range.")
else:
    main_category_selection = alt.selection_point(
        name="main_category_click",
        fields=["main_category"],
        on="click",
        clear="dblclick",
    )
    main_category_chart = (
        alt.Chart(category_totals)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            x=alt.X("spending:Q", title="Spending ($)", axis=alt.Axis(format="$,.0f")),
            y=alt.Y("main_category:N", title=None, sort="-x"),
            color=alt.condition(
                main_category_selection,
                alt.value("#177B68"),
                alt.value("#83B9AD"),
            ),
            tooltip=[
                alt.Tooltip("main_category:N", title="Main category"),
                alt.Tooltip("spending:Q", title="Spending", format="$,.2f"),
                alt.Tooltip("transactions:Q", title="Transactions", format=",.0f"),
            ],
        )
        .add_params(main_category_selection)
        .properties(height=max(220, 30 * len(category_totals)))
    )
    selection_state = st.altair_chart(
        main_category_chart,
        key=f"analytics_main_categories_{start_date:%Y%m%d}_{end_date:%Y%m%d}",
        on_select="rerun",
        selection_mode="main_category_click",
    )
    selection_data = getattr(selection_state, "selection", None)
    if selection_data is None and isinstance(selection_state, dict):
        selection_data = selection_state.get("selection", {})
    selected_points = selection_data.get("main_category_click", [])
    if selected_points:
        selected_main = selected_points[0].get("main_category")

if selected_main:
    st.subheader(f"{selected_main} subcategories")
    selected_main_rows = selected.loc[selected["main_category"] == selected_main]
    main_spending = selected_main_rows.loc[selected_main_rows["amount"] < 0].copy()
    main_spending["spending"] = -main_spending["amount"]
    subcategory_totals = (
        main_spending.groupby("sub_category")["spending"]
        .sum()
        .sort_values(ascending=False)
    )
    subcategory_totals = subcategory_totals.loc[subcategory_totals > 0]
    if not subcategory_totals.empty:
        st.bar_chart(subcategory_totals, x_label="Subcategory", y_label="Spending ($)")
        subcategory_detail = subcategory_totals.rename("Spending").reset_index()
        st.dataframe(subcategory_detail, use_container_width=True, hide_index=True)

st.subheader("Transactions by category")
category_review = transactions_for_category_review(selected)
main_filter_options = [
    "All main categories",
    *sorted(category_review["main_category"].dropna().unique()),
]
main_filter = st.selectbox(
    "Main category filter",
    main_filter_options,
    key="analytics_transaction_main_filter",
)
filter_main = None if main_filter == "All main categories" else main_filter
if filter_main is None:
    available_pairs = sorted(
        set(
            zip(
                category_review["main_category"].astype(str),
                category_review["sub_category"].astype(str),
            )
        )
    )
else:
    available_pairs = sorted(
        set(
            zip(
                category_review.loc[
                    category_review["main_category"] == filter_main, "main_category"
                ].astype(str),
                category_review.loc[
                    category_review["main_category"] == filter_main, "sub_category"
                ].astype(str),
            )
        )
    )
subcategory_filter_options = ["All subcategories", *[f"{main} :: {sub}" for main, sub in available_pairs]]
subcategory_filter = st.selectbox(
    "Subcategory filter",
    subcategory_filter_options,
    key="analytics_transaction_subcategory_filter",
)
if subcategory_filter == "All subcategories":
    filter_subcategory = None
else:
    pair_main_category, filter_subcategory = subcategory_filter.split(" :: ", 1)
    if filter_main is None:
        filter_main = pair_main_category
filtered_transactions = filter_transactions_by_category(
    category_review,
    main_category=filter_main,
    sub_category=filter_subcategory,
)
search_query = st.text_input(
    "Search transactions",
    placeholder="Description, merchant, category, amount...",
    key="analytics_transaction_search",
).strip()
previous_search_query = st.session_state.get("_previous_analytics_transaction_search")
if previous_search_query != search_query:
    if previous_search_query is not None:
        st.session_state.analytics_transaction_editor_revision = (
            st.session_state.get("analytics_transaction_editor_revision", 0) + 1
        )
    st.session_state._previous_analytics_transaction_search = search_query

if search_query:
    searchable_rows = [
        " ".join(row)
        for row in filtered_transactions[
            ["date", "description", "merchant", "amount", "main_category", "sub_category"]
        ].astype("string").fillna("").to_numpy(dtype=str)
    ]
    matching_indices = fuzzy_match_indices(search_query, searchable_rows)
    displayed_transactions = filtered_transactions.iloc[matching_indices].copy()
    st.caption(
        f"Showing {len(displayed_transactions):,} of {len(filtered_transactions):,} "
        "transaction(s) in this date and category selection."
    )
else:
    displayed_transactions = filtered_transactions
    st.caption(f"Showing {len(displayed_transactions):,} transaction(s) in this date and category selection.")

if displayed_transactions.empty:
    st.info("No transactions match these category filters.")
else:
    if "analytics_transaction_editor_revision" not in st.session_state:
        st.session_state.analytics_transaction_editor_revision = 0
    transaction_editor_data = category_editor_data(
        displayed_transactions,
        ["date", "description", "merchant", "amount", "main_category", "sub_category"],
    )
    transaction_editor_data["date"] = transaction_editor_data["date"].dt.strftime("%m/%d/%Y")
    edited_transactions = st.data_editor(
        transaction_editor_data,
        key=f"analytics_transactions_{st.session_state.analytics_transaction_editor_revision}",
        num_rows="fixed",
        use_container_width=True,
        hide_index=True,
        column_config={
            "date": st.column_config.TextColumn("Date", disabled=True),
            "description": st.column_config.TextColumn("Description", disabled=True),
            "merchant": st.column_config.TextColumn("Merchant", required=True),
            "amount": st.column_config.NumberColumn("Amount", format="$%.2f", disabled=True),
            "main_category": st.column_config.SelectboxColumn(
                "Main category", options=FINAL_MAIN_CATEGORY_OPTIONS, required=True
            ),
            "sub_category": st.column_config.SelectboxColumn(
                "Sub-category (Main :: Sub)", options=CATEGORY_PAIR_OPTIONS, required=True
            ),
            "_source_position": None,
        },
    )
    updated_transactions, categories_changed, category_mismatch = apply_transaction_category_edits(
        st.session_state.editable_transactions,
        edited_transactions,
        persist_category_mappings=True,
        persist_merchant_mappings=True,
    )
    if category_mismatch:
        st.error("Choose a sub-category matching its main category. Use Process statements to mark transfers.")
    elif categories_changed:
        st.session_state.editable_transactions = updated_transactions
        st.session_state.analytics_transaction_editor_revision += 1
        if "export_editor_revision" in st.session_state:
            export_revision = st.session_state.export_editor_revision
            st.session_state.pop(f"export_editor_{export_revision}", None)
            st.session_state.export_editor_revision += 1
        st.rerun()

st.caption("Spending is displayed as positive outflow; net savings is income plus signed expenses. Transfers removed by the import rules are not included.")
st.download_button(
    "Download filtered transactions",
    data=analytics_transactions_csv(displayed_transactions),
    file_name="analytics_transactions.csv",
    mime="text/csv",
    key="analytics_transactions_download",
)
