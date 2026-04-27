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
# STRUCTURE-AWARE CLASSIFICATION (DALOOPA STYLE)
# ============================

def detect_row_type(item):
    import re
    
    def detect_row_type(item):
        text = str(item).strip().lower()
    
        if text == "" or text in ["nan", "none"]:
            return "Empty"
    
        # ONLY true headers / metadata
        if (
            "year ended" in text or
            "as at" in text or
            re.fullmatch(r"\d{4}", text) or
            text.startswith("note ")
        ):
            return "Meta"
    
        if any(x in text for x in [
            "total", "subtotal", "net profit", "gross profit"
        ]):
            return "Total"
    
        if any(x in text for x in [
            "income", "expenses"
        ]) and len(text.split()) <= 3:
            return "Header"
    
        return "Line"
    
def detect_sections(df):
    sections = []
    current = "Unknown"

    for item in df["Line Item"]:
        text = str(item).lower()
        if "revenue" in text or "sales" in text:
            current = "Revenue Section"
        
        elif "cost of sales" in text or "cogs" in text:
            current = "COGS Section"
        
        elif "operating expense" in text or "expenses" in text:
            current = "OpEx Section"
        
        elif "other income" in text:
            current = "Other Income Section"
        
        elif "profit" in text:
            current = "Summary"

        sections.append(current)

    df["Section"] = sections
    return df


def smart_classify(df):
    
    df = df.copy()

    def rule(row):
        if row.get("Row Type") != "Line":
            return "Ignore"

        item = str(row["Line Item"]).lower()
        section = row.get("Section", "Unknown")

        # -------- SECTION LOGIC (PRIMARY DRIVER) --------
        if section == "Revenue Section":
            if any(x in item for x in ["revenue", "sales"]):
                return "Revenue"

        if section == "COGS Section":
            return "COGS"

        if section == "OpEx Section":
            if "depreciation" in item:
                return "D&A"
            return "OpEx"

        if section == "Other Income Section":
            return "Other Income"

        # -------- FALLBACK LOGIC (STRICT ORDER) --------

        # 1. Revenue (only explicit)
        if any(x in item for x in ["revenue", "sales"]):
            return "Revenue"

        # 2. COGS (VERY IMPORTANT — put BEFORE OpEx)
        if any(x in item for x in [
            "cost of goods", "cost of sales", "cogs",
            "direct cost", "materials", "inventory", "purchases"
        ]):
            return "COGS"
        
        # 3. D&A (separate from OpEx)
        if any(x in item for x in ["depreciation", "amortization"]):
            return "D&A"
        
        # 4. OpEx (AFTER COGS)
        if any(x in item for x in [
            "salary","wage","bonus","cpf","staff",
            "rent","lease",
            "admin","administrative",
            "marketing","advertising","promotion",
            "utilities","electricity","water",
            "insurance",
            "travel","transport","logistics",
            "professional","legal","audit","accounting",
            "consulting",
            "subscription","software","it",
            "bank","charges","fees",
            "maintenance","repair",
            "office","supplies",
            "telephone","internet",
            "depreciation","amortization"
        ]):
            return "OpEx"
        
        # 5. Other income
        if any(x in item for x in ["grant", "fx", "gain", "interest income"]):
            return "Other Income"
        
        # 6. Below EBITDA
        if any(x in item for x in ["tax", "interest expense"]):
            return "Below EBITDA"
        
        return "Other"
                

    df["Category"] = df.apply(rule, axis=1)
    return df


# ============================
# TOTALS DETECTION
# ============================

def detect_totals(df):
    totals = {}

    for _, row in df.iterrows():
        item = str(row["Line Item"]).lower()
        amt = row["Amount"]

        if "total revenue" in item or "total income" in item:
            totals["Revenue"] = amt

        if "gross profit" in item:
            totals["Gross Profit"] = amt

        if "net profit" in item:
            totals["Net Profit"] = amt

    return totals


# ============================
# RECONCILIATION CHECK
# ============================

def reconcile(df, totals):
    if "Revenue" in totals:
        calc_revenue = df[df.Category == "Revenue"]["Amount"].sum()
        diff = abs(calc_revenue - totals["Revenue"])

        if totals["Revenue"] != 0 and diff > 0.05 * abs(totals["Revenue"]):
            st.error("🚨 Revenue mismatch — classification likely wrong")


# ============================
# CONFIDENCE SCORE (UPGRADED)
# ============================

def classification_confidence(df):
    total = df["Amount"].abs().sum()
    mapped = df[~df["Category"].isin(["Other"])]["Amount"].abs().sum()

    structure_bonus = 0.1 if "Section" in df.columns else 0

    return min(1.0, (mapped / total) + structure_bonus)


# ============================
# AUTO FIX (SELF HEALING)
# ============================

def auto_fix(df, totals):
    if "Revenue" in totals:
        df.loc[
            df["Line Item"].str.contains("sales|revenue", case=False, na=False),
            "Category"
        ] = "Revenue"
    return df

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

st.sidebar.subheader("Advanced LBO")

dna_pct = st.sidebar.slider("D&A (% Revenue)", 0, 20, 3) / 100
nwc_pct = st.sidebar.slider("Change in NWC (% Revenue)", -10, 20, 2) / 100
amort_pct = st.sidebar.slider("Mandatory Debt Amort (%)", 0, 20, 5) / 100

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
    
    df["Row Type"] = df["Line Item"].apply(detect_row_type)
    df = df[~df["Row Type"].isin(["Meta", "Empty"])]
    
    df = detect_sections(df)
    df = smart_classify(df)
    
    totals = detect_totals(df)
    df = auto_fix(df, totals)
    reconcile(df, totals)

    confidence = classification_confidence(df)
    if confidence < 0.7:
        st.warning("⚠️ Low classification confidence")

    with st.expander("Debug View"):
        st.dataframe(df)

    st.subheader("🔍 Classification Breakdown")
    clean_df = df[df["Category"] != "Ignore"]
    st.dataframe(clean_df.groupby("Category")["Amount"].sum())

    clean_df = df[df["Category"] != "Ignore"]

    revenue = clean_df[clean_df.Category == "Revenue"]["Amount"].sum()
    cogs = clean_df[clean_df.Category == "COGS"]["Amount"].sum()
    opex = clean_df[clean_df.Category == "OpEx"]["Amount"].sum()
    other_income = clean_df[clean_df.Category == "Other Income"]["Amount"].sum()

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

    base_ebitda = ebitda
    # ============================
    # FORECAST
    # ============================

    st.header("📈 Forecast")

    rev = revenue
    rows = []

    for y in range(holding_years):
        rev *= (1 + growth_rate)
        ebitda = rev * margins[y]
        rows.append([f"Y{y+1}", rev, ebitda, margins[y]*100])

    f = pd.DataFrame(rows, columns=["Year","Revenue","EBITDA","Margin %"])

    st.dataframe(f.style.format({
        "Revenue": "{:,.0f}",
        "EBITDA": "{:,.0f}",
        "Margin %": "{:.1f}%"
    }))

    exit_ebitda = f.iloc[-1]["EBITDA"]

if pl_file:
# ============================
# REAL 3-STATEMENT LBO ENGINE
# ============================

    st.header("🏦 LBO Analysis (3-Statement)")

    # Debug toggle (define ONCE)
    debug = st.sidebar.checkbox("Show Debug")

    # -----------------------
    # ENTRY
    # -----------------------
    entry_ev = base_ebitda * entry_multiple
    
    if bs_file and debt > 0:
        entry_debt = debt
        entry_equity = entry_ev - entry_debt + cash
    else:
        entry_debt = entry_ev * debt_pct
        entry_equity = entry_ev - entry_debt

    # Use separate LBO cash (DO NOT overwrite BS cash)
    cash_lbo = 0
    debt_open = entry_debt
    
    cash_flows = [-entry_equity]
    lbo_rows = []
    
    rev = revenue
    base_margin = ebitda / revenue if revenue else margins[0]
    
    # -----------------------
    # LOOP
    # -----------------------
    for i in range(holding_years):
    
        prev_rev = rev  # ✅ FIXED
    
        # -----------------------
        # OPERATING MODEL
        # -----------------------
        rev *= (1 + growth_rate)
        margin = margins[i] if margins else base_margin
        ebitda_y = rev * margin
    
        dna = rev * dna_pct
        ebit = ebitda_y - dna
    
        # -----------------------
        # DEBT + INTEREST
        # -----------------------
        interest = debt_open * interest_rate
    
        # -----------------------
        # TAX
        # -----------------------
        taxable_income = max(0, ebit - interest)
        tax = taxable_income * tax_rate
    
        net_income = ebit - interest - tax
    
        # -----------------------
        # CASH FLOW
        # -----------------------
        delta_nwc = (rev - prev_rev) * nwc_pct
        capex = rev * capex_pct
    
        fcf = net_income + dna - capex - delta_nwc
    
        # -----------------------
        # DEBT SCHEDULE
        # -----------------------
        mandatory_amort = debt_open * amort_pct
    
        cash_lbo += max(0, fcf - mandatory_amort)
        cash_sweep = max(0, cash * 0.9)  # keep 10% buffer
    
        total_repayment = min(debt_open, mandatory_amort + cash_sweep)
    
        cash_lbo -= cash_sweep
        debt_close = debt_open - total_repayment
    
        # -----------------------
        # STORE (FIXED POSITION)
        # -----------------------
        lbo_rows.append([
            f"Y{i+1}",
            rev,
            ebitda_y,
            ebit,
            net_income,
            fcf,
            debt_open,
            debt_close
        ])
    
        # -----------------------
        # DEBUG (SAFE)
        # -----------------------
        if debug:
            st.write({
                "Year": i+1,
                "Revenue": rev,
                "EBITDA": ebitda_y,
                "EBIT": ebit,
                "Interest": interest,
                "Net Income": net_income,
                "FCF": fcf,
                "Debt Open": debt_open,
                "Debt Close": debt_close
            })
    
        debt_open = debt_close
    
        # Only exit year has cash flow
        if i < holding_years - 1:
            cash_flows.append(0)
    
    # -----------------------
    # BUILD DATAFRAME (CRITICAL)
    # -----------------------
    lbo_df = pd.DataFrame(
        lbo_rows,
        columns=[
            "Year","Revenue","EBITDA","EBIT",
            "Net Income","FCF","Opening Debt","Closing Debt"
        ]
    )
    
    # -----------------------
    # EXIT (FIXED)
    # -----------------------
    exit_ebitda = lbo_df.iloc[-1]["EBITDA"]
    exit_ev = exit_ebitda * exit_multiple
    exit_equity = exit_ev - lbo_df.iloc[-1]["Closing Debt"]
    
    cash_flows.append(exit_equity)
    
    # -----------------------
    # IRR (ROBUST)
    # -----------------------
    def compute_irr(cf):
        try:
            import numpy_financial as npf
            return npf.irr(cf)
        except:
            return 0
    
    irr = compute_irr(cash_flows)
    moic = exit_equity / entry_equity if entry_equity else 0
    
    # -----------------------
    # DEBUG CASH FLOWS
    # -----------------------
    if debug:
        st.write("Cash Flows:", cash_flows)
        st.write("Exit Equity:", exit_equity)
    
    # -----------------------
    # OUTPUT
    # -----------------------
    st.dataframe(lbo_df.style.format({
        "Revenue": "{:,.0f}",
        "EBITDA": "{:,.0f}",
        "EBIT": "{:,.0f}",
        "Net Income": "{:,.0f}",
        "FCF": "{:,.0f}",
        "Opening Debt": "{:,.0f}",
        "Closing Debt": "{:,.0f}"
    }))
    
    col1, col2 = st.columns(2)
    col1.metric("MOIC", f"{moic:.2f}x")
    col2.metric("IRR", f"{irr*100:.2f}%")

if pl_file:

    # ============================
    # VALUATION
    # ============================

    st.header("💰 Valuation")

    col1, col2 = st.columns(2)
    col1.metric("Entry EV", f"{entry_ev:,.0f}")
    col2.metric("Exit EV", f"{exit_ev:,.0f}")
