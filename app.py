import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(layout="wide")

# ============================
# 🔐 PASSWORD (FROM SECRETS)
# ============================

def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.markdown("## 🔐 Secure Access")

        pwd = st.text_input("Enter Password", type="password")

        if pwd == st.secrets["APP_PASSWORD"]:
            st.session_state.authenticated = True
            st.rerun()
        elif pwd:
            st.error("Incorrect password")

        st.stop()

check_password()

# ============================
# 📊 SMART DETECTION ENGINE
# ============================

def detect_header(df):
    for i in range(min(15, len(df))):
        row = df.iloc[i].fillna("").astype(str).str.lower()
        text = " ".join(row.values)

        if ("line" in text or "description" in text or "account" in text) and \
           ("amount" in text or "value" in text or "total" in text):
            return i

    return 0


def detect_columns(df):
    scores = []

    for col in df.columns:
        col_data = df[col].astype(str)

        numeric = pd.to_numeric(col_data.str.replace(",", ""), errors="coerce")
        numeric_score = numeric.notna().sum()

        text_score = col_data.str.len().mean()

        scores.append((col, numeric_score, text_score))

    amount_col = max(scores, key=lambda x: x[1])[0]
    line_col = max([x for x in scores if x[0] != amount_col], key=lambda x: x[2])[0]

    return line_col, amount_col


def clean_dataframe(df_raw):
    header_row = detect_header(df_raw)

    df = df_raw.copy()
    df.columns = df.iloc[header_row]
    df = df[header_row + 1:].reset_index(drop=True)

    line_col, amount_col = detect_columns(df)

    return df, line_col, amount_col


def standardize(df, line_col, amount_col):
    df = df[[line_col, amount_col]].copy()
    df.columns = ["Line Item", "Amount"]

    df["Line Item"] = df["Line Item"].astype(str).str.strip()

    df["Amount"] = (
        df["Amount"]
        .astype(str)
        .str.replace(",", "")
        .str.replace("sgd", "", case=False)
        .str.strip()
    )

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

    return df


def classify(item):
    if not isinstance(item, str):
        return "Other"

    item = item.lower()

    if "revenue" in item or "income" in item:
        return "Revenue"
    elif "cost" in item or "cogs" in item:
        return "COGS"
    elif "salary" in item or "rent" in item or "expense" in item:
        return "OpEx"
    else:
        return "Other"


# ============================
# 🎯 UI LAYOUT
# ============================

st.title("📊 SME Valuation & LBO Tool")

# Sidebar assumptions
st.sidebar.header("Deal Assumptions")

entry_multiple = st.sidebar.number_input("Entry Multiple", value=5.0)
exit_multiple = st.sidebar.number_input("Exit Multiple", value=6.5)
holding_years = st.sidebar.slider("Holding Period", 1, 7, 3)

growth_rate = st.sidebar.slider("Revenue Growth (%)", 0, 50, 10) / 100
target_margin = st.sidebar.slider("Target EBITDA Margin (%)", 0, 50, 20) / 100

adjustments = st.sidebar.number_input("EBITDA Adjustments", value=0.0)

# File upload
col1, col2 = st.columns(2)

with col1:
    pl_file = st.file_uploader("Upload P&L", type=["xlsx"])

with col2:
    bs_file = st.file_uploader("Upload Balance Sheet", type=["xlsx"])

# ============================
# 📊 PROCESS P&L
# ============================

if pl_file:
    df_raw = pd.read_excel(pl_file, header=None)

    df, auto_line, auto_amount = clean_dataframe(df_raw)

    cols = list(df.columns)

    line_col = st.selectbox("Line Item Column", cols, index=cols.index(auto_line))
    amount_col = st.selectbox("Amount Column", cols, index=cols.index(auto_amount))

    df = standardize(df, line_col, amount_col)
    df["Category"] = df["Line Item"].apply(classify)

    revenue = df[df["Category"] == "Revenue"]["Amount"].sum()
    cogs = df[df["Category"] == "COGS"]["Amount"].sum()
    opex = df[df["Category"] == "OpEx"]["Amount"].sum()

    ebitda = revenue - cogs - opex + adjustments

    st.subheader("📌 P&L Summary")
    st.write(f"Revenue: {revenue:,.0f}")
    st.write(f"EBITDA: {ebitda:,.0f}")

# ============================
# 📊 PROCESS BS
# ============================

net_debt = 0

if bs_file:
    df_raw_bs = pd.read_excel(bs_file, header=None)

    df_bs, auto_line_bs, auto_amount_bs = clean_dataframe(df_raw_bs)

    cols_bs = list(df_bs.columns)

    line_col_bs = st.selectbox("BS Line", cols_bs, index=cols_bs.index(auto_line_bs))
    amount_col_bs = st.selectbox("BS Amount", cols_bs, index=cols_bs.index(auto_amount_bs))

    df_bs = standardize(df_bs, line_col_bs, amount_col_bs)

    cash = df_bs[df_bs["Line Item"].str.contains("cash", case=False)]["Amount"].sum()
    debt = df_bs[df_bs["Line Item"].str.contains("debt|loan", case=False)]["Amount"].sum()

    net_debt = debt - cash

    st.subheader("📌 Balance Sheet")
    st.write(f"Cash: {cash:,.0f}")
    st.write(f"Debt: {debt:,.0f}")
    st.write(f"Net Debt: {net_debt:,.0f}")

# ============================
# 📈 FORECAST
# ============================

if pl_file:
    forecast_revenue = revenue * ((1 + growth_rate) ** holding_years)
    forecast_ebitda = forecast_revenue * target_margin

    st.subheader("📈 Forecast")
    st.write(f"Forecast Revenue: {forecast_revenue:,.0f}")
    st.write(f"Forecast EBITDA: {forecast_ebitda:,.0f}")

# ============================
# 💰 VALUATION
# ============================

if pl_file:
    entry_ev = ebitda * entry_multiple
    exit_ev = forecast_ebitda * exit_multiple

    entry_equity = entry_ev - net_debt
    exit_equity = exit_ev - net_debt

    st.subheader("💰 Valuation")

    col1, col2 = st.columns(2)

    with col1:
        st.metric("Entry EV", f"{entry_ev:,.0f}")
        st.metric("Entry Equity", f"{entry_equity:,.0f}")

    with col2:
        st.metric("Exit EV", f"{exit_ev:,.0f}")
        st.metric("Exit Equity", f"{exit_equity:,.0f}")

# ============================
# 📊 RETURNS
# ============================

if pl_file:
    moic = exit_equity / entry_equity if entry_equity != 0 else 0
    irr = (moic ** (1 / holding_years)) - 1 if holding_years > 0 else 0

    st.subheader("📊 Returns")
    st.write(f"MOIC: {moic:.2f}x")
    st.write(f"IRR: {irr*100:.2f}%")
