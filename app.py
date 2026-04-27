# =========================================
# IMPORTS
# =========================================
import streamlit as st
import pandas as pd
import numpy as np
import json, os

st.set_page_config(layout="wide")

st.title("📊 SME Valuation & LBO Tool")
st.caption("AI-powered SME valuation, classification, and LBO modeling platform")

st.markdown("---")

st.header("📂 Data Ingestion")

col1, col2 = st.columns(2)

with col1:
    pl = st.file_uploader("Upload P&L", type=["xlsx","csv","pdf"])

with col2:
    bs = st.file_uploader("Upload Balance Sheet", type=["xlsx","csv","pdf"])
    
# =========================================
# MEMORY
# =========================================
MEMORY_FILE = "memory.json"

def load_memory():
    if os.path.exists(MEMORY_FILE):
        return json.load(open(MEMORY_FILE))
    return {}

def save_memory(mem):
    json.dump(mem, open(MEMORY_FILE, "w"))

# =========================================
# SAFE INGESTION (BULLETPROOF)
# =========================================

def dedupe_columns(df):
    cols = pd.Series(df.columns)
    for dup in cols[cols.duplicated()].unique():
        idxs = cols[cols == dup].index
        for i, idx in enumerate(idxs):
            cols[idx] = f"{dup}_{i}" if i > 0 else dup
    df.columns = cols
    return df


def clean(df):
    # Use first row as header
    df.columns = df.iloc[0].astype(str)
    df = df[1:].reset_index(drop=True)

    # Drop fully empty rows
    df = df.dropna(how="all")

    return df


def standardize(df):
    # Always take first 2 columns ONLY
    df = df.iloc[:, :2]

    df.columns = ["Line Item", "Amount"]

    # Convert to string safely
    df["Line Item"] = df["Line Item"].astype(str)

    df["Amount"] = (
        df["Amount"]
        .astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("(", "-", regex=False)
        .str.replace(")", "", regex=False)
        .str.strip()
    )

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

    return df


# =========================================
# APPLY PIPELINE (ORDER MATTERS)
# =========================================

if pl:
    df = pd.read_excel(pl, header=None)

    df = clean(df)
    df = dedupe_columns(df)     # 🔥 CRITICAL — DO NOT REMOVE
    df = standardize(df)

    st.subheader("Cleaned P&L Preview")
    st.dataframe(df.head(20))

    if not isinstance(df["Amount"], pd.Series):
        st.error("🚨 Amount column is not valid — check input format")
        st.stop()

# =========================================
# CLASSIFICATION ENGINE (BULLETPROOF)
# =========================================
def normalize(x):
    return "" if pd.isna(x) else str(x).lower().strip()

def detect_row_type(x):
    x = normalize(x)

    if x == "":
        return "Empty"

    if any(k in x for k in ["pte ltd", "for the year", "as at"]):
        return "Meta"

    if any(k in x for k in ["total", "net profit", "gross profit"]):
        return "Total"

    if len(x.split()) <= 3 and any(k in x for k in ["revenue", "income", "expense"]):
        return "Header"

    return "Line"

def detect_sections(df):
    current = "Unknown"
    sections = []

    for item in df["Line Item"]:
        t = normalize(item)

        if "revenue" in t:
            current = "Revenue"
        elif "cost" in t:
            current = "COGS"
        elif "expense" in t:
            current = "OpEx"
        elif "income" in t:
            current = "Other Income"

        sections.append(current)

    df["Section"] = sections
    return df

def classify(df):
    df["Row Type"] = df["Line Item"].apply(detect_row_type)
    df = df[~df["Row Type"].isin(["Meta", "Empty"])]

    df = detect_sections(df)

    def rule(r):
        if r["Row Type"] != "Line":
            return "Ignore"

        item = normalize(r["Line Item"])
        sec = r["Section"]

        if sec == "Revenue":
            return "Revenue"
        if sec == "COGS":
            return "COGS"
        if sec == "OpEx":
            return "OpEx"

        if "depreciation" in item:
            return "D&A"

        return "Other"

    df["Category"] = df.apply(rule, axis=1)
    return df

# =========================================
# METRICS
# =========================================
def compute(df):
    r = df[df.Category == "Revenue"]["Amount"].sum()
    c = df[df.Category == "COGS"]["Amount"].sum()
    o = df[df.Category == "OpEx"]["Amount"].sum()
    oi = df[df.Category == "Other Income"]["Amount"].sum()

    ebitda = r - c - o + oi
    margin = ebitda / r if r else 0

    return r, c, o, oi, ebitda, margin

# =========================================
# UI
# =========================================
st.sidebar.header("Deal")

entry_multiple = st.sidebar.number_input("Entry Multiple", 4.0)
exit_multiple = st.sidebar.number_input("Exit Multiple", 7.0)
years = st.sidebar.slider("Holding Period", 1, 7, 5)

growth = st.sidebar.slider("Revenue Growth %", 0, 30, 10) / 100

st.sidebar.subheader("Margins")
margins = [st.sidebar.slider(f"Y{i+1}", 0, 80, 20) / 100 for i in range(years)]

st.sidebar.subheader("Capital Structure")
tlb_rate = st.sidebar.slider("TLB Rate", 0, 15, 7) / 100
rev_rate = st.sidebar.slider("Revolver Rate", 0, 15, 6) / 100
min_cash = st.sidebar.number_input("Min Cash", 50000)

# =========================================
# FILE UPLOAD
# =========================================
if pl:
    df = pd.read_excel(pl)
    df = standardize(clean(df))
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

    # CLASSIFY
    df = classify(df)

    # MEMORY
    mem = load_memory()
    df["Category"] = df.apply(
        lambda x: mem.get(x["Line Item"], x["Category"]) if x["Category"] == "Other" else x["Category"],
        axis=1
    )

    # DEBUG
    st.subheader("DEBUG CLASSIFICATION")
    st.dataframe(df)

    # MANUAL FIX
    df = st.data_editor(df)
    save_memory({r["Line Item"]: r["Category"] for _, r in df.iterrows()})

    # METRICS
    revenue, cogs, opex, other, ebitda, margin = compute(df)

    st.header("Snapshot")
    st.write(revenue, ebitda, margin)

    # =========================================
    # FORECAST
    # =========================================
    st.header("Forecast")

    rev = revenue
    f = []

    for i in range(years):
        rev *= (1 + growth)
        e = rev * margins[i]
        f.append([i+1, rev, e])

    fdf = pd.DataFrame(f, columns=["Year", "Revenue", "EBITDA"])
    st.dataframe(fdf)

    # =========================================
    # LBO
    # =========================================
    st.header("LBO")

    entry_ev = ebitda * entry_multiple
    debt = entry_ev * 0.6
    tlb = debt * 0.85
    revolver = debt * 0.15
    equity = entry_ev - debt

    cash = min_cash
    prev_rev = revenue

    rows = []

    for i in range(years):
        rev = fdf.iloc[i]["Revenue"]
        ebitda_y = fdf.iloc[i]["EBITDA"]

        interest = tlb * tlb_rate + revolver * rev_rate
        tax = max(0, (ebitda_y - interest) * 0.25)

        delta_nwc = (rev - prev_rev) * 0.05
        capex = rev * 0.05

        fcf = ebitda_y - interest - tax - capex - delta_nwc

        prev_rev = rev

        cash += fcf

        if cash < min_cash:
            draw = min_cash - cash
            revolver += draw
            cash += draw

        excess = max(0, cash - min_cash)

        pay_rev = min(revolver, excess)
        revolver -= pay_rev
        cash -= pay_rev

        excess = max(0, cash - min_cash)
        pay_tlb = min(tlb, excess)
        tlb -= pay_tlb
        cash -= pay_tlb

        rows.append([i+1, rev, ebitda_y, fcf, tlb, revolver])

    lbo = pd.DataFrame(rows, columns=["Year","Revenue","EBITDA","FCF","TLB","Revolver"])
    st.dataframe(lbo)

    # =========================================
    # RETURNS
    # =========================================
    exit_ebitda = lbo.iloc[-1]["EBITDA"]
    exit_ev = exit_ebitda * exit_multiple
    exit_equity = exit_ev - (tlb + revolver) + cash

    moic = exit_equity / equity
    irr = moic ** (1 / years) - 1

    st.header("Returns")
    st.metric("MOIC", f"{moic:.2f}x")
    st.metric("IRR", f"{irr*100:.1f}%")
