"""
SME Valuation & LBO Tool
========================
Production-grade Streamlit app for Singapore/SEA SME valuation.
"""

import streamlit as st
import pandas as pd
import numpy as np
import json, os, re, io, warnings

# ── Optional heavy imports ────────────────────────────────────────────────────
try:
    import pdfplumber
    PDF_DIGITAL = True
except ImportError:
    PDF_DIGITAL = False

try:
    from pdf2image import convert_from_bytes
    import pytesseract
    from PIL import ImageFilter
    PDF_OCR = True
except ImportError:
    PDF_OCR = False

try:
    import plotly.graph_objects as go
    PLOTLY = True
except ImportError:
    PLOTLY = False

try:
    import openpyxl          # noqa – only needed for Excel export
    OPENPYXL = True
except ImportError:
    OPENPYXL = False

warnings.filterwarnings("ignore", category=RuntimeWarning)

st.set_page_config(layout="wide", page_title="SME Valuation Tool", page_icon="📊")

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* Tighten metric cards */
    [data-testid="metric-container"] {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 12px 16px;
    }
    [data-testid="stMetricValue"] { font-size: 1.4rem !important; }
    /* Highlight positive IRR */
    .irr-good  { color: #16a34a; font-weight: 700; }
    .irr-warn  { color: #d97706; font-weight: 700; }
    .irr-bad   { color: #dc2626; font-weight: 700; }
    /* Section headers */
    .section-badge {
        display: inline-block;
        background: #1e3a5f;
        color: white;
        border-radius: 4px;
        padding: 2px 10px;
        font-size: 0.8rem;
        font-weight: 600;
        margin-bottom: 4px;
    }
    /* Sidebar tweak */
    section[data-testid="stSidebar"] { background: #0f172a; }
    section[data-testid="stSidebar"] * { color: #e2e8f0 !important; }
    section[data-testid="stSidebar"] .stSelectbox label,
    section[data-testid="stSidebar"] .stSlider label { color: #94a3b8 !important; }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# CONSTANTS
# =============================================================================
MEMORY_FILE = "memory.json"

PL_CATEGORIES = ["Revenue", "COGS", "OpEx", "D&A", "Other Income",
                 "Interest", "Tax", "Ignore"]
BS_CATEGORIES = ["Cash", "Receivables", "Inventory", "Fixed Assets",
                 "Debt", "Payables", "Equity", "Ignore", "Other"]

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
    "D&A": ["depreciation", "amortis", "amortiz", "d&a", "right-of-use"],
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
        "interest expense", "finance cost", "finance costs",
        "finance charge", "borrowing cost", "loan interest",
        "hire purchase interest",
    ],
    "Tax": ["income tax", "tax expense", "deferred tax", "zakat", "corporate tax"],
    "Ignore": [
        "total", "net profit", "gross profit", "ebitda", "subtotal",
        "pte", "ltd", "sdn bhd", "for the year", "as at", "nan", "none",
        "operating profit", "operating expenses",
        "profit before", "profit after", "loss before", "loss after",
        "cost of sales", "trading income",
    ],
}

BS_KEYWORDS = {
    "Cash": [
        "cash", "bank", "fixed deposit",
        "airwallex", "aspire", "maybank", "ocbc", "dbs", "uob", "cimb",
        "paypal", "wise", "revolut", "stripe", "grabpay", "petty cash",
    ],
    "Receivables": [
        "receivable", "debtor", "trade receivable", "other receivable",
        "amount due from", "contract asset", "due from customer",
        "trade and other receivables", "prepayment", "deposit paid",
        "advance paid", "amount owing from", "owing from",
        "advance salaries", "raffles deposit",
    ],
    "Inventory": ["inventory", "stock", "work in progress", "wip", "finished goods"],
    "Fixed Assets": [
        "property", "plant", "equipment", "ppe", "fixed asset", "right-of-use",
        "motor vehicle", "machinery", "computer", "furniture",
        "app development", "development cost", "less accumulated",
    ],
    "Debt": [
        "loan", "debt", "borrowing", "credit facility", "term loan",
        "revolving", "bank overdraft", "hire purchase", "lease liabilit",
        "amount owing to director", "director loan",
    ],
    "Payables": [
        "payable", "creditor", "trade payable", "accrual",
        "provision for taxation", 
        "trade and other payables", "other payable",
        "advance received", "deposit received", "sales tax", "gst", "vat",
        "wages payable", "income tax payable",
    ],
    "Equity": [
        "equity", "share capital", "retained earning", "reserve",
        "dividend", "owner",
    ],
    "Ignore": [
        "total", "net asset", "total asset", "total liabilit",
        "current assets", "fixed assets", "current liabilities",
        "long term", "non-current",
    ],
}

BS_SECTION_TRIGGERS = {
    "bank":                "Cash",
    "current assets":      "Receivables",
    "fixed assets":        "Fixed Assets",
    "current liabilities": "Payables",
    "long term liabilit":  "Debt",
    "equity":              "Equity",
}

# Singapore comparable transaction multiples reference (EV/EBITDA)
COMPS_TABLE = pd.DataFrame([
    {"Sector": "IT Services / SaaS",       "Rev Size": "<$2M",   "EV/EBITDA": "4–7x",  "EV/Rev": "0.8–2.0x", "Note": "Recurring revenue premium"},
    {"Sector": "IT Services / SaaS",       "Rev Size": "$2–10M", "EV/EBITDA": "6–9x",  "EV/Rev": "1.5–3.5x", "Note": "Sticky customers → premium"},
    {"Sector": "Professional Services",    "Rev Size": "<$2M",   "EV/EBITDA": "3–5x",  "EV/Rev": "0.5–1.0x", "Note": "Key-person risk discount"},
    {"Sector": "Professional Services",    "Rev Size": "$2–10M", "EV/EBITDA": "4–7x",  "EV/Rev": "0.8–1.5x", "Note": ""},
    {"Sector": "F&B / Retail",             "Rev Size": "<$2M",   "EV/EBITDA": "2–4x",  "EV/Rev": "0.3–0.6x", "Note": "Thin margins, execution risk"},
    {"Sector": "F&B / Retail",             "Rev Size": "$2–10M", "EV/EBITDA": "3–5x",  "EV/Rev": "0.5–0.9x", "Note": ""},
    {"Sector": "Construction / Trade",     "Rev Size": "<$5M",   "EV/EBITDA": "2–4x",  "EV/Rev": "0.2–0.5x", "Note": "Project risk, WC intensive"},
    {"Sector": "Healthcare / Wellness",    "Rev Size": "<$5M",   "EV/EBITDA": "5–8x",  "EV/Rev": "1.0–2.5x", "Note": "Defensiveness + licensing moat"},
    {"Sector": "E-commerce / Logistics",   "Rev Size": "$2–10M", "EV/EBITDA": "3–6x",  "EV/Rev": "0.4–1.0x", "Note": "Scale-driven multiple"},
])


# =============================================================================
# IRR CALCULATION — robust Newton-Raphson (replaces removed np.irr)
# =============================================================================
def compute_irr(cashflows: list, guess: float = 0.10) -> float:
    """
    Newton-Raphson IRR solver. Returns float or raises ValueError.
    Handles sign-change check, overflow, and convergence failure gracefully.
    """
    cf = np.array(cashflows, dtype=float)

    # Must have at least one sign change for IRR to exist
    signs = np.sign(cf[cf != 0])
    if len(np.unique(signs)) < 2:
        raise ValueError("No sign change in cash flows — IRR undefined")

    rate = float(guess)
    for _ in range(200):
        t    = np.arange(len(cf), dtype=float)
        # Clip rate to avoid overflow in (1+rate)^t
        r1   = max(rate, -0.9999)
        disc = (1 + r1) ** t
        npv  = np.sum(cf / disc)
        dnpv = -np.sum(t * cf / (disc * (1 + r1)))
        if dnpv == 0:
            break
        new_rate = rate - npv / dnpv
        if abs(new_rate - rate) < 1e-8:
            return new_rate
        rate = new_rate

    # Fallback: bisection between -50% and +500%
    lo, hi = -0.4999, 5.0
    try:
        from scipy.optimize import brentq
        def npv_fn(r):
            t = np.arange(len(cf), dtype=float)
            return np.sum(cf / (1 + r) ** t)
        return brentq(npv_fn, lo, hi, xtol=1e-8, maxiter=200)
    except Exception:
        raise ValueError("IRR did not converge")


# =============================================================================
# AUTO-CALIBRATION ENGINE
# =============================================================================
def auto_calibrate(metrics: dict, cash_bs: float, debt_bs: float) -> dict:
    """
    Derive recommended deal parameters from the SME's financial profile.

    Benchmarked against Singapore/SEA PE deal data:
      - Entry multiple: size tier + margin quality + leverage penalty
      - Exit multiple: entry + operational improvement premium (1–2x)
      - Growth: conservative for micro, moderate for small, ambitious for mid
      - Leverage: debt/EBITDA capped at 3x; floors at 30%
      - CapEx / NWC: asset-light (high-margin) vs asset-heavy proxies
    """
    rev    = metrics.get("Revenue", 0)
    ebitda = metrics.get("EBITDA", 0)
    margin = metrics.get("EBITDA Margin", 0)
    net_debt = max(0.0, debt_bs - cash_bs)

    # ── Entry multiple ────────────────────────────────────────────────────────
    if   rev < 500_000:      base_entry = 2.0
    elif rev < 2_000_000:    base_entry = 3.5
    elif rev < 10_000_000:   base_entry = 5.0
    else:                    base_entry = 6.5

    if margin >= 0.30:
        margin_adj = +0.5 if rev < 1_000_000 else +1.0
    elif margin >= 0.20:
        margin_adj = +0.5
    elif margin >= 0.10:
        margin_adj = 0.0
    else:
        margin_adj = -0.5

    leverage_ratio = (net_debt / ebitda) if ebitda > 0 else 0
    lev_adj = -0.5 if leverage_ratio > 3 else 0.0

    entry = round(base_entry + margin_adj + lev_adj, 1)
    entry = max(2.5, min(10.0, entry))

    # ── Exit multiple ─────────────────────────────────────────────────────────
    if rev < 1_000_000:
        exit_ = round(entry + 0.5, 1)
    else:
        exit_ = round(entry + 1.0, 1)

    # ── Revenue growth ────────────────────────────────────────────────────────
    if   rev < 1_000_000:  growth = 0.08
    elif rev < 5_000_000:  growth = 0.12
    else:                  growth = 0.15

    # ── Target EBITDA margin (exit year) ─────────────────────────────────────
    if margin < 0.20:
        target_margin = margin + 0.05
    elif margin < 0.30:
        target_margin = margin + 0.03
    else:
        target_margin = margin + 0.01   # 👈 high-margin businesses get little uplift
    
    target_margin = round(min(0.45, max(0.10, target_margin)), 3)

    # ── Leverage ──────────────────────────────────────────────────────────────
    # Max debt at entry = lesser of 3× EBITDA or 65% of entry EV
    entry_ev  = entry * ebitda if ebitda > 0 else 1
    if rev < 1_000_000:
        max_debt = min(ebitda * 2.0, 0.50 * entry_ev)
        leverage = round(max(0.25, min(0.50, max_debt / entry_ev)), 2)
    else:
        max_debt = min(ebitda * 3.0, 0.65 * entry_ev)
        leverage = round(max(0.30, min(0.65, max_debt / entry_ev)), 2)

    # ── CapEx / NWC ───────────────────────────────────────────────────────────
    capex = 0.03 if margin >= 0.25 else 0.06
    nwc   = 0.04 if margin >= 0.25 else 0.08

    return {
        "entry_multiple": entry,
        "exit_multiple":  exit_,
        "growth":         growth,
        "target_margin":  target_margin,
        "leverage_pct":   leverage,
        "capex_pct":      capex,
        "nwc_pct":        nwc,
        "rationale": {
            "revenue_tier":   f"${rev/1e6:.2f}M revenue → {base_entry:.1f}x base",
            "margin_quality": f"{margin*100:.1f}% margin → {margin_adj:+.1f}x adj",
            "leverage_ratio": f"{leverage_ratio:.1f}× net debt/EBITDA → {lev_adj:+.1f}x adj",
            "suggested_entry": f"{entry:.1f}x",
            "suggested_exit":  f"{exit_:.1f}x",
        },
    }


def build_scenarios(metrics: dict, cash_bs: float, debt_bs: float,
                    base_params: dict) -> dict:
    """Bear / Base / Bull scenarios derived from current model assumptions."""
    years = base_params.get("years", 5)
    base_margin = base_params["margins"][0]

    def _make(entry_d, exit_d, growth_d, margin_d, lev_d, capex_d, nwc_d):
        return {
            **base_params,
            "entry_multiple": round(base_params["entry_multiple"] + entry_d, 1),
            "exit_multiple":  round(base_params["exit_multiple"]  + exit_d, 1),
            "growth":         max(0.0, base_params["growth"] + growth_d),
            "margins": [round(max(0.05, base_margin + margin_d), 3)] * years,
            "leverage_pct":   round(min(0.75, max(0.20, base_params["leverage_pct"] + lev_d)), 2),
            "capex_pct":      round(max(0.01, base_params["capex_pct"] + capex_d), 3),
            "nwc_pct":        round(max(0.01, base_params["nwc_pct"] + nwc_d), 3),
            "use_override_margin": True,
        }

    return {

        "Bear 🐻": _make(+0.5, -0.5, -0.03, -0.02, +0.08, +0.02, +0.02),
        "Base 📊": {
            **base_params,
            "use_override_margin": True,
        },
        "Bull 🚀": _make(-0.5, +0.5, +0.03, +0.03, -0.05, -0.01, -0.02),
    }


# =============================================================================
# MEMORY
# =============================================================================
def load_memory() -> dict:
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_memory(mem: dict):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(mem, f, indent=2)
    except Exception:
        pass


# =============================================================================
# PDF HELPERS
# =============================================================================
def _preprocess_image_for_ocr(img):
    img = img.convert("L")
    img = img.filter(ImageFilter.SHARPEN)
    img = img.point(lambda x: 255 if x > 140 else 0)
    return img


_AMOUNT_RE = re.compile(r"(\([\d,]+(?:\.\d+)?\)|-?[\d,]+(?:\.\d+)?)")


def _strip_note_refs(label: str) -> str:
    return re.sub(r"\s+\b\d{1,2}\b\s*$", "", label).strip()


def _parse_line_to_label_amount(line: str):
    line = line.strip()
    if not line:
        return None
    found = [(m.start(), m.group()) for m in _AMOUNT_RE.finditer(line)]
    if not found:
        return None

    def _to_float(s):
        s = s.replace(",", "").replace("(", "-").replace(")", "")
        try:    return float(s)
        except: return 0.0

    parsed     = [(pos, raw, _to_float(raw)) for pos, raw in found]
    formatted  = [(p, r, v) for p, r, v in parsed if "," in r or "." in r or "(" in r]
    plain_ints = [(p, r, v) for p, r, v in parsed
                  if not ("," in r or "." in r or "(" in r)]

    if formatted:
        cur_pos, cur_raw, _ = formatted[0]
    else:
        if len(plain_ints) >= 3 and abs(plain_ints[0][2]) <= 20:
            cur_pos, cur_raw, _ = plain_ints[1]
        else:
            cur_pos, cur_raw, _ = plain_ints[0]

    label = _strip_note_refs(line[:cur_pos].strip())
    label = re.sub(r"[|_]{2,}", "", label).strip()
    label = re.sub(r"\s{2,}", " ", label)
    if not label:
        return None
    return label, cur_raw


def _ocr_pdf(file_bytes: bytes):
    if not PDF_OCR:
        st.error("📦 OCR requires pdf2image + pytesseract + Pillow.")
        return None
    try:
        images = convert_from_bytes(file_bytes, dpi=300)
    except Exception as e:
        st.error(f"PDF→image conversion failed: {e}")
        return None

    rows = []
    for img in images:
        img  = _preprocess_image_for_ocr(img)
        text = pytesseract.image_to_string(img, config="--psm 6")
        for line in text.splitlines():
            parsed = _parse_line_to_label_amount(line)
            if parsed:
                rows.append(list(parsed))
            else:
                clean = re.sub(r"[|_]{2,}", "", line).strip()
                if clean:
                    rows.append([clean, "0"])

    if not rows:
        st.error("OCR produced no usable rows. Check PDF quality.")
        return None
    return pd.DataFrame(rows, columns=["c0", "c1"], dtype=str)


def read_any_file(uploaded_file):
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file, header=None, dtype=str)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file, header=None, dtype=str)
    if name.endswith(".pdf"):
        file_bytes = uploaded_file.read()
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
                text_rows = []
                with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                    for page in pdf.pages:
                        for line in (page.extract_text() or "").splitlines():
                            parsed = _parse_line_to_label_amount(line)
                            if parsed:
                                text_rows.append(list(parsed))
                            else:
                                clean = re.sub(r"[|_]{2,}", "", line).strip()
                                if clean and not re.fullmatch(r"[\d\s.,\-()]+", clean):
                                    text_rows.append([clean, "0"])
                if text_rows:
                    st.info("📄 Parsed as plain-text digital PDF.")
                    return pd.DataFrame(text_rows, columns=["c0", "c1"], dtype=str)
            except Exception:
                pass
        st.info("📷 No digital tables found — running OCR on scanned PDF…")
        return _ocr_pdf(file_bytes)
    st.error(f"Unsupported file type: {name}")
    return None


# =============================================================================
# CLEANING PIPELINE
# =============================================================================
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


def score_amount_column(series: pd.Series) -> float:
    nums = parse_amount(series)
    nums = nums[nums != 0]
    if len(nums) == 0:
        return -1.0
    abs_vals = np.abs(nums)
    return (
        len(abs_vals) * 5
        + np.log1p(abs_vals.sum()) * 3
        + np.log1p(np.median(abs_vals)) * 5
        + np.max(abs_vals) * 0.00001
    )


def merge_multiline_rows(df: pd.DataFrame) -> pd.DataFrame:
    merged = []
    buffer = ""
    for _, row in df.iterrows():
        label  = str(row["Line Item"]).strip()
        amount = row["Amount"]
        if amount == 0 and len(label.split()) < 5:
            buffer += " " + label
        else:
            full_label = (buffer + " " + label).strip()
            merged.append([full_label, amount])
            buffer = ""
    if buffer.strip():
        merged.append([buffer.strip(), 0])
    return pd.DataFrame(merged, columns=["Line Item", "Amount"])


_META_EXACT = {"account", "accounts", "nan", "none", ""}
_META_PHRASES = [
    "pte. ltd.", "pte ltd", "sdn bhd", "berhad",
    "for the year", "for the period",
    "as at ", "as of ", "as at",
    "balance sheet", "profit and loss", "income statement",
    "exchange rate", "rates are provided",
    "prepared by", "reviewed by",
]
_DATE_RE = re.compile(
    r"^(31|30|28|29)?\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*\d{4}$",
    re.I,
)


def _is_meta_row(label: str) -> bool:
    xl = label.strip().lower()
    if xl in _META_EXACT:
        return True
    if re.fullmatch(r"[\d\s\-/]+", xl):
        return True
    if _DATE_RE.match(xl):
        return True
    if "page" in xl:
        return True
    return any(p in xl for p in _META_PHRASES)


def smart_clean(df: pd.DataFrame):
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.fillna("").astype(str)
    df = dedupe_columns(df)
    df.columns = [f"c{i}" for i in range(len(df.columns))]

    if df.shape[1] == 1:
        def _to_row(text):
            result = _parse_line_to_label_amount(text)
            if result:
                return pd.Series(result)
            return pd.Series([text.strip(), "0"])
        rows = df.iloc[:, 0].astype(str).apply(_to_row)
        df   = pd.DataFrame({"c0": rows[0], "c1": rows[1]})

    best_col, best_score = None, -1.0
    for col in df.columns:
        sc = score_amount_column(df[col])
        if sc > best_score:
            best_score, best_col = sc, col

    if best_col is None or best_score <= 0:
        st.error("❌ Could not detect any numeric amount column. "
                 "Check file format — expected columns like 'Item | Amount'.")
        return None

    label_col, label_score = None, -1.0
    for col in df.columns:
        if col == best_col:
            continue
        non_empty = (df[col].str.strip() != "").sum()
        numeric   = (parse_amount(df[col]) != 0).sum()
        sc        = non_empty - numeric * 3
        if sc > label_score:
            label_score, label_col = sc, col

    if label_col is None:
        label_col = df.columns[0]

    result = pd.DataFrame({
        "Line Item": df[label_col].astype(str).str.strip(),
        "Amount":    parse_amount(df[best_col]),
    })

    result = result[result["Line Item"].apply(lambda x: not _is_meta_row(x))]
    result = result[~result["Line Item"].str.lower().str.contains(
        r"statement|note|\$\$|comprehensive income", regex=True
    )]
    result = result[~result["Line Item"].str.fullmatch(r"[\d\s.,\-()%\[\]]+")]
    result = result.reset_index(drop=True)
    result = merge_multiline_rows(result)
    return result


# =============================================================================
# CLASSIFICATION — P&L
# =============================================================================
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
            "You are a financial analyst specialising in Singapore SME accounts. "
            "Classify each P&L line item into exactly one of:\n"
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
    df  = df.copy()
    df["Category"] = df["Line Item"].apply(keyword_classify_pl)
    df["Category"] = df.apply(
        lambda r: mem.get(r["Line Item"], r["Category"])
        if r["Category"] == "Unknown" else r["Category"],
        axis=1,
    )
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


# =============================================================================
# METRICS — P&L
# =============================================================================
def compute_pl(df: pd.DataFrame, addbacks: float = 0.0) -> dict:
    def s(cat):
        val = df.loc[df["Category"] == cat, "Amount"].sum()
        
        # Normalize sign for expenses
        if cat in ["COGS", "OpEx", "D&A", "Interest", "Tax"]:
            return abs(val)
        
        return val

    rev  = s("Revenue")
    cogs = s("COGS")
    opex = s("OpEx")
    da   = s("D&A")
    oi   = s("Other Income")
    int_ = s("Interest")
    tax  = abs(s("Tax"))

    gp       = rev - cogs
    opex_adj = opex - addbacks          # normalised OpEx
    ebit     = gp - opex_adj - da
    ebitda   = ebit + da                # = gp - opex_adj (D&A neutral)
    ebt      = ebit + oi - int_
    net      = ebt - tax

    def pct(n, d=rev):
        return (n / d) if d else 0.0

    return {
        "Revenue": rev, "COGS": cogs, "Gross Profit": gp, "GP Margin": pct(gp),
        "OpEx (gross)": opex, "Add-backs": addbacks, "OpEx (adj)": opex_adj,
        "D&A": da, "Other Income": oi,
        "EBITDA": ebitda, "EBITDA Margin": pct(ebitda),
        "EBIT": ebit, "EBIT Margin": pct(ebit),
        "Interest": int_, "EBT": ebt, "Tax": tax,
        "Net Profit": net, "Net Margin": pct(net),
    }


# =============================================================================
# CLASSIFICATION — BALANCE SHEET
# =============================================================================
def classify_bs(df: pd.DataFrame) -> pd.DataFrame:
    cats = []
    current_section = None
    for item in df["Line Item"].fillna("").astype(str):
        # Clean junk + normalize
        x = re.sub(r"[^a-z0-9\s]", " ", str(item).lower())
        x = re.sub(r"\s+", " ", x).strip()
        
        # Remove section headers embedded in line
        for trigger in BS_SECTION_TRIGGERS.keys():
            if trigger in x:
                x = x.replace(trigger, "").strip()
              
        cat = "Other"

        for trigger, section in BS_SECTION_TRIGGERS.items():
            if trigger in x:
                current_section = section
                break

        if any(kw in x for kw in BS_KEYWORDS["Ignore"]):
            cats.append("Ignore")
            continue

        for c, keywords in BS_KEYWORDS.items():
            if c == "Ignore":
                continue
            if any(k in x for k in keywords):
                cat = c
                break
        # Fallback: if we're inside Current Assets section, treat unknowns as Receivables
        if cat == "Other" and current_section == "Receivables":
            cat = "Receivables"
        if cat == "Other" and current_section is not None:
            # Only inherit section for asset/liability buckets
            if current_section in ["Receivables", "Payables", "Inventory"]:
                cat = current_section

        cats.append(cat)

    df = df.copy()
    df["Category"] = cats
    return df


# =============================================================================
# LBO ENGINE
# =============================================================================
def run_lbo(metrics: dict, cash_bs: float, debt_bs: float, params: dict):
    """
    Full LBO model:
      - Entry EV = EBITDA × entry_multiple
      - Debt split 85/15 TLB / Revolver
      - Annual FCF sweeps revolver first, then TLB
      - Exit EV = Year-N EBITDA × exit_multiple; net of exit net debt
      - IRR computed via Newton-Raphson with Brent fallback
    """
    ebitda  = metrics["EBITDA"]
    revenue = metrics["Revenue"]

    entry_ev   = ebitda * params["entry_multiple"]
    # --- leverage haircut for SMEs ---
    size = metrics["Revenue"]
    margin = params["margins"][0]
    
    haircut = 1.0
    
    # small business risk
    if size < 1_000_000:
        haircut -= 0.15
    
    # margin quality
    if margin < 0.20:
        haircut -= 0.10
    
    # cap minimum haircut
    haircut = max(0.5, haircut)
    
    # apply haircut
    effective_leverage = params["leverage_pct"] * haircut

    # initial leverage
    total_debt = entry_ev * effective_leverage
    
    # constraint 1: debt / EBITDA
    max_debt_ebitda = ebitda * 2.5
    
    # constraint 2: interest coverage
    avg_rate = (params["tlb_rate"] * 0.85 + params["rev_rate"] * 0.15)
    max_debt_interest = ebitda / (2.0 * avg_rate) if avg_rate > 0 else total_debt
    
    # final debt = minimum of all
    total_debt = min(total_debt, max_debt_ebitda, max_debt_interest)
    tlb        = total_debt * 0.85
    revolver   = total_debt * 0.15

    net_debt_bs = debt_bs - cash_bs
    equity_in   = entry_ev - total_debt + net_debt_bs

    if equity_in <= 0:
        st.warning(
            f"⚠️ Computed equity investment is negative (${equity_in:,.0f}). "
            "This means the company's existing net debt exceeds the equity check at "
            "current entry parameters. Check BS debt/cash or reduce leverage. "
            "Flooring equity_in at $1 for computation."
        )
        equity_in = 1.0

    cash     = float(params["min_cash"])
    base_margin = (metrics["EBITDA"] / metrics["Revenue"]) if metrics["Revenue"] else 0
    use_margin  = params.get("use_override_margin", True)
    prev_nwc    = params.get("initial_nwc", revenue * params["nwc_pct"])
    rows        = []

    for i in range(params["years"]):
        rev      = revenue * (1 + params["growth"]) ** (i + 1)
        margin_y = params["margins"][i] if use_margin else base_margin
        ebitda_y = rev * margin_y
        da_y     = rev * params["da_pct"]
        ebit_lbo = ebitda_y - da_y
        interest = tlb * params["tlb_rate"] + revolver * params["rev_rate"]
        ebt_lbo  = ebit_lbo - interest
        tax      = max(0.0, ebt_lbo * params["tax_rate"])

        nwc      = rev * params["nwc_pct"]
        # Y1 delta_nwc = 0 (no artificial cash release on acquisition date)
        delta_nwc = 0.0 if i == 0 else nwc - prev_nwc
        prev_nwc  = nwc
        capex     = rev * params["capex_pct"]
        fcf = (ebit_lbo - tax) + da_y - capex - delta_nwc
        cash     += fcf

        # Revolver draw if cash below minimum
        if cash < params["min_cash"]:
            draw      = params["min_cash"] - cash
            revolver += draw
            cash     += draw

        # Cash sweep: revolver first, then TLB
        sweep_pct = params.get("debt_sweep_pct", 0.6)
        
        excess = max(0.0, cash - params["min_cash"])
        sweep  = excess * sweep_pct
        
        # revolver first
        pay_rev = min(revolver, sweep)
        revolver -= pay_rev
        cash -= pay_rev
        
        # remaining to TLB
        sweep -= pay_rev
        
        pay_tlb = min(tlb, sweep)
        tlb -= pay_tlb
        cash -= pay_tlb

        debt_repaid = pay_rev + pay_tlb

        cash_cap_pct = params.get("cash_cap_pct", 0.10)
        max_cash = cash_cap_pct * rev
        cash = min(cash, max_cash)

        rows.append({
            "Year":         i + 1,
            "Revenue":      rev,
            "EBITDA":       ebitda_y,
            "EBITDA Margin": ebitda_y / rev if rev else 0,
            "Interest":     interest,
            "Tax":          tax,
            "CapEx":        capex,
            "ΔNWC":         delta_nwc,
            "FCF":          fcf,
            "Debt Repaid":  debt_repaid,
            "TLB":          tlb,
            "Revolver":     revolver,
            "Cash":         cash,
            "Net Debt":     tlb + revolver - cash,
        })

    lbo_df = pd.DataFrame(rows)
    last   = lbo_df.iloc[-1]
    exit_ev     = last["EBITDA"] * params["exit_multiple"]
    exit_equity = exit_ev - last["Net Debt"]

    if exit_equity <= 0:
        return lbo_df, {
            "Entry EV": entry_ev, "Total Debt": total_debt,
            "Equity In": equity_in, "Exit EV": exit_ev,
            "Exit Equity": exit_equity, "MOIC": 0.0, "IRR": 0.0,
            "total_loss": True,
        }

    moic = exit_equity / equity_in

    # Build cash flow series for IRR: [-equity_in, FCF_1, ..., FCF_N + exit_equity]
    cashflows = [-equity_in] + [0] * (params["years"] - 1) + [exit_equity]

    try:
        irr = compute_irr(cashflows)
    except Exception:
        # Fallback: approximate via MOIC ^ (1/years) - 1
        irr = moic ** (1.0 / params["years"]) - 1.0

    return lbo_df, {
        "Entry EV": entry_ev, "Total Debt": total_debt,
        "Equity In": equity_in, "Exit EV": exit_ev,
        "Exit Equity": exit_equity, "MOIC": moic, "IRR": irr,
        "total_loss": False,
    }


# =============================================================================
# FORMATTING
# =============================================================================
def fmt(x, unit: str = "auto") -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    try:
        x = float(x)
    except (TypeError, ValueError):
        return str(x)
    if unit == "auto":
        if abs(x) >= 1_000_000:  return f"${x/1_000_000:.2f}M"
        if abs(x) >= 1_000:      return f"${x/1_000:.0f}K"
        return f"${x:,.0f}"
    if unit == "pct":  return f"{x*100:.1f}%"
    if unit == "x":    return f"{x:.2f}x"
    return str(x)


FMT_LBO = {
    "Revenue": "${:,.0f}", "EBITDA": "${:,.0f}", "EBITDA Margin": "{:.1%}",
    "Interest": "${:,.0f}", "Tax": "${:,.0f}", "CapEx": "${:,.0f}",
    "ΔNWC": "${:,.0f}", "FCF": "${:,.0f}", "TLB": "${:,.0f}",
    "Revolver": "${:,.0f}", "Debt Repaid": "${:,.0f}",
    "Cash": "${:,.0f}", "Net Debt": "${:,.0f}",
}


# =============================================================================
# CHARTING
# =============================================================================
def chart_debt_paydown(lbo_df: pd.DataFrame):
    if not PLOTLY:
        return
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="TLB",
        x=lbo_df["Year"], y=lbo_df["TLB"],
        marker_color="#1e3a5f",
    ))
    fig.add_trace(go.Bar(
        name="Revolver",
        x=lbo_df["Year"], y=lbo_df["Revolver"],
        marker_color="#3b82f6",
    ))
# =============================================================================
# CHARTING
# =============================================================================
def chart_debt_paydown(lbo_df: pd.DataFrame):
    if not PLOTLY:
        return

    fig = go.Figure()

    fig.add_trace(go.Bar(
        name="TLB",
        x=lbo_df["Year"], y=lbo_df["TLB"],
        marker_color="#1e3a5f",
    ))

    fig.add_trace(go.Bar(
        name="Revolver",
        x=lbo_df["Year"], y=lbo_df["Revolver"],
        marker_color="#3b82f6",
    ))

    fig.add_trace(go.Scatter(
        name="Cash",
        x=lbo_df["Year"], y=lbo_df["Cash"],
        mode="lines+markers",
        line_color="#16a34a",
    ))

    fig.update_layout(
        barmode="stack",
        bargap=0.25,
        title="Debt Paydown & Cash Buildup",
        xaxis_title="Year",
        yaxis_title="$",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.15,
            xanchor="right",
            x=1
        ),  # ✅ comma here
        height=380,
        margin=dict(l=0, r=0, t=80, b=20),
        plot_bgcolor="#f8fafc",
        paper_bgcolor="#ffffff",
    )

    st.plotly_chart(fig, use_container_width=True)

def chart_fcf_ebitda(lbo_df: pd.DataFrame):
    if not PLOTLY:
        return

    fig = go.Figure()

    fig.add_trace(go.Bar(
        name="EBITDA",
        x=lbo_df["Year"], y=lbo_df["EBITDA"],
        marker_color="#0ea5e9",
    ))

    fig.add_trace(go.Bar(
        name="FCF",
        x=lbo_df["Year"], y=lbo_df["FCF"],
        marker_color="#16a34a",
    ))

    fig.update_layout(
        barmode="group",
        title="EBITDA vs Free Cash Flow",
        xaxis_title="Year",
        yaxis_title="$",
        height=350,
        margin=dict(l=0, r=0, t=80, b=20),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.15,
            xanchor="right",
            x=1
        ),
        plot_bgcolor="#f8fafc",
        paper_bgcolor="#ffffff",
    )

    st.plotly_chart(fig, use_container_width=True)

def chart_moic_waterfall(returns: dict):
    if not PLOTLY or returns.get("total_loss"):
        return

    fig = go.Figure(go.Waterfall(
        name="Value Bridge",
        orientation="v",
        measure=["absolute", "relative", "relative", "total"],
        x=["Equity In", "EBITDA Growth", "Multiple Expansion", "Exit Equity"],
        y=[
            -returns["Equity In"],
            returns["Exit EV"] * 0.55,
            returns["Exit EV"] * 0.45 - returns["Entry EV"] * 0.45,
            0,
        ],
        totals={"marker": {"color": "#1e3a5f"}},
        increasing={"marker": {"color": "#16a34a"}},
        decreasing={"marker": {"color": "#dc2626"}},
    ))

    fig.update_layout(
        title="Equity Value Bridge (Illustrative)",
        height=350,
        margin=dict(l=0, r=0, t=80, b=20),
        waterfallgap=0.3,  # 👈 spacing fix
        plot_bgcolor="#f8fafc",
        paper_bgcolor="#ffffff",
    )

    st.plotly_chart(fig, use_container_width=True)

# =============================================================================
# EXCEL EXPORT
# =============================================================================
def build_excel_export(pl_metrics, lbo_df, returns, sc_rows):
    """Returns bytes of an Excel workbook with summary + LBO model sheets."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # Summary sheet
        summary = pd.DataFrame([
            {"Item": "Revenue",      "Value": pl_metrics["Revenue"]},
            {"Item": "Gross Profit", "Value": pl_metrics["Gross Profit"]},
            {"Item": "EBITDA",       "Value": pl_metrics["EBITDA"]},
            {"Item": "EBIT",         "Value": pl_metrics["EBIT"]},
            {"Item": "Net Profit",   "Value": pl_metrics["Net Profit"]},
            {"Item": "Entry EV",     "Value": returns["Entry EV"]},
            {"Item": "Equity In",    "Value": returns["Equity In"]},
            {"Item": "Exit EV",      "Value": returns["Exit EV"]},
            {"Item": "Exit Equity",  "Value": returns["Exit Equity"]},
            {"Item": "MOIC",         "Value": returns.get("MOIC", 0)},
            {"Item": "IRR",          "Value": returns.get("IRR", 0)},
        ])
        summary.to_excel(writer, sheet_name="Summary", index=False)

        # LBO model sheet
        lbo_df.to_excel(writer, sheet_name="LBO Model", index=False)

        # Scenarios sheet
        if sc_rows:
            pd.DataFrame(sc_rows).to_excel(writer, sheet_name="Scenarios", index=False)

    buf.seek(0)
    return buf.read()


# =============================================================================
# SESSION STATE
# =============================================================================
_defaults = {
    "calibrated":   False,
    "cal_entry":    5.0,
    "cal_exit":     7.0,
    "cal_growth":   10,
    "cal_margin":   20,
    "cal_leverage": 60,
    "cal_capex":    5,
    "cal_nwc":      5,
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# =============================================================================
# SIDEBAR
# =============================================================================
st.markdown("""
<style>
section[data-testid="stSidebar"] > div:first-child {
    padding-top: 0.5rem;
}
</style>
""", unsafe_allow_html=True)

st.sidebar.subheader("⚙️ Deal Parameters")

with st.sidebar.expander("🤖 AI Classification (optional)"):
    st.caption(
        "Paste your **Anthropic API key** to let Claude classify any "
        "P&L line items the keyword engine doesn't recognise.\n\n"
        "Get one at **console.anthropic.com → API Keys**."
    )
    api_key = st.text_input("Anthropic API Key", type="password")
    use_ai  = st.checkbox("Enable AI classification", value=bool(api_key))

with st.sidebar.expander("🧹 EBITDA Normalisation (SME add-backs)"):
    st.caption(
        "Add back owner salaries above market rate, one-off items, "
        "and personal expenses to arrive at maintainable EBITDA."
    )
    addback_salary   = st.number_input("Excess owner salary ($)",      0, step=10_000)
    addback_oneoff   = st.number_input("One-off / non-recurring ($)",  0, step=10_000)
    addback_personal = st.number_input("Personal expenses ($)",        0, step=5_000)
total_addbacks = float(addback_salary + addback_oneoff + addback_personal)

# Auto-calibrate status
if st.session_state.calibrated:
    st.sidebar.success("✅ Parameters auto-calibrated from financials")
    if st.sidebar.button("🔄 Reset to manual defaults"):
        for k, v in _defaults.items():
            st.session_state[k] = v
        st.rerun()

st.sidebar.subheader("Valuation")
entry_multiple = st.sidebar.number_input(
    "Entry EV/EBITDA", 2.0, 20.0,
    value=float(st.session_state.cal_entry), step=0.5,
)
exit_multiple = st.sidebar.number_input(
    "Exit EV/EBITDA", 2.0, 20.0,
    value=float(st.session_state.cal_exit), step=0.5,
)

st.sidebar.subheader("Holding Period & Growth")
years  = st.sidebar.slider("Holding Period (years)", 1, 7, 5)
growth = st.sidebar.slider(
    "Revenue Growth % p.a.", 0, 40,
    value=int(st.session_state.cal_growth),
) / 100

margin_mode = st.sidebar.radio("EBITDA Margin Input", ["Flat", "Per Year"], horizontal=True)
if margin_mode == "Flat":
    flat_m  = st.sidebar.slider(
        "EBITDA Margin %", 0, 60,
        value=int(st.session_state.cal_margin),
    ) / 100
    margins = [flat_m] * years
else:
    margins = [
        st.sidebar.slider(f"Y{i+1} EBITDA Margin %", 0, 60, 20 + i) / 100
        for i in range(years)
    ]

st.sidebar.subheader("Capital Structure")
leverage_pct = st.sidebar.slider(
    "Leverage % of Entry EV", 20, 80,
    value=int(st.session_state.cal_leverage),
) / 100
tlb_rate = st.sidebar.slider("TLB Interest Rate %", 0, 20, 7) / 100
rev_rate = st.sidebar.slider("Revolver Rate %",     0, 20, 6) / 100

st.sidebar.subheader("Other Assumptions")
tax_rate  = st.sidebar.slider("Tax Rate %",            0, 35, 17) / 100
da_pct    = st.sidebar.slider("D&A % of Revenue",      0, 15,  3) / 100
nwc_pct   = st.sidebar.slider(
    "NWC % of Revenue", 0, 20,
    value=int(st.session_state.cal_nwc),
) / 100
capex_pct = st.sidebar.slider(
    "CapEx % of Revenue", 0, 20,
    value=int(st.session_state.cal_capex),
) / 100
min_cash  = st.sidebar.number_input("Minimum Cash ($)", 0, value=50_000, step=10_000)

params = dict(
    entry_multiple=entry_multiple,
    exit_multiple=exit_multiple,
    years=years,
    growth=growth,
    margins=margins,
    leverage_pct=leverage_pct,
    tlb_rate=tlb_rate,
    rev_rate=rev_rate,
    tax_rate=tax_rate,
    da_pct=da_pct,
    nwc_pct=nwc_pct,
    capex_pct=capex_pct,
    min_cash=float(min_cash),
    use_override_margin=True,       # always set — prevents KeyError in run_lbo
)


# =============================================================================
# MAIN PAGE
# =============================================================================
st.title("📊 SME Valuation & LBO Tool")
st.caption(
    "Upload a P&L and (optionally) a Balance Sheet to generate a full LBO valuation. "
    "Supports **xlsx, xls, csv, digital PDF, and scanned PDF** (OCR)."
)

# ── Comparable Transactions Reference ─────────────────────────────────────────
with st.expander("📚 Singapore SME Comparable Transaction Multiples — Reference"):
    st.caption(
        "Indicative EV/EBITDA ranges observed in Singapore/SEA private M&A. "
        "Use as a sanity check against your entry/exit multiple assumptions."
    )
    st.dataframe(COMPS_TABLE, use_container_width=True, hide_index=True)

st.markdown("---")

# =============================================================================
# STEP 1 — UPLOAD
# =============================================================================
st.header("📂 Step 1 — Upload Financials")
col_pl, col_bs = st.columns(2)
with col_pl:
    pl_file = st.file_uploader(
        "P&L Statement", type=["xlsx", "xls", "csv", "pdf"],
        help="Most recent full-year P&L. Multi-year not yet supported — use most recent year.",
    )
with col_bs:
    bs_file = st.file_uploader(
        "Balance Sheet (optional)", type=["xlsx", "xls", "csv", "pdf"],
        help="Used to derive cash, debt, and NWC for bridge calculations.",
    )

# Shared state
pl_metrics:    dict  | None = None
bs_derived_nwc: float | None = None
cash_bs = debt_bs = 0.0
sc_rows = []


# =============================================================================
# PROCESS P&L
# =============================================================================
if pl_file:
    raw_pl = read_any_file(pl_file)

    if raw_pl is not None:
        df_pl = smart_clean(raw_pl)

        if df_pl is None:
            st.error("P&L could not be parsed. Please check file format.")
        else:
            df_pl = classify_pl(df_pl, use_ai=use_ai, api_key=api_key or "")

            st.markdown("---")
            st.header("📋 Step 2 — Review & Correct P&L Classifications")
            st.caption(
                "Every row is editable. Use the Category dropdown to fix "
                "misclassified items. Corrections are saved to memory for future uploads."
            )

            if total_addbacks > 0:
                st.info(
                    f"🧹 **EBITDA normalisation active:** {fmt(total_addbacks)} "
                    "will be added back before computing EBITDA."
                )
              
            unknown_rows = df_pl[df_pl["Category"] == "Unknown"]
            
            # Ignore zero-value noise rows
            unknown_rows = unknown_rows[unknown_rows["Amount"] != 0]
            
            unknown_count = len(unknown_rows)
            if unknown_count:
                st.warning(
                    f"⚠️ {unknown_count} row(s) unclassified. "
                    "Fix them below or enable AI Classification in the sidebar."
                )
              
            df_display = df_pl.copy()
            
            # Ensure numeric (fix OCR issues)
            df_display["Amount"] = pd.to_numeric(df_display["Amount"], errors="coerce")
            
            # Flip sign ONLY for Tax (display only)
            mask = df_display["Category"] == "Tax"
            df_display.loc[mask, "Amount"] = df_display.loc[mask, "Amount"].abs()
            
            df_edited = st.data_editor(
                df_display,
                use_container_width=True,
                num_rows="fixed",
                column_config={
                    "Category": st.column_config.SelectboxColumn(
                        "Category",
                        options=["Revenue", "COGS", "OpEx", "D&A", "Interest", "Other Income", "Tax", "Ignore"],
                    )
                },
            )
          
            # Persist corrections to memory
            mem = load_memory()
            for _, r in df_pl.iterrows():
                if r["Category"] not in ("Unknown", "Ignore"):
                    mem[r["Line Item"]] = r["Category"]
            save_memory(mem)

            active_pl  = df_pl[~df_pl["Category"].isin(["Ignore", "Unknown"])]
            pl_metrics = compute_pl(active_pl, addbacks=total_addbacks)


# =============================================================================
# PROCESS BALANCE SHEET
# =============================================================================
if bs_file:
    raw_bs = read_any_file(bs_file)

    if raw_bs is not None:
        df_bs = smart_clean(raw_bs)

        if df_bs is None:
            st.error("Balance Sheet could not be parsed. Please check file format.")
        else:
            df_bs = classify_bs(df_bs)

            st.markdown("---")
            st.subheader("🏦 Balance Sheet — Review Classifications")
            st.caption(
                "Company-named bank accounts are auto-classified as Cash. "
                "Director loans appear under Debt."
            )

            df_bs = st.data_editor(
                df_bs,
                column_config={
                    "Category": st.column_config.SelectboxColumn(
                        "Category", options=BS_CATEGORIES
                    ),
                    "Amount": st.column_config.NumberColumn("Amount", format="$ %.0f"),
                },
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
            )

            cash_bs     = df_bs.loc[df_bs["Category"] == "Cash",        "Amount"].sum()
            debt_bs     = df_bs.loc[df_bs["Category"] == "Debt",        "Amount"].sum()
            receivables = df_bs.loc[df_bs["Category"] == "Receivables", "Amount"].sum()
            payables    = df_bs.loc[df_bs["Category"] == "Payables",    "Amount"].sum()
            inventory   = df_bs.loc[df_bs["Category"] == "Inventory",   "Amount"].sum()
            bs_derived_nwc = receivables + inventory - payables

            if receivables == 0:
                st.warning(
                    "⚠️ No receivables detected. This usually means:\n"
                    "- Assets page missing, OR\n"
                    "- Receivables misclassified (e.g. 'amount due from customers')."
                )

# =============================================================================
# AUTO-CALIBRATE BUTTON
# =============================================================================
if pl_metrics and pl_metrics.get("EBITDA", 0) > 0:
    cal = auto_calibrate(pl_metrics, cash_bs, debt_bs)

    st.markdown("---")
    col_cal, col_info = st.columns([1, 2])
    with col_cal:
        if st.button(
            "🎯 Auto-calibrate deal parameters", type="primary",
            help="Sets entry/exit multiples, growth, margins, and leverage "
                 "based on this company's financial profile.",
        ):
            st.session_state.calibrated   = True
            st.session_state.cal_entry    = cal["entry_multiple"]
            st.session_state.cal_exit     = cal["exit_multiple"]
            st.session_state.cal_growth   = int(cal["growth"] * 100)
            st.session_state.cal_margin   = int(cal["target_margin"] * 100)
            st.session_state.cal_leverage = int(cal["leverage_pct"] * 100)
            st.session_state.cal_capex    = int(cal["capex_pct"] * 100)
            st.session_state.cal_nwc      = int(cal["nwc_pct"] * 100)
            st.rerun()

    with col_info:
        r = cal["rationale"]
        with st.expander("ℹ️ Calibration rationale"):
            st.markdown(
                f"- **Revenue tier:** {r['revenue_tier']}\n"
                f"- **Margin quality:** {r['margin_quality']}\n"
                f"- **Leverage:** {r['leverage_ratio']}\n"
                f"- **Suggested entry:** {r['suggested_entry']}  |  "
                f"**Suggested exit:** {r['suggested_exit']}"
            )


# =============================================================================
# VALUATION OUTPUT
# =============================================================================
if pl_metrics:
    st.markdown("---")
    st.header("📊 Step 3 — Valuation Output")
    m = pl_metrics

    # ── P&L Headline metrics ──────────────────────────────────────────────────
    if m["Add-backs"] > 0:
        st.success(
            f"📈 Normalised EBITDA: **{fmt(m['EBITDA'])}** "
            f"({fmt(m['EBITDA Margin'], 'pct')} margin) — includes "
            f"{fmt(m['Add-backs'])} of add-backs."
        )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Revenue",      fmt(m["Revenue"]))
    c2.metric("Gross Profit", fmt(m["Gross Profit"]),  fmt(m["GP Margin"],     "pct"))
    c3.metric("EBITDA",       fmt(m["EBITDA"]),        fmt(m["EBITDA Margin"], "pct"))
    c4.metric("EBIT",         fmt(m["EBIT"]),          fmt(m["EBIT Margin"],   "pct"))
    c5.metric("Net Profit",   fmt(m["Net Profit"]),    fmt(m["Net Margin"],    "pct"))

    # ── P&L Bridge ────────────────────────────────────────────────────────────
    with st.expander("📄 Full P&L Bridge"):
        bridge_rows = [
            ("Revenue",                    m["Revenue"]),
            ("(−) COGS",                  -m["COGS"]),
            ("= Gross Profit",             m["Gross Profit"]),
            ("(−) OpEx (gross)",          -m["OpEx (gross)"]),
        ]
        if m["Add-backs"] > 0:
            bridge_rows.append(("(+) Add-backs", m["Add-backs"]))
        bridge_rows += [
            ("(−) D&A",                   -m["D&A"]),
            ("= EBIT (Operating Profit)",  m["EBIT"]),
            ("(+) D&A",                    m["D&A"]),
            ("= EBITDA",                   m["EBITDA"]),
            ("───────────────",            None),
            ("EBIT",                       m["EBIT"]),
            ("(+) Other Income",           m["Other Income"]),
            ("(−) Interest Expense",      -m["Interest"]),
            ("= EBT",                      m["EBT"]),
            ("(−) Tax",                   -m["Tax"]),
            ("= Net Profit",               m["Net Profit"]),
        ]
        pl_bridge = pd.DataFrame(
            [(r, fmt(v) if v is not None else "────") for r, v in bridge_rows],
            columns=["Item", "Amount"],
        )
        st.dataframe(pl_bridge, use_container_width=True, hide_index=True)

    # ── Balance Sheet Snapshot ────────────────────────────────────────────────
    if bs_file:
        st.subheader("Balance Sheet Snapshot")
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Cash & Equivalents",  fmt(cash_bs))
        b2.metric("Total Debt",          fmt(debt_bs))
        b3.metric("Net Debt",            fmt(debt_bs - cash_bs))
        if bs_derived_nwc is not None:
            b4.metric("Working Capital", fmt(bs_derived_nwc))

    st.markdown("---")

    # ── EBITDA guard ──────────────────────────────────────────────────────────
    if m["EBITDA"] <= 0:
        st.error(
            "⚠️ EBITDA is zero or negative — LBO model cannot run. "
            "Check Revenue and COGS/OpEx classifications in Step 2, "
            "or add back non-cash / one-off items in the sidebar."
        )
        st.stop()

    # ── LBO params (inject initial_nwc if available) ──────────────────────────
    lbo_params = {
        **params,
        **({"initial_nwc": bs_derived_nwc} if bs_derived_nwc is not None else {}),
    }

    # ── Scenario Comparison ───────────────────────────────────────────────────
    st.subheader("📐 Scenario Analysis — Bear / Base / Bull")
    scenarios = build_scenarios(pl_metrics, cash_bs, debt_bs, lbo_params)

    sc_rows = []
    for sc_name, sc_params in scenarios.items():
        _, sc_ret = run_lbo(pl_metrics, cash_bs, debt_bs, sc_params)
        sc_rows.append({
            "Scenario":  sc_name,
            "Entry":     f"{sc_params['entry_multiple']:.1f}x",
            "Exit":      f"{sc_params['exit_multiple']:.1f}x",
            "Growth":    fmt(sc_params["growth"], "pct"),
            "Margin":    fmt(sc_params["margins"][0], "pct"),
            "Leverage":  fmt(sc_params["leverage_pct"], "pct"),
            "Entry EV":  fmt(sc_ret["Entry EV"]),
            "Equity In": fmt(sc_ret["Equity In"]),
            "Exit EV":   fmt(sc_ret["Exit EV"]),
            "MOIC":      "Loss" if sc_ret["total_loss"] else f"{sc_ret['MOIC']:.2f}x",
            "IRR":       "—"    if sc_ret["total_loss"] else fmt(sc_ret["IRR"], "pct"),
        })
    st.caption(
        "Base = current model assumptions | Bear/Bull adjust growth, margins, and leverage — not just multiples."
    )
    st.markdown("**Scenario positioning:**")
    for sc in sc_rows:
        st.write(f"{sc['Scenario']}: Entry {sc['Entry']} | Exit {sc['Exit']}")
  
    # ── Current parameters — Returns ──────────────────────────────────────────
    lbo_df, returns = run_lbo(pl_metrics, cash_bs, debt_bs, lbo_params)

    st.subheader("📈 Current Parameters — Returns")
    if returns.get("total_loss"):
        st.error(
            "⚠️ **Total loss** at current parameters. "
            "Try a lower entry multiple, lower leverage, or a higher exit multiple. "
            "Use 🎯 Auto-calibrate for suggested parameters."
        )
        r1, r2 = st.columns(2)
        r1.metric("Entry EV", fmt(returns["Entry EV"]))
        r2.metric("Exit EV",  fmt(returns["Exit EV"]))
    else:
        r1, r2, r3, r4, r5 = st.columns(5)
        r1.metric("Entry EV",  fmt(returns["Entry EV"]))
        r2.metric("Equity In", fmt(returns["Equity In"]))
        r3.metric("Exit EV",   fmt(returns["Exit EV"]))
        r4.metric("MOIC",      fmt(returns["MOIC"], "x"))

        irr_val = returns["IRR"]
        irr_str = fmt(irr_val, "pct")
        r5.metric("IRR", irr_str)

        # IRR quality indicator
        if irr_val >= 0.25:
            st.success(f"✅ IRR of {irr_str} — exceeds typical PE hurdle rate (20–25%). Strong deal.")
        elif irr_val >= 0.15:
            st.warning(f"⚠️ IRR of {irr_str} — borderline. Typical PE threshold is 20–25%.")
        else:
            st.error(f"❌ IRR of {irr_str} — below PE hurdle rate. Re-examine deal structure.")

        # ── Investor View — Cash vs Debt ─────────────────────────────
        st.subheader("💰 Investor View — Your Investment")

        entry_ev = returns["Entry EV"]
        equity_in = returns["Equity In"]
        net_debt_bs = debt_bs - cash_bs
        debt_used = entry_ev - equity_in + net_debt_bs

        c1, c2 = st.columns(2)
        c1.metric("💵 Your Cash Invested", fmt(equity_in))
        c2.metric("🏦 Debt Financing", fmt(debt_used))

        if entry_ev > 0:
                st.caption(
                    f"Funding mix: {equity_in/entry_ev:.0%} equity | {debt_used/entry_ev:.0%} debt"
                )
        # ── Deleveraging — Debt Paydown ─────────────────────────────
        st.subheader("📉 Deleveraging — Debt Paydown")
        
        initial_debt = returns["Total Debt"]
        final_debt = lbo_df.iloc[-1]["TLB"] + lbo_df.iloc[-1]["Revolver"]
        debt_repaid = initial_debt - final_debt
        
        d1, d2, d3 = st.columns(3)
        d1.metric("Initial Debt", fmt(initial_debt))
        d2.metric("Debt Repaid", fmt(debt_repaid))
        d3.metric("Debt Remaining at Exit", fmt(final_debt))
        
        st.caption("Debt is repaid using the company's free cash flow over the investment period.")

        # ── Charts ────────────────────────────────────────────────────────────
        if PLOTLY:
            ch1, ch2 = st.columns(2)
            with ch1:
                chart_debt_paydown(lbo_df)
            with ch2:
                chart_fcf_ebitda(lbo_df)
            chart_moic_waterfall(returns)
        else:
            st.info("Install plotly for charts: `pip install plotly`")

        # ── MOIC Sensitivity grid ─────────────────────────────────────────────
        st.subheader("🔢 Sensitivity: MOIC (Entry × Exit)")
        entry_steps = sorted(set([
            round(entry_multiple + d, 1) for d in (-1.0, -0.5, 0, +0.5, +1.0)
        ] + [round(entry_multiple, 1)]))
        
        exit_steps = sorted(set([
            round(exit_multiple + d, 1) for d in (-1.0, -0.5, 0, +0.5, +1.0)
        ] + [round(exit_multiple, 1)]))
        rows_sens = []
        for em in entry_steps:
            row = {"Entry \\ Exit": f"{em:.1f}x"}
            for xm in exit_steps:
                if xm <= em:
                    row[f"{xm:.1f}x"] = "—"
                    continue
                _, ret2 = run_lbo(
                    pl_metrics, cash_bs, debt_bs,
                    {**lbo_params, "entry_multiple": em, "exit_multiple": xm},
                )
                row[f"{xm:.1f}x"] = (
                    "Loss" if ret2["total_loss"] else f"{ret2['MOIC']:.2f}x"
                )
            rows_sens.append(row)
        df_sens = pd.DataFrame(rows_sens).set_index("Entry \\ Exit")
        
        def highlight_base(row):
            styles = []
            for col in df_sens.columns:
                if row.name == f"{entry_multiple:.1f}x" and col == f"{exit_multiple:.1f}x":
                    styles.append("background-color: #16a34a; color: white;")
                else:
                    styles.append("")
            return styles
        
        st.dataframe(
            df_sens.style.apply(highlight_base, axis=1),
            use_container_width=True,
        )

        # ── IRR Sensitivity grid ──────────────────────────────────────────────
        st.subheader("🔢 Sensitivity: IRR (Entry × Exit)")
        rows_irr = []
        for em in entry_steps:
            row = {"Entry \\ Exit": f"{em:.1f}x"}
            for xm in exit_steps:
                if xm <= em:
                    row[f"{xm:.1f}x"] = "—"
                    continue
                _, ret2 = run_lbo(
                    pl_metrics, cash_bs, debt_bs,
                    {**lbo_params, "entry_multiple": em, "exit_multiple": xm},
                )
                row[f"{xm:.1f}x"] = (
                    "Loss" if ret2["total_loss"] else fmt(ret2["IRR"], "pct")
                )
            rows_irr.append(row)
        st.dataframe(
            pd.DataFrame(rows_irr).set_index("Entry \\ Exit"),
            use_container_width=True,
        )

    # ── LBO Model table ───────────────────────────────────────────────────────
    st.subheader("📋 LBO Model — Annual Detail")
    st.dataframe(
        lbo_df.style.format(FMT_LBO),
        use_container_width=True,
        hide_index=True,
    )

    # ── Valuation Bridge ──────────────────────────────────────────────────────
    with st.expander("🏗️ Valuation Bridge"):
        bridge = pd.DataFrame([
            {"Item": "Entry EV",               "Value": fmt(returns["Entry EV"])},
            {"Item": "  (−) Transaction Debt", "Value": fmt(returns["Total Debt"])},
            {"Item": "  (+) BS Cash",          "Value": fmt(cash_bs)},
            {"Item": "  (−) BS Debt",          "Value": fmt(debt_bs)},
            {"Item": "= Equity Invested",       "Value": fmt(returns["Equity In"])},
            {"Item": "─────────────────────",  "Value": ""},
            {"Item": "Exit EV",                "Value": fmt(returns["Exit EV"])},
            {"Item": "  (−) Exit Net Debt",    "Value": fmt(
                returns["Exit EV"] - returns["Exit Equity"])},
            {"Item": "= Exit Equity",          "Value": fmt(returns["Exit Equity"])},
            {"Item": "─────────────────────",  "Value": ""},
            {"Item": "MOIC",  "Value": fmt(returns["MOIC"], "x")   if not returns["total_loss"] else "Loss"},
            {"Item": "IRR",   "Value": fmt(returns["IRR"],  "pct") if not returns["total_loss"] else "—"},
        ])
        st.dataframe(bridge, use_container_width=True, hide_index=True)

    # ── Excel Export ──────────────────────────────────────────────────────────
    if OPENPYXL and not returns.get("total_loss"):
        xl_bytes = build_excel_export(pl_metrics, lbo_df, returns, sc_rows)
        st.download_button(
            label="⬇️ Download Full Model (Excel)",
            data=xl_bytes,
            file_name="sme_lbo_model.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    elif not OPENPYXL:
        st.caption("Install openpyxl for Excel export: `pip install openpyxl`")

elif not pl_file:
    st.info("👆 Upload a P&L statement above to get started.")
    st.markdown("""
    **What this tool does:**
    - Parses P&L and Balance Sheet from any format (Excel, CSV, PDF, scanned PDF)
    - Classifies line items automatically (keyword engine + optional AI)
    - Computes normalised EBITDA with owner add-backs
    - Runs a full LBO model with debt paydown mechanics
    - Outputs Bear / Base / Bull scenarios, MOIC & IRR sensitivity grids
    - Charts debt paydown, FCF, and equity value bridge
    - Exports to Excel
    """)
