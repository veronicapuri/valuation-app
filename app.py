# SME VALUATION & LBO TOOL — PHASE 1 (PRODUCTION-READY INGESTION + NORMALISATION)

import streamlit as st
import pandas as pd
import numpy as np
import re, io

st.set_page_config(layout="wide", page_title="SME Deal Tool", page_icon="📊")

# =========================
# FILE INGESTION (SIMPLIFIED + ROBUST)
# =========================
def read_file(file):
    name = file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(file, header=None)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(file, header=None)
    return None

# =========================
# CLEANING LOGIC
# =========================
def parse_amount(x):
    try:
        x = str(x).replace(",", "").replace("(", "-").replace(")", "")
        return float(re.sub(r"[^0-9.-]", "", x))
    except:
        return 0
        
def smart_clean(df):
    df = df.dropna(how="all").fillna("")
    df.columns = [f"c{i}" for i in range(len(df.columns))]

    # detect numeric column
    best_col = None
    best_score = -1

    for col in df.columns:
        vals = df[col].apply(parse_amount)
        score = (vals != 0).sum()
        if score > best_score:
            best_score = score
            best_col = col

    # 🚨 FIX: handle edge case
    other_cols = [c for c in df.columns if c != best_col]

    if not other_cols:
        # fallback: assume first column is label
        label_col = df.columns[0]
    else:
        label_col = other_cols[0]

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

    if any(k in x for k in ["cost of","cogs","direct cost","cost of sales"]):
        return "COGS"

    if any(k in x for k in ["depreci","amort"]):
        return "D&A"

    if any(k in x for k in ["interest","finance"]):
        return "Interest"

    if any(k in x for k in ["tax"]):
        return "Tax"

    if any(k in x for k in ["other income","grant","gain"]):
        return "Other Income"

    # everything else = OpEx
    return "OpEx"

# =========================
# METRICS
# =========================
def compute_metrics(df, addbacks=0):
    def s(cat): return df[df["Category"]==cat]["Amount"].sum()

    rev = s("Revenue")
    cogs = s("COGS")
    opex = s("OpEx")
    da = s("D&A")

    ebitda = rev - cogs - (opex - addbacks)

    return {
        "Revenue": rev,
        "EBITDA": ebitda,
        "EBITDA Margin": ebitda/rev if rev else 0
    }

# =========================
# UI
# =========================
st.title("📊 SME Deal Tool — Phase 1")
st.caption("Upload financials → clean → classify → normalise EBITDA")

# Upload
st.header("Step 1 — Upload P&L")
file = st.file_uploader("Upload P&L (Excel or CSV)", type=["xlsx","xls","csv"])

if file:
    raw = read_file(file)
    df = smart_clean(raw)

    # classification
    df["Category"] = df["Line Item"].apply(classify_simple)

    st.header("Step 2 — Review & Fix Classification")
    df = st.data_editor(
        df,
        column_config={
            "Category": st.column_config.SelectboxColumn(options=PL_CATEGORIES)
        },
        use_container_width=True
    )
    st.subheader("Category Summary")
    summary = df.groupby("Category")["Amount"].sum()
    st.write(summary)
    
    # =========================
    # NORMALISATION
    # =========================
    st.header("Step 3 — EBITDA Normalisation")

    col1, col2, col3 = st.columns(3)

    with col1:
        owner_adj = st.number_input("Owner salary adjustment", 0)
    with col2:
        one_off = st.number_input("One-off costs", 0)
    with col3:
        personal = st.number_input("Personal expenses", 0)

    addbacks = owner_adj + one_off + personal

    metrics = compute_metrics(df, addbacks)

    # =========================
    # OUTPUT
    # =========================
    st.header("Step 4 — Results")

    col1, col2, col3 = st.columns(3)

    col1.metric("Revenue", f"${metrics['Revenue']:,.0f}")
    col2.metric("EBITDA", f"${metrics['EBITDA']:,.0f}")
    col3.metric("EBITDA Margin", f"{metrics['EBITDA Margin']*100:.1f}%")

    st.subheader("EBITDA Bridge")
    st.write({
        "Reported EBITDA": metrics["EBITDA"] - addbacks,
        "Add-backs": addbacks,
        "Adjusted EBITDA": metrics["EBITDA"]
    })
