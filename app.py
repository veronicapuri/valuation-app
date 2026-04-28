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
    from PIL import ImageFilter
    PDF_OCR = True
except ImportError:
    PDF_OCR = False

st.set_page_config(layout="wide", page_title="SME Valuation Tool", page_icon="📊")

# =========================================
# CONSTANTS
# =========================================
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
        "insurance", "admin", "general & admin", "office", "printing",
        "stationery", "postage", "courier", "freight", "shipping",
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
        "finance charge", "bank charge", "bank charges",
        "borrowing cost", "loan interest", "hire purchase interest",
    ],
    "Tax": ["income tax", "tax expense", "deferred tax", "zakat", "corporate tax"],
    "Ignore": [
        "total", "net profit", "gross profit", "ebitda", "subtotal",
        "pte", "ltd", "sdn bhd", "for the year", "as at", "nan", "none",
        "operating profit", "operating expenses",
        "profit before", "profit after", "loss before", "loss after",
        "cost of sales", "other income", "trading income",
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
        "trade and other receivables", "prepayment", "deposit paid",
        "advance paid", "amount owing from", "owing from",
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
        "payable", "creditor", "trade payable", "accrual",
        "trade and other payables", "other payable",
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

# Section triggers — checked on the RAW (not cleaned) label
BS_SECTION_TRIGGERS = {
    "bank":                "Cash",
    "current assets":      "Receivables",
    "fixed assets":        "Fixed Assets",
    "current liabilities": "Payables",
    "long term liabilit":  "Debt",
    "equity":              "Equity",
}


# =========================================
# SESSION STATE INITIALISATION
# (must run before any widget is rendered)
# =========================================
_DEFAULTS = dict(
    entry_multiple=5.0,
    exit_multiple=7.0,
    years=5,
    growth=10,
    flat_margin=20,
    leverage_pct=60,
    tlb_rate=7,
    rev_rate=6,
    tax_rate=17,
    da_pct=3,
    nwc_pct=5,
    capex_pct=5,
    min_cash=50_000,
    auto_rationale="",
)
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# =========================================
# AUTO-VALUATION ENGINE
# =========================================
def auto_value_sme(metrics: dict, cash_bs: float, debt_bs: float,
                   revenue_bs: float = 0) -> dict:
    """
    Recommend deal parameters based on the SME's financials.
    Returns a dict of suggested values + human-readable rationale lines.
    """
    rev    = metrics["Revenue"]
    ebitda = metrics["EBITDA"]
    margin = metrics["EBITDA Margin"]
    gp_m   = metrics["GP Margin"]
    net_m  = metrics["Net Margin"]

    reasons = []

    # ── Entry multiple: size + margin ────────────────────────────────────────
    # Singapore SME benchmark ranges (EV/EBITDA):
    # Micro (<$1M rev):   3–4x   │  Small ($1–5M):  4–6x
    # Mid ($5–20M):       5–8x   │  Larger (>$20M): 7–10x
    if rev < 1_000_000:
        base_entry = 3.5
        size_band  = "micro (<$1M revenue)"
    elif rev < 5_000_000:
        base_entry = 5.0
        size_band  = "small ($1–5M revenue)"
    elif rev < 20_000_000:
        base_entry = 6.5
        size_band  = "mid ($5–20M revenue)"
    else:
        base_entry = 8.0
        size_band  = "larger (>$20M revenue)"

    # Margin quality adjustments
    if margin >= 0.35:
        margin_adj = +1.5
        reasons.append(f"EBITDA margin {margin:.0%} is excellent → +1.5x entry multiple")
    elif margin >= 0.25:
        margin_adj = +0.5
        reasons.append(f"EBITDA margin {margin:.0%} is strong → +0.5x entry multiple")
    elif margin >= 0.15:
        margin_adj = 0.0
        reasons.append(f"EBITDA margin {margin:.0%} is typical for the size band")
    elif margin >= 0.08:
        margin_adj = -0.5
        reasons.append(f"EBITDA margin {margin:.0%} is thin → −0.5x entry multiple")
    else:
        margin_adj = -1.0
        reasons.append(f"EBITDA margin {margin:.0%} is very thin → −1.0x entry multiple")

    # Net profitability adjustment
    if net_m < 0:
        margin_adj -= 0.5
        reasons.append("Company is loss-making at net level → −0.5x")

    entry = round(max(3.0, min(12.0, base_entry + margin_adj)) * 2) / 2  # round to 0.5x
    reasons.insert(0, f"Entry multiple: {entry:.1f}x (base {base_entry:.1f}x for {size_band}, adj {margin_adj:+.1f}x)")

    # ── Exit multiple ─────────────────────────────────────────────────────────
    # Assume 1–2x expansion over 5 years for a cleaned-up SME
    exit_ = round(min(entry + 2.0, 12.0) * 2) / 2
    reasons.append(f"Exit multiple: {exit_:.1f}x (entry + 2.0x value creation assumption)")

    # ── Revenue growth ────────────────────────────────────────────────────────
    # Conservative for SME: 8–15% p.a. default
    if rev < 500_000:
        growth = 15          # small base, higher growth potential
    elif rev < 2_000_000:
        growth = 12
    elif rev < 10_000_000:
        growth = 10
    else:
        growth = 8
    reasons.append(f"Revenue growth: {growth}% p.a. (conservative for size band)")

    # ── Leverage ─────────────────────────────────────────────────────────────
    # SME LBOs are typically less leveraged than large-cap.
    # High margin + low existing debt → can sustain more leverage.
    net_debt = debt_bs - cash_bs
    net_debt_ebitda = net_debt / ebitda if ebitda > 0 else 0

    if net_debt_ebitda > 3.0:
        leverage = 40     # already heavily levered
        reasons.append(f"Leverage: {leverage}% — existing net debt is {net_debt_ebitda:.1f}x EBITDA, keeping light")
    elif margin >= 0.25 and net_debt_ebitda < 1.0:
        leverage = 55
        reasons.append(f"Leverage: {leverage}% — strong margin + clean BS supports moderate leverage")
    elif margin >= 0.15:
        leverage = 50
        reasons.append(f"Leverage: {leverage}% — typical for SME with this margin profile")
    else:
        leverage = 40
        reasons.append(f"Leverage: {leverage}% — conservative given thin margins")

    # ── Margins for LBO forecast ──────────────────────────────────────────────
    # Use current margin as Year 1, grow toward exit target
    current_margin_pct = int(round(margin * 100))
    # Target modest improvement (PE operational improvements)
    target_margin_pct  = min(current_margin_pct + 5, 45)
    reasons.append(
        f"Forecast EBITDA margin: {current_margin_pct}% → "
        f"{target_margin_pct}% over hold period (operational improvements)"
    )

    # ── D&A, CapEx, NWC ──────────────────────────────────────────────────────
    # Asset-light services: low CapEx + D&A; product/mfg: higher
    is_asset_light = gp_m > 0.60   # proxy: high gross margin = service business
    da_pct    = 2 if is_asset_light else 4
    capex_pct = 2 if is_asset_light else 6
    nwc_pct   = 3 if is_asset_light else 8
    if is_asset_light:
        reasons.append("Asset-light business detected (GP margin > 60%) → lower D&A/CapEx/NWC")
    else:
        reasons.append("Asset-heavy business → higher D&A/CapEx/NWC assumptions")

    return {
        "entry_multiple": entry,
        "exit_multiple":  exit_,
        "years":          5,
        "growth":         growth,
        "flat_margin":    current_margin_pct,
        "leverage_pct":   leverage,
        "tlb_rate":       7,
        "rev_rate":       6,
        "tax_rate":       17,
        "da_pct":         da_pct,
        "nwc_pct":        nwc_pct,
        "capex_pct":      capex_pct,
        "min_cash":       50_000,
        "auto_rationale": "\n".join(f"• {r}" for r in reasons),
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
# OCR + PDF HELPERS
# =========================================
_AMOUNT_RE = re.compile(r"(\([\d,]+(?:\.\d+)?\)|-?[\d,]+(?:\.\d+)?)")
_NOTE_RE    = re.compile(r"\b([1-9][0-9]?)\b")


def _strip_note_refs(label: str) -> str:
    return re.sub(r"\s+\b\d{1,2}\b\s*$", "", label).strip()


def _parse_line_to_label_amount(line: str) -> "tuple[str, str] | None":
    """
    Parse 'Label [NoteRef] CurrentYearAmt [PriorYearAmt]' → (label, cur_amount).
    Current-year is always the leftmost financial figure after any note reference.
    """
    line = line.strip()
    if not line:
        return None

    found = [(m.start(), m.group()) for m in _AMOUNT_RE.finditer(line)]
    if not found:
        return None

    def _to_float(s: str) -> float:
        s = s.replace(",", "").replace("(", "-").replace(")", "")
        try:
            return float(s)
        except ValueError:
            return 0.0

    parsed     = [(pos, raw, _to_float(raw)) for pos, raw in found]
    formatted  = [(p, r, v) for p, r, v in parsed if "," in r or "." in r or "(" in r]
    plain_ints = [(p, r, v) for p, r, v in parsed if not ("," in r or "." in r or "(" in r)]

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
    return (label, cur_raw) if label else None


def _preprocess_image_for_ocr(img):
    img = img.convert("L")
    img = img.filter(ImageFilter.SHARPEN)
    img = img.point(lambda x: 255 if x > 140 else 0)
    return img


def _ocr_pdf(file_bytes: bytes) -> "pd.DataFrame | None":
    if not PDF_OCR:
        st.error("📦 OCR requires: pip install pdf2image pytesseract pillow")
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
                if clean and not re.fullmatch(r"[\d\s.,\-()]+", clean):
                    rows.append([clean, "0"])

    if not rows:
        st.error("OCR produced no usable rows. Check PDF quality.")
        return None
    return pd.DataFrame(rows, columns=["c0", "c1"], dtype=str)


def read_any_file(uploaded_file) -> "pd.DataFrame | None":
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

                # Fallback: plain-text extraction for digital PDFs without table marks
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


def score_amount_column(series: pd.Series) -> float:
    nums = parse_amount(series)
    nums = nums[nums != 0]
    if len(nums) == 0:
        return -1
    abs_vals = np.abs(nums)
    return (
        len(abs_vals) * 5
        + np.log1p(abs_vals.sum()) * 3
        + np.log1p(np.median(abs_vals)) * 5
        + np.max(abs_vals) * 0.00001
    )


def merge_multiline_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge continuation label rows (amount=0, short label) into the NEXT
    row that has a real amount. Also flushes any trailing buffer at end.

    FIX vs v3: flushed buffer at loop end → no data loss on trailing zero-rows.
    Also changed direction: buffer accumulates BEFORE a real-amount row, not after,
    so 'Employee\nbenefit expense\n12,000' → 'Employee benefit expense, 12000'.
    """
    merged  = []
    buffer  = ""

    for _, row in df.iterrows():
        label  = str(row["Line Item"]).strip()
        amount = row["Amount"]
        is_continuation = (amount == 0 and len(label.split()) <= 4
                           and not label.endswith(":"))

        if is_continuation:
            buffer += (" " + label) if buffer else label
        else:
            full_label = (buffer + " " + label).strip() if buffer else label
            merged.append([full_label, amount])
            buffer = ""

    # FIX: flush any remaining buffer (was lost in v3)
    if buffer:
        merged.append([buffer.strip(), 0])

    return pd.DataFrame(merged, columns=["Line Item", "Amount"])


_META_EXACT   = {"account", "accounts", "nan", "none", ""}
_META_PHRASES = [
    "pte. ltd.", "pte ltd", "sdn bhd", "berhad",
    "for the year", "for the period",
    "as at", "as of",
    "balance sheet", "profit and loss", "income statement",
    "exchange rate", "rates are provided",
    "prepared by", "reviewed by",
]
_DATE_RE = re.compile(
    r"^(31|30|28|29)?\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*\d{4}$",
    re.I,
)


def _is_meta_row(label: str) -> bool:
    x = label.strip()
    xl = x.lower()
    if xl in _META_EXACT:
        return True
    if re.fullmatch(r"[\d\s\-/]+", xl):
        return True
    if _DATE_RE.match(xl):
        return True
    if "page" in xl:
        return True
    return any(p in xl for p in _META_PHRASES)


def smart_clean(df: pd.DataFrame) -> "pd.DataFrame | None":
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.fillna("").astype(str)
    df = dedupe_columns(df)
    df.columns = [f"c{i}" for i in range(len(df.columns))]

    # Single-column raw text (OCR / plain-PDF fallback)
    if df.shape[1] == 1:
        raw_col = df.iloc[:, 0].astype(str)
        rows = []
        for text in raw_col:
            r = _parse_line_to_label_amount(text)
            rows.append(list(r) if r else [text.strip(), "0"])
        df = pd.DataFrame({"c0": [r[0] for r in rows],
                           "c1": [r[1] for r in rows]})

    # Best amount column
    best_col, best_score = None, -1
    for col in df.columns:
        s = score_amount_column(df[col])
        if s > best_score:
            best_score, best_col = s, col

    if best_col is None:
        st.error("❌ Could not detect an amount column. Check the file format.")
        return None

    # Best label column (penalise numeric-heavy columns)
    label_col, label_score = None, -1
    for col in df.columns:
        if col == best_col:
            continue
        non_empty = (df[col].str.strip() != "").sum()
        numeric   = (parse_amount(df[col]) != 0).sum()
        score     = non_empty - numeric * 3
        if score > label_score:
            label_score, label_col = score, col

    if label_col is None:
        st.warning("⚠️ Could not detect label column — using first column.")
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


# =========================================
# P&L CLASSIFICATION
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
            "exactly one of:\nRevenue, COGS, OpEx, D&A, Other Income, "
            "Interest, Tax, Ignore\n\n"
            "Return ONLY a JSON object {\"line item\": \"Category\"}. "
            "No markdown, no explanation.\n\n"
            f"Items:\n{json.dumps(items)}"
        )
        resp = client.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = re.sub(r"^```[a-z]*\n?", "", resp.content[0].text.strip()).rstrip("`").strip()
        return json.loads(raw)
    except Exception as e:
        st.warning(f"AI classification failed: {e}")
        return {}


def classify_pl(df: pd.DataFrame, use_ai: bool, api_key: str) -> pd.DataFrame:
    mem = load_memory()
    df["Category"] = df["Line Item"].apply(keyword_classify_pl)
    # Memory only overrides Unknown — never overwrites a correctly-classified row
    df["Category"] = df.apply(
        lambda r: mem.get(r["Line Item"], r["Category"])
        if r["Category"] == "Unknown" else r["Category"], axis=1,
    )
    if use_ai and api_key:
        unknowns = df[df["Category"] == "Unknown"]["Line Item"].tolist()
        if unknowns:
            ai_map = ai_classify_pl(unknowns, api_key)
            df["Category"] = df.apply(
                lambda r: ai_map.get(r["Line Item"], r["Category"])
                if r["Category"] == "Unknown" else r["Category"], axis=1,
            )
    return df


# =========================================
# P&L METRICS
# Standard order: Revenue − COGS = GP → GP − OpEx − D&A = EBIT
# EBITDA = EBIT + D&A.  Other Income is non-operating (below EBIT).
# =========================================
def compute_pl(df: pd.DataFrame, addbacks: float = 0.0) -> dict:
    def s(cat):
        return df.loc[df["Category"] == cat, "Amount"].sum()

    rev  = s("Revenue");  cogs = s("COGS")
    opex = s("OpEx") - addbacks          # subtract normalisation add-backs
    da   = s("D&A");      oi   = s("Other Income")
    int_ = s("Interest"); tax  = s("Tax")

    gp     = rev - cogs
    ebit   = gp - opex - da              # operating profit
    ebitda = ebit + da                   # add back D&A (standard definition)
    ebt    = ebit + oi - int_            # Other Income below operating line
    net    = ebt - tax

    def pct(n, d=rev):
        return n / d if d else 0

    return {
        "Revenue": rev, "COGS": cogs, "Gross Profit": gp, "GP Margin": pct(gp),
        "OpEx": opex, "D&A": da, "Other Income": oi,
        "EBITDA": ebitda, "EBITDA Margin": pct(ebitda),
        "EBIT": ebit,     "EBIT Margin": pct(ebit),
        "Interest": int_, "EBT": ebt, "Tax": tax,
        "Net Profit": net, "Net Margin": pct(net),
        "Add-backs": addbacks,
    }


# =========================================
# BS CLASSIFICATION
# FIX vs v3: section triggers are checked on the ORIGINAL label (not after
# clean_bs_label strips it), so 'Equity' correctly sets current_section.
# clean_bs_label() is removed — it was causing section triggers to miss.
# =========================================
def classify_bs(df: pd.DataFrame) -> pd.DataFrame:
    cats = []
    current_section = None

    for item in df["Line Item"].fillna("").astype(str):
        x   = item.lower().strip()   # use raw label for trigger matching
        cat = "Other"

        # Update section context FIRST (before Ignore check)
        for trigger, section in BS_SECTION_TRIGGERS.items():
            if trigger in x:
                current_section = section
                break

        # Ignore (totals / section dividers)
        if any(kw in x for kw in BS_KEYWORDS["Ignore"]):
            cats.append("Ignore")
            continue

        # Keyword match
        for c, keywords in BS_KEYWORDS.items():
            if c == "Ignore":
                continue
            if any(k in x for k in keywords):
                cat = c
                break

        # Section-context fallback for unrecognised accounts
        # (e.g. "Jaanik Business Solutions" under "Bank" section → Cash)
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

    net_debt_bs = debt_bs - cash_bs
    equity_in   = entry_ev - total_debt + net_debt_bs

    cash     = float(params["min_cash"])
    prev_nwc = params.get("initial_nwc", revenue * params["nwc_pct"])
    rows     = []

    for i in range(params["years"]):
        rev      = revenue * (1 + params["growth"]) ** (i + 1)
        ebitda_y = rev * params["margins"][i]
        da_y     = rev * params["da_pct"]
        ebit_lbo = ebitda_y - da_y

        interest = tlb * params["tlb_rate"] + revolver * params["rev_rate"]
        ebt_lbo  = ebit_lbo - interest
        tax      = max(0.0, ebt_lbo * params["tax_rate"])

        nwc       = rev * params["nwc_pct"]
        delta_nwc = 0 if i == 0 else (nwc - prev_nwc)   # no artificial Y1 release
        prev_nwc  = nwc
        capex     = rev * params["capex_pct"]

        fcf   = ebitda_y - interest - tax - capex - delta_nwc
        cash += fcf

        if cash < params["min_cash"]:
            draw = params["min_cash"] - cash
            revolver += draw; cash += draw

        excess = max(0.0, cash - params["min_cash"])
        pay_rev = min(revolver, excess)
        revolver -= pay_rev; cash -= pay_rev

        excess  = max(0.0, cash - params["min_cash"])
        pay_tlb = min(tlb, excess)
        tlb -= pay_tlb; cash -= pay_tlb

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

    if exit_equity <= 0 or equity_in <= 0:
        return lbo_df, {
            "Entry EV": entry_ev, "Total Debt": total_debt, "Equity In": equity_in,
            "Exit EV": exit_ev, "Exit Equity": exit_equity,
            "MOIC": 0.0, "IRR": 0.0, "total_loss": True,
        }

    moic = exit_equity / equity_in
    irr  = moic ** (1 / params["years"]) - 1 if moic > 0 else 0
    return lbo_df, {
        "Entry EV": entry_ev, "Total Debt": total_debt, "Equity In": equity_in,
        "Exit EV": exit_ev, "Exit Equity": exit_equity,
        "MOIC": moic, "IRR": irr, "total_loss": False,
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

# ── EBITDA Normalisation ──────────────────────────────────────────────────────
with st.sidebar.expander("🧹 EBITDA Normalisation (SME add-backs)"):
    st.caption(
        "Owner-operators often run personal expenses or above-market salaries "
        "through the P&L. Add-backs adjust OpEx to reflect maintainable earnings."
    )
    addback_salary   = st.number_input("Excess owner salary ($)",    0, value=0, step=10_000)
    addback_oneoff   = st.number_input("One-off / non-recurring ($)", 0, value=0, step=10_000)
    addback_personal = st.number_input("Personal expenses ($)",      0, value=0, step=5_000)
total_addbacks = float(addback_salary + addback_oneoff + addback_personal)

st.sidebar.markdown("---")
st.sidebar.subheader("Valuation Inputs")

# ── All sidebar widgets bound to session_state ────────────────────────────────
entry_multiple = st.sidebar.number_input(
    "Entry EV/EBITDA", 3.0, 20.0, step=0.5,
    key="entry_multiple",
)
exit_multiple = st.sidebar.number_input(
    "Exit EV/EBITDA", 3.0, 20.0, step=0.5,
    key="exit_multiple",
)

st.sidebar.subheader("Holding Period & Growth")
years  = st.sidebar.slider("Holding Period (years)", 1, 7, key="years")
growth = st.sidebar.slider("Revenue Growth % p.a.", 0, 40, key="growth") / 100

margin_mode = st.sidebar.radio("EBITDA Margin Input", ["Flat", "Per Year"], horizontal=True)
if margin_mode == "Flat":
    flat_m  = st.sidebar.slider("EBITDA Margin %", 0, 60, key="flat_margin") / 100
    margins = [flat_m] * years
else:
    margins = [
        st.sidebar.slider(f"Y{i+1} EBITDA Margin %", 0, 60, 20 + i) / 100
        for i in range(years)
    ]

st.sidebar.subheader("Capital Structure")
leverage_pct = st.sidebar.slider("Leverage % of Entry EV", 20, 80, key="leverage_pct") / 100
tlb_rate     = st.sidebar.slider("TLB Interest Rate %", 0, 20, key="tlb_rate") / 100
rev_rate     = st.sidebar.slider("Revolver Rate %", 0, 20, key="rev_rate") / 100

st.sidebar.subheader("Other Assumptions")
tax_rate  = st.sidebar.slider("Tax Rate %", 0, 35, key="tax_rate") / 100
da_pct    = st.sidebar.slider("D&A % of Revenue", 0, 15, key="da_pct") / 100
nwc_pct   = st.sidebar.slider("NWC % of Revenue", 0, 20, key="nwc_pct") / 100
capex_pct = st.sidebar.slider("CapEx % of Revenue", 0, 20, key="capex_pct") / 100
min_cash  = st.sidebar.number_input("Minimum Cash ($)", 0, step=10_000, key="min_cash")

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
    pl_file = st.file_uploader("P&L Statement", type=["xlsx", "xls", "csv", "pdf"])
with col_bs:
    bs_file = st.file_uploader("Balance Sheet", type=["xlsx", "xls", "csv", "pdf"])


# ── State holders ─────────────────────────────────────────────────────────────
pl_metrics    = None
cash_bs       = 0.0
debt_bs       = 0.0
bs_derived_nwc = None


# =========================================
# PROCESS P&L
# =========================================
if pl_file:
    raw_pl = read_any_file(pl_file)
    if raw_pl is not None:
        df_pl = smart_clean(raw_pl)
        if df_pl is not None:
            df_pl = classify_pl(df_pl, use_ai=use_ai, api_key=api_key or "")

            st.markdown("---")
            st.header("📋 Step 2 — Review & Correct P&L Classifications")
            st.caption(
                "Every row is editable. Use the Category dropdown to fix "
                "any misclassified items. Corrections are saved and "
                "auto-applied on the next upload of the same company."
            )

            if total_addbacks > 0:
                st.info(
                    f"🧹 **Normalisation active:** {fmt(total_addbacks)} "
                    "will be added back before computing EBITDA."
                )

            unknown_count = (df_pl["Category"] == "Unknown").sum()
            if unknown_count:
                st.warning(
                    f"⚠️ {unknown_count} row(s) unclassified. "
                    "Fix below or enable AI Classification in the sidebar."
                )

            df_pl = st.data_editor(
                df_pl,
                column_config={
                    "Category": st.column_config.SelectboxColumn(
                        "Category", options=PL_CATEGORIES),
                    "Amount": st.column_config.NumberColumn(
                        "Amount", format="$ %.0f"),
                },
                use_container_width=True, hide_index=True, num_rows="fixed",
            )

            mem = load_memory()
            for _, r in df_pl.iterrows():
                if r["Category"] not in ("Unknown", "Ignore"):
                    mem[r["Line Item"]] = r["Category"]
            save_memory(mem)

            active_pl  = df_pl[~df_pl["Category"].isin(["Ignore", "Unknown"])]
            pl_metrics = compute_pl(active_pl, addbacks=total_addbacks)


# =========================================
# PROCESS BALANCE SHEET
# =========================================
if bs_file:
    raw_bs = read_any_file(bs_file)
    if raw_bs is not None:
        df_bs = smart_clean(raw_bs)
        if df_bs is not None:
            df_bs = classify_bs(df_bs)

            st.markdown("---")
            st.subheader("🏦 Balance Sheet — Review Classifications")
            st.caption(
                "Company-named bank accounts are auto-classified as Cash "
                "based on their position in the statement."
            )

            df_bs = st.data_editor(
                df_bs,
                column_config={
                    "Category": st.column_config.SelectboxColumn(
                        "Category", options=BS_CATEGORIES),
                    "Amount": st.column_config.NumberColumn(
                        "Amount", format="$ %.0f"),
                },
                use_container_width=True, hide_index=True, num_rows="dynamic",
            )

            cash_bs = df_bs.loc[df_bs["Category"] == "Cash",        "Amount"].sum()
            debt_bs = df_bs.loc[df_bs["Category"] == "Debt",        "Amount"].sum()
            recv    = df_bs.loc[df_bs["Category"] == "Receivables", "Amount"].sum()
            pays    = df_bs.loc[df_bs["Category"] == "Payables",    "Amount"].sum()
            inv     = df_bs.loc[df_bs["Category"] == "Inventory",   "Amount"].sum()
            bs_derived_nwc = recv + inv - pays


# =========================================
# AUTO-VALUATION PANEL
# =========================================
if pl_metrics is not None:
    st.markdown("---")
    st.header("🤖 Auto-Valuation")

    col_auto, col_note = st.columns([1, 2])
    with col_auto:
        do_auto = st.button(
            "⚡ Auto-set Parameters",
            help="Analyses the uploaded financials and recommends deal parameters. "
                 "You can still override anything in the sidebar afterwards.",
            use_container_width=True,
            type="primary",
        )
    with col_note:
        st.caption(
            "Click to automatically populate the sidebar with recommended "
            "entry/exit multiples, leverage, growth, and margin assumptions "
            "based on this SME's size and profitability. "
            "All values remain fully editable."
        )

    if do_auto:
        suggested = auto_value_sme(pl_metrics, cash_bs, debt_bs)
        for k, v in suggested.items():
            if k in st.session_state and k != "auto_rationale":
                st.session_state[k] = v
        st.session_state["auto_rationale"] = suggested["auto_rationale"]
        st.success("✅ Sidebar parameters updated — scroll up to review or adjust.")
        st.rerun()

    if st.session_state.get("auto_rationale"):
        with st.expander("📋 Auto-valuation rationale", expanded=True):
            st.markdown(st.session_state["auto_rationale"])
            st.caption(
                "These are starting-point recommendations. Adjust in the sidebar "
                "based on your knowledge of the specific business, sector, "
                "and deal dynamics."
            )


# =========================================
# VALUATION OUTPUT
# =========================================
if pl_metrics:
    st.markdown("---")
    st.header("📊 Step 3 — Valuation Output")

    m = pl_metrics

    if m["Add-backs"] > 0:
        st.success(
            f"📈 Normalised EBITDA: **{fmt(m['EBITDA'])}** "
            f"({fmt(m['EBITDA Margin'], 'pct')} margin) — includes "
            f"{fmt(m['Add-backs'])} of add-backs."
        )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Revenue",      fmt(m["Revenue"]))
    c2.metric("Gross Profit", fmt(m["Gross Profit"]), fmt(m["GP Margin"],     "pct"))
    c3.metric("EBITDA",       fmt(m["EBITDA"]),        fmt(m["EBITDA Margin"], "pct"))
    c4.metric("EBIT",         fmt(m["EBIT"]),           fmt(m["EBIT Margin"],  "pct"))
    c5.metric("Net Profit",   fmt(m["Net Profit"]),    fmt(m["Net Margin"],    "pct"))

    with st.expander("📄 Full P&L Bridge"):
        bridge_rows = [
            ("Revenue",              m["Revenue"]),
            ("(-)  COGS",           -m["COGS"]),
            ("Gross Profit",         m["Gross Profit"]),
            ("(-)  OpEx",           -m["OpEx"]),
            ("(-)  D&A",            -m["D&A"]),
            ("EBIT (operating)",     m["EBIT"]),
            ("(+)  D&A add-back",    m["D&A"]),
            ("EBITDA",               m["EBITDA"]),
            ("── non-operating ──",  None),
            ("(+)  Other Income",    m["Other Income"]),
            ("(-)  Interest",       -m["Interest"]),
            ("EBT",                  m["EBT"]),
            ("(-)  Tax",            -m["Tax"]),
            ("Net Profit",           m["Net Profit"]),
        ]
        if m["Add-backs"] > 0:
            bridge_rows.insert(3, ("(+)  Add-backs", m["Add-backs"]))
        pl_bridge = pd.DataFrame(
            [(r, fmt(v) if v is not None else "──────") for r, v in bridge_rows],
            columns=["Item", "Amount"]
        )
        st.dataframe(pl_bridge, use_container_width=True, hide_index=True)

    if bs_file:
        st.subheader("Balance Sheet Snapshot")
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Cash & Equivalents", fmt(cash_bs))
        b2.metric("Total Debt",         fmt(debt_bs))
        b3.metric("Net Debt",           fmt(debt_bs - cash_bs))
        if bs_derived_nwc is not None:
            b4.metric("Working Capital (BS)", fmt(bs_derived_nwc))

    st.markdown("---")

    if m["EBITDA"] <= 0:
        st.error(
            "⚠️ EBITDA is zero or negative — LBO model cannot run. "
            "Check Revenue / COGS / OpEx classifications, or add normalisation "
            "add-backs in the sidebar."
        )
    else:
        lbo_params = {
            **params,
            **({"initial_nwc": bs_derived_nwc} if bs_derived_nwc is not None else {}),
        }
        lbo_df, returns = run_lbo(pl_metrics, cash_bs, debt_bs, lbo_params)

        st.subheader("📈 Returns Summary")
        if returns.get("total_loss"):
            st.error(
                "⚠️ **Total loss scenario** — exit equity is zero or negative. "
                "Try lower entry multiple, lower leverage, or higher exit multiple."
            )
            col1, col2 = st.columns(2)
            col1.metric("Entry EV", fmt(returns["Entry EV"]))
            col2.metric("Exit EV",  fmt(returns["Exit EV"]))
        else:
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
                                  {**lbo_params, "entry_multiple": em,
                                   "exit_multiple": xm})
                row[f"Exit {xm:.1f}x"] = (
                    "Loss" if ret2.get("total_loss") else f"{ret2['MOIC']:.2f}x"
                )
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
                {"Item": "MOIC",  "Value": fmt(returns["MOIC"], "x")  if not returns.get("total_loss") else "Loss"},
                {"Item": "IRR",   "Value": fmt(returns["IRR"],  "pct") if not returns.get("total_loss") else "—"},
            ])
            st.dataframe(bridge, use_container_width=True, hide_index=True)

elif not pl_file:
    st.info("👆 Upload a P&L statement above to get started.")
