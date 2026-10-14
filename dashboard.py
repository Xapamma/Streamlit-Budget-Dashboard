"""Multipage entrypoint for the bank statement and budget dashboard."""

import streamlit as st

st.set_page_config(page_title="Budget Dashboard", page_icon="📊", layout="wide")

# Custom CSS to reduce padding and let content use more viewport width
st.markdown(
    """
    <style>
        /* Reduce horizontal padding on the main content area */
        .stMainBlockContainer {
            padding-left: 2rem !important;
            padding-right: 2rem !important;
        }
        /* Ensure tables/dataframes use full available width */
        .stDataFrame, .stDataFrame > div, .stDataFrame > div > div {
            width: 100% !important;
        }
        /* Reduce column gap for more horizontal space */
        div[data-testid="column"] {
            gap: 0.5rem !important;
        }
        /* Make the main app container use full width */
        .stAppHeader, .stMain, .stApp {
            max-width: 100% !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

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
