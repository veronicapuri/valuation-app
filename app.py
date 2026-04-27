# =========================================
# SME VALUATION & LBO TOOL v2.0
# =========================================
# Supports: xlsx, xls, csv, pdf
# Features: AI-assisted classification, memory, full LBO model
# =========================================
 
import streamlit as st
import pandas as pd
import numpy as np
import json, os
 
try:
    import pdfplumber
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
 
st.set_page_config(layout="wide", page_title="SME Valuation Tool", page_icon="📊")
 
# =========================================
# CONSTANTS
# =========================================
MEMORY_FILE = "memory.json"
 
PL_CATEGORIES  = ["Revenue", "COGS", "OpEx", "D&A", "Other Income", "Interest", "Tax", "Ignore"]
BS_CATEGORIES  = ["Cash", "Receivables", "Inventory", "Fixed Assets", "Debt", "Payables", "Equity", "Other"]
 
PL_KEYWORDS = {
    # Revenue: requires explicit revenue/sales/income-from words — NOT bare "income"
    "Revenue":      ["revenue", "sales", "turnover", "income from operation", "service fee",
                     "service income", "contract revenue", "fee income", "trading income",
                     "gross income", "operating income"],
    "COGS":         ["cost of", "cogs", "direct cost", "material", "purchase", "subcontract",
                     "cost of revenue", "cost of sales", "cost of goods"],
    "OpEx":         [
                     # Payroll & HR (broad "employee" and "staff" catch typos like "Benfits")
                     "salary", "salaries", "wage", "wages", "bonus", "payroll",
                     "staff cost", "manpower", "cpf", "contribution",
                     "employee", "staff benefit", "director salary", "director fee",
                     # Premises
                     "rent", "rental", "utilities", "cleaning", "renovation",
                     # Insurance
                     "insurance",
                     # Admin & Office
                     "admin", "general & admin", "office", "printing", "stationery",
                     "postage", "courier", "freight", "shipping",
                     # Sales & Marketing
                     "marketing", "advertising", "entertainment", "promotion",
                     # Professional services
                     "professional fee", "consultancy", "audit", "legal", "accounting",
                     # IT & Software
                     "subscription", "software", "stripe", "payment gateway",
                     "processing fee", "hosting",
                     # Banking (fees, not interest)
                     "bank fee", "bank charge", "bank revaluation",
                     # Travel & Transport
                     "travel", "transport", "motor vehicle", "parking",
                     # Government levies (Singapore)
                     "levy", "sdl", "skills development", "foreign worker levy",
                     # Bad debts & write-offs
                     "bad debt", "write off", "write-off", "doubtful",
                     # Repairs & maintenance
                     "maintenance", "repair", "upkeep",
                     # Other OpEx
                     "telephone", "internet", "communication", "allowance",
                     "commission", "discount",    # "discount given", "trade discount"
                    ],
    "D&A":          ["depreciation", "amortis", "amortiz", "d&a", "right-of-use",
                     "accumulated depreciation"],
    "Other Income": ["other income", "interest income", "dividend",
                     "gain on disposal", "gain on sale",
                     "foreign exchange gain", "forex gain", "miscellaneous income",
                     # Government grants (Singapore)
                     "govt grant", "government grant", "grant income", "subsidy",
                     "enterprise development", "psg grant", "mra grant",
                     # Currency gains
                     "realised currency", "unrealised currency", "currency gain",
                     "exchange gain", "fx gain", "revaluation gain",
                    ],
    "Interest":     ["interest expense", "finance cost", "finance charge",
                     "borrowing cost", "loan interest", "hire purchase interest"],
    "Tax":          ["income tax", "tax expense", "deferred tax", "zakat",
                     "corporate tax"],
    # Ignore: section headers, totals, subtotals
    "Ignore":       ["total", "net profit", "gross profit", "ebitda", "subtotal",
                     "pte", "ltd", "sdn bhd", "for the year", "as at", "nan", "none",
                     "operating profit", "operating expenses", "profit before",
                     "profit after", "loss before", "loss after", "cost of sales",
                     "other income", "trading income"],  # section headers
}
 
BS_KEYWORDS = {
    "Cash":         ["cash", "bank", "airwallex", "aspire", "maybank", "ocbc", "dbs", "uob",
                     "cimb", "paypal", "wise", "stripe", "fixed deposit", "petty cash"],
    "Receivables":  ["receivable", "debtor", "trade receivable", "other receivable",
                     "prepayment", "deposit paid", "advance paid", "amount owing from",
                     "owing from", "advance salaries", "raffles deposit"],
    "Inventory":    ["inventory", "stock", "work in progress", "wip", "finished goods"],
    "Fixed Assets": ["property", "plant", "equipment", "ppe", "fixed asset", "right-of-use",
                     "motor vehicle", "renovation", "machinery", "computer", "furniture",
                     "app development", "development cost", "less accumulated"],
    "Debt":         ["loan", "debt", "borrowing", "credit facility", "term loan",
                     "revolving", "bank overdraft", "hire purchase", "lease liabilit",
                     "amount owing to director", "director loan"],
    "Payables":     ["payable", "creditor", "trade payable", "accrual", "other payable",
                     "advance received", "deposit received", "sales tax", "gst", "vat",
                     "wages payable", "income tax payable", "regis"],
    "Equity":       ["equity", "share capital", "retained earning", "reserve",
                     "current year earning", "dividend", "owner"],
    # Section headers / totals — ignored in cash/debt sums
    "Ignore":       ["total", "net asset", "total asset", "total liabilit",
                     "current assets", "fixed assets", "current liabilities",
                     "long term", "non-current", "accumulated"],
}
 

# =========================================
# SAFE INITIALIZATION (FIXES NameError)
# =========================================
pl_metrics = None

cash_bs = 0.0
debt_bs = 0.0
receivables_bs = 0.0
inventory_bs = 0.0
payables_bs = 0.0

# =========================================
# MEMORY
# =========================================
def load_memory() -> dict:
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE) as f:
            return json.load(f)
    return {}
 
def save_memory(mem: dict):
    with open(MEMORY_FILE, "w") as f:
        json.dump(mem, f, indent=2)
 
 
# =========================================
# UNIVERSAL FILE READER
# =========================================
def read_any_file(uploaded_file) -> pd.DataFrame | None:
    """Read xlsx / xls / csv / pdf → raw DataFrame (no header assumed)."""
    name = uploaded_file.name.lower()
 
    if name.endswith(".csv"):
        df = pd.read_csv(uploaded_file, header=None, dtype=str)
 
    elif name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(uploaded_file, header=None, dtype=str)
 
    elif name.endswith(".pdf"):
        if not PDF_SUPPORT:
            st.error("📦 PDF support requires pdfplumber — run: pip install pdfplumber")
            return None
        tables = []
        with pdfplumber.open(uploaded_file) as pdf:
            for page in pdf.pages:
                for tbl in page.extract_tables():
                    if tbl:
                        tables.append(pd.DataFrame(tbl, dtype=str))
        if not tables:
            st.error("No tables detected in the PDF. Try converting to xlsx first.")
            return None
        df = pd.concat(tables, ignore_index=True)
    else:
        st.error(f"Unsupported file type: {name}")
        return None
 
    return df
 
 
# =========================================
# CLEANING PIPELINE
# =========================================
def dedupe_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols = pd.Series(df.columns.astype(str))
    for dup in cols[cols.duplicated()].unique():
        for i, idx in enumerate(cols[cols == dup].index):
            if i > 0:
                cols[idx] = f"{dup}_{i}"
    df.columns = cols
    return df
 
 
def parse_amount(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace(r"\(([0-9.,]+)\)", r"-\1", regex=True)   # (1,234) → -1234
        .str.replace(r"[^0-9.\-]", "", regex=True)
        .pipe(lambda s: pd.to_numeric(s, errors="coerce"))
        .fillna(0)
    )
 
 
def smart_clean(df: pd.DataFrame) -> pd.DataFrame:
    """
    Robustly extract (Line Item, Amount) from ANY financial statement layout.
 
    Handles two common Xero/QuickBooks export formats:
      - Single-label:  col0=label, col1=amount  (most P&Ls)
      - Nested-label:  col0=section, col1=label, col2=amount  (Xero BS)
 
    Strategy:
      1. Rename all columns numerically to avoid confusion.
      2. Score each column for numeric content → pick best_col (amount).
      3. Score remaining columns for non-numeric text content → pick label_col.
      4. Strip metadata rows (company name, dates, totals).
    """
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.fillna("").astype(str)
    df = dedupe_columns(df)
 
    # Give columns safe numeric names
    df.columns = [f"c{i}" for i in range(len(df.columns))]
 
    # ── 1. Find the best AMOUNT column (most non-zero numeric values) ──
    best_col, best_score = None, -1
    for col in df.columns:
        score = (parse_amount(df[col]) != 0).sum()
        if score > best_score:
            best_score, best_col = score, col
 
    # ── 2. Find the best LABEL column (most non-empty text, not the amount col) ──
    # We score by: non-empty cells minus cells that look numeric (amount-like)
    label_col, label_score = None, -1
    for col in df.columns:
        if col == best_col:
            continue
        non_empty = (df[col].str.strip() != "").sum()
        # Penalise columns that are mostly numeric
        numeric_count = (parse_amount(df[col]) != 0).sum()
        score = non_empty - numeric_count * 3   # heavy penalty for numeric cols
        if score > label_score:
            label_score, label_col = score, col
 
    result = pd.DataFrame({
        "Line Item": df[label_col].str.strip(),
        "Amount":    parse_amount(df[best_col]),
    })
 
    # ── 3. Drop metadata / empty / purely-numeric rows ──
    META = [
        "pte", "ltd", "sdn bhd", "berhad",
        "for the year", "as at", "as of", "balance sheet", "profit and loss",
        "income statement", "31 dec", "31 mar", "31 jan", "31 jun",
        "[fx]", "exchange rate", "usd", "sgd",
        # Column header row (e.g. "account", "31 dec 2025")
        "account",
    ]
    result = result[result["Line Item"] != ""]
    result = result[~result["Line Item"].str.lower().apply(
        lambda x: any(m in x for m in META)
    )]
    result = result[~result["Line Item"].str.fullmatch(r"[\d\s.,\-()%\[\]]+")]
    result = result.reset_index(drop=True)
 
    return result
 
 
# =========================================
# CLASSIFICATION — P&L
# =========================================
def keyword_classify_pl(item: str) -> str:
    x = str(item).lower().strip()
 
    if not x or x in ("nan", "none", ""):
        return "Ignore"
 
    # Check Ignore FIRST — section headers and totals must not bleed into other cats
    for kw in PL_KEYWORDS["Ignore"]:
        if kw in x:
            return "Ignore"
 
    # Then check in priority order
    for cat in ["Tax", "D&A", "Other Income", "Interest", "Revenue", "COGS", "OpEx"]:
        if any(kw in x for kw in PL_KEYWORDS[cat]):
            return cat
 
    return "Unknown"
 
 
def ai_classify_pl(items: list[str], api_key: str) -> dict:
    """Claude classifies a batch of unknown P&L line items."""
    if not items:
        return {}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            "You are a financial analyst. Classify each P&L line item into exactly one of:\n"
            "Revenue, COGS, OpEx, D&A, Other Income, Interest, Tax, Ignore\n\n"
            "Return ONLY a JSON object {\"line item\": \"Category\"}. No markdown, no explanation.\n\n"
            f"Items:\n{json.dumps(items)}"
        )
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return json.loads(resp.content[0].text)
    except Exception as e:
        st.warning(f"AI classification failed: {e}")
        return {}
 
 
def classify_pl(df: pd.DataFrame, use_ai: bool, api_key: str) -> pd.DataFrame:
    mem = load_memory()
    df["Category"] = df["Line Item"].apply(keyword_classify_pl)
 
    # Apply saved memory
    df["Category"] = df.apply(
        lambda r: mem.get(r["Line Item"], r["Category"]), axis=1
    )
 
    # AI for remaining unknowns
    if use_ai and api_key:
        unknowns = df[df["Category"] == "Unknown"]["Line Item"].tolist()
        if unknowns:
            ai_map = ai_classify_pl(unknowns, api_key)
            df["Category"] = df.apply(
                lambda r: ai_map.get(r["Line Item"], r["Category"])
                if r["Category"] == "Unknown" else r["Category"],
                axis=1,
            )
 
    return df
 
 
# =========================================
# METRICS — P&L
# =========================================
def compute_pl(df: pd.DataFrame) -> dict:
    def s(cat):
        return df.loc[df["Category"] == cat, "Amount"].sum()
 
    rev   = s("Revenue")
    cogs  = s("COGS")
    opex  = s("OpEx")
    da    = s("D&A")
    oi    = s("Other Income")
    int_  = s("Interest")
    tax   = s("Tax")
 
    gp      = rev - cogs
    ebitda  = gp - opex + oi
    ebit    = ebitda - da
    ebt     = ebit - int_
    net     = ebt - tax
 
    def pct(n, d=rev):
        return n / d if d else 0
 
    return {
        "Revenue":        rev,
        "COGS":           cogs,
        "Gross Profit":   gp,
        "GP Margin":      pct(gp),
        "OpEx":           opex,
        "D&A":            da,
        "Other Income":   oi,
        "EBITDA":         ebitda,
        "EBITDA Margin":  pct(ebitda),
        "EBIT":           ebit,
        "EBIT Margin":    pct(ebit),
        "Interest":       int_,
        "EBT":            ebt,
        "Tax":            tax,
        "Net Profit":     net,
        "Net Margin":     pct(net),
    }
 
 
# =========================================
# CLASSIFICATION — BALANCE SHEET
# =========================================
def classify_bs(df: pd.DataFrame) -> pd.DataFrame:
    cats = []
    for item in df["Line Item"].fillna("").astype(str).str.lower():
        cat = "Other"
        # Check Ignore first (section headers, totals)
        for kw in BS_KEYWORDS["Ignore"]:
            if kw in item:
                cat = "Ignore"
                break
        if cat == "Ignore":
            cats.append(cat)
            continue
        for c, keywords in BS_KEYWORDS.items():
            if c == "Ignore":
                continue
            if any(k in item for k in keywords):
                cat = c
                break
        cats.append(cat)
    df["Category"] = cats
    return df

# =========================================
# LBO ENGINE
# =========================================
bs_data = {
    "cash": cash_bs,
    "debt": debt_bs,
    "receivables": receivables_bs,
    "inventory": inventory_bs,
    "payables": payables_bs
}

lbo_df, returns = run_lbo(pl_metrics, bs_data, params)

cash_bs = bs.get("cash", 0)
debt_bs = bs.get("debt", 0)

ebitda = metrics.get("EBITDA", 0)
revenue = metrics.get("Revenue", 0)

entry_ev = ebitda * params["entry_multiple"]
total_debt = entry_ev * params["leverage_pct"]

tlb = total_debt * 0.85
revolver = total_debt * 0.15

equity_in = entry_ev - total_debt + (debt_bs - cash_bs)

cash = params["min_cash"]

prev_nwc = revenue * params["nwc_pct"]

rows = []

for i in range(params["years"]):

    rev = revenue * (1 + params["growth"]) ** (i + 1)
    ebitda_y = rev * params["margins"][i]

    da = rev * params["da_pct"]
    ebit = ebitda_y - da

    interest = tlb * params["tlb_rate"] + revolver * params["rev_rate"]
    tax = max(0, (ebit - interest) * params["tax_rate"])

    nwc = rev * params["nwc_pct"]
    delta_nwc = nwc - prev_nwc
    prev_nwc = nwc

    capex = rev * params["capex_pct"]

    fcf = ebitda_y - interest - tax - capex - delta_nwc

    cash += fcf

    if cash < params["min_cash"]:
        draw = params["min_cash"] - cash
        revolver += draw
        cash += draw

    excess = max(0, cash - params["min_cash"])

    pay_rev = min(revolver, excess)
    revolver -= pay_rev
    cash -= pay_rev

    excess = max(0, cash - params["min_cash"])

    pay_tlb = min(tlb, excess)
    tlb -= pay_tlb
    cash -= pay_tlb

    rows.append({
        "Year": i + 1,
        "Revenue": rev,
        "EBITDA": ebitda_y,
        "FCF": fcf,
        "TLB": tlb,
        "Revolver": revolver,
        "Cash": cash,
        "Net Debt": tlb + revolver - cash
    })

lbo_df = pd.DataFrame(rows)

last = lbo_df.iloc[-1]

exit_ev = last["EBITDA"] * params["exit_multiple"]
exit_equity = exit_ev - last["Net Debt"]

moic = exit_equity / equity_in if equity_in > 0 else 0
irr = moic ** (1 / params["years"]) - 1 if moic > 0 else 0

returns = {
    "MOIC": moic,
    "IRR": irr
}

return lbo_df, returns

# =========================================
# ---- LBO ----
# =========================================
if pl_metrics is not None and pl_metrics.get("EBITDA", 0) > 0:

    bs_data = {
        "cash": cash_bs,
        "debt": debt_bs,
        "receivables": receivables_bs,
        "inventory": inventory_bs,
        "payables": payables_bs
    }

    lbo_df, returns = run_lbo(pl_metrics, bs_data, params)

    # ---- OUTPUT ----
    st.subheader("📈 Returns Summary")

    c1, c2 = st.columns(2)
    c1.metric("MOIC", f"{returns['MOIC']:.2f}x")
    c2.metric("IRR", f"{returns['IRR']*100:.1f}%")

    st.subheader("📋 LBO Model")
    st.dataframe(lbo_df, use_container_width=True)

elif pl_metrics is not None:
    st.warning("⚠️ EBITDA is zero or negative — LBO not run.")

else:
    st.info("Upload a P&L file to run the LBO model.")

# =========================================
# FORMATTING HELPERS
# =========================================
def fmt(x: float, unit: str = "auto") -> str:
    if unit == "auto":
        if abs(x) >= 1_000_000:
            return f"${x/1_000_000:.2f}M"
        elif abs(x) >= 1_000:
            return f"${x/1_000:.0f}K"
        return f"${x:,.0f}"
    if unit == "pct":
        return f"{x*100:.1f}%"
    if unit == "x":
        return f"{x:.2f}x"
    return str(x)
 
FMT_LBO = {
    "Revenue":       "${:,.0f}",
    "EBITDA":        "${:,.0f}",
    "EBITDA Margin": "{:.1%}",
    "Interest":      "${:,.0f}",
    "Tax":           "${:,.0f}",
    "CapEx":         "${:,.0f}",
    "ΔNWC":          "${:,.0f}",
    "FCF":           "${:,.0f}",
    "TLB":           "${:,.0f}",
    "Revolver":      "${:,.0f}",
    "Cash":          "${:,.0f}",
    "Net Debt":      "${:,.0f}",
}
 
 
# =========================================
# ---- SIDEBAR ----
# =========================================
st.sidebar.header("⚙️ Deal Parameters")
 
with st.sidebar.expander("🤖 AI Classification (optional)"):
    api_key = st.text_input("Anthropic API Key", type="password",
                            help="Used only to classify unknown P&L line items")
    use_ai  = st.checkbox("Enable AI classification", value=bool(api_key))
 
st.sidebar.markdown("---")
st.sidebar.subheader("Valuation")
entry_multiple = st.sidebar.number_input("Entry EV/EBITDA", 3.0, 20.0, 5.0, 0.5)
exit_multiple  = st.sidebar.number_input("Exit EV/EBITDA",  3.0, 20.0, 7.0, 0.5)
 
st.sidebar.subheader("Holding Period & Growth")
years  = st.sidebar.slider("Holding Period (years)", 1, 7, 5)
growth = st.sidebar.slider("Revenue Growth % p.a.", 0, 40, 10) / 100
 
margin_mode = st.sidebar.radio("EBITDA Margin Input", ["Flat", "Per Year"], horizontal=True)
if margin_mode == "Flat":
    flat_m = st.sidebar.slider("EBITDA Margin %", 0, 60, 20) / 100
    margins = [flat_m] * years
else:
    margins = [st.sidebar.slider(f"Y{i+1} EBITDA Margin %", 0, 60, 20 + i) / 100
               for i in range(years)]
 
st.sidebar.subheader("Capital Structure")
leverage_pct = st.sidebar.slider("Leverage % of Entry EV", 20, 80, 60) / 100
tlb_rate     = st.sidebar.slider("TLB Interest Rate %", 0, 20, 7) / 100
rev_rate     = st.sidebar.slider("Revolver Rate %", 0, 20, 6) / 100
 
st.sidebar.subheader("Other Assumptions")
tax_rate  = st.sidebar.slider("Tax Rate %", 0, 35, 17) / 100
da_pct    = st.sidebar.slider("D&A % of Revenue (if not in P&L)", 0, 15, 3) / 100
nwc_pct   = st.sidebar.slider("NWC % of Revenue",  0, 20, 5) / 100
capex_pct = st.sidebar.slider("CapEx % of Revenue", 0, 20, 5) / 100
min_cash  = st.sidebar.number_input("Minimum Cash ($)", 0, value=50_000, step=10_000)
 
params = dict(
    entry_multiple = entry_multiple,
    exit_multiple  = exit_multiple,
    years          = years,
    growth         = growth,
    margins        = margins,
    leverage_pct   = leverage_pct,
    tlb_rate       = tlb_rate,
    rev_rate       = rev_rate,
    tax_rate       = tax_rate,
    da_pct         = da_pct,
    nwc_pct        = nwc_pct,
    capex_pct      = capex_pct,
    min_cash       = float(min_cash),
)
 
 
# =========================================
# ---- MAIN PAGE ----
# =========================================
st.title("📊 SME Valuation & LBO Tool")
st.caption("Upload any P&L and Balance Sheet to generate a full LBO valuation. "
           "Supports .xlsx, .xls, .csv, and .pdf.")
st.markdown("---")
 
st.header("📂 Step 1 — Upload Financials")
col_pl, col_bs = st.columns(2)
 
with col_pl:
    pl_file = st.file_uploader("P&L Statement", type=["xlsx", "xls", "csv", "pdf"])
 
with col_bs:
    bs_file = st.file_uploader("Balance Sheet", type=["xlsx", "xls", "csv", "pdf"])
 
 
# =========================================
# PROCESS P&L  (single pass — no duplicate reads)
# =========================================
pl_metrics = None
 
if pl_file:
    raw_pl = read_any_file(pl_file)
 
    if raw_pl is not None:
        df_pl = smart_clean(raw_pl)
        df_pl = classify_pl(df_pl, use_ai=use_ai, api_key=api_key or "")
 
        st.markdown("---")
        st.header("📋 Step 2 — Review & Correct Classifications")
        st.caption("Use the dropdowns to fix any misclassified rows. Corrections are saved automatically.")
 
        unknown_count = (df_pl["Category"] == "Unknown").sum()
        if unknown_count:
            st.warning(f"⚠️ {unknown_count} line item(s) could not be classified automatically. "
                       "Please assign them below, or enable AI classification in the sidebar.")
 
        df_pl = st.data_editor(
            df_pl,
            column_config={
                "Category": st.column_config.SelectboxColumn("Category", options=PL_CATEGORIES),
                "Amount":   st.column_config.NumberColumn("Amount", format="$ %.0f"),
            },
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
        )
 
        # Persist corrections to memory
        mem = load_memory()
        for _, r in df_pl.iterrows():
            if r["Category"] != "Unknown":
                mem[r["Line Item"]] = r["Category"]
        save_memory(mem)
 
        active_pl = df_pl[~df_pl["Category"].isin(["Ignore", "Unknown"])]
        pl_metrics = compute_pl(active_pl)
 
 
# =========================================
# PROCESS BALANCE SHEET
# =========================================
cash_bs = 0.0
debt_bs = 0.0
receivables_bs = 0.0
inventory_bs = 0.0
payables_bs = 0.0
 
if bs_file:
    raw_bs = read_any_file(bs_file)
 
    if raw_bs is not None:
        df_bs = smart_clean(raw_bs)
        df_bs = classify_bs(df_bs)
        receivables_bs = df_bs.loc[df_bs["Category"] == "Receivables", "Amount"].sum()
        inventory_bs   = df_bs.loc[df_bs["Category"] == "Inventory",   "Amount"].sum()
        payables_bs    = df_bs.loc[df_bs["Category"] == "Payables",    "Amount"].sum()
 
        with st.expander("🔍 Balance Sheet Preview", expanded=False):
            st.dataframe(df_bs, use_container_width=True, hide_index=True)
 
        cash_bs = df_bs.loc[df_bs["Category"] == "Cash",  "Amount"].sum()
        debt_bs = df_bs.loc[df_bs["Category"] == "Debt",  "Amount"].sum()
 
 
# =========================================
# VALUATION OUTPUT
# =========================================
if pl_metrics:
    st.markdown("---")
    st.header("📊 Step 3 — Valuation Output")
 
    # ---- P&L Snapshot ----
    st.subheader("P&L Snapshot")
    m = pl_metrics
 
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Revenue",      fmt(m["Revenue"]))
    c2.metric("Gross Profit", fmt(m["Gross Profit"]),  fmt(m["GP Margin"], "pct"))
    c3.metric("EBITDA",       fmt(m["EBITDA"]),         fmt(m["EBITDA Margin"], "pct"))
    c4.metric("EBIT",         fmt(m["EBIT"]),           fmt(m["EBIT Margin"], "pct"))
    c5.metric("Net Profit",   fmt(m["Net Profit"]),     fmt(m["Net Margin"], "pct"))
 
    # ---- P&L Waterfall table ----
    with st.expander("📄 Full P&L Bridge"):
        pl_bridge = pd.DataFrame([
            {"Item": "Revenue",        "Amount": m["Revenue"]},
            {"Item": "(-)  COGS",      "Amount": -m["COGS"]},
            {"Item": "Gross Profit",   "Amount": m["Gross Profit"]},
            {"Item": "(-)  OpEx",      "Amount": -m["OpEx"]},
            {"Item": "(-)  D&A",       "Amount": -m["D&A"]},
            {"Item": "(+) Other Inc.", "Amount":  m["Other Income"]},
            {"Item": "EBITDA",         "Amount": m["EBITDA"]},
            {"Item": "(-)  Interest",  "Amount": -m["Interest"]},
            {"Item": "(-)  Tax",       "Amount": -m["Tax"]},
            {"Item": "Net Profit",     "Amount": m["Net Profit"]},
        ])
        pl_bridge["Amount"] = pl_bridge["Amount"].apply(fmt)
        st.dataframe(pl_bridge, use_container_width=True, hide_index=True)
 
    # ---- Balance Sheet summary ----
    if bs_file:
        st.subheader("Balance Sheet Snapshot")
        b1, b2, b3 = st.columns(3)
        b1.metric("Cash & Equivalents", fmt(cash_bs))
        b2.metric("Total Debt",         fmt(debt_bs))
        b3.metric("Net Debt",           fmt(debt_bs - cash_bs))
 
    st.markdown("---")
 
    # ---- LBO ----
    ebitda  = m["EBITDA"]
    revenue = m["Revenue"]
 
    if ebitda <= 0:
        st.error("⚠️ EBITDA is zero or negative. LBO model cannot run. "
                 "Check that Revenue and COGS/OpEx rows are classified correctly.")
    else:
        bs_data = {
            "cash": cash_bs,
            "debt": debt_bs,
            "receivables": receivables_bs,
            "inventory": inventory_bs,
            "payables": payables_bs
        }
        
        lbo_df, returns = run_lbo(pl_metrics, bs_data, params)
 
        # Returns headline
        st.subheader("📈 Returns Summary")
        r1, r2, r3, r4, r5 = st.columns(5)
        r1.metric("Entry EV",   fmt(returns["Entry EV"]))
        r2.metric("Equity In",  fmt(returns["Equity In"]))
        r3.metric("Exit EV",    fmt(returns["Exit EV"]))
        r4.metric("MOIC",       fmt(returns["MOIC"], "x"))
        r5.metric("IRR",        fmt(returns["IRR"],  "pct"))
 
        # Sensitivity table — MOIC grid
        st.subheader("🔢 Sensitivity: MOIC")
        entry_range = [entry_multiple - 1, entry_multiple, entry_multiple + 1]
        exit_range  = [exit_multiple  - 1, exit_multiple,  exit_multiple  + 1]
 
        rows_sens = []
        for em in entry_range:
            row = {"Entry \\ Exit": f"{em:.1f}x"}
            for xm in exit_range:
                p2 = {**params, "entry_multiple": em, "exit_multiple": xm}
                _, ret2 = run_lbo(pl_metrics, bs_data, p2)
                row[f"Exit {xm:.1f}x"] = f"{ret2['MOIC']:.2f}x"
            rows_sens.append(row)
 
        st.dataframe(pd.DataFrame(rows_sens).set_index("Entry \\ Exit"),
                     use_container_width=True)
 
        # LBO model table
        st.subheader("📋 LBO Model")
        st.dataframe(
            lbo_df.style.format(FMT_LBO),
            use_container_width=True,
            hide_index=True,
        )
 
        # Valuation bridge
        with st.expander("🏗️ Valuation Bridge"):
            bridge = pd.DataFrame([
                {"Item": "Entry EV",               "Value": fmt(returns["Entry EV"])},
                {"Item": "  (-) Total Debt",        "Value": fmt(returns["Debt"])},
                {"Item": "  (+) Balance Sheet Cash","Value": fmt(cash_bs)},
                {"Item": "  (-) Balance Sheet Debt","Value": fmt(debt_bs)},
                {"Item": "Equity Invested",         "Value": fmt(returns["Equity In"])},
                {"Item": "────────────────",        "Value": ""},
                {"Item": "Exit EV",                 "Value": fmt(returns["Exit EV"])},
                {"Item": "  (-) Exit Net Debt",     "Value": fmt(returns["Exit EV"] - returns["Exit Equity"])},
                {"Item": "Exit Equity",             "Value": fmt(returns["Exit Equity"])},
                {"Item": "────────────────",        "Value": ""},
                {"Item": "MOIC",                    "Value": fmt(returns["MOIC"], "x")},
                {"Item": "IRR",                     "Value": fmt(returns["IRR"],  "pct")},
            ])
            st.dataframe(bridge, use_container_width=True, hide_index=True)
 
elif not pl_file:
    st.info("👆 Upload a P&L statement above to get started.")
