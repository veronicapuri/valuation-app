import streamlit as st
import pandas as pd
import numpy as np
import re

# Optional PDF support
try:
    import pdfplumber
except:
    pdfplumber = None

from openai import OpenAI

st.set_page_config(layout="wide")

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

# ============================
# 🔐 API SETUP (SAFE)
# ============================
api_key = st.secrets.get("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if api_key else None

# ============================
# 🧼 HELPERS
# ============================
def sanitize(x):
    return str(x).lower().strip()

def safe_read(file):
    try:
        if file.name.endswith(".pdf") and pdfplumber:
            return parse_pdf(file)
        return pd.read_excel(file, header=None)
    except:
        st.error("File could not be read")
        return None

def parse_pdf(file):
    rows = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            for line in text.split("\n"):
                parts = line.split()
                if len(parts) < 2:
                    continue
                try:
                    amt = float(parts[-1].replace(",", ""))
                    item = " ".join(parts[:-1])
                    rows.append([item, amt])
                except:
                    continue
    return pd.DataFrame(rows, columns=["Line Item", "Amount"])

# ============================
# 🔍 DETECTION
# ============================
def detect_header(df):
    for i in range(min(10, len(df))):
        row = df.iloc[i].fillna("").astype(str).str.lower()
        text = " ".join(row.values)

        if "amount" in text or "value" in text:
            return i
    return 0

def detect_columns(df):
    df.columns = df.columns.astype(str).str.strip()

    scores = []
    for col in df.columns:
        s = df[col].astype(str)
        num_score = pd.to_numeric(s, errors="coerce").notna().sum()
        text_score = s.str.len().mean()

        scores.append((col, num_score, text_score))

    amount_col = max(scores, key=lambda x: x[1])[0]
    line_col = max([x for x in scores if x[0] != amount_col], key=lambda x: x[2])[0]

    return line_col, amount_col

def standardize(df, line_col, amount_col):

    df.columns = df.columns.astype(str).str.strip()

    if line_col not in df.columns or amount_col not in df.columns:
        st.warning("Auto-detect failed. Please select manually")

        line_col = st.selectbox("Line Item Column", df.columns)
        amount_col = st.selectbox("Amount Column", df.columns)

    df = df[[line_col, amount_col]].copy()
    df.columns = ["Line Item", "Amount"]

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

    return df

# ============================
# 🧠 CLASSIFICATION
# ============================
def classify(item):
    item = sanitize(item)

    if "revenue" in item or "sales" in item:
        return "Revenue"
    if "cost" in item:
        return "COGS"
    if "salary" in item or "rent" in item or "expense" in item:
        return "OpEx"

    return "Other"

# ============================
# 📊 MODEL
# ============================
def build_pl(df):
    revenue = df[df.Category=="Revenue"]["Amount"].sum()
    cogs = df[df.Category=="COGS"]["Amount"].sum()
    opex = df[df.Category=="OpEx"]["Amount"].sum()

    ebitda = revenue - cogs - opex

    return revenue, ebitda

def build_bs(df):
    cash = df[df["Line Item"].str.contains("cash", case=False)]["Amount"].sum()
    debt = df[df["Line Item"].str.contains("debt|loan", case=False)]["Amount"].sum()

    return cash, debt, debt - cash

# ============================
# UI
# ============================
st.title("📊 Valuation Tool")

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
# PROCESS P&L
# ============================
if pl_file:

    raw = safe_read(pl_file)
    if raw is None:
        st.stop()

    if "Line Item" not in raw.columns:
        h = detect_header(raw)
        raw.columns = raw.iloc[h]
        raw = raw[h+1:]

    raw.columns = raw.columns.astype(str).str.strip()

    line_col, amt_col = detect_columns(raw)
    df = standardize(raw, line_col, amt_col)

    df["Category"] = df["Line Item"].apply(classify)

    st.subheader("Cleaned P&L")
    st.dataframe(df)

    revenue, ebitda = build_pl(df)

    st.metric("Revenue", f"{revenue:,.0f}")
    st.metric("EBITDA", f"{ebitda:,.0f}")

# ============================
# PROCESS BS
# ============================
if bs_file:

    raw_bs = safe_read(bs_file)
    if raw_bs is None:
        st.stop()

    if "Line Item" not in raw_bs.columns:
        h = detect_header(raw_bs)
        raw_bs.columns = raw_bs.iloc[h]
        raw_bs = raw_bs[h+1:]

    raw_bs.columns = raw_bs.columns.astype(str).str.strip()

    line_col, amt_col = detect_columns(raw_bs)
    df_bs = standardize(raw_bs, line_col, amt_col)

    st.subheader("Balance Sheet")
    st.dataframe(df_bs)

    cash, debt, net_debt = build_bs(df_bs)

    st.metric("Net Debt", f"{net_debt:,.0f}")

# ============================
# FORECAST + VALUATION
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
