# =========================================
# IMPORTS
# =========================================
import streamlit as st
import pandas as pd
import numpy as np
import json, os, re
from openai import OpenAI

st.set_page_config(layout="wide")
client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

# =========================================
# MEMORY (LEARNING SYSTEM)
# =========================================
MEMORY_FILE = "memory.json"

def load_memory():
    if os.path.exists(MEMORY_FILE):
        return json.load(open(MEMORY_FILE))
    return {}

def save_memory(mem):
    json.dump(mem, open(MEMORY_FILE, "w"))

# =========================================
# INGESTION
# =========================================
def clean(df):
    df.columns = df.iloc[0]
    df = df[1:].reset_index(drop=True)
    return df

def standardize(df):
    return df.rename(columns={df.columns[0]:"Line Item", df.columns[1]:"Amount"})

# =========================================
# METRICS
# =========================================
def compute(df):
    r=df[df.Category=="Revenue"]["Amount"].sum()
    c=df[df.Category=="COGS"]["Amount"].sum()
    o=df[df.Category=="OpEx"]["Amount"].sum()
    oi=df[df.Category=="Other Income"]["Amount"].sum()
    e=r-c-o+oi
    m=e/r if r else 0
    return r,c,o,oi,e,m

# =========================================
# UI INPUTS
# =========================================
st.sidebar.header("Deal")

entry_multiple=st.sidebar.number_input("Entry Multiple",4.0)
exit_multiple=st.sidebar.number_input("Exit Multiple",7.0)
years=st.sidebar.slider("Holding Period",1,7,5)

growth=st.sidebar.slider("Revenue Growth %",0,30,10)/100

st.sidebar.subheader("Margins")
margins=[st.sidebar.slider(f"Y{i+1}",0,80,20)/100 for i in range(years)]

st.sidebar.subheader("Capital Structure")
tlb_rate=st.sidebar.slider("TLB Rate",0,15,7)/100
rev_rate=st.sidebar.slider("Revolver Rate",0,15,6)/100
min_cash=st.sidebar.number_input("Min Cash",50000)

# =========================================
# FILE UPLOAD
# =========================================
pl=st.file_uploader("Upload P&L")
bs=st.file_uploader("Upload BS")

if pl:
    df=pd.read_excel(pl)
    df=standardize(clean(df))
    df["Amount"]=pd.to_numeric(df["Amount"],errors="coerce").fillna(0)

    df = classify_financials(df)

    # ✅ DEBUG HERE (correct)
    st.subheader("DEBUG CLASSIFICATION")
    st.dataframe(df[["Line Item","Row Type","Section","Category","Amount"]])
# =========================
# BULLETPROOF CLASSIFICATION
# =========================

def normalize_text(x):
    if pd.isna(x):
        return ""
    return str(x).strip().lower()

def detect_row_type(text):
    text = normalize_text(text)

    if text == "":
        return "Empty"

    if any(x in text for x in [
        "pte ltd", "for the year", "as at", "account", "unaudited"
    ]):
        return "Meta"

    if any(x in text for x in [
        "total", "net profit", "profit for the year",
        "comprehensive income", "gross profit"
    ]):
        return "Total"

    if len(text.split()) <= 3 and any(x in text for x in [
        "revenue","income","expenses","cost"
    ]):
        return "Header"

    return "Line"


def detect_sections(df):
    current = "Unknown"
    sections = []

    for item in df["Line Item"]:
        text = normalize_text(item)

        if "revenue" in text or "trading income" in text:
            current = "Revenue"

        elif "cost of sales" in text or "cogs" in text:
            current = "COGS"

        elif "operating expenses" in text:
            current = "OpEx"

        elif "other income" in text:
            current = "Other Income"

        elif "tax" in text:
            current = "Below EBITDA"

        sections.append(current)

    df["Section"] = sections
    return df


def classify_row(row):
    if row["Row Type"] != "Line":
        return "Ignore"

    item = normalize_text(row["Line Item"])
    section = row.get("Section", "Unknown")

    if section == "Revenue":
        return "Revenue"

    if section == "COGS":
        return "COGS"

    if section == "OpEx":
        if "depreciation" in item or "amortization" in item:
            return "D&A"
        return "OpEx"

    if section == "Other Income":
        return "Other Income"

    if section == "Below EBITDA":
        return "Below EBITDA"

    # fallback
    if any(x in item for x in ["sales","revenue"]):
        return "Revenue"

    if any(x in item for x in ["cost","materials","purchases"]):
        return "COGS"

    if any(x in item for x in ["salary","rent","admin","marketing","expense"]):
        return "OpEx"

    return "Other"


def classify_financials(df):
    df = df.copy()

    df["Row Type"] = df["Line Item"].apply(detect_row_type)

    # REMOVE garbage BEFORE classification
    df = df[~df["Row Type"].isin(["Meta","Empty"])]

    df = detect_sections(df)

    df["Category"] = df.apply(classify_row, axis=1)

    return df


# APPLY
df = classify_financials(df)

# memory
mem=load_memory()
df["Category"] = df.apply(
    lambda x: mem.get(x["Line Item"], x["Category"]) if x["Category"] == "Other" else x["Category"],
    axis=1
)

# manual override
df=st.data_editor(df)
save_memory({r["Line Item"]:r["Category"] for _,r in df.iterrows()})

# compute
revenue,cogs,opex,other,ebitda,margin=compute(df)

st.header("Snapshot")
st.write(revenue,ebitda,margin)

# =========================================
# BALANCE SHEET
# =========================================
cash=0
debt=0

if bs:
    dfb = pd.read_excel(bs, header=None)
    
    # ---- CLEAN HEADER SAFELY ----
    dfb.columns = dfb.iloc[0].astype(str)
    dfb = dfb[1:].reset_index(drop=True)
    
    # Ensure columns exist
    dfb.columns = [str(c).strip() for c in dfb.columns]
    
    # ---- FORCE FIRST 2 COLUMNS ----
    dfb = dfb.iloc[:, :2]
    dfb.columns = ["Line Item", "Amount"]
    
    # ---- SAFE CONVERSION ----
    dfb["Amount"] = (
    dfb["Amount"]
    .astype(str)
    .str.replace(",", "")
    .str.replace("(", "-")
    .str.replace(")", "")
    )
    
    dfb["Amount"] = pd.to_numeric(dfb["Amount"], errors="coerce")
    dfb["Amount"] = dfb["Amount"].fillna(0)
    
    # ---- DEBUG CHECK (VERY IMPORTANT) ----
    st.write("BS preview:", dfb.head(10))
    
    # ---- CLASSIFY ----
    cash = dfb[dfb["Line Item"].str.contains("cash|bank", case=False, na=False)]["Amount"].sum()
    debt = dfb[dfb["Line Item"].str.contains("loan|debt|borrow", case=False, na=False)]["Amount"].sum()
    # =========================================
    # FORECAST
    # =========================================
    st.header("Forecast")
    rev=revenue
    f=[]
    for i in range(years):
        rev*=1+growth
        if i == 0:
            e = ebitda * (1 + growth)
        else:
            e = rev * margins[i]
        f.append([i+1,rev,e])
    fdf=pd.DataFrame(f,columns=["Year","Revenue","EBITDA"])
    st.dataframe(fdf)

    # =========================================
    # LBO (FULL WATERFALL)
    # =========================================
    st.header("LBO")

    entry_ev=ebitda*entry_multiple
    debt_pct = 0.6  # make this a slider later
    entry_debt = entry_ev * debt_pct
    
    tlb = entry_debt * 0.85
    revolver = entry_debt * 0.15
    
    equity = entry_ev - entry_debt
    
    cash_lbo=min_cash

    rows=[]
    rev=revenue

    for i in range(years):
        rev = fdf.iloc[i]["Revenue"]
        ebitda_y = fdf.iloc[i]["EBITDA"]
        rev *= (1 + growth)
        e = rev * margins[i]
        interest=tlb*tlb_rate+revolver*rev_rate
        dna = rev * 0.03
        ebit = ebitda_y - dna
        tax = max(0, ebit - interest) * 0.25
        ni = ebit - interest - tax


        da = ebitda_y * 0.1
        
        delta_nwc = (rev * 0.05) - (prev_rev * 0.05 if i > 0 else 0)
        
        capex = max(da, rev * 0.06)

        if i == 0:
            prev_rev = revenue
        
        delta_nwc = (rev - prev_rev) * 0.05
        dna = rev * 0.03
        capex = max(dna, rev * 0.05)
        
        fcf = ebitda_y - interest - tax - capex - delta_nwc
        
        prev_rev = rev

        da = ebitda_y * 0.1
        capex = max(da, rev * 0.04)
        fcf=ni-capex
        dna = rev * 0.03
        prev_rev = rev
        delta_nwc = rev * 0.05 - prev_rev * 0.05
        fcf = ni + dna - capex - delta_nwc
        

        # cash
        cash_lbo+=fcf

        # revolver draw
        if cash_lbo<min_cash:
            draw=min_cash-cash_lbo
            revolver+=draw
            cash_lbo+=draw

        # paydown
        excess=max(0,cash_lbo-min_cash)

        pay_rev=min(revolver,excess)
        revolver-=pay_rev
        cash_lbo-=pay_rev

        excess=max(0,cash_lbo-min_cash)
        pay_tlb=min(tlb,excess)
        tlb-=pay_tlb
        cash_lbo-=pay_tlb
        
        mandatory = tlb * 0.05
        tlb -= mandatory
        cash_lbo -= mandatory

        rows.append([i+1,rev,ebitda_y,fcf,tlb,revolver])

    lbo=pd.DataFrame(rows,columns=["Year","Revenue","EBITDA","FCF","TLB","Revolver"])
    st.dataframe(lbo)

    # =========================================
    # EXIT
    # =========================================
    exit_ebitda=lbo.iloc[-1]["EBITDA"]
    exit_ev=exit_ebitda*exit_multiple
    exit_equity = exit_ev - (tlb + revolver) + cash_lbo
    moic=exit_equity/equity
    irr=moic**(1/years)-1

    st.header("Returns")
    st.metric("MOIC",f"{moic:.2f}x")
    st.metric("IRR",f"{irr*100:.1f}%")

    # =========================================
    # SENSITIVITY
    # =========================================
    st.header("Sensitivity")

    mults=np.arange(exit_multiple-2,exit_multiple+2,1)
    res=[]
    for m in mults:
        exit_ev=exit_ebitda*m
        eq=exit_ev-(tlb+revolver)
        mo=eq/equity
        ir=mo**(1/years)-1
        res.append([m,round(ir*100,1)])
    st.dataframe(pd.DataFrame(res,columns=["Exit Multiple","IRR"]))
