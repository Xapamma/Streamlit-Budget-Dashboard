"""Multipage entrypoint for the bank statement and budget dashboard."""

import streamlit as st

st.set_page_config(page_title="Budget Dashboard", page_icon="📊", layout="wide")

home_page = st.Page("pages/home.py", title="Home", icon=":material/home:", default=True)
process_page = st.Page("app.py", title="Process statements", icon=":material/upload_file:")
overview_page = st.Page("pages/overview.py", title="Overview", icon=":material/dashboard:")
analytics_page = st.Page("pages/analytics.py", title="Analytics", icon=":material/monitoring:")
budget_page = st.Page("pages/budget.py", title="Budget", icon=":material/account_balance_wallet:")

st.session_state["budget_dashboard_pages"] = {
    "home": home_page,
    "process": process_page,
    "overview": overview_page,
    "analytics": analytics_page,
    "budget": budget_page,
}

pages = [
    home_page,
    process_page,
    overview_page,
    analytics_page,
    budget_page,
]

navigation = st.navigation(pages, position="sidebar")
navigation.run()
