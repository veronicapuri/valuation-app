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
        if "amount" in " ".join(row.values):
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
    lines = df["Line Item"].astype(str).str.lower()

    def rule(item):
        item = item.lower()

        # 1️⃣ OTHER INCOME FIRST (IMPORTANT)
        if any(x in item for x in ["grant", "fx", "gain", "interest income"]):
            return "Other Income"

        # 2️⃣ REVENUE
        if any(x in item for x in ["revenue", "sales", "turnover"]):
            return "Revenue"

        # 3️⃣ COGS
        if any(x in item for x in ["cost of sales", "cogs", "materials"]):
            return "COGS"

        # 4️⃣ OPEX LAST
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
# UI
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

# FILES
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

    if df_raw is None:
        st.error("❌ Could not extract data")
        st.stop()

    df, lc, ac = clean_dataframe(df_raw)
    df = standardize(df, lc, ac)
    df = smart_classify(df)

    confidence = classification_confidence(df)

    if confidence < 0.7:
        st.warning("⚠️ Low classification confidence")

    with st.expander("🔍 Debug View"):
        st.dataframe(df)

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
        debt = df_bs[df_bs["Line Item"].str.contains("loan|borrow|debt|payable|liability", case=False)]["Amount"].sum()

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
