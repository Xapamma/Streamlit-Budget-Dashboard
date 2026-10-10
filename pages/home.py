"""Home page with the basic bank-import workflow."""

import streamlit as st

st.title("Budget Dashboard")
st.write("Bring bank statements into one consistent transaction list, review your spending, and plan monthly budgets.")

st.subheader("Getting started")
st.markdown(
    """
1. **Process statements:** Upload CSV statements, choose the bank and account, and match the columns.
2. **Review transactions:** Check dates, signs, transfers, merchants, and categories before downloading.
3. **Explore your finances:** Use Overview for a snapshot and Analytics for monthly, year-to-date, yearly, or custom trends.
4. **Set a plan:** Choose a month on Budget, enter income line by line (paychecks, dividends, refunds, and other sources), then set spending limits. Assign any remainder to savings.
    """
)

st.page_link(
    st.session_state["budget_dashboard_pages"]["process"],
    label="Process bank statements",
    icon=":material/upload_file:",
)

st.info(
    "Imported transactions and budget targets are kept in your current Streamlit session. "
    "They are cleared when that session ends; download your categorized transactions to keep a copy."
)
st.caption("AI merchant suggestions (bring your own provider key) are optional. Categorization rules, review, analytics, and budgeting work without AI.")
