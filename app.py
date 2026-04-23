import streamlit as st
import pandas as pd
import numpy as np
import re
import numpy_financial as npf

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
# 🧼 HELPERS
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
    header = detect_header(df_raw)

    df = df_raw.copy()
    df.columns = df.iloc[header]
    df = df[header + 1:].reset_index(drop=True)

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
def smart_classify(df):

    df = df.copy()
    df["Category"] = "Other"

    lines = df["Line Item"].astype(str).str.lower()

    # =========================
    # 1. DETECT STRUCTURE
    # =========================
    anchors = {
        "revenue": lines[lines.str.contains("revenue|income|turnover")],
        "gross_profit": lines[lines.str.contains("gross profit")],
        "cogs": lines[lines.str.contains("cost of sales|cogs")],
        "opex": lines[lines.str.contains("operating expenses|expenses")]
    }

    has_structure = len(anchors["gross_profit"]) > 0

    # =========================
    # 2. STRUCTURE-BASED CLASSIFICATION
    # =========================
    if has_structure:

        gp_idx = anchors["gross_profit"].index.min()

        # try to detect opex start
        opex_idx = anchors["opex"].index.min() if len(anchors["opex"]) else None

        for i in df.index:

            if i <= gp_idx:
                df.loc[i, "Category"] = "Revenue"

            elif opex_idx and gp_idx < i <= opex_idx:
                df.loc[i, "Category"] = "COGS"

            elif opex_idx and i > opex_idx:
                df.loc[i, "Category"] = "OpEx"

        return df

    # =========================
    # 3. HEURISTIC FALLBACK
    # =========================
    def rule(item):
        item = item.lower()

        if any(x in item for x in ["sales","turnover"]):
            return "Revenue"

        if any(x in item for x in ["cost","cogs","materials"]):
            return "COGS"

        if any(x in item for x in [
            "salary","wage","rent","expense","admin","marketing",
            "utilities","insurance","travel","professional",
            "depreciation","tax","levy","subscription","fee","bank"
        ]):
            return "OpEx"

        if any(x in item for x in ["grant","fx","gain","interest income"]):
            return "Other Income"

        return "Other"

    df["Category"] = df["Line Item"].apply(rule)

    return df

def classification_confidence(df):

    total = df["Amount"].abs().sum()
    mapped = df[df["Category"] != "Other"]["Amount"].abs().sum()

    return mapped / total if total else 0

confidence = classification_confidence(df)

if confidence < 0.7:
    st.warning("⚠️ Low classification confidence — check mapping")
with st.expander("🔍 Debug View"):
    st.dataframe(df)
# ============================
# 📄 PDF PARSER
# ============================
def smart_pdf_extract(file):
    import pdfplumber

    results = []

    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue

            for line in text.split("\n"):
                match = re.search(r"[-]?\(?\d[\d,.\s]*\)?", line)

                if match:
                    val = match.group(0)
                    val = val.replace(",", "").replace(" ", "")
                    val = val.replace("(", "-").replace(")", "")

                    label = line.replace(match.group(0), "").strip()

                    if len(label) > 2:
                        try:
                            results.append([label, float(val)])
                        except:
                            pass

    if results:
        return pd.DataFrame(results, columns=["Line Item", "Amount"])

    return None

# ============================
# 📂 FILE LOADER
# ============================
def load_file(file):
    name = file.name.lower()

    if name.endswith(".xlsx"):
        return pd.read_excel(file, header=None, engine="openpyxl")
    elif name.endswith(".csv"):
        return pd.read_csv(file, header=None)
    elif name.endswith(".pdf"):
        return smart_pdf_extract(file)

    return None

# ============================
# 🎯 UI
# ============================
st.title("📊 SME Valuation & LBO Tool")

st.sidebar.header("Deal Assumptions")

entry_multiple = st.sidebar.number_input("Entry Multiple", 4.0)
exit_multiple = st.sidebar.number_input("Exit Multiple", 6.5)
holding_years = st.sidebar.slider("Holding Period", 1, 10, 5)

growth_rate = st.sidebar.slider("Revenue Growth (%)", 0, 50, 10)/100

st.sidebar.subheader("LBO Assumptions")
debt_pct = st.sidebar.slider("Debt % at Entry", 0, 90, 60)/100
interest_rate = st.sidebar.slider("Interest Rate (%)", 0, 15, 8)/100
tax_rate = st.sidebar.slider("Tax Rate (%)", 0, 40, 25)/100
capex_pct = st.sidebar.slider("Capex (% Revenue)", 0, 20, 5)/100

# ============================
# FILE INPUT
# ============================
col1, col2 = st.columns(2)

with col1:
    pl_file = st.file_uploader("Upload P&L", type=["xlsx","csv","pdf"])

with col2:
    bs_file = st.file_uploader("Upload Balance Sheet", type=["xlsx","csv","pdf"])

# ============================
# PROCESS P&L
# ============================
if pl_file:
    df_raw = load_file(pl_file)

    df, lc, ac = clean_dataframe(df_raw)
    df = standardize(df, lc, ac)
    df = classify_df(df)

    st.subheader("🔍 Classification Breakdown")
    st.dataframe(df.groupby("Category")["Amount"].sum())

    revenue = df[df.Category=="Revenue"]["Amount"].sum()
    cogs = df[df.Category=="COGS"]["Amount"].sum()
    opex = df[df.Category=="OpEx"]["Amount"].sum()
    other_income = df[df.Category=="Other Income"]["Amount"].sum()

    ebitda = revenue - cogs - opex + other_income

# ============================
# PROCESS BS
# ============================
net_debt = 0

if bs_file:
    df_raw = load_file(bs_file)

    if df_raw is not None:
        df_bs, lc, ac = clean_dataframe(df_raw)
        df_bs = standardize(df_bs, lc, ac)

        cash = df_bs[df_bs["Line Item"].str.contains("cash|bank", case=False)]["Amount"].sum()

        debt = df_bs[df_bs["Line Item"].str.contains(
            "loan|borrow|debt|payable|liability|owing", case=False
        )]["Amount"].sum()

        net_debt = debt - cash

# ============================
# SNAPSHOT
# ============================
if pl_file:
    st.header("📊 Snapshot")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Revenue", f"{revenue:,.0f}")
    col2.metric("EBITDA", f"{ebitda:,.0f}")
    col3.metric("Margin", f"{(ebitda/revenue*100):.1f}%")

    if net_debt < 0:
        col4.metric("Net Cash", f"{abs(net_debt):,.0f}")
    else:
        col4.metric("Net Debt", f"{net_debt:,.0f}")

# ============================
# FORECAST
# ============================
if pl_file:
    st.header("📈 Forecast")

    margins = []
    for y in range(1, holding_years+1):
        m = st.sidebar.slider(f"Margin Y{y}", 0, 80, 20, key=f"m{y}")
        margins.append(m/100)

    rev = revenue
    rows = []

    for y in range(1, holding_years+1):
        rev *= (1 + growth_rate)
        ebit = rev * margins[y-1]
        rows.append([f"Y{y}", rev, ebit, margins[y-1]*100])

    f = pd.DataFrame(rows, columns=["Year","Revenue","EBITDA","Margin %"]).set_index("Year")

    st.dataframe(f.style.format({
        "Revenue": "{:,.0f}",
        "EBITDA": "{:,.0f}",
        "Margin %": "{:.1f}%"
    }))

# ============================
# LBO MODEL
# ============================
if pl_file:

    st.header("🏦 LBO")

    entry_ev = ebitda * entry_multiple

    entry_debt = net_debt if net_debt > 0 else entry_ev * debt_pct
    entry_equity = entry_ev - entry_debt

    debt = entry_debt
    cash_flows = [-entry_equity]

    lbo_rows = []

    for i, row in f.iterrows():

        rev_y = row["Revenue"]
        ebitda_y = row["EBITDA"]

        capex = rev_y * capex_pct
        interest = debt * interest_rate
        depreciation = ebitda_y * 0.05

        ebit = ebitda_y - depreciation
        tax = max(0, ebit * tax_rate)

        fcf = ebitda_y - capex - interest - tax

        repayment = min(debt * 0.4, max(0, fcf))
        debt -= repayment

        cash_flows.append(fcf)

        lbo_rows.append([i, rev_y, ebitda_y, fcf, debt])

    exit_ebitda = f.iloc[-1]["EBITDA"]
    exit_ev = exit_ebitda * exit_multiple
    exit_equity = exit_ev - debt

    cash_flows[-1] += exit_equity

    try:
        irr = npf.irr(cash_flows)
    except:
        irr = 0

    moic = exit_equity / entry_equity

    lbo_df = pd.DataFrame(
        lbo_rows,
        columns=["Year","Revenue","EBITDA","FCF","Debt"]
    ).set_index("Year")

    st.dataframe(lbo_df.style.format("{:,.0f}"))

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
