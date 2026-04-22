import streamlit as st
import pandas as pd
import numpy as np
import re

# Optional AI
from openai import OpenAI

st.set_page_config(layout="wide")

# ============================
# 🔐 PASSWORD
# ============================
def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.markdown("## 🔐 Secure Access")
        pwd = st.text_input("Enter Password", type="password")

        if pwd == st.secrets.get("APP_PASSWORD"):
            st.session_state.authenticated = True
            st.rerun()
        elif pwd:
            st.error("Incorrect password")

        st.stop()

check_password()

# ============================
# ⚙️ SETTINGS
# ============================
use_ai = st.sidebar.checkbox("🤖 Enable AI Classification", value=False)
confidential_mode = st.sidebar.checkbox("🔐 Confidential Mode", value=True)

client = None
if "OPENAI_API_KEY" in st.secrets and use_ai and not confidential_mode:
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# ============================
# 🧼 HELPERS
# ============================
def sanitize(item):
    item = str(item)
    item = re.sub(r'\d+', '', item)
    item = re.sub(r'[^a-zA-Z\s]', '', item)
    return item.lower().strip()

def make_unique(cols):
    seen = {}
    new_cols = []
    for col in cols:
        col = str(col)
        if col in seen:
            seen[col] += 1
            new_cols.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 0
            new_cols.append(col)
    return new_cols

# ============================
# 📊 DETECTION
# ============================
def detect_header(df):
    for i in range(min(15, len(df))):
        row = df.iloc[i].fillna("").astype(str).str.lower()
        text = " ".join(row.values)
        if "amount" in text or "value" in text:
            return i
    return 0

def detect_columns(df):
    scores = []

    for col in df.columns:
        try:
            col_data = df[col]

            if isinstance(col_data, pd.DataFrame):
                col_data = col_data.iloc[:, 0]

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

def clean_dataframe(df_raw):
    header_row = detect_header(df_raw)

    df = df_raw.copy()
    df.columns = df.iloc[header_row]
    df = df[header_row + 1:].reset_index(drop=True)

    df.columns = (
        pd.Series(df.columns)
        .fillna("")
        .astype(str)
        .str.strip()
    )

    df.columns = make_unique(df.columns)

    return df, *detect_columns(df)

def standardize(df, line_col, amount_col):
    df = df[[line_col, amount_col]].copy()
    df.columns = ["Line Item", "Amount"]

    df["Amount"] = (
        df["Amount"]
        .astype(str)
        .str.replace(",", "")
        .str.replace("(", "-")
        .str.replace(")", "")
    )

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

    return df

# ============================
# 🧠 CLASSIFICATION
# ============================
def rule_classify(item):
    item = str(item).lower()

    if any(x in item for x in ["revenue","sales","income","fees"]):
        return "Revenue"

    if any(x in item for x in ["cost","cogs","direct","materials","subcontract"]):
        return "COGS"

    if any(x in item for x in [
        "salary","wage","rent","expense","admin","marketing",
        "utilities","insurance","travel","professional"
    ]):
        return "OpEx"

    return "Other"

@st.cache_data
def batch_ai(items):
    clean = [sanitize(i) for i in items]

    prompt = f"""
    Classify each into: Revenue, COGS, OpEx, Other.
    Return JSON mapping.

    {clean}
    """

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0
    )

    import json
    return json.loads(res.choices[0].message.content)

def classify_df(df):
    df["Category"] = df["Line Item"].apply(rule_classify)

    if client:
        unknown = df[df["Category"]=="Other"]["Line Item"].unique().tolist()
        if unknown:
            ai_map = batch_ai(unknown)
            df["Category"] = df.apply(
                lambda x: ai_map.get(x["Line Item"], x["Category"]), axis=1
            )

    return df

# ============================
# 🎯 UI
# ============================
st.title("📊 Investment Committee Model")
st.caption("Automated financial normalization + valuation engine")

# Sidebar
st.sidebar.header("Deal Assumptions")

entry_multiple = st.sidebar.number_input("Entry Multiple", 4.0)
exit_multiple = st.sidebar.number_input("Exit Multiple", 6.5)
holding_years = st.sidebar.slider("Holding Period", 1, 10, 5)

growth_rate = st.sidebar.slider("Revenue Growth (%)", 0, 50, 10)/100
target_margin = st.sidebar.slider("EBITDA Margin (%)", 0, 80, 20)/100

# Upload
st.header("📂 Data Ingestion")
col1, col2 = st.columns(2)

with col1:
    pl_file = st.file_uploader("Upload P&L", type=["xlsx", "csv", "pdf"])

with col2:
    bs_file = st.file_uploader("Upload Balance Sheet", type=["xlsx", "csv", "pdf"])

def load_file(file):
    name = file.name.lower()

    # Excel
    if name.endswith(".xlsx"):
        return pd.read_excel(file, header=None, engine="openpyxl")

    # CSV
    if name.endswith(".csv"):
        return pd.read_csv(file, header=None)

    # PDF
    if name.endswith(".pdf"):
    import pdfplumber

    text_data = []
    table_data = []

    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if table:
                table_data.extend(table)

            text = page.extract_text()
            if text:
                text_data.append(text)

    if table_data:
        return pd.DataFrame(table_data)

    if text_data:
        lines = "\n".join(text_data).split("\n")

        parsed = []

        for line in lines:
            parts = line.split()

            if len(parts) >= 2:
                try:
                    value = parts[-1].replace(",", "").replace("(", "-").replace(")", "")
                    value = float(value)

                    label = " ".join(parts[:-1])
                    parsed.append([label, value])
                except:
                    continue

        if parsed:
            return pd.DataFrame(parsed)

    st.error("❌ Could not extract usable data from PDF")
    st.stop()
    
# ============================
# PROCESS P&L
# ============================
revenue, ebitda = 0, 0

if pl_file:
    df_raw = load_file(pl_file)
    df, lc, ac = clean_dataframe(df_raw)
    df = standardize(df, lc, ac)
    df = classify_df(df)

    revenue = df[df.Category=="Revenue"]["Amount"].sum()
    cogs = df[df.Category=="COGS"]["Amount"].sum()
    opex = df[df.Category=="OpEx"]["Amount"].sum()

    ebitda = revenue - cogs - opex

    # sanity check
    if revenue > 0:
        margin = ebitda / revenue
        if margin > 0.6:
            st.warning("⚠️ EBITDA margin unusually high — check classification")

# ============================
# PROCESS BS
# ============================
net_debt = 0

if bs_file:
    df_raw = load_file(pl_file)
    df_bs, lc, ac = clean_dataframe(df_raw)
    df_bs = standardize(df_bs, lc, ac)

    cash = df_bs[df_bs["Line Item"].str.contains("cash|bank", case=False)]["Amount"].sum()
    debt = df_bs[df_bs["Line Item"].str.contains("debt|loan|borrow", case=False)]["Amount"].sum()

    net_debt = debt - cash

# ============================
# 📊 SNAPSHOT
# ============================
if pl_file:
    st.header("📊 Investment Snapshot")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Revenue", f"{revenue:,.0f}")
    col2.metric("EBITDA", f"{ebitda:,.0f}")
    col3.metric("Margin", f"{(ebitda/revenue*100 if revenue else 0):.1f}%")

    if net_debt < 0:
        col4.metric("Net Cash", f"{abs(net_debt):,.0f}")
    else:
        col4.metric("Net Debt", f"{net_debt:,.0f}")

# ============================
# 📈 FORECAST
# ============================
if pl_file:
    st.header("📈 Forecast")

    rev = revenue
    rows = []

    for y in range(1, holding_years+1):
        rev *= (1+growth_rate)
        ebit = rev * target_margin
        rows.append([y, rev, ebit])

    f = pd.DataFrame(rows, columns=["Year","Revenue","EBITDA"])
    st.dataframe(f)

    exit_ebitda = f.iloc[-1]["EBITDA"]

# ============================
# 💰 VALUATION
# ============================
if pl_file:
    st.header("💰 Valuation")

    entry_ev = ebitda * entry_multiple
    exit_ev = exit_ebitda * exit_multiple

    entry_eq = entry_ev - net_debt
    exit_eq = exit_ev - net_debt

    col1, col2 = st.columns(2)

    col1.metric("Entry Equity", f"{entry_eq:,.0f}")
    col2.metric("Exit Equity", f"{exit_eq:,.0f}")

# ============================
# 📊 RETURNS
# ============================
if pl_file:
    st.header("📊 Returns")

    moic = exit_eq / entry_eq if entry_eq else 0
    irr = moic**(1/holding_years)-1 if holding_years else 0

    col1, col2 = st.columns(2)
    col1.metric("MOIC", f"{moic:.2f}x")
    col2.metric("IRR", f"{irr*100:.2f}%")
