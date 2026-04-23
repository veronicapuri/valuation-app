import streamlit as st
import pandas as pd
import numpy as np
import re
import numpy_financial as npf

# Optional AI
from openai import OpenAI

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
# ⚙️ SETTINGS
# ============================
use_ai = st.sidebar.checkbox("🤖 Enable AI Classification", value=False)
confidential_mode = st.sidebar.checkbox("🔐 Confidential Mode", value=True)

client = None
if "OPENAI_API_KEY" in st.secrets and use_ai and not confidential_mode:
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# ============================
# 🧼 HELPERS
# ============================
def sanitize(item):
    item = str(item)
    item = re.sub(r'\d+', '', item)
    item = re.sub(r'[^a-zA-Z\s]', '', item)
    return item.lower().strip()

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

# ============================
# 📊 DETECTION
# ============================
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
        try:
            col_data = df[col]

            if isinstance(col_data, pd.DataFrame):
                col_data = col_data.iloc[:, 0]

            col_data = col_data.fillna("").astype(str)

            numeric = pd.to_numeric(
                col_data.str.replace(",", "")
                        .str.replace("(", "-")
                        .str.replace(")", ""),
                errors="coerce"
            )

            numeric_score = numeric.notna().sum()
            text_score = col_data.str.len().mean()

            scores.append((col, numeric_score, text_score))

        except:
            continue

    if len(scores) < 2:
        st.error("Could not detect columns")
        st.stop()

    amount_col = max(scores, key=lambda x: x[1])[0]
    line_col = max([x for x in scores if x[0] != amount_col], key=lambda x: x[2])[0]

    return line_col, amount_col

def clean_dataframe(df_raw):
    header_row = detect_header(df_raw)

    df = df_raw.copy()
    df.columns = df.iloc[header_row]
    df = df[header_row + 1:].reset_index(drop=True)

    df.columns = (
        pd.Series(df.columns)
        .fillna("")
        .astype(str)
        .str.strip()
    )

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
def rule_classify(item):
    item = str(item).lower()

    if any(x in item for x in ["revenue","sales","income","fees"]):
        return "Revenue"

    if any(x in item for x in ["cost","cogs","direct","materials","subcontract"]):
        return "COGS"

    if any(x in item for x in [
        "salary","wage","rent","expense","admin","marketing",
        "utilities","insurance","travel","professional"
    ]):
        return "OpEx"

    return "Other"

def classify_df(df):
    df["Category"] = df["Line Item"].apply(rule_classify)
    return df

# ============================
# 📄 PDF PARSER (CLEAN)
# ============================
def smart_pdf_extract(file):

    import pdfplumber

    results = []

    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:

            text = page.extract_text()

            if not text:
                continue

            lines = text.split("\n")

            for line in lines:
                match = re.search(r"[-]?\(?\d[\d,.\s]*\)?", line)

                if match:
                    val = match.group(0)
                    val = val.replace(",", "")
                    val = val.replace(" ", "")
                    val = val.replace("(", "-").replace(")", "")

                    label = line.replace(match.group(0), "").strip()

                    if len(label) < 2:
                        continue

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
st.title("📊 Investment Committee Model")
st.caption("Automated financial normalization + valuation engine")

st.sidebar.header("Deal Assumptions")

entry_multiple = st.sidebar.number_input("Entry Multiple", 4.0)
exit_multiple = st.sidebar.number_input("Exit Multiple", 6.5)
holding_years = st.sidebar.slider("Holding Period", 1, 10, 5)

growth_rate = st.sidebar.slider("Revenue Growth (%)", 0, 50, 10)/100
st.sidebar.subheader("LBO Assumptions")

debt_pct = st.sidebar.slider("Debt % at Entry", 0, 90, 60) / 100
interest_rate = st.sidebar.slider("Interest Rate (%)", 0, 15, 8) / 100
tax_rate = st.sidebar.slider("Tax Rate (%)", 0, 40, 25) / 100
capex_pct = st.sidebar.slider("Capex (% of Revenue)", 0, 20, 5) / 100

st.sidebar.subheader("EBITDA Margin by Year")

st.header("📂 Data Ingestion")

col1, col2 = st.columns(2)

with col1:
    pl_file = st.file_uploader("Upload P&L", type=["xlsx","csv","pdf"])

with col2:
    bs_file = st.file_uploader("Upload Balance Sheet", type=["xlsx","csv","pdf"])

# ============================
# PROCESS P&L
# ============================
revenue, ebitda = 0, 0

if pl_file:
    df_raw = load_file(pl_file)

    if df_raw is None:
        st.error("❌ Could not extract usable data from file")
        st.stop()

    df, lc, ac = clean_dataframe(df_raw)
    df = standardize(df, lc, ac)
    df = classify_df(df)
    df = classify_df(df)

    # 🔍 DEBUG: see how items are classified
    st.subheader("🔍 Classification Breakdown")
    st.dataframe(df.groupby("Category")["Amount"].sum())
    st.dataframe(df.sort_values("Amount", key=abs, ascending=False).head(20))

    revenue = df[df.Category=="Revenue"]["Amount"].sum()
    cogs = df[df.Category=="COGS"]["Amount"].sum()
    opex = df[df.Category=="OpEx"]["Amount"].sum()

    ebitda = revenue - cogs - opex

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
        debt = df_bs[df_bs["Line Item"].str.contains("debt|loan|borrow", case=False)]["Amount"].sum()

        net_debt = debt - cash

# ============================
# SNAPSHOT
# ============================
if pl_file:
    st.header("📊 Investment Snapshot")

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
if pl_file:
    st.header("📈 Forecast")

    rev = revenue
    rows = []

    # 👇 Per-year margin inputs
    margins = []
    for y in range(1, holding_years + 1):
        m = st.sidebar.slider(f"EBITDA Margin Y{y} (%)", 0, 80, 20, key=f"margin_{y}")
        margins.append(m / 100)

    for y in range(1, holding_years + 1):
        rev *= (1 + growth_rate)

        margin = margins[y - 1]
        ebit = rev * margin

        rows.append([y, rev, ebit, margin * 100])

    f = pd.DataFrame(rows, columns=["Year", "Revenue", "EBITDA", "Margin %"])

    f["Year"] = f["Year"].apply(lambda x: f"Y{x}")
    f = f.set_index("Year")

    st.dataframe(
        f.style.format({
            "Revenue": "{:,.0f}",
            "EBITDA": "{:,.0f}",
            "Margin %": "{:.1f}%"
        })
    )

    exit_ebitda = f.iloc[-1]["EBITDA"]

# ============================
# 🏦 LBO MODEL
# ============================
if pl_file:

    st.header("🏦 LBO Analysis")

    # Entry
    entry_ev = ebitda * entry_multiple
    if bs_file:
        entry_debt = max(0, net_debt)   # use actual net debt
        entry_equity = entry_ev - entry_debt
    else:
        entry_debt = entry_ev * debt_pct
        entry_equity = entry_ev - entry_debt

    debt = entry_debt
    cash_flows = [-entry_equity]

    lbo_rows = []

    for i, row in f.iterrows():

        revenue = row["Revenue"]
        ebitda_y = row["EBITDA"]

        # Simple assumptions
        capex = revenue * capex_pct
        interest = debt * interest_rate

        ebt = ebitda_y - interest
        tax = max(0, ebt * tax_rate)

        # Cash available for debt paydown
        fcf = ebitda_y - capex - interest - tax

        debt_paydown = max(0, fcf)
        debt = max(0, debt - debt_paydown)

        cash_flows.append(fcf)

        lbo_rows.append([
            i,
            revenue,
            ebitda_y,
            fcf,
            debt
        ])

    # Exit
    exit_ebitda = f.iloc[-1]["EBITDA"]
    exit_ev = exit_ebitda * exit_multiple
    exit_equity = exit_ev - debt

    cash_flows[-1] += exit_equity  # add exit proceeds

    # IRR
    try:
        irr = npf.irr(cash_flows)
    except:
        irr = None

    moic = exit_equity / entry_equity if entry_equity else 0

    # Display LBO table
    lbo_df = pd.DataFrame(
        lbo_rows,
        columns=["Year", "Revenue", "EBITDA", "FCF", "Remaining Debt"]
    )

    lbo_df["Year"] = lbo_df["Year"].apply(lambda x: f"Y{x}")
    lbo_df = lbo_df.set_index("Year")

    st.dataframe(
        lbo_df.style.format({
            "Revenue": "{:,.0f}",
            "EBITDA": "{:,.0f}",
            "FCF": "{:,.0f}",
            "Remaining Debt": "{:,.0f}",
        })
    )

    # Metrics
    col1, col2 = st.columns(2)
    col1.metric("MOIC", f"{moic:.2f}x")
    if irr is not None:
        col2.metric("IRR", f"{irr*100:.2f}%")
    else:
        col2.metric("IRR", "N/A")
        
# ============================
# VALUATION
# ============================
    st.header("💰 Valuation")

    col1, col2 = st.columns(2)

    col1.metric("Entry EV", f"{entry_ev:,.0f}")
    col2.metric("Exit EV", f"{exit_ev:,.0f}")
