import streamlit as st
import pandas as pd
import numpy as np
import re
import pdfplumber
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
        if pwd == st.secrets["APP_PASSWORD"]:
            st.session_state.auth = True
            st.rerun()
        elif pwd:
            st.error("Incorrect password")
        st.stop()

check_password()

# ============================
# 🔐 SETTINGS
# ============================
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
confidential_mode = st.sidebar.checkbox("🔐 Confidential Mode", value=True)
use_ai = st.sidebar.checkbox("Use AI Classification", value=True)

# ============================
# 🧼 SANITIZATION
# ============================
def sanitize(item):
    item = str(item)
    item = re.sub(r'\d+', '', item)
    item = re.sub(r'[^a-zA-Z\s]', '', item)
    return item.lower().strip()

# ============================
# 📂 LOAD FILE
# ============================
def load_file(file):
    if file.name.endswith(".pdf"):
        return parse_pdf(file)
    return pd.read_excel(file, header=None)

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
    best_row = 0
    best_score = 0

    for i in range(min(15, len(df))):
        row = df.iloc[i].fillna("").astype(str).str.lower()

        score = sum([
            "amount" in " ".join(row.values),
            "line item" in " ".join(row.values),
            "description" in " ".join(row.values),
        ])

        if score > best_score:
            best_score = score
            best_row = i

    return best_row

raw.columns = (
    raw.columns
    .astype(str)
    .str.strip()
    .str.replace("\n", " ")
)

def detect_columns(df):

    df.columns = df.columns.astype(str).str.strip()

    scores = []

    for col in df.columns:
        series = df[col].astype(str)

        numeric_score = pd.to_numeric(series, errors="coerce").notna().sum()
        text_score = series.str.len().mean()

        scores.append((col, numeric_score, text_score))

    # best numeric column = amount
    amount_col = max(scores, key=lambda x: x[1])[0]

    # best text column = line item
    text_candidates = [x for x in scores if x[0] != amount_col]
    line_col = max(text_candidates, key=lambda x: x[2])[0]

    return line_col, amount_col
    
def standardize(df, line_col, amount_col):

    df.columns = df.columns.astype(str).str.strip()

    # 🔐 SAFETY CHECK
    if line_col not in df.columns or amount_col not in df.columns:
        st.error("Column detection failed. Please select columns manually.")
        st.write("Detected columns:", df.columns.tolist())
        st.stop()

    df = df[[line_col, amount_col]].copy()

    df.columns = ["Line Item", "Amount"]

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

    return df

if line_col not in df.columns or amount_col not in df.columns:

    st.warning("Auto-detect failed. Please select manually.")

    line_col = st.selectbox("Select Line Item Column", df.columns)
    amount_col = st.selectbox("Select Amount Column", df.columns)

    df = df[[line_col, amount_col]].copy()

# ============================
# 🧠 RULE CLASSIFIER
# ============================
def classify_rule(item):
    item = str(item).lower()
    if "revenue" in item or "sales" in item:
        return "Revenue"
    if "cost" in item:
        return "COGS"
    if "rent" in item or "salary" in item or "expense" in item:
        return "OpEx"
    return "Other"

# ============================
# 🤖 BATCH AI
# ============================
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

# ============================
# ⚡ HYBRID CLASSIFICATION
# ============================
def classify_df(df):
    df["Category"] = df["Line Item"].apply(classify_rule)

    if use_ai and not confidential_mode:
        unknown = df[df["Category"] == "Other"]["Line Item"].unique().tolist()

        if unknown:
            ai_map = batch_ai(unknown)
            df["Category"] = df.apply(
                lambda x: ai_map.get(x["Line Item"], x["Category"]), axis=1
            )

    return df

# ============================
# 📊 BUILD MODELS
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
# 📊 UI
# ============================
st.title("📊 Enterprise Valuation Tool")

pl_file = st.file_uploader("Upload P&L")
bs_file = st.file_uploader("Upload Balance Sheet")

# ============================
# ⚙️ ASSUMPTIONS
# ============================
entry_mult = st.sidebar.number_input("Entry Multiple", 5.0)
exit_mult = st.sidebar.number_input("Exit Multiple", 6.5)
growth = st.sidebar.slider("Growth %", 0, 50, 10)/100
margin = st.sidebar.slider("EBITDA Margin %", 0, 50, 20)/100
years = st.sidebar.slider("Years", 1, 7, 5)

# ============================
# 📊 PROCESS
# ============================
if pl_file:
    raw = load_file(pl_file)

    if "Line Item" not in raw.columns:
        h = detect_header(raw)
        raw.columns = raw.iloc[h]
        raw = raw[h+1:]

    line, amt = detect_columns(raw)
    df = standardize(raw, line, amt)

    df = classify_df(df)

    st.subheader("Cleaned P&L")
    st.dataframe(df)

    revenue, ebitda = build_pl(df)

    st.write("Revenue:", revenue)
    st.write("EBITDA:", ebitda)

if bs_file:
    raw = load_file(bs_file)

    if "Line Item" not in raw.columns:
        h = detect_header(raw)
        raw.columns = raw.iloc[h]
        raw = raw[h+1:]

    line, amt = detect_columns(raw)
    df_bs = standardize(raw, line, amt)

    st.subheader("Cleaned BS")
    st.dataframe(df_bs)

    cash, debt, net_debt = build_bs(df_bs)

    st.write("Net Debt:", net_debt)

# ============================
# 📈 FORECAST
# ============================
if pl_file:
    rows = []
    rev = revenue

    for y in range(1, years+1):
        rev *= (1+growth)
        ebit = rev * margin
        rows.append([y, rev, ebit])

    f = pd.DataFrame(rows, columns=["Year","Revenue","EBITDA"])
    st.subheader("5-Year Forecast")
    st.dataframe(f)

    exit_ebitda = f.iloc[-1]["EBITDA"]

# ============================
# 💰 VALUATION
# ============================
if pl_file:
    entry_ev = ebitda * entry_mult
    exit_ev = exit_ebitda * exit_mult

    entry_eq = entry_ev - net_debt
    exit_eq = exit_ev - net_debt

    st.subheader("Valuation")
    st.metric("Entry Equity", entry_eq)
    st.metric("Exit Equity", exit_eq)

    moic = exit_eq / entry_eq if entry_eq else 0
    irr = moic**(1/years)-1 if years else 0

    st.subheader("Returns")
    st.write("MOIC:", round(moic,2))
    st.write("IRR:", round(irr*100,2), "%")
