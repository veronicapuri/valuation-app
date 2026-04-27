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
# CLASSIFICATION (HYBRID)
# =========================================
SCHEMA = {
    "Revenue":["revenue","sales"],
    "COGS":["cost","materials"],
    "OpEx":["salary","rent","expense","admin","marketing","office"],
    "D&A":["depreciation","amortization"],
    "Other Income":["interest income","grant"],
    "Below EBITDA":["tax","interest expense"]
}

def score_classify(x):
    x=str(x).lower()
    scores={k:0 for k in SCHEMA}
    for k,words in SCHEMA.items():
        for w in words:
            if w in x:
                scores[k]+=1
    best=max(scores,key=scores.get)
    return best if scores[best]>0 else "Other"

def hybrid_classify(df):
    df["Category"]=df["Line Item"].apply(score_classify)

    unknown=df[df["Category"]=="Other"]["Line Item"].tolist()
    if unknown:
        try:
            res=client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"user","content":f"Classify: {unknown}"}],
                temperature=0
            )
        except:
            pass
    return df

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

    # classify
    df=hybrid_classify(df)

    # memory
    mem=load_memory()
    df["Category"]=df.apply(lambda x:mem.get(x["Line Item"],x["Category"]),axis=1)

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
        dfb=pd.read_excel(bs)
        dfb=standardize(clean(dfb))
        dfb["Amount"]=pd.to_numeric(dfb["Amount"],errors="coerce").fillna(0)

        cash=dfb[dfb["Line Item"].str.contains("cash",case=False,na=False)]["Amount"].sum()
        debt=dfb[dfb["Line Item"].str.contains("debt|loan",case=False,na=False)]["Amount"].sum()

    # =========================================
    # FORECAST
    # =========================================
    st.header("Forecast")
    rev=revenue
    f=[]
    for i in range(years):
        rev*=1+growth
        e=rev*margins[i]
        f.append([i+1,rev,e])
    fdf=pd.DataFrame(f,columns=["Year","Revenue","EBITDA"])
    st.dataframe(fdf)

    # =========================================
    # LBO (FULL WATERFALL)
    # =========================================
    st.header("LBO")

    entry_ev=ebitda*entry_multiple
    tlb=entry_ev*0.5
    revolver=entry_ev*0.1
    equity=entry_ev-(tlb+revolver)

    cash_lbo=min_cash

    rows=[]
    rev=revenue

    for i in range(years):

        rev*=1+growth
        ebitda_y=rev*margins[i]

        interest=tlb*tlb_rate+revolver*rev_rate
        tax=max(0,(ebitda_y-interest))*0.25
        ni=ebitda_y-interest-tax

        capex=rev*0.05
        fcf=ni-capex

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

        rows.append([i+1,rev,ebitda_y,fcf,tlb,revolver])

    lbo=pd.DataFrame(rows,columns=["Year","Revenue","EBITDA","FCF","TLB","Revolver"])
    st.dataframe(lbo)

    # =========================================
    # EXIT
    # =========================================
    exit_ebitda=lbo.iloc[-1]["EBITDA"]
    exit_ev=exit_ebitda*exit_multiple
    exit_equity=exit_ev-(tlb+revolver)+cash_lbo

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
