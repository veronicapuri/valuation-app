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
# 📊 SMART COLUMN DETECTION
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

        # Numeric score
        numeric = pd.to_numeric(col_data.str.replace(",", ""), errors="coerce")
        numeric_score = numeric.notna().sum()

        # Text score
        text_score = col_data.str.len().mean()

        scores.append((col, numeric_score, text_score))

    # Amount = most numeric
    amount_col = max(scores, key=lambda x: x[1])[0]

    # Line item = most text but NOT amount
    line_candidates = [x for x in scores if x[0] != amount_col]
    line_col = max(line_candidates, key=lambda x: x[2])[0]

    return line_col, amount_col


def clean_dataframe(df_raw):
    header_row = detect_header(df_raw)

    df = df_raw.copy()
    df.columns = df.iloc[header_row]
    df = df[header_row + 1:].reset_index(drop=True)

    # Auto detect columns
    line_col, amount_col = detect_columns(df)

    return df, line_col, amount_col


def standardize(df, line_col, amount_col):
    df = df[[line_col, amount_col]].copy()
    df.columns = ["Line Item", "Amount"]

    # Clean line item
    df["Line Item"] = df["Line Item"].astype(str).str.strip()

    # Clean amount
    df["Amount"] = (
        df["Amount"]
        .astype(str)
        .str.replace(",", "")
        .str.replace("sgd", "", case=False)
        .str.strip()
    )

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

    return df


# ============================
# 🧾 CLASSIFICATION
# ============================

def classify(item):
    if not isinstance(item, str):
        return "Other"

    item = item.lower()

    if "revenue" in item or "income" in item or "sales" in item:
        return "Revenue"
    elif "cost" in item or "cogs" in item:
        return "COGS"
    elif "salary" in item or "rent" in item or "expense" in item:
        return "OpEx"
    else:
        return "Other"


# ============================
# 📊 UI
# ============================

st.title("📊 SME Valuation & LBO Tool")

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

    st.subheader("Raw P&L Preview")
    st.dataframe(df_raw.head())

    df, auto_line, auto_amount = clean_dataframe(df_raw)

    st.success(f"Auto-detected: Line Item = {auto_line}, Amount = {auto_amount}")

    # Manual override
    cols = list(df.columns)

    line_col = st.selectbox("Line Item Column", cols, index=cols.index(auto_line))
    amount_col = st.selectbox("Amount Column", cols, index=cols.index(auto_amount))

    if line_col == amount_col:
        st.error("Columns must be different")
        st.stop()

    df = standardize(df, line_col, amount_col)

    df["Category"] = df["Line Item"].apply(classify)

    st.subheader("Cleaned P&L")
    st.dataframe(df)

    revenue = df[df["Category"] == "Revenue"]["Amount"].sum()
    cogs = df[df["Category"] == "COGS"]["Amount"].sum()
    opex = df[df["Category"] == "OpEx"]["Amount"].sum()

    ebitda = revenue - cogs - opex

    st.subheader("📌 P&L Summary")
    st.write(f"Revenue: {revenue:,.0f}")
    st.write(f"EBITDA: {ebitda:,.0f}")


# ============================
# 📊 PROCESS BALANCE SHEET
# ============================

if bs_file:
    df_raw_bs = pd.read_excel(bs_file, header=None)

    st.subheader("Balance Sheet Preview")
    st.dataframe(df_raw_bs.head())

    df_bs, auto_line_bs, auto_amount_bs = clean_dataframe(df_raw_bs)

    st.success(f"Auto-detected: Line Item = {auto_line_bs}, Amount = {auto_amount_bs}")

    cols_bs = list(df_bs.columns)

    line_col_bs = st.selectbox("BS Line Item", cols_bs, index=cols_bs.index(auto_line_bs))
    amount_col_bs = st.selectbox("BS Amount", cols_bs, index=cols_bs.index(auto_amount_bs))

    if line_col_bs == amount_col_bs:
        st.error("Columns must be different")
        st.stop()

    df_bs = standardize(df_bs, line_col_bs, amount_col_bs)

    st.subheader("Balance Sheet")
    st.dataframe(df_bs)

    cash = df_bs[df_bs["Line Item"].str.contains("cash", case=False)]["Amount"].sum()
    debt = df_bs[df_bs["Line Item"].str.contains("debt|loan", case=False)]["Amount"].sum()

    st.subheader("📌 Balance Sheet Summary")
    st.write(f"Cash: {cash:,.0f}")
    st.write(f"Debt: {debt:,.0f}")
    st.write(f"Net Debt: {debt - cash:,.0f}")
