"""
SME Valuation & LBO Tool  ·  Production-ready
==============================================
Singapore / SEA SME buyout modelling.

NEW in this version
───────────────────
 1. Multi-year P&L  — auto-detects multiple amount columns; user picks the year
 2. Sector selector — wired into auto-calibration (IT/SaaS, Prof Services, F&B,
                      Construction, Healthcare, E-commerce)
 3. Revolver as a fixed facility size, not a forced % of total debt
 4. Leverage sensitivity grid  (entry × leverage → MOIC)
 5. HP / finance-lease split in BS classifier
 6. Earnout toggle  — milestone-conditional payments (EBITDA hurdle per year)
 7. Mezzanine / PIK tranche  — second debt layer with PIK roll option
 8. Management equity pool + preferred return waterfall
    (hurdle IRR → sponsor carry → mgmt pool split)
 9. All original bug-fixes retained (equity sign, rollover at entry+exit,
    IRR anchoring, scenario table, debt-sweep/cash-cap controls,
    mandatory TLB amort, BS section-trigger detection)
10. Deployment notes in sidebar — secrets / package checklist
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
    import plotly.express as px
    PLOTLY = True
except ImportError:
    PLOTLY = False

try:
    import openpyxl          # noqa
    OPENPYXL = True
except ImportError:
    OPENPYXL = False


# =============================================================================
# PASSWORD GATE
# =============================================================================
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets.get("APP_PASSWORD", ""):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Enter password", type="password",
                      on_change=password_entered, key="password")
        st.stop()
    elif not st.session_state["password_correct"]:
        st.text_input("Enter password", type="password",
                      on_change=password_entered, key="password")
        st.error("Incorrect password")
        st.stop()


# Only gate if secret is set
if "APP_PASSWORD" in st.secrets:
    check_password()

warnings.filterwarnings("ignore", category=RuntimeWarning)
st.set_page_config(layout="wide", page_title="SME Valuation Tool", page_icon="📊")

st.markdown("""
<style>
[data-testid="metric-container"] {
    background: #f8fafc; border: 1px solid #e2e8f0;
    border-radius: 8px; padding: 12px 16px;
}
[data-testid="stMetricValue"] { font-size: 1.4rem !important; }
.irr-good  { color: #16a34a; font-weight: 700; }
.irr-warn  { color: #d97706; font-weight: 700; }
.irr-bad   { color: #dc2626; font-weight: 700; }
section[data-testid="stSidebar"] { background-color: #0f172a; }
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] span { color: #e2e8f0; }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# CONSTANTS
# =============================================================================
MEMORY_FILE = "memory.json"

BS_CATEGORIES = ["Cash", "Receivables", "Inventory", "Fixed Assets",
                 "Debt - Bank Loan", "Debt - Hire Purchase", "Debt - Finance Lease",
                 "Debt - Director Loan", "Payables", "Equity", "Ignore", "Other"]

SECTORS = [
    "IT Services / SaaS",
    "Professional Services",
    "F&B / Retail",
    "Construction / Trade",
    "Healthcare / Wellness",
    "E-commerce / Logistics",
    "Manufacturing",
    "Other",
]

# Sector calibration: (base_entry_small, base_entry_mid, margin_premium, note)
SECTOR_CAL = {
    "IT Services / SaaS":       (4.5, 7.0, +0.5,  "Recurring revenue premium"),
    "Professional Services":    (3.5, 5.5,  0.0,  "Key-person risk discount"),
    "F&B / Retail":             (2.5, 4.0, -0.5,  "Thin margins, execution risk"),
    "Construction / Trade":     (2.0, 3.5, -0.5,  "Project risk, WC intensive"),
    "Healthcare / Wellness":    (4.0, 6.5, +0.5,  "Defensiveness + licensing moat"),
    "E-commerce / Logistics":   (3.0, 5.0,  0.0,  "Scale-driven multiple"),
    "Manufacturing":            (2.5, 4.5, -0.25, "Asset-heavy, cyclical"),
    "Other":                    (3.0, 5.0,  0.0,  ""),
}

COMPS_TABLE = pd.DataFrame([
    {"Sector": "IT Services / SaaS",     "Rev Size": "<$2M",   "EV/EBITDA": "4–7x",  "EV/Rev": "0.8–2.0x", "Note": "Recurring revenue premium"},
    {"Sector": "IT Services / SaaS",     "Rev Size": "$2–10M", "EV/EBITDA": "6–9x",  "EV/Rev": "1.5–3.5x", "Note": "Sticky customers"},
    {"Sector": "Professional Services",  "Rev Size": "<$2M",   "EV/EBITDA": "3–5x",  "EV/Rev": "0.5–1.0x", "Note": "Key-person risk"},
    {"Sector": "Professional Services",  "Rev Size": "$2–10M", "EV/EBITDA": "4–7x",  "EV/Rev": "0.8–1.5x", "Note": ""},
    {"Sector": "F&B / Retail",           "Rev Size": "<$2M",   "EV/EBITDA": "2–4x",  "EV/Rev": "0.3–0.6x", "Note": "Thin margins"},
    {"Sector": "F&B / Retail",           "Rev Size": "$2–10M", "EV/EBITDA": "3–5x",  "EV/Rev": "0.5–0.9x", "Note": ""},
    {"Sector": "Construction / Trade",   "Rev Size": "<$5M",   "EV/EBITDA": "2–4x",  "EV/Rev": "0.2–0.5x", "Note": "Project risk, WC intensive"},
    {"Sector": "Healthcare / Wellness",  "Rev Size": "<$5M",   "EV/EBITDA": "5–8x",  "EV/Rev": "1.0–2.5x", "Note": "Licensing moat"},
    {"Sector": "E-commerce / Logistics", "Rev Size": "$2–10M", "EV/EBITDA": "3–6x",  "EV/Rev": "0.4–1.0x", "Note": "Scale-driven"},
    {"Sector": "Manufacturing",          "Rev Size": "<$10M",  "EV/EBITDA": "2–5x",  "EV/Rev": "0.3–0.8x", "Note": "Asset-heavy"},
])

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
        "rent", "rental", "utilities", "cleaning", "renovation", "insurance",
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
        "advance salaries",
    ],
    "Inventory": ["inventory", "stock", "work in progress", "wip", "finished goods"],
    "Fixed Assets": [
        "property", "plant", "equipment", "ppe", "fixed asset",
        "motor vehicle", "machinery", "computer", "furniture",
        "app development", "development cost", "less accumulated",
    ],
    # Detailed debt sub-types
    "Debt - Hire Purchase": ["hire purchase", "hp payable", "hp creditor"],
    "Debt - Finance Lease": ["right-of-use", "finance lease", "lease liabilit", "rou asset"],
    "Debt - Director Loan": ["amount owing to director", "director loan", "due to director"],
    "Debt - Bank Loan": [
        "loan", "debt", "borrowing", "credit facility", "term loan",
        "revolving", "bank overdraft",
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
    "long term liabilit":  "Debt - Bank Loan",
    "equity":              "Equity",
}


# =============================================================================
# IRR — Newton-Raphson + Brent fallback
# =============================================================================
def compute_irr(cashflows: list, guess: float = 0.10) -> float:
    cf = np.array(cashflows, dtype=float)
    signs = np.sign(cf[cf != 0])
    if len(np.unique(signs)) < 2:
        raise ValueError("No sign change — IRR undefined")

    rate = float(guess)
    for _ in range(200):
        t    = np.arange(len(cf), dtype=float)
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

    try:
        from scipy.optimize import brentq
        def npv_fn(r):
            t = np.arange(len(cf), dtype=float)
            return np.sum(cf / (1 + r) ** t)
        return brentq(npv_fn, -0.4999, 5.0, xtol=1e-8, maxiter=200)
    except Exception:
        raise ValueError("IRR did not converge")


# =============================================================================
# AUTO-CALIBRATION  (sector-aware)
# =============================================================================
def auto_calibrate(metrics: dict, cash_bs: float, debt_bs: float,
                   sector: str = "Other") -> dict:
    rev    = metrics.get("Revenue", 0)
    ebitda = metrics.get("EBITDA", 0)
    margin = metrics.get("EBITDA Margin", 0)
    net_debt = max(0.0, debt_bs - cash_bs)

    base_small, base_mid, margin_premium, _ = SECTOR_CAL.get(
        sector, SECTOR_CAL["Other"]
    )

    # Revenue tier
    if   rev < 1_000_000:  base_entry = base_small
    elif rev < 5_000_000:  base_entry = (base_small + base_mid) / 2
    else:                  base_entry = base_mid

    # Margin quality adjustment
    if margin >= 0.30:
        margin_adj = margin_premium + 0.5
    elif margin >= 0.20:
        margin_adj = margin_premium
    elif margin >= 0.10:
        margin_adj = margin_premium - 0.25
    else:
        margin_adj = margin_premium - 0.75

    # Leverage ratio discount
    leverage_ratio = (net_debt / ebitda) if ebitda > 0 else 0
    lev_adj = -0.5 if leverage_ratio > 3 else 0.0

    entry = round(base_entry + margin_adj + lev_adj, 1)
    entry = max(2.0, min(12.0, entry))
    exit_ = round(entry + (0.5 if rev < 1_000_000 else 1.0), 1)

    # Growth
    if   rev < 1_000_000: growth = 0.08
    elif rev < 5_000_000: growth = 0.12
    else:                  growth = 0.15

    # Target margin
    if margin < 0.20:     target_margin = margin + 0.05
    elif margin < 0.30:   target_margin = margin + 0.03
    else:                 target_margin = margin + 0.01
    target_margin = round(min(0.45, max(0.10, target_margin)), 3)

    # Leverage
    entry_ev = entry * ebitda if ebitda > 0 else 1
    if rev < 1_000_000:
        max_debt = min(ebitda * 2.0, 0.50 * entry_ev)
        leverage = round(max(0.25, min(0.50, max_debt / entry_ev)), 2)
    else:
        max_debt = min(ebitda * 3.0, 0.65 * entry_ev)
        leverage = round(max(0.30, min(0.65, max_debt / entry_ev)), 2)

    capex = 0.03 if margin >= 0.25 else 0.06
    nwc   = 0.04 if margin >= 0.25 else 0.08

    base_cash_pct = 0.2 if rev < 2_000_000 else 0.3
    if margin >= 0.25: base_cash_pct += 0.1
    base_cash_pct -= leverage * 0.2
    base_cash_pct = max(0.1, min(0.6, base_cash_pct))

    years = 5
    annual_payment = (entry * ebitda * base_cash_pct) / years
    payment_schedule = [annual_payment] * years

    return {
        "entry_multiple":   entry,
        "exit_multiple":    exit_,
        "growth":           growth,
        "target_margin":    target_margin,
        "payment_schedule": payment_schedule,
        "cash_pct":         base_cash_pct,
        "leverage_pct":     leverage,
        "capex_pct":        capex,
        "nwc_pct":          nwc,
        "rationale": {
            "sector":          sector,
            "revenue_tier":    f"${rev/1e6:.2f}M revenue → {base_entry:.1f}x base ({sector})",
            "margin_quality":  f"{margin*100:.1f}% margin → {margin_adj:+.2f}x adj",
            "leverage_ratio":  f"{leverage_ratio:.1f}× net debt/EBITDA → {lev_adj:+.1f}x adj",
            "suggested_entry": f"{entry:.1f}x",
            "suggested_exit":  f"{exit_:.1f}x",
        },
    }


def build_scenarios(metrics: dict, cash_bs: float, debt_bs: float,
                    base_params: dict) -> dict:
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
        "Base 📊": {**base_params, "use_override_margin": True},
        "Bull 🚀": _make(-0.5, +0.5, +0.03, +0.03, -0.05, -0.01, -0.02),
    }


# =============================================================================
# MEMORY
# =============================================================================
def load_memory() -> dict:
    if "mem_store" in st.session_state:
        return dict(st.session_state["mem_store"])
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE) as f:
                data = json.load(f)
                st.session_state["mem_store"] = data
                return data
    except Exception:
        pass
    return {}


def save_memory(mem: dict):
    st.session_state["mem_store"] = mem
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(mem, f, indent=2)
    except Exception:
        pass


# =============================================================================
# PDF / FILE HELPERS
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
    # Always use first number position for label split
    first_pos = parsed[0][0]
    label = _strip_note_refs(line[:first_pos].strip())
    label = re.sub(r"[|_]{2,}", "", label).strip()
    label = re.sub(r"\s{2,}", " ", label)
    
    if not label:
        return None
    
    values = [v for _, _, v in parsed]
    
    return label, values

    label = _strip_note_refs(line[:cur_pos].strip())
    label = re.sub(r"[|_]{2,}", "", label).strip()
    label = re.sub(r"\s{2,}", " ", label)
    if not label:
        return None
    
    # 🔥 NEW: return ALL values, not just one
    values = [v for _, _, v in parsed]
    
    return label, values

def _ocr_pdf(file_bytes: bytes):
    if not PDF_OCR:
        st.error("📦 OCR requires pdf2image + pytesseract + Pillow.")
        return None

    try:
        images = convert_from_bytes(file_bytes, dpi=300)
    except Exception as e:
        st.error(f"PDF→image conversion failed: {e}")
        return None

    parsed_rows = []

    for img in images:
        img  = _preprocess_image_for_ocr(img)
        text = pytesseract.image_to_string(img, config="--psm 6")

        for line in text.splitlines():
            parsed = _parse_line_multi_amount(line)  # ← your new function

            if parsed:
                label, values = parsed
                parsed_rows.append((label, values))
            else:
                clean = re.sub(r"[|_]{2,}", "", line).strip()
                if clean:
                    parsed_rows.append((clean, []))

    if not parsed_rows:
        st.error("OCR produced no usable rows. Check PDF quality.")
        return None

    # 🔥 Determine max number of value columns
    max_vals = max(len(v) for _, v in parsed_rows)

    rows = []
    for label, values in parsed_rows:
        # pad missing values with 0
        padded = values + [0.0] * (max_vals - len(values))
        rows.append([label] + padded)

    cols = ["c0"] + [f"c{i+1}" for i in range(max_vals)]

    return pd.DataFrame(rows, columns=cols)

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
        st.info("📷 Running OCR on scanned PDF…")
        return _ocr_pdf(file_bytes)
    st.error(f"Unsupported file type: {name}")
    return None


# =============================================================================
# CLEANING PIPELINE  (with multi-year column detection)
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


_META_EXACT = {"account", "accounts", "nan", "none", ""}
_META_PHRASES = [
    "pte. ltd.", "pte ltd", "sdn bhd", "berhad",
    "for the year", "for the period", "as at ", "as of ", "as at",
    "balance sheet", "profit and loss", "income statement",
    "exchange rate", "prepared by", "reviewed by",
]
_DATE_RE = re.compile(
    r"^(31|30|28|29)?\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*\d{4}$",
    re.I,
)


def _is_meta_row(label: str) -> bool:
    xl = label.strip().lower()
    if xl in _META_EXACT:         return True
    if re.fullmatch(r"[\d\s\-/]+", xl): return True
    if _DATE_RE.match(xl):        return True
    if "page" in xl:              return True
    return any(p in xl for p in _META_PHRASES)


def detect_year_columns(df: pd.DataFrame):
    """
    Return list of (col_index, label) for columns that look like amount columns,
    sorted by score descending. Used for multi-year P&L detection.
    """
    df = df.fillna("").astype(str)
    df = dedupe_columns(df)
    df.columns = [f"c{i}" for i in range(len(df.columns))]

    # Identify label column (most text, fewest numbers)
    label_col, label_score = None, -1.0
    for col in df.columns:
        non_empty = (df[col].str.strip() != "").sum()
        numeric   = (parse_amount(df[col]) != 0).sum()
        sc        = non_empty - numeric * 3
        if sc > label_score:
            label_score, label_col = sc, col

    amount_cols = []
    for col in df.columns:
        if col == label_col:
            continue
        sc = score_amount_column(df[col])
        if sc > 0:
            # Try to find a year header for this column
            header_candidates = df[col].iloc[:5].tolist()
            year_label = col
            for h in header_candidates:
                if re.search(r"\b20\d{2}\b", str(h)):
                    year_label = re.search(r"\b20\d{2}\b", str(h)).group()
                    break
            amount_cols.append((col, year_label, sc))

    amount_cols.sort(key=lambda x: -x[2])
    return label_col, amount_cols


def smart_clean(df: pd.DataFrame, amount_col: str = None):
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

    if amount_col is None:
        best_col, best_score = None, -1.0
        for col in df.columns:
            sc = score_amount_column(df[col])
            if sc > best_score:
                best_score, best_col = sc, col
        amount_col = best_col

    if amount_col is None or score_amount_column(df[amount_col]) <= 0:
        st.error("❌ Could not detect numeric amount column.")
        return None

    label_col, label_score = None, -1.0
    for col in df.columns:
        if col == amount_col:
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
        "Amount":    parse_amount(df[amount_col]),
    })

    result = result[result["Line Item"].apply(lambda x: not _is_meta_row(x))]
    result = result[~result["Line Item"].str.lower().str.contains(
        r"statement|note|\$\$|comprehensive income", regex=True
    )]
    result = result[~result["Line Item"].str.fullmatch(r"[\d\s.,\-()%\[\]]+")]
    return result.reset_index(drop=True)


def load_and_combine(files, amount_col_override: str = None):
    dfs = []
    for f in files:
        raw = read_any_file(f)
        if raw is not None:
            clean = smart_clean(raw, amount_col=amount_col_override)
            if clean is not None:
                dfs.append(clean)
    if not dfs:
        return None
    df = pd.concat(dfs, ignore_index=True)
    
    # 🔥 group ALL numeric columns
    value_cols = [c for c in df.columns if c != "Line Item"]
    
    df = df.groupby("Line Item", as_index=False)[value_cols].sum()
    return df


# =============================================================================
# P&L CLASSIFICATION
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
# P&L METRICS
# =============================================================================
def compute_pl(df: pd.DataFrame, addbacks: float = 0.0) -> dict:
    def s(cat):
        val = df.loc[df["Category"] == cat, "Amount"].sum()
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
    opex_adj = opex - addbacks
    ebit     = gp - opex_adj - da
    ebitda   = ebit + da
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
# BALANCE SHEET CLASSIFICATION  (HP / Finance Lease split)
# =============================================================================
def classify_bs(df: pd.DataFrame) -> pd.DataFrame:
    cats = []
    current_section = None

    for item in df["Line Item"].fillna("").astype(str):
        x = re.sub(r"[^a-z0-9\s]", " ", str(item).lower())
        x = re.sub(r"\s+", " ", x).strip()

        for trigger, section in BS_SECTION_TRIGGERS.items():
            if trigger in x:
                current_section = section
                break

        if any(kw in x for kw in BS_KEYWORDS["Ignore"]):
            cats.append("Ignore")
            continue

        cat = "Other"

        # Check detailed debt types first (more specific → first)
        for c in ["Debt - Hire Purchase", "Debt - Finance Lease", "Debt - Director Loan",
                  "Debt - Bank Loan", "Cash", "Receivables", "Inventory",
                  "Fixed Assets", "Payables", "Equity"]:
            if any(k in x for k in BS_KEYWORDS[c]):
                cat = c
                break

        if cat == "Other" and current_section is not None:
            cat = current_section

        cats.append(cat)

    df = df.copy()
    df["Category"] = cats
    return df


def get_total_debt_bs(df_bs: pd.DataFrame) -> float:
    """Sum all debt sub-categories."""
    debt_cats = ["Debt - Bank Loan", "Debt - Hire Purchase",
                 "Debt - Finance Lease", "Debt - Director Loan"]
    return df_bs.loc[df_bs["Category"].isin(debt_cats), "Amount"].sum()


# =============================================================================
# LBO ENGINE  (mezzanine + earnout + mgmt pool + waterfall)
# =============================================================================
def run_lbo(metrics: dict, cash_bs: float, debt_bs: float, params: dict):
    ebitda  = metrics["EBITDA"]
    revenue = metrics["Revenue"]

    entry_ev = ebitda * params["entry_multiple"]

    # SME leverage haircut
    size    = revenue
    margin  = params["margins"][0]
    haircut = 1.0
    if size   < 1_000_000: haircut -= 0.15
    if margin < 0.20:      haircut -= 0.10
    haircut = max(0.5, haircut)
    effective_leverage = params["leverage_pct"] * haircut

    # Senior debt sizing
    target_senior_debt = entry_ev * effective_leverage
    net_debt_bs = debt_bs - cash_bs

    # Transaction costs
    txn_cost_pct = params.get("transaction_cost_pct", 0.0)
    txn_costs    = entry_ev * txn_cost_pct

    # Mezzanine / PIK tranche
    use_mezz    = params.get("use_mezz", False)
    mezz_amount = params.get("mezz_amount", 0.0) if use_mezz else 0.0
    mezz_rate   = params.get("mezz_rate", 0.12)
    mezz_pik    = params.get("mezz_pik", False)   # True = PIK (no cash interest)

    total_debt_at_entry = target_senior_debt + mezz_amount

    # Equity rollover
    if params.get("use_equity_rollover"):
        equity_pct        = params.get("equity_pct", 0.0)
        sponsor_ownership = 1.0 - equity_pct
    else:
        equity_pct        = 0.0
        sponsor_ownership = 1.0

    # Management equity pool
    mgmt_pool_pct = params.get("mgmt_pool_pct", 0.0) if params.get("use_mgmt_pool") else 0.0
    # Mgmt pool comes out of sponsor's share (dilutes sponsor at exit)
    sponsor_net_pct = sponsor_ownership * (1.0 - mgmt_pool_pct)

    # Equity check
    if params.get("use_payment_plan"):
        payment_schedule  = params.get("payment_schedule", [])
        earnout_schedule  = params.get("earnout_schedule", []) if params.get("use_earnout") else []
        base_equity_value = max(0.0, entry_ev - total_debt_at_entry - net_debt_bs)
        equity_rollover   = base_equity_value * equity_pct
        min_sponsor_equity = base_equity_value * sponsor_ownership + txn_costs
        equity_in = sum(payment_schedule) + txn_costs - equity_rollover
        equity_in = max(equity_in, min_sponsor_equity)
        total_debt_at_entry = max(0.0, entry_ev + txn_costs - equity_in - equity_rollover)
    else:
        earnout_schedule  = []
        base_equity_value = max(0.0, entry_ev - total_debt_at_entry - net_debt_bs)
        equity_rollover   = base_equity_value * equity_pct
        equity_in         = base_equity_value * sponsor_ownership + txn_costs

    # Split senior into TLB + revolver
    # Revolver is now a FIXED facility size; only drawn if needed
    revolver_facility = params.get("revolver_facility", min(total_debt_at_entry * 0.15, 500_000))
    tlb          = max(0.0, total_debt_at_entry - mezz_amount - revolver_facility)
    revolver     = 0.0                  # drawn balance (starts undrawn)
    tlb_original = tlb
    mezz_balance = mezz_amount

    if equity_in <= 0:
        st.warning("⚠️ Equity check is zero/negative — flooring at $1.")
        equity_in = 1.0

    # Model parameters
    cash           = float(params["min_cash"])
    use_margin     = params.get("use_override_margin", True)
    prev_nwc       = params.get("initial_nwc", revenue * params["nwc_pct"])
    debt_sweep_pct = params.get("debt_sweep_pct", 0.60)
    cash_cap_pct   = params.get("cash_cap_pct", 0.10)
    tlb_amort_pct  = params.get("tlb_amort_pct", 0.01)

    # Preferred return hurdle for waterfall
    hurdle_irr     = params.get("hurdle_irr", 0.08)

    rows = []

    for i in range(params["years"]):
        rev      = revenue * (1 + params["growth"]) ** (i + 1)
        margin_y = params["margins"][i] if use_margin else (ebitda / revenue if revenue else 0)
        ebitda_y = rev * margin_y
        da_y     = rev * params["da_pct"]
        ebit_lbo = ebitda_y - da_y

        # Interest: senior + revolver + mezz (cash only)
        interest_senior = tlb * params["tlb_rate"] + revolver * params["rev_rate"]
        interest_mezz   = 0.0 if mezz_pik else mezz_balance * mezz_rate
        interest_total  = interest_senior + interest_mezz

        # PIK: mezz compounds
        if mezz_pik:
            mezz_balance *= (1 + mezz_rate)

        ebt_lbo = ebit_lbo - interest_total
        tax     = max(0.0, ebt_lbo * params["tax_rate"])

        nwc       = rev * params["nwc_pct"]
        delta_nwc = 0.0 if i == 0 else nwc - prev_nwc
        prev_nwc  = nwc
        capex     = rev * params["capex_pct"]

        fcf = (ebit_lbo - tax) + da_y - capex - delta_nwc

        # Seller cash payment
        payment = 0.0
        if params.get("use_payment_plan"):
            sched  = params.get("payment_schedule", [])
            payment = sched[i] if i < len(sched) else 0.0

        # Earnout — only paid if EBITDA hurdle met
        earnout_paid = 0.0
        if params.get("use_earnout") and i < len(earnout_schedule):
            eo = earnout_schedule[i]
            ebitda_hurdle = eo.get("ebitda_hurdle", 0.0)
            if ebitda_y >= ebitda_hurdle:
                earnout_paid = eo.get("amount", 0.0)

        fcf_after = fcf - payment - earnout_paid
        cash += fcf_after

        # Mandatory TLB amortisation
        mandatory_amort = min(tlb, tlb_original * tlb_amort_pct)
        tlb  -= mandatory_amort
        cash -= mandatory_amort

        # Revolver draw if cash < min
        if cash < params["min_cash"]:
            draw      = min(params["min_cash"] - cash, revolver_facility - revolver)
            revolver += draw
            cash     += draw

        # FCF sweep — revolver first, TLB second
        excess   = max(0.0, cash - params["min_cash"])
        sweep    = excess * debt_sweep_pct

        pay_rev  = min(revolver, sweep)
        revolver -= pay_rev
        cash     -= pay_rev
        sweep    -= pay_rev

        pay_tlb  = min(tlb, sweep)
        tlb     -= pay_tlb
        cash    -= pay_tlb
        sweep   -= pay_tlb

        # Remaining sweep goes to mezz (if not PIK)
        pay_mezz = 0.0
        if not mezz_pik:
            pay_mezz   = min(mezz_balance, sweep)
            mezz_balance -= pay_mezz
            cash       -= pay_mezz

        # Cap cash
        max_cash = cash_cap_pct * rev
        cash     = min(cash, max_cash)

        rows.append({
            "Year":            i + 1,
            "Revenue":         rev,
            "EBITDA":          ebitda_y,
            "EBITDA Margin":   ebitda_y / rev if rev else 0,
            "Interest":        interest_total,
            "Tax":             tax,
            "CapEx":           capex,
            "ΔNWC":            delta_nwc,
            "FCF":             fcf,
            "Payment":         payment,
            "Earnout Paid":    earnout_paid,
            "Mandatory Amort": mandatory_amort,
            "TLB Repaid":      pay_tlb,
            "Rev Repaid":      pay_rev,
            "Mezz Repaid":     pay_mezz,
            "TLB":             tlb,
            "Revolver":        revolver,
            "Mezz Balance":    mezz_balance,
            "Cash":            cash,
            "Net Debt":        tlb + revolver + mezz_balance - cash,
        })

    lbo_df = pd.DataFrame(rows)
    last   = lbo_df.iloc[-1]

    exit_ev       = last["EBITDA"] * params["exit_multiple"]
    exit_net_debt = last["TLB"] + last["Revolver"] + last["Mezz Balance"] - last["Cash"]
    gross_equity  = exit_ev - exit_net_debt

    # Waterfall
    # 1. Return of capital to sponsor
    # 2. Preferred return (hurdle_irr on equity_in for each year held)
    years_held = params["years"]
    preferred_return = equity_in * ((1 + hurdle_irr) ** years_held - 1)

    # Sponsor gets capital + pref first, remainder split by ownership after mgmt dilution
    sponsor_gross = gross_equity * sponsor_ownership
    sponsor_after_pref = max(0.0, sponsor_gross - equity_in - preferred_return)

    # Management pool participates in upside above hurdle
    mgmt_proceeds  = sponsor_after_pref * mgmt_pool_pct if params.get("use_mgmt_pool") else 0.0
    sponsor_exit   = sponsor_gross - mgmt_proceeds

    # Seller rollover proceeds
    seller_exit    = gross_equity * equity_pct

    if sponsor_exit <= 0:
        return lbo_df, {
            "Entry EV": entry_ev, "Total Debt": total_debt_at_entry,
            "Equity In": equity_in, "Exit EV": exit_ev,
            "Exit Equity": sponsor_exit, "MOIC": 0.0, "IRR": 0.0,
            "total_loss": True, "txn_costs": txn_costs,
            "equity_rollover": equity_rollover,
            "sponsor_ownership": sponsor_ownership,
            "sponsor_net_pct": sponsor_net_pct,
        }

    moic = sponsor_exit / max(equity_in, 1.0)

    # IRR cashflows
    if params.get("use_payment_plan"):
        schedule  = params.get("payment_schedule", [])
        cashflows = [0.0] + [-p for p in schedule]
        cashflows[-1] += sponsor_exit
    else:
        cashflows = [-equity_in] + [0.0] * (params["years"] - 1) + [sponsor_exit]

    try:
        irr = compute_irr(cashflows)
    except Exception:
        irr = moic ** (1.0 / params["years"]) - 1.0

    return lbo_df, {
        "Entry EV":         entry_ev,
        "Total Debt":       total_debt_at_entry,
        "Senior Debt":      tlb_original + revolver_facility,
        "Mezz Amount":      mezz_amount,
        "Equity In":        equity_in,
        "Equity Rollover":  equity_rollover,
        "Txn Costs":        txn_costs,
        "Sponsor %":        sponsor_ownership,
        "Sponsor Net %":    sponsor_net_pct,
        "Mgmt Proceeds":    mgmt_proceeds,
        "Seller Exit":      seller_exit,
        "Preferred Return": preferred_return,
        "Exit EV":          exit_ev,
        "Exit Equity":      sponsor_exit,
        "Gross Equity":     gross_equity,
        "MOIC":             moic,
        "IRR":              irr,
        "total_loss":       False,
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
    if unit == "pct": return f"{x*100:.1f}%"
    if unit == "x":   return f"{x:.2f}x"
    return str(x)


FMT_LBO = {
    "Revenue":         "${:,.0f}",
    "EBITDA":          "${:,.0f}",
    "EBITDA Margin":   "{:.1%}",
    "Interest":        "${:,.0f}",
    "Tax":             "${:,.0f}",
    "CapEx":           "${:,.0f}",
    "ΔNWC":            "${:,.0f}",
    "FCF":             "${:,.0f}",
    "Payment":         "${:,.0f}",
    "Earnout Paid":    "${:,.0f}",
    "Mandatory Amort": "${:,.0f}",
    "TLB Repaid":      "${:,.0f}",
    "Mezz Balance":    "${:,.0f}",
    "TLB":             "${:,.0f}",
    "Revolver":        "${:,.0f}",
    "Cash":            "${:,.0f}",
    "Net Debt":        "${:,.0f}",
}


# =============================================================================
# CHARTS
# =============================================================================
def chart_debt_paydown(lbo_df: pd.DataFrame):
    if not PLOTLY: return
    fig = go.Figure()
    fig.add_trace(go.Bar(name="TLB",      x=lbo_df["Year"], y=lbo_df["TLB"],          marker_color="#1e3a5f"))
    fig.add_trace(go.Bar(name="Revolver", x=lbo_df["Year"], y=lbo_df["Revolver"],     marker_color="#3b82f6"))
    fig.add_trace(go.Bar(name="Mezz",     x=lbo_df["Year"], y=lbo_df["Mezz Balance"], marker_color="#7c3aed"))
    fig.add_trace(go.Scatter(name="Cash", x=lbo_df["Year"], y=lbo_df["Cash"],
                             mode="lines+markers", line_color="#16a34a"))
    fig.update_layout(
        barmode="stack", title="Debt Paydown & Cash Buildup",
        xaxis_title="Year", yaxis_title="$",
        legend=dict(orientation="h", yanchor="bottom", y=1.15, xanchor="right", x=1),
        height=380, margin=dict(l=0, r=0, t=80, b=20),
        plot_bgcolor="#f8fafc", paper_bgcolor="#ffffff",
    )
    st.plotly_chart(fig, use_container_width=True)


def chart_fcf_ebitda(lbo_df: pd.DataFrame):
    if not PLOTLY: return
    fig = go.Figure()
    fig.add_trace(go.Bar(name="EBITDA", x=lbo_df["Year"], y=lbo_df["EBITDA"], marker_color="#0ea5e9"))
    fig.add_trace(go.Bar(name="FCF",    x=lbo_df["Year"], y=lbo_df["FCF"],    marker_color="#16a34a"))
    if lbo_df["Earnout Paid"].sum() > 0:
        fig.add_trace(go.Bar(name="Earnout Out", x=lbo_df["Year"], y=lbo_df["Earnout Paid"], marker_color="#f59e0b"))
    fig.update_layout(
        barmode="group", title="EBITDA vs FCF",
        xaxis_title="Year", yaxis_title="$",
        height=350, margin=dict(l=0, r=0, t=80, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.15, xanchor="right", x=1),
        plot_bgcolor="#f8fafc", paper_bgcolor="#ffffff",
    )
    st.plotly_chart(fig, use_container_width=True)


def chart_waterfall(returns: dict):
    if not PLOTLY: return
    labels = ["Equity In", "Gross Exit Equity", "Preferred Return",
              "Mgmt Pool", "Sponsor Exit"]
    vals   = [
        -returns["Equity In"],
        returns["Gross Equity"] * returns.get("Sponsor %", 1.0),
        -returns.get("Preferred Return", 0),
        -returns.get("Mgmt Proceeds", 0),
        returns["Exit Equity"],
    ]
    colors = ["#dc2626", "#16a34a", "#f59e0b", "#7c3aed", "#0ea5e9"]
    fig = go.Figure(go.Bar(x=labels, y=vals, marker_color=colors))
    fig.update_layout(
        title="Equity Waterfall", yaxis_title="$",
        height=320, margin=dict(l=0, r=0, t=60, b=20),
        plot_bgcolor="#f8fafc", paper_bgcolor="#ffffff",
    )
    st.plotly_chart(fig, use_container_width=True)


# =============================================================================
# EXCEL EXPORT
# =============================================================================
def build_excel_export(pl_metrics, lbo_df, returns, sc_rows):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary = pd.DataFrame([
            {"Item": "Revenue",              "Value": pl_metrics["Revenue"]},
            {"Item": "Gross Profit",         "Value": pl_metrics["Gross Profit"]},
            {"Item": "EBITDA",               "Value": pl_metrics["EBITDA"]},
            {"Item": "Net Profit",           "Value": pl_metrics["Net Profit"]},
            {"Item": "Entry EV",             "Value": returns["Entry EV"]},
            {"Item": "Transaction Costs",    "Value": returns.get("Txn Costs", 0)},
            {"Item": "Equity Rollover",      "Value": returns.get("Equity Rollover", 0)},
            {"Item": "Mezz Amount",          "Value": returns.get("Mezz Amount", 0)},
            {"Item": "Sponsor Equity In",    "Value": returns["Equity In"]},
            {"Item": "Preferred Return",     "Value": returns.get("Preferred Return", 0)},
            {"Item": "Mgmt Pool Proceeds",   "Value": returns.get("Mgmt Proceeds", 0)},
            {"Item": "Exit EV",              "Value": returns["Exit EV"]},
            {"Item": "Gross Exit Equity",    "Value": returns.get("Gross Equity", 0)},
            {"Item": "Sponsor Exit Equity",  "Value": returns["Exit Equity"]},
            {"Item": "MOIC",                 "Value": returns.get("MOIC", 0)},
            {"Item": "IRR",                  "Value": returns.get("IRR", 0)},
        ])
        summary.to_excel(writer, sheet_name="Summary",   index=False)
        lbo_df.to_excel(writer,  sheet_name="LBO Model", index=False)
        if sc_rows:
            pd.DataFrame(sc_rows).to_excel(writer, sheet_name="Scenarios", index=False)
    buf.seek(0)
    return buf.read()


# =============================================================================
# SESSION STATE DEFAULTS
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
    "pl_year_col":  None,
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# =============================================================================
# SIDEBAR
# =============================================================================
st.sidebar.subheader("⚙️ Deal Parameters")

with st.sidebar.expander("📋 Deployment checklist", expanded=False):
    st.markdown("""
    **Required packages**
    ```
    streamlit pandas numpy
    pdfplumber          # digital PDF
    pdf2image pytesseract Pillow  # OCR
    plotly              # charts
    openpyxl            # Excel export
    anthropic           # AI classification
    scipy               # IRR Brent fallback
    ```
    **Secrets** — `.streamlit/secrets.toml`
    ```toml
    APP_PASSWORD = "yourpassword"
    ```
    Remove `check_password()` call if no gate needed.
    """)

with st.sidebar.expander("🤖 AI Classification (optional)"):
    st.caption("Paste Anthropic API key for AI P&L classification.")
    api_key = st.text_input("Anthropic API Key", type="password")
    use_ai  = st.checkbox("Enable AI classification", value=bool(api_key))

with st.sidebar.expander("🧹 EBITDA Add-backs"):
    addback_salary   = st.number_input("Excess owner salary ($)",     0, step=10_000)
    addback_oneoff   = st.number_input("One-off / non-recurring ($)", 0, step=10_000)
    addback_personal = st.number_input("Personal expenses ($)",       0, step=5_000)

total_addbacks = float(addback_salary + addback_oneoff + addback_personal)

# Sector
st.sidebar.subheader("🏭 Sector")
sector = st.sidebar.selectbox("Company sector", SECTORS)
_, _, _, sector_note = SECTOR_CAL.get(sector, SECTOR_CAL["Other"])
if sector_note:
    st.sidebar.caption(f"ℹ️ {sector_note}")

if st.session_state.calibrated:
    st.sidebar.success("✅ Parameters auto-calibrated")
    if st.sidebar.button("🔄 Reset to defaults"):
        for k, v in _defaults.items():
            st.session_state[k] = v
        st.rerun()

# Valuation
st.sidebar.subheader("Valuation")
entry_multiple = st.sidebar.number_input(
    "Entry EV/EBITDA", 2.0, 20.0, value=float(st.session_state.cal_entry), step=0.5)
exit_multiple = st.sidebar.number_input(
    "Exit EV/EBITDA",  2.0, 20.0, value=float(st.session_state.cal_exit),  step=0.5)

# Holding period & growth
st.sidebar.subheader("Holding Period & Growth")
years  = st.sidebar.slider("Holding Period (years)", 1, 7, 5)
growth = st.sidebar.slider("Revenue Growth % p.a.", 0, 40,
                            value=int(st.session_state.cal_growth)) / 100

margin_mode = st.sidebar.radio("EBITDA Margin Input", ["Flat", "Per Year"], horizontal=True)
if margin_mode == "Flat":
    flat_m  = st.sidebar.slider("EBITDA Margin %", 0, 60,
                                 value=int(st.session_state.cal_margin)) / 100
    margins = [flat_m] * years
else:
    margins = [st.sidebar.slider(f"Y{i+1} EBITDA Margin %", 0, 60, 20 + i) / 100
               for i in range(years)]

# Payment structure
st.sidebar.subheader("💰 Payment Structure")
use_payment_plan = st.sidebar.checkbox("Enable staged payments (vendor finance)")

payment_schedule = []
if use_payment_plan:
    st.sidebar.caption("Year 1 = first payment after closing.")
    for i in range(years):
        default_val = 0
        if st.session_state.calibrated and "cal_payment" in st.session_state:
            cp = st.session_state.cal_payment
            if i < len(cp):
                default_val = int(round(cp[i]))
        val = st.sidebar.number_input(f"Year {i+1} payment ($)", 0,
                                       value=default_val, step=10_000, key=f"pay_{i}")
        payment_schedule.append(float(val))
    if any(p > 0 for p in payment_schedule):
        st.sidebar.caption(f"Total staged payments: {fmt(sum(payment_schedule))}")

# Earnout
with st.sidebar.expander("📈 Earnout (conditional payments)"):
    use_earnout = st.checkbox("Enable earnout payments", key="use_earnout_cb")
    earnout_schedule = []
    if use_earnout:
        st.caption("Payments only made if EBITDA target is met in that year.")
        for i in range(years):
            st.markdown(f"**Year {i+1}**")
            eo_amt     = st.number_input(f"Earnout amount Y{i+1} ($)",   0, step=10_000, key=f"eo_amt_{i}")
            eo_hurdle  = st.number_input(f"EBITDA hurdle Y{i+1} ($)",    0, step=10_000, key=f"eo_hrd_{i}")
            earnout_schedule.append({"amount": float(eo_amt), "ebitda_hurdle": float(eo_hurdle)})

# Equity structure
st.sidebar.subheader("📈 Equity Structure")
use_equity_rollover = st.sidebar.checkbox("Seller equity rollover")
equity_pct = 0.0
if use_equity_rollover:
    equity_pct = st.sidebar.slider("Seller Rollover %", 0, 50, 20) / 100
    st.sidebar.caption(f"Seller {equity_pct:.0%} → Sponsor {1-equity_pct:.0%}")

use_mgmt_pool = st.sidebar.checkbox("Management equity pool")
mgmt_pool_pct = 0.0
if use_mgmt_pool:
    mgmt_pool_pct = st.sidebar.slider("Mgmt pool % of sponsor upside", 0, 30, 10) / 100
    st.sidebar.caption(
        f"Mgmt receives {mgmt_pool_pct:.0%} of sponsor proceeds above hurdle IRR."
    )
hurdle_irr = st.sidebar.slider("Preferred return hurdle %", 0, 30, 8) / 100

# Capital structure
st.sidebar.subheader("Capital Structure")
leverage_pct = st.sidebar.slider(
    "Senior leverage % of Entry EV", 0, 100,
    value=int(st.session_state.cal_leverage)) / 100
tlb_rate = st.sidebar.slider("TLB Interest Rate %", 0, 20, 7) / 100
rev_rate = st.sidebar.slider("Revolver Rate %",     0, 20, 6) / 100
revolver_facility = st.sidebar.number_input(
    "Revolver facility size ($)", 0, value=500_000, step=50_000,
    help="Fixed committed revolver. Drawn only if cash falls below minimum.")

# Mezzanine
with st.sidebar.expander("🏦 Mezzanine / PIK (optional)"):
    use_mezz = st.checkbox("Add mezzanine tranche")
    mezz_amount = 0.0
    mezz_rate   = 0.12
    mezz_pik    = False
    if use_mezz:
        mezz_amount = st.number_input("Mezzanine amount ($)", 0, step=50_000, value=0)
        mezz_rate   = st.slider("Mezz rate %", 0, 25, 12) / 100
        mezz_pik    = st.checkbox("PIK (interest rolls up, no cash payment)")
        if mezz_pik:
            st.caption("⚠️ PIK compounds annually — increases exit debt.")

# Other assumptions
st.sidebar.subheader("Other Assumptions")
tax_rate  = st.sidebar.slider("Tax Rate %",       0, 35, 17) / 100
da_pct    = st.sidebar.slider("D&A % Revenue",    0, 15,  3) / 100
nwc_pct   = st.sidebar.slider("NWC % Revenue",    0, 20,
                                value=int(st.session_state.cal_nwc)) / 100
capex_pct = st.sidebar.slider("CapEx % Revenue",  0, 20,
                                value=int(st.session_state.cal_capex)) / 100
min_cash  = st.sidebar.number_input("Minimum Cash ($)", 0, value=50_000, step=10_000)

with st.sidebar.expander("⚙️ Advanced Mechanics"):
    debt_sweep_pct = st.slider("FCF Debt Sweep %",       0, 100, 60) / 100
    cash_cap_pct   = st.slider("Cash Cap % of Revenue",  0, 30,  10) / 100
    tlb_amort_pct  = st.slider("TLB Mandatory Amort % p.a.", 0, 10, 1) / 100
    txn_cost_pct   = st.slider("Transaction Costs % of EV",  0, 10, 3) / 100

# Assemble params
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
    use_override_margin=True,
    debt_sweep_pct=debt_sweep_pct,
    cash_cap_pct=cash_cap_pct,
    tlb_amort_pct=tlb_amort_pct,
    transaction_cost_pct=txn_cost_pct,
    revolver_facility=float(revolver_facility),
    use_mezz=use_mezz,
    mezz_amount=float(mezz_amount) if use_mezz else 0.0,
    mezz_rate=mezz_rate,
    mezz_pik=mezz_pik,
    use_payment_plan=use_payment_plan,
    use_equity_rollover=use_equity_rollover,
    equity_pct=equity_pct,
    use_mgmt_pool=use_mgmt_pool,
    mgmt_pool_pct=mgmt_pool_pct,
    hurdle_irr=hurdle_irr,
    use_earnout=use_earnout if use_payment_plan else False,
)

if use_payment_plan:
    params["payment_schedule"] = payment_schedule
if use_earnout and use_payment_plan:
    params["earnout_schedule"] = earnout_schedule


# =============================================================================
# MAIN PAGE
# =============================================================================
st.title("📊 SME Valuation & LBO Tool")
st.caption(
    "Upload P&L and (optionally) Balance Sheet to generate a full LBO valuation. "
    "Supports xlsx, xls, csv, digital PDF, and scanned PDF (OCR). "
    "Multi-year P&L — select which year to model. "
    "Buyout structures: cash · debt · seller rollover · earnout · mezzanine · management pool."
)

with st.expander("📚 Singapore SME Comparable Transaction Multiples"):
    st.dataframe(COMPS_TABLE, use_container_width=True, hide_index=True)

st.markdown("---")

# =============================================================================
# STEP 1 — UPLOAD
# =============================================================================
st.header("📂 Step 1 — Upload Financials")
col_pl, col_bs = st.columns(2)
with col_pl:
    pl_files = st.file_uploader(
        "P&L Statement(s)", type=["xlsx", "xls", "csv", "pdf"],
        accept_multiple_files=True,
    )
with col_bs:
    bs_files = st.file_uploader(
        "Balance Sheet(s) (optional)", type=["xlsx", "xls", "csv", "pdf"],
        accept_multiple_files=True,
    )

pl_metrics:     dict  | None = None
bs_derived_nwc: float | None = None
cash_bs = debt_bs = 0.0
sc_rows = []

# =============================================================================
# MULTI-YEAR P&L COLUMN SELECTOR
# =============================================================================
if pl_files:
    # Peek at first file for column detection
    first_file = pl_files[0]
    first_file.seek(0)
    raw_peek = read_any_file(first_file)
    first_file.seek(0)

    amount_col_to_use = None

    if raw_peek is not None and raw_peek.shape[1] > 2:
        peek_clean = raw_peek.dropna(how="all").fillna("").astype(str)
        peek_clean = dedupe_columns(peek_clean)
        peek_clean.columns = [f"c{i}" for i in range(len(peek_clean.columns))]
        label_col_peek, year_cols = detect_year_columns(peek_clean)

        if len(year_cols) > 1:
            st.info(
                f"📅 **Multi-year P&L detected** — found {len(year_cols)} amount columns. "
                "Select which year to model."
            )
            year_labels = [label for _, label, _ in year_cols]
            chosen_label = st.selectbox("Select year / column to model", year_labels)
            for col, label, _ in year_cols:
                if label == chosen_label:
                    amount_col_to_use = col
                    st.session_state["pl_year_col"] = col
                    break
        else:
            st.session_state["pl_year_col"] = None

    # Now load with chosen column
    for f in pl_files:
        f.seek(0)
    
    df_raw = load_and_combine(pl_files, amount_col_override=st.session_state.get("pl_year_col"))
    
    if df_raw is None:
        st.error("P&L could not be parsed.")
    else:
        # 🔥 NEW: handle multi-column data BEFORE classification
        if df_raw.shape[1] > 2:
            selected_col = st.selectbox(
                "📅 Select year / column to model",
                options=df_raw.columns[1:]
            )
    
            df_pl = pd.DataFrame({
                "Line Item": df_raw["Line Item"],
                "Amount": df_raw[selected_col]
            })
        else:
            df_pl = df_raw.rename(columns={
                df_raw.columns[0]: "Line Item",
                df_raw.columns[1]: "Amount"
            })
    
        # ✅ Now classify AFTER fixing structure
        df_pl = classify_pl(df_pl, use_ai=use_ai, api_key=api_key or "")

    st.markdown("---")
    st.header("📋 Step 2 — Review & Correct P&L Classifications")

    if total_addbacks > 0:
        st.info(f"🧹 EBITDA normalisation: {fmt(total_addbacks)} of add-backs applied.")

    unknown_rows  = df_pl[df_pl["Category"] == "Unknown"]
    unknown_rows  = unknown_rows[unknown_rows["Amount"] != 0]
    if len(unknown_rows):
        st.warning(f"⚠️ {len(unknown_rows)} row(s) unclassified. Fix below or enable AI.")

    df_display = df_pl.copy()
    df_display["Amount"] = pd.to_numeric(df_display["Amount"], errors="coerce")
    mask = df_display["Category"] == "Tax"
    df_display.loc[mask, "Amount"] = df_display.loc[mask, "Amount"].abs()

    df_edited = st.data_editor(
        df_display,
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "Category": st.column_config.SelectboxColumn(
                "Category",
                options=["Revenue", "COGS", "OpEx", "D&A", "Interest",
                         "Other Income", "Tax", "Ignore"],
            )
        },
    )

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
if bs_files:
    df_bs_raw = load_and_combine(bs_files)

    if df_bs_raw is None:
        st.error("Balance Sheet could not be parsed.")
    else:
        df_bs_raw = classify_bs(df_bs_raw)

    st.markdown("---")
    st.subheader("🏦 Balance Sheet — Review Classifications")
    st.caption(
        "Debt is split into Bank Loan / Hire Purchase / Finance Lease / Director Loan. "
        "All sub-types flow into total debt for the CFDF bridge."
    )

    df_bs = st.data_editor(
        df_bs_raw,
        column_config={
            "Category": st.column_config.SelectboxColumn(
                "Category", options=BS_CATEGORIES),
            "Amount": st.column_config.NumberColumn("Amount", format="$ %.0f"),
        },
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
    )

    cash_bs     = df_bs.loc[df_bs["Category"] == "Cash",        "Amount"].sum()
    debt_bs     = get_total_debt_bs(df_bs)
    receivables = df_bs.loc[df_bs["Category"] == "Receivables", "Amount"].sum()
    payables    = df_bs.loc[df_bs["Category"] == "Payables",    "Amount"].sum()
    inventory   = df_bs.loc[df_bs["Category"] == "Inventory",   "Amount"].sum()
    bs_derived_nwc = receivables + inventory - payables

    # Show debt breakdown
    debt_cats = ["Debt - Bank Loan", "Debt - Hire Purchase",
                 "Debt - Finance Lease", "Debt - Director Loan"]
    debt_breakdown = {
        c.replace("Debt - ", ""): df_bs.loc[df_bs["Category"] == c, "Amount"].sum()
        for c in debt_cats
    }
    debt_breakdown = {k: v for k, v in debt_breakdown.items() if v != 0}

    if debt_breakdown:
        b1, b2, b3, b4 = st.columns(4)
        cols = [b1, b2, b3, b4]
        for idx, (k, v) in enumerate(debt_breakdown.items()):
            cols[idx % 4].metric(k, fmt(v))

    if receivables == 0:
        st.warning("⚠️ No receivables detected. Check if assets page is present.")


# =============================================================================
# AUTO-CALIBRATE
# =============================================================================
if pl_metrics and pl_metrics.get("EBITDA", 0) > 0:
    cal = auto_calibrate(pl_metrics, cash_bs, debt_bs, sector=sector)

    st.markdown("---")
    col_cal, col_info = st.columns([1, 2])
    with col_cal:
        if st.button("🎯 Auto-calibrate parameters", type="primary"):
            st.session_state.calibrated   = True
            st.session_state.cal_entry    = cal["entry_multiple"]
            st.session_state.cal_exit     = cal["exit_multiple"]
            st.session_state.cal_growth   = int(cal["growth"] * 100)
            st.session_state.cal_margin   = int(cal["target_margin"] * 100)
            st.session_state.cal_payment  = cal["payment_schedule"]
            st.session_state.cal_leverage = int(cal["leverage_pct"] * 100)
            st.session_state.cal_capex    = int(cal["capex_pct"] * 100)
            st.session_state.cal_nwc      = int(cal["nwc_pct"] * 100)
            st.rerun()

    with col_info:
        r = cal["rationale"]
        with st.expander("ℹ️ Calibration rationale"):
            st.markdown(
                f"- **Sector:** {r['sector']}\n"
                f"- **Revenue tier:** {r['revenue_tier']}\n"
                f"- **Margin quality:** {r['margin_quality']}\n"
                f"- **Current leverage (BS):** {r['leverage_ratio']}\n"
                f"- **Suggested entry:** {r['suggested_entry']} | "
                f"**exit:** {r['suggested_exit']}"
            )


# =============================================================================
# VALUATION OUTPUT
# =============================================================================
if pl_metrics:
    st.markdown("---")
    st.header("📊 Step 3 — Valuation Output")
    m = pl_metrics

    if m["Add-backs"] > 0:
        st.success(
            f"📈 Normalised EBITDA: **{fmt(m['EBITDA'])}** "
            f"({fmt(m['EBITDA Margin'], 'pct')} margin) — includes {fmt(m['Add-backs'])} add-backs."
        )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Revenue",      fmt(m["Revenue"]))
    c2.metric("Gross Profit", fmt(m["Gross Profit"]),  fmt(m["GP Margin"],     "pct"))
    c3.metric("EBITDA",       fmt(m["EBITDA"]),        fmt(m["EBITDA Margin"], "pct"))
    c4.metric("EBIT",         fmt(m["EBIT"]),          fmt(m["EBIT Margin"],   "pct"))
    c5.metric("Net Profit",   fmt(m["Net Profit"]),    fmt(m["Net Margin"],    "pct"))

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
            ("= EBIT",                     m["EBIT"]),
            ("(+) D&A",                    m["D&A"]),
            ("= EBITDA",                   m["EBITDA"]),
            ("─" * 30,                     None),
            ("EBIT",                       m["EBIT"]),
            ("(+) Other Income",           m["Other Income"]),
            ("(−) Interest",              -m["Interest"]),
            ("= EBT",                      m["EBT"]),
            ("(−) Tax",                   -m["Tax"]),
            ("= Net Profit",               m["Net Profit"]),
        ]
        pl_bridge = pd.DataFrame(
            [(r, fmt(v) if v is not None else "────") for r, v in bridge_rows],
            columns=["Item", "Amount"],
        )
        st.dataframe(pl_bridge, use_container_width=True, hide_index=True)

    if bs_files:
        st.subheader("Balance Sheet Snapshot")
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Cash",        fmt(cash_bs))
        b2.metric("Total Debt",  fmt(debt_bs))
        b3.metric("Net Debt",    fmt(debt_bs - cash_bs))
        if bs_derived_nwc is not None:
            b4.metric("Working Capital", fmt(bs_derived_nwc))

    st.markdown("---")

    if m["EBITDA"] <= 0:
        st.error(
            "⚠️ EBITDA ≤ 0 — LBO cannot run. "
            "Check classifications in Step 2, or add owner add-backs in the sidebar."
        )
        st.stop()

    lbo_params = {
        **params,
        **({"initial_nwc": bs_derived_nwc} if bs_derived_nwc is not None else {}),
    }

    # ── Scenario Analysis ────────────────────────────────────────────────────
    st.subheader("📐 Scenario Analysis — Bear / Base / Bull")
    scenarios = build_scenarios(pl_metrics, cash_bs, debt_bs, lbo_params)
    sc_rows   = []

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

    st.dataframe(pd.DataFrame(sc_rows), use_container_width=True, hide_index=True)

    # ── Current Parameters Returns ───────────────────────────────────────────
    lbo_df, returns = run_lbo(pl_metrics, cash_bs, debt_bs, lbo_params)

    st.subheader("📈 Current Parameters — Returns")

    if returns.get("total_loss"):
        st.error(
            "⚠️ **Total loss** at current parameters. "
            "Lower entry multiple, reduce leverage, or raise exit multiple."
        )
        r1, r2 = st.columns(2)
        r1.metric("Entry EV", fmt(returns["Entry EV"]))
        r2.metric("Exit EV",  fmt(returns["Exit EV"]))
    else:
        r1, r2, r3, r4, r5 = st.columns(5)
        r1.metric("Entry EV",          fmt(returns["Entry EV"]))
        r2.metric("Sponsor Equity In", fmt(returns["Equity In"]))
        r3.metric("Exit EV",           fmt(returns["Exit EV"]))
        r4.metric("MOIC",              fmt(returns["MOIC"], "x"))

        irr_val = returns["IRR"]
        irr_str = fmt(irr_val, "pct")
        r5.metric("IRR", irr_str)

        if irr_val >= 0.25:
            st.success(f"✅ IRR {irr_str} — exceeds PE hurdle (20–25%). Strong deal.")
        elif irr_val >= 0.15:
            st.warning(f"⚠️ IRR {irr_str} — borderline. Typical threshold is 20–25%.")
        else:
            st.error(f"❌ IRR {irr_str} — below PE hurdle rate.")

        # ── Funding Stack ─────────────────────────────────────────────────────
        st.subheader("💰 Funding Stack at Entry")
        f1, f2, f3, f4, f5 = st.columns(5)
        f1.metric("Sponsor Cash",    fmt(returns["Equity In"]))
        f2.metric("Seller Rollover", fmt(returns.get("Equity Rollover", 0)))
        f3.metric("Senior Debt",     fmt(returns.get("Senior Debt", returns["Total Debt"])))
        f4.metric("Mezzanine",       fmt(returns.get("Mezz Amount", 0)))
        f5.metric("Txn Costs",       fmt(returns.get("Txn Costs", 0)))

        total_sources = (returns["Equity In"] + returns.get("Equity Rollover", 0)
                         + returns["Total Debt"])
        if total_sources > 0:
            st.caption(
                f"Mix: {returns['Equity In']/total_sources:.0%} sponsor  |  "
                f"{returns.get('Equity Rollover',0)/total_sources:.0%} seller rollover  |  "
                f"{returns['Total Debt']/total_sources:.0%} debt  "
                f"(sponsor ownership: {returns.get('Sponsor %', 1):.0%})"
            )

        # ── Equity Waterfall ──────────────────────────────────────────────────
        if params.get("use_mgmt_pool") or params.get("use_equity_rollover"):
            st.subheader("🏦 Exit Equity Waterfall")
            w1, w2, w3, w4, w5 = st.columns(5)
            w1.metric("Gross Exit Equity",  fmt(returns.get("Gross Equity", 0)))
            w2.metric("Preferred Return",   fmt(returns.get("Preferred Return", 0)))
            w3.metric("Mgmt Pool",          fmt(returns.get("Mgmt Proceeds", 0)))
            w4.metric("Sponsor Proceeds",   fmt(returns["Exit Equity"]))
            w5.metric("Seller Rollover $",  fmt(returns.get("Seller Exit", 0)))
            chart_waterfall(returns)

        # ── Deleveraging ──────────────────────────────────────────────────────
        st.subheader("📉 Deleveraging")
        initial_debt = returns["Total Debt"]
        final_debt   = lbo_df.iloc[-1]["TLB"] + lbo_df.iloc[-1]["Revolver"] + lbo_df.iloc[-1]["Mezz Balance"]
        d1, d2, d3 = st.columns(3)
        d1.metric("Initial Debt",  fmt(initial_debt))
        d2.metric("Debt Repaid",   fmt(initial_debt - final_debt))
        d3.metric("Debt at Exit",  fmt(final_debt))

        # ── Charts ────────────────────────────────────────────────────────────
        if PLOTLY:
            ch1, ch2 = st.columns(2)
            with ch1: chart_debt_paydown(lbo_df)
            with ch2: chart_fcf_ebitda(lbo_df)
        else:
            st.info("Install plotly for charts.")

        # ── Sensitivity grids ─────────────────────────────────────────────────
        entry_steps = sorted({round(entry_multiple + d, 1) for d in (-1.0, -0.5, 0, +0.5, +1.0)})
        exit_steps  = sorted({round(exit_multiple  + d, 1) for d in (-1.0, -0.5, 0, +0.5, +1.0)})
        lev_steps   = sorted({round(leverage_pct   + d, 2) for d in (-0.15, -0.075, 0, +0.075, +0.15)
                               if 0.10 <= round(leverage_pct + d, 2) <= 0.80})

        tab1, tab2, tab3 = st.tabs(["MOIC Sensitivity", "IRR Sensitivity", "Leverage Sensitivity"])

        def _run_grid(em, xm, lp=None):
            p = {**lbo_params, "entry_multiple": em, "exit_multiple": xm}
            if lp is not None:
                p["leverage_pct"] = lp
            _, ret2 = run_lbo(pl_metrics, cash_bs, debt_bs, p)
            return ret2

        with tab1:
            rows_moic = []
            for em in entry_steps:
                row = {"Entry \\ Exit": f"{em:.1f}x"}
                for xm in exit_steps:
                    if xm <= em:
                        row[f"{xm:.1f}x"] = "—"
                    else:
                        ret2 = _run_grid(em, xm)
                        row[f"{xm:.1f}x"] = "Loss" if ret2["total_loss"] else f"{ret2['MOIC']:.2f}x"
                rows_moic.append(row)
            df_moic = pd.DataFrame(rows_moic).set_index("Entry \\ Exit")
            def hl_base(row):
                return ["background-color: #16a34a; color: white;"
                        if (row.name == f"{entry_multiple:.1f}x" and col == f"{exit_multiple:.1f}x") else ""
                        for col in df_moic.columns]
            st.dataframe(df_moic.style.apply(hl_base, axis=1), use_container_width=True)

        with tab2:
            rows_irr = []
            for em in entry_steps:
                row = {"Entry \\ Exit": f"{em:.1f}x"}
                for xm in exit_steps:
                    if xm <= em:
                        row[f"{xm:.1f}x"] = "—"
                    else:
                        ret2 = _run_grid(em, xm)
                        row[f"{xm:.1f}x"] = "Loss" if ret2["total_loss"] else fmt(ret2["IRR"], "pct")
                rows_irr.append(row)
            st.dataframe(pd.DataFrame(rows_irr).set_index("Entry \\ Exit"), use_container_width=True)

        with tab3:
            st.caption("MOIC at current entry/exit multiple × varying leverage levels")
            rows_lev = []
            for em in entry_steps:
                row = {"Entry \\ Leverage": f"{em:.1f}x"}
                for lp in lev_steps:
                    ret2 = _run_grid(em, exit_multiple, lp=lp)
                    row[f"{lp:.0%}"] = "Loss" if ret2["total_loss"] else f"{ret2['MOIC']:.2f}x"
                rows_lev.append(row)
            st.dataframe(pd.DataFrame(rows_lev).set_index("Entry \\ Leverage"), use_container_width=True)

    # ── LBO Model Table ───────────────────────────────────────────────────────
    st.subheader("📋 LBO Model — Annual Detail")
    st.dataframe(
        lbo_df.style.format(FMT_LBO),
        use_container_width=True,
        hide_index=True,
    )

    # ── Valuation Bridge ──────────────────────────────────────────────────────
    with st.expander("🏗️ Valuation Bridge"):
        if not returns.get("total_loss"):
            net_debt_bs  = debt_bs - cash_bs
            gross_exit_e = returns.get("Gross Equity", returns["Exit Equity"])
            bridge_items = [
                ("Entry EV (EBITDA × multiple)",         fmt(returns["Entry EV"])),
                ("  (−) Senior LBO Debt",                fmt(returns.get("Senior Debt", returns["Total Debt"]))),
                ("  (−) Mezzanine",                      fmt(returns.get("Mezz Amount", 0))),
                ("  (−) Net BS Debt (CFDF basis)",        fmt(net_debt_bs)),
                ("  = Net Equity Value",                  fmt(returns["Entry EV"] - returns["Total Debt"] - net_debt_bs)),
                ("  (−) Seller Equity Rollover",          fmt(returns.get("Equity Rollover", 0))),
                ("  (+) Transaction Costs",               fmt(returns.get("Txn Costs", 0))),
                ("= Sponsor Equity Check",                fmt(returns["Equity In"])),
                ("─" * 35,                               ""),
                ("Exit EV",                              fmt(returns["Exit EV"])),
                ("  (−) Exit Net Debt",                  fmt(returns["Exit EV"] - gross_exit_e)),
                ("  = Gross Exit Equity",                fmt(gross_exit_e)),
                ("  × Sponsor Ownership",                fmt(returns.get("Sponsor %", 1), "pct")),
                ("  = Sponsor Share of Exit",            fmt(gross_exit_e * returns.get("Sponsor %", 1))),
                ("  (−) Mgmt Pool Proceeds",             fmt(returns.get("Mgmt Proceeds", 0))),
                ("  (−) Preferred Return",               fmt(returns.get("Preferred Return", 0))),
                ("= Sponsor Net Exit Equity",            fmt(returns["Exit Equity"])),
                ("─" * 35,                               ""),
                ("MOIC",  fmt(returns["MOIC"], "x")),
                ("IRR",   fmt(returns["IRR"],  "pct")),
            ]
        else:
            bridge_items = [
                ("Entry EV", fmt(returns["Entry EV"])),
                ("Exit EV",  fmt(returns["Exit EV"])),
                ("Outcome",  "Total Loss"),
            ]
        st.dataframe(
            pd.DataFrame(bridge_items, columns=["Item", "Value"]),
            use_container_width=True, hide_index=True,
        )

    # ── Excel Export ───────────────────────────────────────────────────────────
    if OPENPYXL and not returns.get("total_loss"):
        xl_bytes = build_excel_export(pl_metrics, lbo_df, returns, sc_rows)
        st.download_button(
            label="⬇️ Download Full Model (Excel)",
            data=xl_bytes,
            file_name="sme_lbo_model.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    elif not OPENPYXL:
        st.caption("Install openpyxl for Excel export.")

elif not pl_files:
    st.info("👆 Upload a P&L statement to get started.")
    st.markdown("""
    **Supported buyout structures**
    - 🏦 LBO debt — TLB + committed revolver facility
    - 💰 Staged cash payments — vendor finance / deferred consideration
    - 📈 Earnout — conditional payments gated on annual EBITDA hurdles
    - 🔄 Seller equity rollover — seller retains % of NewCo
    - 🏦 Mezzanine / PIK — second debt tranche with PIK option
    - 👥 Management equity pool — dilutes sponsor upside above preferred return
    - Any combination of the above

    **What the model computes**
    - Normalised EBITDA with owner add-backs
    - Full LBO cashflow model (FCF, debt service, sweep, mandatory amort)
    - CFDF equity bridge with transaction costs
    - Exit waterfall (preferred return → sponsor → mgmt pool → seller rollover)
    - Bear / Base / Bull scenarios
    - MOIC × IRR sensitivity grids + new leverage sensitivity grid
    - Excel export with Summary, LBO Model, and Scenarios sheets
    """)
