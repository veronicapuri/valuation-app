import streamlit as st
import pandas as pd
import numpy as np

# ---------------------------
# 🔐 PASSWORD (SECURE VERSION)
# ---------------------------
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Enter Password", type="password", on_change=password_entered, key="password")
        return False

    elif not st.session_state["password_correct"]:
        st.text_input("Enter Password", type="password", on_change=password_entered, key="password")
        st.error("Incorrect password")
        return False

    else:
        return True


if not check_password():
    st.stop()

st.set_page_config(layout="wide")

# =============================
# 🧠 HELPER FUNCTIONS
# =============================
def safe_str(x):
    return str(x).lower() if pd.notnull(x) else ""

def detect_columns(df):
    line_col = None
    amt_col = None

    for col in df.columns:
        col_str = str(col).lower()

        if any(x in col_str for x in ["line", "item", "description", "account"]):
            line_col = col

        if any(x in col_str for x in ["amount", "value", "total"]):
            amt_col = col

    return line_col, amt_col


def clean_df(df, line_col, amt_col):
    df = df[[line_col, amt_col]].copy()
    df.columns = ["Line Item", "Amount"]
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)
    return df


def classify(item):
    item = item.lower()

    if any(x in item for x in ["revenue", "sales", "income"]):
        return "Revenue"
    elif any(x in item for x in ["cost", "cogs", "direct"]):
        return "COGS"
    elif any(x in item for x in ["salary", "rent", "marketing", "expense", "admin"]):
        return "OpEx"
    return "Other"


# =============================
# 🎯 TITLE
# =============================
st.title("📊 SME Valuation & LBO Tool")

# =============================
# 📂 FILE UPLOAD
# =============================
col1, col2 = st.columns(2)

with col1:
    pl_file = st.file_uploader("Upload P&L", type=["xlsx"])

with col2:
    bs_file = st.file_uploader("Upload Balance Sheet", type=["xlsx"])

# =============================
# ⚙️ ASSUMPTIONS
# =============================
st.sidebar.header("Deal Assumptions")

entry_multiple = st.sidebar.number_input("Entry Multiple", value=5.0)
exit_multiple = st.sidebar.number_input("Exit Multiple", value=6.5)
holding_period = st.sidebar.slider("Holding Period", 1, 7, 3)

growth_rate = st.sidebar.slider("Revenue Growth (%)", 0, 50, 10)
target_margin = st.sidebar.slider("EBITDA Margin (%)", 0, 50, 20)
adjustments = st.sidebar.number_input("EBITDA Adjustments", value=0.0)

# LBO
st.sidebar.header("LBO Assumptions")
debt_percent = st.sidebar.slider("Debt %", 0, 80, 50)
interest_rate = st.sidebar.slider("Interest (%)", 0, 15, 8)
tax_rate = st.sidebar.slider("Tax (%)", 0, 40, 25)
capex_percent = st.sidebar.slider("Capex (%)", 0, 20, 5)
wc_percent = st.sidebar.slider("Working Capital (%)", 0, 20, 5)
debt_amort = st.sidebar.slider("Debt Repayment (%)", 0, 30, 10)

# =============================
# 📊 PROCESS P&L
# =============================
if pl_file:

    df_raw = pd.read_excel(pl_file)

    st.subheader("Raw P&L Preview")
    st.dataframe(df_raw.head())

    line_col, amt_col = detect_columns(df_raw)

    if line_col is None or amt_col is None:
        st.warning("Select columns manually")

        line_col = st.selectbox("Select Line Item Column", df_raw.columns)
        amt_col = st.selectbox("Select Amount Column", df_raw.columns)

    df = clean_df(df_raw, line_col, amt_col)
    df["Category"] = df["Line Item"].apply(classify)

    st.subheader("Cleaned P&L")
    st.dataframe(df)

    revenue = df[df["Category"] == "Revenue"]["Amount"].sum()
    cogs = df[df["Category"] == "COGS"]["Amount"].sum()
    opex = df[df["Category"] == "OpEx"]["Amount"].sum()

    ebitda = revenue - cogs - opex + adjustments
    margin = ebitda / revenue if revenue else 0

    st.subheader("P&L Summary")
    st.write(f"Revenue: {revenue:,.0f}")
    st.write(f"EBITDA: {ebitda:,.0f}")
    st.write(f"Margin: {margin:.2%}")

    # =============================
    # 📊 BALANCE SHEET
    # =============================
    cash = 0
    debt = 0

    if bs_file:
        bs_raw = pd.read_excel(bs_file)

        line_col, amt_col = detect_columns(bs_raw)

        if line_col is None or amt_col is None:
            st.warning("Select BS columns manually")
            line_col = st.selectbox("BS Line", bs_raw.columns)
            amt_col = st.selectbox("BS Amount", bs_raw.columns)

        bs = clean_df(bs_raw, line_col, amt_col)

        st.subheader("Balance Sheet")
        st.dataframe(bs)

        for _, row in bs.iterrows():
            name = row["Line Item"].lower()

            if "cash" in name:
                cash += row["Amount"]

            if "debt" in name or "loan" in name:
                debt += row["Amount"]

    net_debt = debt - cash

    # =============================
    # 📈 FORECAST
    # =============================
    forecast_revenue = revenue * (1 + growth_rate/100) ** holding_period
    forecast_ebitda = forecast_revenue * (target_margin/100)

    # =============================
    # 💰 VALUATION
    # =============================
    entry_ev = ebitda * entry_multiple
    exit_ev = forecast_ebitda * exit_multiple

    entry_equity = entry_ev - net_debt
    exit_equity = exit_ev - net_debt

    st.subheader("Valuation")

    col1, col2 = st.columns(2)
    col1.metric("Entry EV", f"{entry_ev:,.0f}")
    col2.metric("Exit EV", f"{exit_ev:,.0f}")

    col1.metric("Entry Equity", f"{entry_equity:,.0f}")
    col2.metric("Exit Equity", f"{exit_equity:,.0f}")

    # =============================
    # 🏦 LBO MODEL
    # =============================
    st.subheader("LBO Model")

    debt_used = entry_ev * (debt_percent / 100)
    equity_used = entry_ev - debt_used

    current_debt = debt_used
    current_rev = revenue

    rows = []

    for year in range(1, holding_period + 1):

        current_rev *= (1 + growth_rate/100)
        ebitda_y = current_rev * (target_margin/100)

        interest = current_debt * (interest_rate/100)
        tax = max(0, (ebitda_y - interest) * (tax_rate/100))

        capex = current_rev * (capex_percent/100)
        wc = current_rev * (wc_percent/100)

        cashflow = ebitda_y - interest - tax - capex - wc

        repay = min(current_debt, current_debt * (debt_amort/100))
        current_debt -= repay

        rows.append({
            "Year": year,
            "Revenue": current_rev,
            "EBITDA": ebitda_y,
            "Debt": current_debt,
            "Cash Flow": cashflow
        })

    lbo_df = pd.DataFrame(rows)
    st.dataframe(lbo_df)

    exit_equity_lbo = exit_ev - current_debt

    moic = exit_equity_lbo / equity_used if equity_used else 0
    irr = moic ** (1/holding_period) - 1

    st.subheader("Returns")

    col1, col2 = st.columns(2)
    col1.metric("MOIC", f"{moic:.2f}x")
    col2.metric("IRR", f"{irr:.2%}")
