import streamlit as st
import pandas as pd
import numpy as np
import json, os, re, io

# ── Optional imports ──────────────────────────────────────────────────────────
try:
    import pdfplumber
    PDF_DIGITAL = True
except ImportError:
    PDF_DIGITAL = False

try:
    from pdf2image import convert_from_bytes
    import pytesseract
    PDF_OCR = True
except ImportError:
    PDF_OCR = False

USE_ADOBE = False  # turn on only if needed

def adobe_extract(file_bytes):
    # Placeholder — prevents crashes if toggled on accidentally
    raise NotImplementedError("Adobe extraction not implemented yet")

st.set_page_config(layout="wide", page_title="SME Valuation Tool", page_icon="📊")

# =========================================
# CONSTANTS
# =========================================
MEMORY_FILE = "memory.json"

PL_CATEGORIES = ["Revenue", "COGS", "OpEx", "D&A", "Other Income",
                 "Interest", "Tax", "Ignore"]
BS_CATEGORIES = ["Cash", "Receivables", "Inventory", "Fixed Assets",
                 "Debt", "Payables", "Equity", "Ignore", "Other"]

# ── P&L keyword map ───────────────────────────────────────────────────────────
PL_KEYWORDS = {
    "Revenue": [
        "revenue", "sales", "turnover", "income from operation",
        "service fee", "service income", "contract revenue", "fee income",
        "gross income",
    ],
    "COGS": [
        "cost of", "cogs", "direct cost", "subcontract",
        "cost of revenue", "cost of goods",
    ],
    "OpEx": [
        "salary", "salaries", "wage", "wages", "bonus", "payroll",
        "staff cost", "manpower", "cpf", "contribution",
        "employee", "director salary", "director fee",
        "rent", "rental", "utilities", "cleaning", "renovation",
        "insurance",
        "admin", "general & admin", "office", "printing", "stationery",
        "postage", "courier", "freight", "shipping",
        "marketing", "advertising", "entertainment", "promotion",
        "professional fee", "consultancy", "audit", "legal", "accounting",
        "subscription", "software", "stripe", "payment gateway",
        "processing fee", "hosting",
        "bank fee", "bank charge", "bank revaluation",
        "travel", "transport", "motor vehicle", "parking",
        "levy", "sdl", "skills development", "foreign worker levy",
        "bad debt", "write off", "write-off", "doubtful",
        "maintenance", "repair", "upkeep",
        "telephone", "internet", "communication", "allowance",
        "commission", "discount",
    ],
    "D&A": [
        "depreciation", "amortis", "amortiz", "d&a", "right-of-use",
    ],
    "Other Income": [
        "other income", "interest income", "dividend",
        "gain on disposal", "gain on sale",
        "foreign exchange gain", "forex gain", "miscellaneous income",
        "govt grant", "government grant", "grant income", "subsidy",
        "enterprise development", "psg grant", "mra grant",
        "realised currency", "unrealised currency", "currency gain",
        "exchange gain", "fx gain", "revaluation gain",
    ],
    "Interest": [
        "interest expense", "finance cost", "finance charge",
        "borrowing cost", "loan interest", "hire purchase interest",
    ],
    "Tax": [
        "income tax", "tax expense", "deferred tax", "zakat", "corporate tax",
    ],
    "Ignore": [
        "total", "net profit", "gross profit", "ebitda", "subtotal",
        "pte", "ltd", "sdn bhd", "for the year", "as at", "nan", "none",
        "operating profit", "operating expenses",
        "profit before", "profit after", "loss before", "loss after",
        "cost of sales", "other income", "trading income",
    ],
}

# ── BS keyword map ────────────────────────────────────────────────────────────
BS_KEYWORDS = {
    "Cash": [
        "cash", "bank", "fixed deposit",
        "airwallex", "aspire", "maybank", "ocbc", "dbs", "uob", "cimb",
        "paypal", "wise", "revolut", "stripe", "grabpay", "petty cash",
    ],
    "Receivables": [
        "receivable", "debtor", "trade receivable", "other receivable",
        "prepayment", "deposit paid", "advance paid",
        "amount owing from", "owing from",
        "advance salaries", "raffles deposit",
    ],
    "Inventory": ["inventory", "stock", "work in progress", "wip", "finished goods"],
    "Fixed Assets": [
        "property", "plant", "equipment", "ppe", "fixed asset", "right-of-use",
        "motor vehicle", "renovation", "machinery", "computer", "furniture",
        "app development", "development cost", "less accumulated",
    ],
    "Debt": [
        "loan", "debt", "borrowing", "credit facility", "term loan",
        "revolving", "bank overdraft", "hire purchase", "lease liabilit",
        "amount owing to director", "director loan",
    ],
    "Payables": [
        "payable", "creditor", "trade payable", "accrual", "other payable",
        "advance received", "deposit received", "sales tax", "gst", "vat",
        "wages payable", "income tax payable", "regis",
    ],
    "Equity": [
        "equity", "share capital", "retained earning", "reserve",
        "current year earning", "dividend", "owner",
    ],
    "Ignore": [
        "total", "net asset", "total asset", "total liabilit",
        "current assets", "fixed assets", "current liabilities",
        "long term", "non-current",
    ],
}

# Section header text → BS category it introduces
BS_SECTION_TRIGGERS = {
    "bank":                "Cash",
    "current assets":      "Receivables",
    "fixed assets":        "Fixed Assets",
    "current liabilities": "Payables",
    "long term liabilit":  "Debt",
    "equity":              "Equity",
}


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
# FILE READER  (digital PDF → OCR fallback)
# =========================================
def _ocr_pdf(file_bytes: bytes) -> pd.DataFrame | None:
    """
    OCR pipeline for scanned / image-only PDFs.
    Converts each page to a 300-dpi image, runs Tesseract,
    then parses each text line into (label, amount) pairs.
    """
    if not PDF_OCR:
        st.error("📦 OCR requires pdf2image + pytesseract.\n"
                 "Run: pip install pdf2image pytesseract")
        return None

    try:
        images = convert_from_bytes(file_bytes, dpi=300)
    except Exception as e:
        st.error(f"PDF→image conversion failed: {e}")
        return None

    rows = []
    for img in images:
        text = pytesseract.image_to_string(img, config="--psm 6")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # Find trailing number (amount)
            m = re.search(
                r"(-?\s*[\d,]+\.?\d*|\([\d,]+\.?\d*\))\s*$", line
            )
            if m:
                label = re.sub(r"[|_]{2,}", "", line[: m.start()]).strip()
                if label:
                    rows.append([label, m.group(0).strip()])
            else:
                clean = re.sub(r"[|_]{2,}", "", line).strip()
                if clean:
                    rows.append([clean, "0"])

    if not rows:
        st.error("OCR produced no usable rows. Check PDF quality.")
        return None

    return pd.DataFrame(rows, columns=["c0", "c1"], dtype=str)


def read_any_file(uploaded_file) -> "pd.DataFrame | None":
    """
    Read xlsx / xls / csv / digital-PDF / scanned-PDF → raw DataFrame.
    PDF strategy: try pdfplumber tables first; fall back to OCR.
    """
    name = uploaded_file.name.lower()

    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file, header=None, dtype=str)

    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file, header=None, dtype=str)

    if name.endswith(".pdf"):
        file_bytes = uploaded_file.read()
    
        # 🔥 STEP 0: Optional Adobe preprocessing
        if USE_ADOBE:
            try:
                st.info("☁️ Using Adobe extraction...")
                return adobe_extract(file_bytes)
            except Exception:
                st.warning("⚠️ Adobe failed — falling back to local parsing")
    
        # Strategy 1: digital text tables
        if PDF_DIGITAL:
            try:
                tables = []
                with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                    for page in pdf.pages:
                        for tbl in (page.extract_tables() or []):
                            if tbl:
                                tables.append(pd.DataFrame(tbl, dtype=str))
                if tables:
                    return pd.concat(tables, ignore_index=True)
            except Exception:
                pass
    
        # Strategy 2: OCR fallback
        st.info("📷 No digital tables found — running OCR on scanned PDF…")
        return _ocr_pdf(file_bytes)
    
    st.error(f"Unsupported file type: {name}")
    return None


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
        .str.replace(r"\(([0-9.,]+)\)", r"-\1", regex=True)
        .str.replace(r"[^0-9.\-]", "", regex=True)
        .pipe(lambda s: pd.to_numeric(s, errors="coerce"))
        .fillna(0)
    )

def score_amount_column(series):
    nums = parse_amount(series)

    # Remove zeros
    nums = nums[nums != 0]

    if len(nums) == 0:
        return -1

    abs_vals = np.abs(nums)

    # 🔥 MUCH stronger signal
    score = (
        len(abs_vals) * 5
        + np.log1p(abs_vals.sum()) * 3
        + np.log1p(np.median(abs_vals)) * 5
        + np.max(abs_vals) * 0.00001   # <-- KEY: pushes large columns to win
    )

    return score

def detect_column_confidence(df):
    scores = {}

    for col in df.columns:
        scores[col] = score_amount_column(df[col])

    # Sort columns by score
    sorted_cols = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    if len(sorted_cols) < 2:
        return 1.0, sorted_cols

    best = sorted_cols[0][1]
    second = sorted_cols[1][1]

    # Confidence = how much better best is than second
    confidence = (best - second) / (abs(best) + 1e-6)

    return confidence, sorted_cols

def merge_multiline_rows(df):
    merged = []
    buffer = ""

    for _, row in df.iterrows():
        label = str(row["Line Item"]).strip()
        amount = row["Amount"]

        if amount == 0 and len(label.split()) < 5:
            buffer += " " + label
        else:
            full_label = (buffer + " " + label).strip()
            merged.append([full_label, amount])
            buffer = ""

    return pd.DataFrame(merged, columns=["Line Item", "Amount"])
    
# ── Metadata row detection ────────────────────────────────────────────────────
# Uses explicit phrase matching — NOT substring matching on short tokens like
# "sgd", "usd" — to avoid dropping valid account names like "AirWallex(SGD)".
_META_EXACT = {"account", "accounts", "nan", "none", ""}
_META_PHRASES = [
    "pte. ltd.", "pte ltd", "sdn bhd", "berhad",
    "for the year", "for the period",
    "as at ", "as of ",
    "balance sheet", "profit and loss", "income statement",
    "exchange rate", "rates are provided",
    "prepared by", "reviewed by",
]
_DATE_RE = re.compile(
    r"^(31|30|28|29)?\s*"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*\d{4}$",
    re.I,
)


def _is_meta_row(label: str) -> bool:
    x = label.strip()
    xl = x.lower()
    if xl in _META_EXACT:
        return True
    if re.fullmatch(r"[\d\s\-/]+", xl):   # pure date/year numbers
        return True
    if _DATE_RE.match(xl):
        return True
    return any(p in xl for p in _META_PHRASES)

def smart_clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.fillna("").astype(str)
    df = dedupe_columns(df)
    df.columns = [f"c{i}" for i in range(len(df.columns))]


    # ── OCR fallback: handle single-column messy PDFs ─────────────────────────
    if df.shape[1] == 1:
        raw_col = df.iloc[:, 0].astype(str)
    
        def extract_label_and_numbers(text):
            numbers = re.findall(r"\(?-?\d[\d,]*\.?\d*\)?", text)
            numbers = [n for n in numbers if re.search(r"\d", n)]
            
            # 🔥 NEW: remove small "noise" numbers (like 10, 11, 12)
            clean_numbers = []
            for n in numbers:
                val = float(n.replace(",", "").replace("(", "-").replace(")", ""))
                
                # Keep only meaningful financial values
                if abs(val) >= 1:
                    clean_numbers.append(n)
            
            numbers = clean_numbers
    
            # Remove ALL numbers from label
            label = text
            for num in numbers:
                label = label.replace(num, "")
    
            label = re.sub(r"[-–—]+$", "", label).strip()
    
            return [label] + numbers  # keep ALL numbers
    
        rows = raw_col.apply(extract_label_and_numbers)
    
        # Find max number of columns needed
        max_len = rows.apply(len).max()
    
        # Pad rows so all same length
        rows = rows.apply(lambda x: x + [""] * (max_len - len(x)))
    
        # Build dataframe
        df = pd.DataFrame(rows.tolist())
    
        # Rename columns → c0 = label, rest = numeric candidates
        df.columns = [f"c{i}" for i in range(len(df.columns))]
   
    def detect_column_confidence(df):
        scores = {}
    
        for col in df.columns:
            scores[col] = score_amount_column(df[col])
    
        # Sort columns by score
        sorted_cols = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    
        if len(sorted_cols) < 2:
            return 1.0, sorted_cols
    
        best = sorted_cols[0][1]
        second = sorted_cols[1][1]
    
        # Confidence = how much better best is than second
        confidence = (best - second) / (abs(best) + 1e-6)
    
        return confidence, sorted_cols
    
    # ── Detect amount column ──────────────────────────────────────────────────
    best_col, best_score = None, -1
    
    for col in df.columns:
        score = score_amount_column(df[col])
    
        if score > best_score:
            best_score, best_col = score, col

    confidence, ranked_cols = detect_column_confidence(df)
    
    # Optional debug
    st.write("Column ranking:", ranked_cols)
    st.write("Confidence:", confidence)
    
    if confidence < 0.15:
        st.warning("⚠️ Low confidence in detected amount column — please review")

    # ── Detect label column ───────────────────────────────────────────────────
    label_col, label_score = None, -1
    for col in df.columns:
        if col == best_col:
            continue
        non_empty = (df[col].str.strip() != "").sum()
        numeric   = (parse_amount(df[col]) != 0).sum()
        score     = non_empty - numeric * 3
        if score > label_score:
            label_score, label_col = score, col

    # ── Fallbacks (CRITICAL) ──────────────────────────────────────────────────
    if label_col is None:
        st.warning("⚠️ Could not detect label column — using first column.")
        label_col = df.columns[0]

    if best_col is None:
        st.error("❌ Could not detect amount column.")
        return None

    # ── Build final table ─────────────────────────────────────────────────────
    result = pd.DataFrame({
        "Line Item": df[label_col].astype(str).str.strip(),
        "Amount": parse_amount(df[best_col]),
    })
    result = result[result["Line Item"].apply(lambda x: not _is_meta_row(x))]
    result = result[~result["Line Item"].str.lower().str.contains(
        r"statement|note|\$\$|comprehensive income"
    )]
    result = result[~result["Line Item"].str.fullmatch(r"[\d\s.,\-()%\[\]]+")]
    result = result.reset_index(drop=True)
    result = merge_multiline_rows(result)
    return result

# =========================================
# CLASSIFICATION — P&L
# =========================================
def keyword_classify_pl(item: str) -> str:
    x = str(item).lower().strip()
    if not x or x in ("nan", "none"):
        return "Ignore"
    for kw in PL_KEYWORDS["Ignore"]:
        if kw in x:
            return "Ignore"
    for cat in ["Tax", "D&A", "Interest", "Other Income", "Revenue", "COGS", "OpEx"]:
        if any(kw in x for kw in PL_KEYWORDS[cat]):
            return cat
    return "Unknown"


def ai_classify_pl(items: list, api_key: str) -> dict:
    if not items:
        return {}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            "You are a financial analyst. Classify each P&L line item into "
            "exactly one of:\n"
            "Revenue, COGS, OpEx, D&A, Other Income, Interest, Tax, Ignore\n\n"
            "Return ONLY a JSON object {\"line item\": \"Category\"}. "
            "No markdown, no explanation.\n\n"
            f"Items:\n{json.dumps(items)}"
        )
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = re.sub(r"^```[a-z]*\n?", "", resp.content[0].text.strip()).rstrip("`").strip()
        return json.loads(raw)
    except Exception as e:
        st.warning(f"AI classification failed: {e}")
        return {}


def classify_pl(df: pd.DataFrame, use_ai: bool, api_key: str) -> pd.DataFrame:
    mem = load_memory()

    # Step 1: keyword
    df["Category"] = df["Line Item"].apply(keyword_classify_pl)

    # Step 2: memory — ONLY for Unknown (never overwrite correct classifications)
    df["Category"] = df.apply(
        lambda r: mem.get(r["Line Item"], r["Category"])
        if r["Category"] == "Unknown" else r["Category"],
        axis=1,
    )

    # Step 3: AI for remaining unknowns
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

    rev  = s("Revenue");  cogs = s("COGS");   opex = s("OpEx")
    da   = s("D&A");      oi   = s("Other Income")
    int_ = s("Interest"); tax  = s("Tax")

    gp     = rev - cogs
    ebitda = gp - opex + oi
    ebit   = ebitda - da
    ebt    = ebit - int_
    net    = ebt - tax

    def pct(n, d=rev):
        return n / d if d else 0

    return {
        "Revenue": rev, "COGS": cogs, "Gross Profit": gp, "GP Margin": pct(gp),
        "OpEx": opex, "D&A": da, "Other Income": oi,
        "EBITDA": ebitda, "EBITDA Margin": pct(ebitda),
        "EBIT": ebit, "EBIT Margin": pct(ebit),
        "Interest": int_, "EBT": ebt, "Tax": tax,
        "Net Profit": net, "Net Margin": pct(net),
    }


# =========================================
# CLASSIFICATION — BALANCE SHEET
# =========================================
def classify_bs(df: pd.DataFrame) -> pd.DataFrame:
    """
    Two-pass BS classification:
      1. Keyword matching (Ignore checked first).
      2. Section-context fallback: items that are still "Other" inherit
         the category of the most recent section header.
         This catches company-named bank accounts and un-named sub-accounts.
    """
    cats = []
    current_section = None   # tracks which BS section we're currently in

    for item in df["Line Item"].fillna("").astype(str):
        x   = item.lower().strip()
        cat = "Other"

        # ── Always update section context FIRST ─────────────────────────────
        # "Bank" header classifies as Cash keyword AND sets current_section.
        # Run this before keyword matching so unnamed sub-accounts below it
        # (e.g. "Jaanik Business Solutions") inherit the correct context.
        for trigger, section in BS_SECTION_TRIGGERS.items():
            if trigger in x:
                current_section = section
                break

        # ── Ignore (totals / section dividers) ───────────────────────────────
        if any(kw in x for kw in BS_KEYWORDS["Ignore"]):
            cats.append("Ignore")
            continue

        # ── Keyword match ─────────────────────────────────────────────────────
        for c, keywords in BS_KEYWORDS.items():
            if c == "Ignore":
                continue
            if any(k in x for k in keywords):
                cat = c
                break

        # ── Section-context fallback for unrecognised accounts ────────────────
        if cat == "Other" and current_section is not None:
            cat = current_section

        cats.append(cat)

    df = df.copy()
    df["Category"] = cats
    return df


# =========================================
# LBO ENGINE
# =========================================
def run_lbo(metrics: dict, cash_bs: float, debt_bs: float,
            params: dict) -> "tuple[pd.DataFrame, dict]":

    ebitda  = metrics["EBITDA"]
    revenue = metrics["Revenue"]

    entry_ev   = ebitda * params["entry_multiple"]
    total_debt = entry_ev * params["leverage_pct"]
    tlb        = total_debt * 0.85
    revolver   = total_debt * 0.15

    equity_in = entry_ev - total_debt + max(0.0, debt_bs - cash_bs)

    cash     = float(params["min_cash"])
    prev_nwc = revenue * params["nwc_pct"]
    rows     = []

    for i in range(params["years"]):
        rev      = revenue * (1 + params["growth"]) ** (i + 1)
        ebitda_y = rev * params["margins"][i]
        da_y     = rev * params["da_pct"]
        ebit     = ebitda_y - da_y

        interest = tlb * params["tlb_rate"] + revolver * params["rev_rate"]
        tax      = max(0.0, (ebit - interest) * params["tax_rate"])

        nwc       = rev * params["nwc_pct"]
        delta_nwc = nwc - prev_nwc
        prev_nwc  = nwc
        capex     = rev * params["capex_pct"]

        fcf   = ebitda_y - interest - tax - capex - delta_nwc
        cash += fcf

        if cash < params["min_cash"]:
            draw = params["min_cash"] - cash
            revolver += draw
            cash     += draw

        excess    = max(0.0, cash - params["min_cash"])
        pay_rev   = min(revolver, excess)
        revolver -= pay_rev
        cash     -= pay_rev

        excess    = max(0.0, cash - params["min_cash"])
        pay_tlb   = min(tlb, excess)
        tlb      -= pay_tlb
        cash     -= pay_tlb

        rows.append({
            "Year": i + 1, "Revenue": rev, "EBITDA": ebitda_y,
            "EBITDA Margin": ebitda_y / rev if rev else 0,
            "Interest": interest, "Tax": tax,
            "CapEx": capex, "ΔNWC": delta_nwc, "FCF": fcf,
            "TLB": tlb, "Revolver": revolver, "Cash": cash,
            "Net Debt": tlb + revolver - cash,
        })

    lbo_df = pd.DataFrame(rows)
    last   = lbo_df.iloc[-1]

    exit_ev     = last["EBITDA"] * params["exit_multiple"]
    exit_equity = exit_ev - last["Net Debt"]
    moic        = exit_equity / equity_in if equity_in > 0 else 0
    irr         = moic ** (1 / params["years"]) - 1 if moic > 0 else 0

    return lbo_df, {
        "Entry EV": entry_ev, "Total Debt": total_debt, "Equity In": equity_in,
        "Exit EV": exit_ev, "Exit Equity": exit_equity, "MOIC": moic, "IRR": irr,
    }


# =========================================
# FORMATTING
# =========================================
def fmt(x: float, unit: str = "auto") -> str:
    if unit == "auto":
        if abs(x) >= 1_000_000:
            return f"${x/1_000_000:.2f}M"
        if abs(x) >= 1_000:
            return f"${x/1_000:.0f}K"
        return f"${x:,.0f}"
    if unit == "pct":
        return f"{x*100:.1f}%"
    if unit == "x":
        return f"{x:.2f}x"
    return str(x)


FMT_LBO = {
    "Revenue": "${:,.0f}", "EBITDA": "${:,.0f}", "EBITDA Margin": "{:.1%}",
    "Interest": "${:,.0f}", "Tax": "${:,.0f}", "CapEx": "${:,.0f}",
    "ΔNWC": "${:,.0f}", "FCF": "${:,.0f}", "TLB": "${:,.0f}",
    "Revolver": "${:,.0f}", "Cash": "${:,.0f}", "Net Debt": "${:,.0f}",
}


# =========================================
# SIDEBAR
# =========================================
st.sidebar.header("⚙️ Deal Parameters")

with st.sidebar.expander("🤖 AI Classification (optional)"):
    st.caption(
        "Paste your **Anthropic API key** to let Claude classify any "
        "P&L line items the keyword engine doesn't recognise.\n\n"
        "Get one at **console.anthropic.com → API Keys**."
    )
    api_key = st.text_input("Anthropic API Key", type="password")
    use_ai  = st.checkbox("Enable AI classification", value=bool(api_key))

st.sidebar.markdown("---")
st.sidebar.subheader("Valuation")
entry_multiple = st.sidebar.number_input("Entry EV/EBITDA", 3.0, 20.0, 5.0, 0.5)
exit_multiple  = st.sidebar.number_input("Exit EV/EBITDA",  3.0, 20.0, 7.0, 0.5)

st.sidebar.subheader("Holding Period & Growth")
years  = st.sidebar.slider("Holding Period (years)", 1, 7, 5)
growth = st.sidebar.slider("Revenue Growth % p.a.", 0, 40, 10) / 100

margin_mode = st.sidebar.radio("EBITDA Margin Input", ["Flat", "Per Year"],
                                horizontal=True)
if margin_mode == "Flat":
    flat_m  = st.sidebar.slider("EBITDA Margin %", 0, 60, 20) / 100
    margins = [flat_m] * years
else:
    margins = [
        st.sidebar.slider(f"Y{i+1} EBITDA Margin %", 0, 60, 20 + i) / 100
        for i in range(years)
    ]

st.sidebar.subheader("Capital Structure")
leverage_pct = st.sidebar.slider("Leverage % of Entry EV", 20, 80, 60) / 100
tlb_rate     = st.sidebar.slider("TLB Interest Rate %", 0, 20, 7) / 100
rev_rate     = st.sidebar.slider("Revolver Rate %", 0, 20, 6) / 100

st.sidebar.subheader("Other Assumptions")
tax_rate  = st.sidebar.slider("Tax Rate %", 0, 35, 17) / 100
da_pct    = st.sidebar.slider("D&A % of Revenue", 0, 15, 3) / 100
nwc_pct   = st.sidebar.slider("NWC % of Revenue", 0, 20, 5) / 100
capex_pct = st.sidebar.slider("CapEx % of Revenue", 0, 20, 5) / 100
min_cash  = st.sidebar.number_input("Minimum Cash ($)", 0, value=50_000,
                                    step=10_000)

params = dict(
    entry_multiple=entry_multiple, exit_multiple=exit_multiple,
    years=years, growth=growth, margins=margins,
    leverage_pct=leverage_pct, tlb_rate=tlb_rate, rev_rate=rev_rate,
    tax_rate=tax_rate, da_pct=da_pct, nwc_pct=nwc_pct,
    capex_pct=capex_pct, min_cash=float(min_cash),
)


# =========================================
# MAIN PAGE
# =========================================
st.title("📊 SME Valuation & LBO Tool")
st.caption(
    "Upload any P&L and Balance Sheet to generate a full LBO valuation. "
    "Supports **xlsx, xls, csv, digital PDF, and scanned PDF** (OCR)."
)
st.markdown("---")

st.header("📂 Step 1 — Upload Financials")
col_pl, col_bs = st.columns(2)
with col_pl:
    pl_file = st.file_uploader("P&L Statement",
                               type=["xlsx", "xls", "csv", "pdf"])
with col_bs:
    bs_file = st.file_uploader("Balance Sheet",
                               type=["xlsx", "xls", "csv", "pdf"])


# =========================================
# PROCESS P&L
# =========================================
pl_metrics = None

if pl_file:
    raw_pl = read_any_file(pl_file)

    if raw_pl is not None:
        df_pl = smart_clean(raw_pl)
        df_pl = classify_pl(df_pl, use_ai=use_ai, api_key=api_key or "")

        st.markdown("---")
        st.header("📋 Step 2 — Review & Correct P&L Classifications")
        st.caption(
            "Every row is editable. Use the Category dropdown to fix "
            "any misclassified items. Corrections are saved to memory "
            "and auto-applied on the next upload of the same company."
        )

        unknown_count = (df_pl["Category"] == "Unknown").sum()
        if unknown_count:
            st.warning(
                f"⚠️ {unknown_count} row(s) unclassified. "
                "Fix them below or enable AI Classification in the sidebar."
            )

        df_pl = st.data_editor(
            df_pl,
            column_config={
                "Category": st.column_config.SelectboxColumn(
                    "Category", options=PL_CATEGORIES
                ),
                "Amount": st.column_config.NumberColumn(
                    "Amount", format="$ %.0f"
                ),
            },
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
        )

        # Save memory — only persist non-Unknown entries
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

if bs_file:
    raw_bs = read_any_file(bs_file)

    if raw_bs is not None:
        df_bs = smart_clean(raw_bs)
        df_bs = classify_bs(df_bs)

        st.markdown("---")
        st.subheader("🏦 Balance Sheet — Review Classifications")
        st.caption(
            "Company-named bank accounts are auto-classified as Cash based on "
            "their position in the statement. Override anything that looks wrong."
        )

        df_bs = st.data_editor(
            df_bs,
            column_config={
                "Category": st.column_config.SelectboxColumn(
                    "Category", options=BS_CATEGORIES
                ),
                "Amount": st.column_config.NumberColumn(
                    "Amount", format="$ %.0f"
                ),
            },
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
        )

        cash_bs = df_bs.loc[df_bs["Category"] == "Cash", "Amount"].sum()
        debt_bs = df_bs.loc[df_bs["Category"] == "Debt", "Amount"].sum()


# =========================================
# VALUATION OUTPUT
# =========================================
if pl_metrics:
    st.markdown("---")
    st.header("📊 Step 3 — Valuation Output")

    m = pl_metrics
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Revenue",      fmt(m["Revenue"]))
    c2.metric("Gross Profit", fmt(m["Gross Profit"]),  fmt(m["GP Margin"],     "pct"))
    c3.metric("EBITDA",       fmt(m["EBITDA"]),         fmt(m["EBITDA Margin"], "pct"))
    c4.metric("EBIT",         fmt(m["EBIT"]),           fmt(m["EBIT Margin"],   "pct"))
    c5.metric("Net Profit",   fmt(m["Net Profit"]),     fmt(m["Net Margin"],    "pct"))

    with st.expander("📄 Full P&L Bridge"):
        bridge_rows = [
            ("Revenue",        m["Revenue"]),
            ("(-)  COGS",     -m["COGS"]),
            ("Gross Profit",   m["Gross Profit"]),
            ("(-)  OpEx",     -m["OpEx"]),
            ("(-)  D&A",      -m["D&A"]),
            ("(+) Other Inc.", m["Other Income"]),
            ("EBITDA",         m["EBITDA"]),
            ("(-)  Interest", -m["Interest"]),
            ("(-)  Tax",      -m["Tax"]),
            ("Net Profit",     m["Net Profit"]),
        ]
        pl_bridge = pd.DataFrame(bridge_rows, columns=["Item", "Amount"])
        pl_bridge["Amount"] = pl_bridge["Amount"].apply(fmt)
        st.dataframe(pl_bridge, use_container_width=True, hide_index=True)

    if bs_file:
        st.subheader("Balance Sheet Snapshot")
        b1, b2, b3 = st.columns(3)
        b1.metric("Cash & Equivalents", fmt(cash_bs))
        b2.metric("Total Debt",         fmt(debt_bs))
        b3.metric("Net Debt",           fmt(debt_bs - cash_bs))

    st.markdown("---")

    if m["EBITDA"] <= 0:
        st.error(
            "⚠️ EBITDA is zero or negative — LBO model cannot run. "
            "Check Revenue and COGS/OpEx classifications in Step 2."
        )
    else:
        lbo_df, returns = run_lbo(pl_metrics, cash_bs, debt_bs, params)

        st.subheader("📈 Returns Summary")
        r1, r2, r3, r4, r5 = st.columns(5)
        r1.metric("Entry EV",  fmt(returns["Entry EV"]))
        r2.metric("Equity In", fmt(returns["Equity In"]))
        r3.metric("Exit EV",   fmt(returns["Exit EV"]))
        r4.metric("MOIC",      fmt(returns["MOIC"], "x"))
        r5.metric("IRR",       fmt(returns["IRR"],  "pct"))

        st.subheader("🔢 Sensitivity: MOIC")
        rows_sens = []
        for em in [entry_multiple - 1, entry_multiple, entry_multiple + 1]:
            row = {"Entry \\ Exit": f"{em:.1f}x"}
            for xm in [exit_multiple - 1, exit_multiple, exit_multiple + 1]:
                _, ret2 = run_lbo(pl_metrics, cash_bs, debt_bs,
                                  {**params, "entry_multiple": em, "exit_multiple": xm})
                row[f"Exit {xm:.1f}x"] = f"{ret2['MOIC']:.2f}x"
            rows_sens.append(row)
        st.dataframe(
            pd.DataFrame(rows_sens).set_index("Entry \\ Exit"),
            use_container_width=True,
        )

        st.subheader("📋 LBO Model")
        st.dataframe(lbo_df.style.format(FMT_LBO),
                     use_container_width=True, hide_index=True)

        with st.expander("🏗️ Valuation Bridge"):
            bridge = pd.DataFrame([
                {"Item": "Entry EV",               "Value": fmt(returns["Entry EV"])},
                {"Item": "  (-) Transaction Debt", "Value": fmt(returns["Total Debt"])},
                {"Item": "  (+) BS Cash",          "Value": fmt(cash_bs)},
                {"Item": "  (-) BS Debt",          "Value": fmt(debt_bs)},
                {"Item": "Equity Invested",        "Value": fmt(returns["Equity In"])},
                {"Item": "─────────────────",      "Value": ""},
                {"Item": "Exit EV",                "Value": fmt(returns["Exit EV"])},
                {"Item": "  (-) Exit Net Debt",    "Value": fmt(
                    returns["Exit EV"] - returns["Exit Equity"])},
                {"Item": "Exit Equity",            "Value": fmt(returns["Exit Equity"])},
                {"Item": "─────────────────",      "Value": ""},
                {"Item": "MOIC",                   "Value": fmt(returns["MOIC"], "x")},
                {"Item": "IRR",                    "Value": fmt(returns["IRR"],  "pct")},
            ])
            st.dataframe(bridge, use_container_width=True, hide_index=True)

elif not pl_file:
    st.info("👆 Upload a P&L statement above to get started.")
