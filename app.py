# SME VALUATION & LBO TOOL — PHASE 1 (IMPROVED PARSING)

import streamlit as st
import pandas as pd
import numpy as np
import re

st.set_page_config(layout="wide", page_title="SME Deal Tool", page_icon="📊")

# =========================
# FILE INGESTION
# =========================
def read_file(file):
    name = file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(file, header=None)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(file, header=None)
    return None

# =========================
# PARSING HELPERS
# =========================
AMOUNT_RE = re.compile(r"(\(?-?[\d,]+(?:\.\d+)?\)?)")

def parse_amount(x):
    try:
        x = str(x).replace(",", "").replace("(", "-").replace(")", "")
        return float(re.sub(r"[^0-9.-]", "", x))
    except:
        return 0

# Extract label + amount from messy single column

def extract_line(line):
    line = str(line).strip()
    if not line:
        return None

    matches = list(AMOUNT_RE.finditer(line))
    if not matches:
        return None

    # take last number as amount
    match = matches[-1]
    amount_raw = match.group()
    amount = parse_amount(amount_raw)

    label = line[:match.start()].strip()

    if not label:
        return None

    return label, amount

# =========================
# SMART CLEAN (UPGRADED)
# =========================
def smart_clean(df):
    df = df.dropna(how="all").fillna("")
    df.columns = [f"c{i}" for i in range(len(df.columns))]

    # CASE 1: single column → parse lines
    if len(df.columns) == 1:
        rows = []
        for val in df.iloc[:,0]:
            parsed = extract_line(val)
            if parsed:
                rows.append(parsed)

        return pd.DataFrame(rows, columns=["Line Item","Amount"])

    # CASE 2: multi-column → detect best numeric column
    best_col = None
    best_score = -1

    for col in df.columns:
        vals = df[col].apply(parse_amount)
        score = (vals != 0).sum()
        if score > best_score:
            best_score = score
            best_col = col

    other_cols = [c for c in df.columns if c != best_col]
    label_col = other_cols[0] if other_cols else df.columns[0]

    result = pd.DataFrame({
        "Line Item": df[label_col].astype(str),
        "Amount": df[best_col].apply(parse_amount)
    })

    result = result[result["Line Item"].str.strip() != ""]
    return result.reset_index(drop=True)

# =========================
# CLASSIFICATION
# =========================
PL_CATEGORIES = ["Revenue","COGS","OpEx","D&A","Other Income","Interest","Tax","Ignore"]

def classify_simple(label):
    x = label.lower()

    if any(k in x for k in ["revenue","sales","turnover","income"]):
        return "Revenue"

    if any(k in x for k in ["cost of","cogs","direct cost","subcontract"]):
        return "COGS"

    if any(k in x for k in ["depreci","amort"]):
        return "D&A"

    if any(k in x for k in ["interest","finance"]):
        return "Interest"

    if any(k in x for k in ["tax"]):
        return "Tax"

    if any(k in x for k in ["other income","grant","gain"]):
        return "Other Income"

    return "OpEx"

# =========================
# METRICS
# =========================
def compute_metrics(df, addbacks=0):
    def s(cat): return df[df["Category"]==cat]["Amount"].sum()

    rev = s("Revenue")
    cogs = s("COGS")
    opex = s("OpEx")

    ebitda = rev - cogs - (opex - addbacks)

    return {
        "Revenue": rev,
        "EBITDA": ebitda,
        "EBITDA Margin": ebitda/rev if rev else 0
    }

# =========================
# UI
# =========================
st.title("📊 SME Deal Tool — Improved Parsing")

file = st.file_uploader("Upload P&L", type=["xlsx","xls","csv"])

if file:
    raw = read_file(file)
    df = smart_clean(raw)

    st.subheader("DEBUG — Parsed Data")
    st.dataframe(df)

    df["Category"] = df["Line Item"].apply(classify_simple)

    st.subheader("Edit Classification")
    df = st.data_editor(df, use_container_width=True)

    st.subheader("Category Summary")
    st.write(df.groupby("Category")["Amount"].sum())

    # Normalisation
    owner = st.number_input("Owner adj", 0)
    oneoff = st.number_input("One-off", 0)
    personal = st.number_input("Personal", 0)

    addbacks = owner + oneoff + personal

    metrics = compute_metrics(df, addbacks)

    st.metric("Revenue", f"${metrics['Revenue']:,.0f}")
    st.metric("EBITDA", f"${metrics['EBITDA']:,.0f}")
    st.metric("Margin", f"{metrics['EBITDA Margin']*100:.1f}%")
