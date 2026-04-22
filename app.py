import streamlit as st
import pandas as pd
import numpy as np

# ============================
# 🔐 PASSWORD
# ============================
def check_password():
    if "auth" not in st.session_state:
        st.session_state.auth = False

    if not st.session_state.auth:
        pwd = st.text_input("Enter Password", type="password")

        if pwd == st.secrets.get("APP_PASSWORD"):
            st.session_state.auth = True
            st.rerun()
        elif pwd:
            st.error("Incorrect password")

        st.stop()

check_password()

st.set_page_config(layout="wide")

# ============================
# 📂 FILE UPLOAD
# ============================
pl_file = st.file_uploader("Upload P&L")
bs_file = st.file_uploader("Upload Balance Sheet")

# ============================
# ⚙️ ASSUMPTIONS
# ============================
growth = st.sidebar.slider("Growth %", 0, 50, 10)/100
margin = st.sidebar.slider("EBITDA Margin %", 0, 50, 20)/100
entry_mult = st.sidebar.number_input("Entry Multiple", 5.0)
exit_mult = st.sidebar.number_input("Exit Multiple", 6.5)
years = st.sidebar.slider("Years", 1, 7, 5)

# ============================
# 🔍 DETECT HEADER
# ============================
def detect_header(df):
    for i in range(min(10, len(df))):
        row = df.iloc[i].fillna("").astype(str).str.lower()
        text = " ".join(row.values)

        if "amount" in text or "value" in text:
            return i
    return 0

# ============================
# 🔍 DETECT COLUMNS
# ============================
def detect_columns(df):

    scores = []

    for col in df.columns:
        try:
            col_data = df[col]

            if not isinstance(col_data, pd.Series):
                continue

            col_data = col_data.fillna("").astype(str)

            numeric = pd.to_numeric(
                col_data.str.replace(",", "")
                        .str.replace("(", "-")
                        .str.replace(")", ""),
                errors="coerce"
            )

            numeric_score = numeric.notna().sum()
            text_score = col_data.str.len().mean()

            scores.append((col, numeric_score, text_score))

        except:
            continue

    if len(scores) < 2:
        st.error("Could not detect columns")
        st.stop()

    amount_col = max(scores, key=lambda x: x[1])[0]
    line_col = max([x for x in scores if x[0] != amount_col], key=lambda x: x[2])[0]

    return line_col, amount_col

# ============================
# 🧼 CLEAN DATAFRAME
# ============================
def clean_dataframe(df_raw):

    df = df_raw.copy()

    header_row = detect_header(df)

    df.columns = df.iloc[header_row]
    df = df[header_row + 1:].reset_index(drop=True)

    df.columns = (
        pd.Series(df.columns)
        .fillna("")
        .astype(str)
        .str.replace("\n", " ")
        .str.strip()
    )

    df.columns = [f"col_{i}" if c == "" else c for i, c in enumerate(df.columns)]

    line_col, amount_col = detect_columns(df)

    return df, line_col, amount_col

# ============================
# 🧾 STANDARDIZE
# ============================
def standardize(df, line_col, amount_col):

    df.columns = df.columns.astype(str).str.strip()

    if line_col not in df.columns or amount_col not in df.columns:
        st.warning("Auto-detect failed. Select manually")

        line_col = st.selectbox("Line Item Column", df.columns)
        amount_col = st.selectbox("Amount Column", df.columns)

    df = df[[line_col, amount_col]].copy()
    df.columns = ["Line Item", "Amount"]

    df["Line Item"] = df["Line Item"].astype(str).str.strip()

    df["Amount"] = (
        df["Amount"]
        .astype(str)
        .str.replace(",", "")
        .str.replace("(", "-")
        .str.replace(")", "")
        .str.strip()
    )

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

    return df

# ============================
# 🧠 CLASSIFY
# ============================
def classify(item):
    item = str(item).lower()

    if "revenue" in item or "sales" in item:
        return "Revenue"
    if "cost" in item:
        return "COGS"
    if "salary" in item or "rent" in item or "expense" in item:
        return "OpEx"

    return "Other"

# ============================
# 📊 BUILD P&L
# ============================
def build_pl(df):
    revenue = df[df.Category=="Revenue"]["Amount"].sum()
    cogs = df[df.Category=="COGS"]["Amount"].sum()
    opex = df[df.Category=="OpEx"]["Amount"].sum()

    ebitda = revenue - cogs - opex

    return revenue, ebitda

# ============================
# 📊 BUILD BS
# ============================
def build_bs(df):
    cash = df[df["Line Item"].str.contains("cash", case=False)]["Amount"].sum()
    debt = df[df["Line Item"].str.contains("debt|loan", case=False)]["Amount"].sum()

    return cash, debt, debt - cash

# ============================
# 🚀 PROCESS P&L
# ============================
if pl_file:

    df_raw = pd.read_excel(pl_file, header=None)

    df_clean, line_col, amt_col = clean_dataframe(df_raw)
    df = standardize(df_clean, line_col, amt_col)

    df["Category"] = df["Line Item"].apply(classify)

    st.subheader("Cleaned P&L")
    st.dataframe(df)

    revenue, ebitda = build_pl(df)

    st.metric("Revenue", f"{revenue:,.0f}")
    st.metric("EBITDA", f"{ebitda:,.0f}")

# ============================
# 🚀 PROCESS BS
# ============================
if bs_file:

    df_raw_bs = pd.read_excel(bs_file, header=None)

    df_clean_bs, line_col, amt_col = clean_dataframe(df_raw_bs)
    df_bs = standardize(df_clean_bs, line_col, amt_col)

    st.subheader("Balance Sheet")
    st.dataframe(df_bs)

    cash, debt, net_debt = build_bs(df_bs)

    st.metric("Net Debt", f"{net_debt:,.0f}")

# ============================
# 📈 FORECAST + VALUATION
# ============================
if pl_file:

    rev = revenue
    forecast = []

    for y in range(1, years+1):
        rev *= (1+growth)
        ebit = rev * margin
        forecast.append([y, rev, ebit])

    f = pd.DataFrame(forecast, columns=["Year","Revenue","EBITDA"])

    st.subheader("Forecast")
    st.dataframe(f)

    exit_ebitda = f.iloc[-1]["EBITDA"]

    entry_ev = ebitda * entry_mult
    exit_ev = exit_ebitda * exit_mult

    entry_eq = entry_ev - net_debt if bs_file else entry_ev
    exit_eq = exit_ev - net_debt if bs_file else exit_ev

    st.subheader("Valuation")
    st.metric("Entry Equity", f"{entry_eq:,.0f}")
    st.metric("Exit Equity", f"{exit_eq:,.0f}")

    moic = exit_eq / entry_eq if entry_eq else 0
    irr = moic**(1/years)-1 if years else 0

    st.subheader("Returns")
    st.write("MOIC:", round(moic,2))
    st.write("IRR:", round(irr*100,2), "%")
