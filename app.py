import streamlit as st
import pandas as pd
import numpy as np
import re
import json
import os
from openai import OpenAI

st.set_page_config(layout="wide")

# ============================
# OPENAI
# ============================
client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

# ============================
# MEMORY (LEARNING SYSTEM)
# ============================
MEMORY_FILE = "memory.json"

def load_memory():
    if os.path.exists(MEMORY_FILE):
        return json.load(open(MEMORY_FILE))
    return {}

def save_memory(mem):
    json.dump(mem, open(MEMORY_FILE, "w"))

# ============================
# FILE INGESTION
# ============================

def clean_dataframe(df):
    df.columns = df.iloc[0]
    df = df[1:].reset_index(drop=True)
    df.columns = [str(c) for c in df.columns]
    return df

def standardize(df):
    cols = df.columns.tolist()
    return df.rename(columns={
        cols[0]: "Line Item",
        cols[1]: "Amount"
    })

# ============================
# CLASSIFICATION
# ============================

SCHEMA = {
    "Revenue": ["revenue","sales"],
    "COGS": ["cost","materials","cogs"],
    "OpEx": ["salary","rent","expense","admin","marketing","office"],
    "D&A": ["depreciation","amortization"],
    "Other Income": ["interest income","grant","gain"],
    "Below EBITDA": ["tax","interest expense"]
}

def score_classify(item):
    item = str(item).lower()
    scores = {k:0 for k in SCHEMA}

    for k, words in SCHEMA.items():
        for w in words:
            if w in item:
                scores[k] += 1

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Other"

# ============================
# AI CLASSIFIER
# ============================

def ai_classify(items):
    prompt = f"""
    Classify each item into:
    Revenue, COGS, OpEx, D&A, Other Income, Below EBITDA

    Return JSON mapping.

    Items:
    {items}
    """

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0
    )

    return json.loads(res.choices[0].message.content)

def hybrid_classify(df):
    df = df.copy()
    df["Category"] = df["Line Item"].apply(score_classify)

    unknown = df[df["Category"] == "Other"]["Line Item"].tolist()

    if unknown:
        try:
            ai_map = ai_classify(unknown)
            df["Category"] = df.apply(
                lambda x: ai_map.get(x["Line Item"], x["Category"]),
                axis=1
            )
        except:
            pass

    return df

# ============================
# AUTO RECLASSIFICATION
# ============================

def compute_metrics(df):
    r = df[df.Category=="Revenue"]["Amount"].sum()
    c = df[df.Category=="COGS"]["Amount"].sum()
    o = df[df.Category=="OpEx"]["Amount"].sum()
    oi = df[df.Category=="Other Income"]["Amount"].sum()
    e = r - c - o + oi
    m = e / r if r else 0
    return r,c,o,oi,e,m

def auto_reclassify(df):
    df = df.copy()

    for _ in range(3):
        r,c,o,oi,e,m = compute_metrics(df)

        if m > 0.5:
            df.loc[df.Category=="Other","Category"] = "OpEx"

        if m < 0:
            df.loc[df.Category=="OpEx","Category"] = "Other Income"

    return df

# ============================
# LBO MODEL
# ============================

def run_lbo(revenue, ebitda_margin, entry_multiple, exit_multiple, years):

    entry_ev = revenue * ebitda_margin * entry_multiple
    debt = entry_ev * 0.6
    equity = entry_ev - debt

    rev = revenue
    cash = 0

    for _ in range(years):
        rev *= 1.1
        ebitda = rev * ebitda_margin
        fcf = ebitda * 0.7
        cash += fcf
        debt -= fcf * 0.5

    exit_ev = ebitda * exit_multiple
    exit_equity = exit_ev - debt + cash

    moic = exit_equity / equity
    irr = moic ** (1/years) - 1

    return entry_ev, exit_ev, moic, irr

# ============================
# UI
# ============================

st.title("📊 AI LBO / Ingestion Platform")

file = st.file_uploader("Upload P&L", type=["xlsx","csv"])

if file:
    df = pd.read_excel(file)
    df = clean_dataframe(df)
    df = standardize(df)

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

    # classification
    df = hybrid_classify(df)

    # memory
    mem = load_memory()
    df["Category"] = df.apply(lambda x: mem.get(x["Line Item"], x["Category"]), axis=1)

    # auto fix
    df = auto_reclassify(df)

    # manual edit
    st.subheader("Edit Classification")
    df = st.data_editor(df)

    # update memory
    new_mem = {row["Line Item"]: row["Category"] for _,row in df.iterrows()}
    save_memory(new_mem)

    # metrics
    r,c,o,oi,e,m = compute_metrics(df)

    st.subheader("Summary")
    col1,col2,col3 = st.columns(3)
    col1.metric("Revenue", f"{r:,.0f}")
    col2.metric("EBITDA", f"{e:,.0f}")
    col3.metric("Margin", f"{m*100:.1f}%")

    # LBO
    entry_ev, exit_ev, moic, irr = run_lbo(r, m, 5, 7, 5)

    st.subheader("LBO Output")
    col1,col2 = st.columns(2)
    col1.metric("MOIC", f"{moic:.2f}x")
    col2.metric("IRR", f"{irr*100:.1f}%")

    # Sensitivity
    st.subheader("IRR Sensitivity")

    mult_range = np.arange(5,9,1)
    irr_vals = []

    for mlt in mult_range:
        _,_,_,irr_tmp = run_lbo(r, m, 5, mlt, 5)
        irr_vals.append(round(irr_tmp*100,1))

    sens = pd.DataFrame({"Exit Multiple":mult_range,"IRR %":irr_vals})
    st.dataframe(sens)
