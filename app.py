import streamlit as st
import pandas as pd
import numpy as np

# ============================
# 🔐 PASSWORD PROTECTION
# ============================

PASSWORD = "app password"

def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.markdown("## 🔐 Secure Access")

        pwd = st.text_input("Enter Password", type="password")

        if pwd == PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        elif pwd:
            st.error("Incorrect password")

        st.stop()

check_password()

# ============================
# ⚙️ PAGE CONFIG
# ============================

st.set_page_config(layout="wide")
st.title("📊 SME Valuation & LBO Tool")

# ============================
# 📥 FILE UPLOAD
# ============================

col1, col2 = st.columns(2)

with col1:
    pl_file = st.file_uploader("Upload P&L", type=["xlsx"])

with col2:
    bs_file = st.file_uploader("Upload Balance Sheet", type=["xlsx"])


# ============================
# 🔧 HELPER FUNCTIONS
# ============================

def load_excel(file):
    df = pd.read_excel(file, header=None)

    for i in range(len(df)):
        row = df.iloc[i].fillna("").astype(str).str.lower()
        row_text = " ".join(row.values)

        if any(x in row_text for x in ["line item", "description"]) and \
           any(x in row_text for x in ["amount", "value", "total"]):

            df.columns = df.iloc[i]
            df = df[i+1:]
            df = df.reset_index(drop=True)
            return df

    return df


def classify(item):
    if pd.isna(item):
        return "Other"

    item = str(item).lower()

    if any(x in item for x in ["revenue", "sales", "income", "fees"]):
        return "Revenue"

    elif any(x in item for x in ["cost", "cogs", "direct"]):
        return "COGS"

    elif any(x in item for x in ["rent", "salary", "marketing", "expense", "admin", "utilities"]):
        return "OpEx"

    return "Other"


def clean_amount(series):
    return pd.to_numeric(series, errors="coerce").fillna(0)


# ============================
# 📊 PROCESS P&L
# ============================

revenue = 0
cogs = 0
opex = 0
ebitda = 0

if pl_file:

    df_raw = load_excel(pl_file)

    st.subheader("Raw P&L Preview")
    st.dataframe(df_raw.head())

    columns = df_raw.columns.tolist()

    line_col = None
    amount_col = None

    for col in columns:
        col_lower = str(col).lower()

        if "line" in col_lower or "description" in col_lower:
            line_col = col

        if "amount" in col_lower or "value" in col_lower:
            amount_col = col

    if line_col is None or amount_col is None:
        st.warning("Auto-detect failed. Select columns manually")

        line_col = st.selectbox("Line Item Column", columns)
        amount_col = st.selectbox("Amount Column", columns)

    df = df_raw[[line_col, amount_col]].copy()
    df.columns = ["Line Item", "Amount"]

    df["Amount"] = clean_amount(df["Amount"])
    df["Category"] = df["Line Item"].apply(classify)

    st.subheader("Cleaned P&L")
    st.dataframe(df)

    revenue = df[df["Category"] == "Revenue"]["Amount"].sum()
    cogs = df[df["Category"] == "COGS"]["Amount"].sum()
    opex = df[df["Category"] == "OpEx"]["Amount"].sum()

    ebitda = revenue - cogs - opex
    margin = (ebitda / revenue) * 100 if revenue != 0 else 0

    st.subheader("📌 P&L Breakdown")
    st.write(f"Revenue: {revenue:,.0f}")
    st.write(f"COGS: {cogs:,.0f}")
    st.write(f"OpEx: {opex:,.0f}")
    st.write(f"EBITDA: {ebitda:,.0f}")
    st.write(f"Margin: {margin:.2f}%")


# ============================
# 🏦 BALANCE SHEET
# ============================

net_debt = 0

if bs_file:

    df_bs_raw = load_excel(bs_file)

    st.subheader("Balance Sheet Preview")
    st.dataframe(df_bs_raw.head())

    columns = df_bs_raw.columns.tolist()

    line_col_bs = st.selectbox("BS Line Item Column", columns)
    amount_col_bs = st.selectbox("BS Amount Column", columns)

    df_bs = df_bs_raw[[line_col_bs, amount_col_bs]].copy()
    df_bs.columns = ["Line Item", "Amount"]

    df_bs["Amount"] = clean_amount(df_bs["Amount"])

    st.subheader("Balance Sheet")
    st.dataframe(df_bs)

    cash = df_bs[df_bs["Line Item"].str.lower().str.contains("cash", na=False)]["Amount"].sum()
    debt = df_bs[df_bs["Line Item"].str.lower().str.contains("debt|loan", na=False)]["Amount"].sum()

    net_debt = debt - cash

    st.subheader("📌 Balance Sheet Breakdown")
    st.write(f"Cash: {cash:,.0f}")
    st.write(f"Debt: {debt:,.0f}")
    st.write(f"Net Debt: {net_debt:,.0f}")


# ============================
# ⚙️ ASSUMPTIONS
# ============================

st.sidebar.header("Deal Assumptions")

entry_multiple = st.sidebar.number_input("Entry Multiple", value=5.0)
exit_multiple = st.sidebar.number_input("Exit Multiple", value=6.5)
growth_rate = st.sidebar.slider("Revenue Growth (%)", 0, 50, 10)
margin_target = st.sidebar.slider("Target EBITDA Margin (%)", 0, 50, 20)
holding_period = st.sidebar.slider("Holding Period (Years)", 1, 7, 3)


# ============================
# 📈 FORECAST + VALUATION
# ============================

if pl_file:

    forecast_revenue = revenue * (1 + growth_rate / 100) ** holding_period
    forecast_ebitda = forecast_revenue * (margin_target / 100)

    entry_ev = ebitda * entry_multiple
    exit_ev = forecast_ebitda * exit_multiple

    entry_equity = entry_ev - net_debt
    exit_equity = exit_ev - net_debt

    moic = exit_equity / entry_equity if entry_equity != 0 else 0
    irr = (moic ** (1 / holding_period)) - 1 if moic > 0 else 0

    st.subheader("📊 Forecast")
    st.write(f"Revenue: {forecast_revenue:,.0f}")
    st.write(f"EBITDA: {forecast_ebitda:,.0f}")

    st.subheader("💰 Valuation")
    col1, col2 = st.columns(2)

    with col1:
        st.metric("Entry EV", f"{entry_ev:,.0f}")
        st.metric("Entry Equity", f"{entry_equity:,.0f}")

    with col2:
        st.metric("Exit EV", f"{exit_ev:,.0f}")
        st.metric("Exit Equity", f"{exit_equity:,.0f}")

    st.subheader("📈 Returns")
    st.metric("MOIC", f"{moic:.2f}x")
    st.metric("IRR", f"{irr*100:.2f}%")
