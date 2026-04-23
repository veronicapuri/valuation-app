import streamlit as st
import pandas as pd
import numpy as np
import re

st.set_page_config(layout="wide")

# ============================
# HELPERS
# ============================

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
        col_data = df[col].fillna("").astype(str)

        numeric = pd.to_numeric(
            col_data.str.replace(",", "")
                    .str.replace("(", "-")
                    .str.replace(")", ""),
            errors="coerce"
        )

        scores.append((col, numeric.notna().sum(), col_data.str.len().mean()))

    amount_col = max(scores, key=lambda x: x[1])[0]
    line_col = max([x for x in scores if x[0] != amount_col], key=lambda x: x[2])[0]

    return line_col, amount_col

def clean_dataframe(df_raw):
    header_row = detect_header(df_raw)

    df = df_raw.copy()
    df.columns = df.iloc[header_row]
    df = df[header_row + 1:].reset_index(drop=True)

    df.columns = pd.Series(df.columns).astype(str).str.strip()
    df.columns = make_unique(df.columns)

    return df, *detect_columns(df)

def standardize(df, line_col, amount_col):
    df = df[[line_col, amount_col]].copy()
    df.columns = ["Line Item", "Amount"]

    df["Line Item"] = df["Line Item"].astype(str)

    df["Amount"] = (
        df["Amount"]
        .astype(str)
        .str.replace(",", "")
        .str.replace("(", "-")
        .str.replace(")", "")
    )

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

    return df

def load_file(file):
    name = file.name.lower()

    if name.endswith(".xlsx"):
        return pd.read_excel(file, header=None)

    elif name.endswith(".csv"):
        return pd.read_csv(file, header=None)

    return None

# ============================
# CLASSIFICATION
# ============================

def smart_classify(df):
    df = df.copy()

    def rule(item):
        item = str(item).lower()

        if any(x in item for x in ["grant", "fx", "gain", "interest income"]):
            return "Other Income"

        if any(x in item for x in ["revenue", "sales"]):
            return "Revenue"

        if any(x in item for x in ["cost of sales", "cogs"]):
            return "COGS"

        if any(x in item for x in [
            "salary","wage","rent","expense","admin","marketing",
            "utilities","insurance","travel","professional",
            "depreciation","tax","levy","subscription","fee","bank"
        ]):
            return "OpEx"

        return "Other"

    df["Category"] = df["Line Item"].apply(rule)
    return df

def classification_confidence(df):
    total = df["Amount"].abs().sum()
    mapped = df[df["Category"] != "Other"]["Amount"].abs().sum()
    return mapped / total if total else 0

# ============================
# UI
# ============================

st.title("📊 SME Valuation & LBO Tool")

# Sidebar
st.sidebar.header("Deal Assumptions")

entry_multiple = st.sidebar.number_input("Entry Multiple", 4.0)
exit_multiple = st.sidebar.number_input("Exit Multiple", 6.5)
holding_years = st.sidebar.slider("Holding Period", 1, 10, 5)

growth_rate = st.sidebar.slider("Revenue Growth (%)", 0, 50, 10) / 100

# LBO
st.sidebar.header("LBO Assumptions")
debt_pct = st.sidebar.slider("Debt % at Entry", 0, 90, 60) / 100
interest_rate = st.sidebar.slider("Interest Rate (%)", 0, 20, 8) / 100
tax_rate = st.sidebar.slider("Tax Rate (%)", 0, 40, 25) / 100
capex_pct = st.sidebar.slider("Capex (% Revenue)", 0, 20, 5) / 100

# Margins
st.sidebar.header("EBITDA Margin by Year")
margins = [
    st.sidebar.slider(f"Y{i+1}", 0, 80, 20) / 100
    for i in range(holding_years)
]

# Upload
st.header("📂 Data Ingestion")

col1, col2 = st.columns(2)

with col1:
    pl_file = st.file_uploader("Upload P&L", type=["xlsx", "csv"])

with col2:
    bs_file = st.file_uploader("Upload Balance Sheet", type=["xlsx", "csv"])

# ============================
# PROCESS P&L
# ============================

if pl_file:
    df_raw = load_file(pl_file)
    df, lc, ac = clean_dataframe(df_raw)
    df = standardize(df, lc, ac)
    df = smart_classify(df)

    confidence = classification_confidence(df)
    if confidence < 0.7:
        st.warning("⚠️ Low classification confidence")

    with st.expander("Debug View"):
        st.dataframe(df)

    st.subheader("🔍 Classification Breakdown")
    st.dataframe(df.groupby("Category")["Amount"].sum())

    revenue = df[df.Category == "Revenue"]["Amount"].sum()
    cogs = df[df.Category == "COGS"]["Amount"].sum()
    opex = df[df.Category == "OpEx"]["Amount"].sum()
    other_income = df[df.Category == "Other Income"]["Amount"].sum()

    ebitda = revenue - cogs - opex + other_income

    # ============================
    # PROCESS BS
    # ============================

    net_debt = 0
    cash = 0
    debt = 0

    if bs_file:
        df_raw_bs = load_file(bs_file)
        df_bs, lc_bs, ac_bs = clean_dataframe(df_raw_bs)
        df_bs = standardize(df_bs, lc_bs, ac_bs)

        df_bs["Line Item"] = df_bs["Line Item"].astype(str)

        cash = df_bs[df_bs["Line Item"].str.contains("cash|bank", case=False, na=False)]["Amount"].sum()

        debt = df_bs[df_bs["Line Item"].str.contains("loan|debt|borrow", case=False, na=False)]["Amount"].sum()

        net_debt = debt - cash

    # ============================
    # SNAPSHOT
    # ============================

    st.header("📊 Snapshot")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Revenue", f"{revenue:,.0f}")
    col2.metric("EBITDA", f"{ebitda:,.0f}")
    col3.metric("Margin", f"{(ebitda/revenue*100 if revenue else 0):.1f}%")

    if net_debt < 0:
        col4.metric("Net Cash", f"{abs(net_debt):,.0f}")
    else:
        col4.metric("Net Debt", f"{net_debt:,.0f}")

    # ============================
    # FORECAST
    # ============================

    st.header("📈 Forecast")

    rev = revenue
    rows = []

    for y in range(holding_years):
        rev *= (1 + growth_rate)
        ebit = rev * margins[y]
        rows.append([f"Y{y+1}", rev, ebit, margins[y]*100])

    f = pd.DataFrame(rows, columns=["Year","Revenue","EBITDA","Margin %"])

    st.dataframe(f.style.format({
        "Revenue": "{:,.0f}",
        "EBITDA": "{:,.0f}",
        "Margin %": "{:.1f}%"
    }))

    exit_ebitda = f.iloc[-1]["EBITDA"]

    # ============================
    # LBO
    # ============================

    st.header("🏦 LBO Analysis")

    entry_ev = ebitda * entry_multiple
    entry_debt = entry_ev * debt_pct
    entry_equity = entry_ev - entry_debt

    debt_balance = entry_debt
    cash_flows = [-entry_equity]

    lbo_rows = []

    for i in range(holding_years):
        ebitda_y = f.iloc[i]["EBITDA"]

        interest = debt_balance * interest_rate
        tax = max(0, (ebitda_y - interest) * tax_rate)
        capex = f.iloc[i]["Revenue"] * capex_pct

        fcf = ebitda_y - interest - tax - capex

        repayment = max(0, min(fcf, debt_balance))
        debt_balance -= repayment

        lbo_rows.append([
            f"Y{i+1}",
            f.iloc[i]["Revenue"],
            ebitda_y,
            fcf,
            debt_balance
        ])

        if i < holding_years - 1:
            cash_flows.append(0)

    exit_ev = exit_ebitda * exit_multiple
    exit_equity = exit_ev - debt_balance
    cash_flows.append(exit_equity)

    # IRR fallback
    def compute_irr(cf):
        try:
            return np.irr(cf)
        except:
            return 0

    irr = compute_irr(cash_flows)
    moic = exit_equity / entry_equity if entry_equity else 0

    lbo_df = pd.DataFrame(
        lbo_rows,
        columns=["Year","Revenue","EBITDA","FCF","Remaining Debt"]
    )

    st.dataframe(lbo_df.style.format({
        "Revenue": "{:,.0f}",
        "EBITDA": "{:,.0f}",
        "FCF": "{:,.0f}",
        "Remaining Debt": "{:,.0f}"
    }))

    col1, col2 = st.columns(2)
    col1.metric("MOIC", f"{moic:.2f}x")
    col2.metric("IRR", f"{irr*100:.2f}%")

    # ============================
    # VALUATION
    # ============================

    st.header("💰 Valuation")

    col1, col2 = st.columns(2)
    col1.metric("Entry EV", f"{entry_ev:,.0f}")
    col2.metric("Exit EV", f"{exit_ev:,.0f}")
