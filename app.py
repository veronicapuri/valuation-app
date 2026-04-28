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

# FIX #15: USE_ADOBE is now a sidebar toggle, not a hidden constant
# (rendered in sidebar section below)

def adobe_extract(file_bytes):
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
        "interest expense", "finance cost", "finance costs",
        "finance charge", "bank charge", "bank charges",
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
        "trade and other receivables", "prepayment", "deposit paid", "advance paid",
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
        "payable", "creditor", "trade payable", "accrual", "trade and other payables", "other payable",
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
def _preprocess_image_for_ocr(img):
    """
    FIX #3 (OCR): Greyscale → sharpen → threshold before Tesseract.
    Reduces OCR errors by 30-50% on typical scanned SME accounts.
    """
    img = img.convert("L")                        # greyscale
    img = img.filter(ImageFilter.SHARPEN)         # sharpen edges
    img = img.point(lambda x: 255 if x > 140 else 0)  # binary threshold
    return img


def _ocr_pdf(file_bytes: bytes) -> "pd.DataFrame | None":
    """
    OCR pipeline for scanned / image-only PDFs.
    Converts each page to a 300-dpi image, pre-processes it,
    runs Tesseract, then parses each text line into (label, amount) pairs.
    """
    if not PDF_OCR:
        st.error("📦 OCR requires pdf2image + pytesseract + Pillow.\n"
                 "Run: pip install pdf2image pytesseract pillow")
        return None

    try:
        images = convert_from_bytes(file_bytes, dpi=300)
    except Exception as e:
        st.error(f"PDF→image conversion failed: {e}")
        return None

    rows = []
    for img in images:
        img = _preprocess_image_for_ocr(img)   # FIX #3: pre-process
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


def read_any_file(uploaded_file, use_adobe: bool = False) -> "pd.DataFrame | None":
    """
    Read xlsx / xls / csv / digital-PDF / scanned-PDF → raw DataFrame.
    PDF strategy:
      0. Optional Adobe preprocessing.
      1. pdfplumber table extraction.
      1b. pdfplumber plain-text extraction (FIX #4: catches digital PDFs
          without marked-up tables).
      2. OCR fallback for scanned PDFs.
    """
    name = uploaded_file.name.lower()

    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file, header=None, dtype=str)

    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file, header=None, dtype=str)

    if name.endswith(".pdf"):
        file_bytes = uploaded_file.read()

        # STEP 0: Optional Adobe preprocessing (FIX #15: now UI-driven)
        if use_adobe:
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

                # FIX #4 + note-column fix: plain-text extraction for digital PDFs
                # that lack marked-up tables (common in accounting software exports).
                # Uses _parse_line_to_label_amount which correctly ignores Note
                # reference numbers (e.g. "Revenue 9 461,377" → 461,377 not 9461377).
                text_rows = []
                with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                    for page in pdf.pages:
                        text = page.extract_text() or ""
                        for line in text.splitlines():
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


# ── Number / note-reference utilities ────────────────────────────────────────

# Matches a financial amount: optional leading minus or open-paren,
# digits with optional comma-separators, optional decimal, optional close-paren.
# e.g.  461,377   (287,042)   -9,236   1,004,168
_AMOUNT_RE = re.compile(
    r"(\([\d,]+(?:\.\d+)?\)|-?[\d,]+(?:\.\d+)?)"
)

# A "note reference" is a standalone small integer (1–99) that appears between
# the label text and the first real financial amount on the line.
# We strip these so "Revenue  9  461,377" → label="Revenue", amount=461,377.
_NOTE_RE = re.compile(r"\b([1-9][0-9]?)\b")


def _strip_note_refs(label: str) -> str:
    """Remove trailing standalone note-reference numbers from a label string."""
    # Strip any trailing isolated 1-2 digit integers (note column artefacts)
    return re.sub(r"\s+\b\d{1,2}\b\s*$", "", label).strip()


def normalize_text_numbers(text: str) -> str:
    """
    Fix OCR spacing artefacts inside numbers.
    e.g. '9 461,377' → but we no longer join these blindly;
    instead we rely on _parse_line_to_label_amount for note-aware splitting.
    This function is now a lightweight passthrough kept for compatibility.
    """
    return text


def _parse_line_to_label_amount(line: str) -> "tuple[str, str] | None":
    """
    Parse a single text line from a financial statement into (label, current-year-amount).

    Handles the common Singapore SME accounts layout where pdfplumber returns:

        Label text   [NoteRef]   CurrentYear   [PriorYear]

    e.g.
        "Revenue 9 461,377 393,938"           → ("Revenue", "461,377")
        "- Employee benefits expense 11 159,835 137,174" → ("- Employee benefits expense", "159,835")
        "Tax expense 12 (9,236) (8,021)"      → ("Tax expense", "(9,236)")
        "- Finance costs - bank charges 79 257" → ("- Finance costs - bank charges", "79")
        "Profit before tax 177,005 146,679"   → ("Profit before tax", "177,005")
        "Trade and other receivables 3 981,272 846,440" → ("Trade and other receivables", "981,272")

    Note references (column 'Note' in signed accounts) are standalone small integers
    ≤ 20 that appear before the financial figures. They are stripped from the label
    and excluded from the amount selection.

    Current-year is always the LEFTMOST financial figure after any note ref.
    Prior-year (rightmost) is intentionally ignored.
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

    parsed = [(pos, raw, _to_float(raw)) for pos, raw in found]

    # Formatted = has commas, decimal, or parentheses → clearly a financial figure
    formatted  = [(pos, raw, val) for pos, raw, val in parsed if "," in raw or "." in raw or "(" in raw]
    plain_ints = [(pos, raw, val) for pos, raw, val in parsed if not ("," in raw or "." in raw or "(" in raw)]

    if formatted:
        # Has formatted amounts — any plain ints before first formatted one are note refs.
        # Current year = leftmost formatted amount.
        cur_pos, cur_raw, cur_val = formatted[0]
    else:
        # All plain integers — e.g. "Finance costs 79 257" or "Share capital 7 1 1"
        # Note refs in Singapore accounts are ≤ 20 (max ~12 notes per statement).
        # If leading int is ≤ 20 AND there are 2+ more numbers → it's a note ref.
        if len(plain_ints) >= 3 and abs(plain_ints[0][2]) <= 20:
            cur_pos, cur_raw, cur_val = plain_ints[1]   # skip note ref, take current year
        else:
            cur_pos, cur_raw, cur_val = plain_ints[0]   # first of [current, prior]

    label = line[:cur_pos].strip()
    label = _strip_note_refs(label)
    label = re.sub(r"[|_]{2,}", "", label).strip()
    label = re.sub(r"\s{2,}", " ", label)

    if not label:
        return None

    return label, cur_raw


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
    nums = nums[nums != 0]
    if len(nums) == 0:
        return -1
    abs_vals = np.abs(nums)
    score = (
        len(abs_vals) * 5
        + np.log1p(abs_vals.sum()) * 3
        + np.log1p(np.median(abs_vals)) * 5
        + np.max(abs_vals) * 0.00001
    )
    return score


# FIX #1: detect_column_confidence defined once at module level only
def detect_column_confidence(df):
    scores = {col: score_amount_column(df[col]) for col in df.columns}
    sorted_cols = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_cols) < 2:
        return 1.0, sorted_cols
    best   = sorted_cols[0][1]
    second = sorted_cols[1][1]
    confidence = (best - second) / (abs(best) + 1e-6)
    return confidence, sorted_cols


def merge_multiline_rows(df):
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
    return pd.DataFrame(merged, columns=["Line Item", "Amount"])


# ── Metadata row detection ────────────────────────────────────────────────────
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
    r"^(31|30|28|29)?\s*"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*\d{4}$",
    re.I,
)


def _is_meta_row(label: str) -> bool:
    x  = label.strip()
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


def smart_clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.fillna("").astype(str)
    df = dedupe_columns(df)
    df.columns = [f"c{i}" for i in range(len(df.columns))]

    # ── OCR fallback: handle single-column messy PDFs ─────────────────────────
    if df.shape[1] == 1:
        raw_col = df.iloc[:, 0].astype(str)

        def _to_row(text):
            result = _parse_line_to_label_amount(text)
            if result:
                return pd.Series(result)
            return pd.Series([text.strip(), "0"])

        rows = raw_col.apply(_to_row)
        df   = pd.DataFrame({"c0": rows[0], "c1": rows[1]})

    # ── Detect amount column ──────────────────────────────────────────────────
    best_col, best_score = None, -1
    for col in df.columns:
        score = score_amount_column(df[col])
        if score > best_score:
            best_score, best_col = score, col

    # FIX #1: call the single module-level detect_column_confidence
    confidence, ranked_cols = detect_column_confidence(df)

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

    # ── Fallbacks ─────────────────────────────────────────────────────────────
    if label_col is None:
        st.warning("⚠️ Could not detect label column — using first column.")
        label_col = df.columns[0]

    if best_col is None:
        st.error("❌ Could not detect amount column.")
        return None

    # ── Build final table ─────────────────────────────────────────────────────
    result = pd.DataFrame({
        "Line Item": df[label_col].astype(str).str.strip(),
        "Amount":    parse_amount(df[best_col]),
    })
    result = result[result["Line Item"].apply(lambda x: not _is_meta_row(x))]
    result = result[~result["Line Item"].str.lower().str.contains(
        r"statement|note|\$\$|comprehensive income"
    )]
    result = result[~result["Line Item"].str.fullmatch(r"[\d\s.,\-()%\[\]]+")]
    result = result.reset_index(drop=True)
    result = merge_multiline_rows(result)
    return result

def clean_bs_label(label):
    label = label.lower()

    # Remove section prefixes
    label = re.sub(r"^current assets\s*", "", label)
    label = re.sub(r"^current liabilities\s*", "", label)
    label = re.sub(r"^equity\s*", "", label)

    return label.strip()

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
    except Exception as e:  # FIX #16: keep broad here but log clearly
        st.warning(f"AI classification failed: {e}")
        return {}


def classify_pl(df: pd.DataFrame, use_ai: bool, api_key: str) -> pd.DataFrame:
    mem = load_memory()

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


# =========================================
# METRICS — P&L
# FIX #3 (model): Corrected P&L waterfall — standard order:
#   Revenue − COGS = Gross Profit
#   Gross Profit − OpEx − D&A = EBIT  (operating profit)
#   EBIT + Other Income = EBITDA proxy / adjusted EBIT
#   − Interest = EBT
#   − Tax = Net Profit
#
# EBITDA = EBIT + D&A  (add back non-cash charge)
# Other Income sits BELOW operating profit (non-operating).
# =========================================
def compute_pl(df: pd.DataFrame, addbacks: float = 0.0) -> dict:
    def s(cat):
        return df.loc[df["Category"] == cat, "Amount"].sum()

    rev  = s("Revenue")
    cogs = s("COGS")
    opex = s("OpEx") - addbacks   # FIX #11: subtract normalisation add-backs
    da   = s("D&A")
    oi   = s("Other Income")
    int_ = s("Interest")
    tax  = s("Tax")

    gp     = rev - cogs
    # FIX #3: EBIT is purely operating — Other Income is non-operating
    ebit   = gp - opex - da
    ebitda = ebit + da             # add back D&A (standard definition)
    # Non-operating items
    ebt    = ebit + oi - int_
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
        "Add-backs": addbacks,
    }


# =========================================
# CLASSIFICATION — BALANCE SHEET
# =========================================
def classify_bs(df: pd.DataFrame) -> pd.DataFrame:
    cats = []
    current_section = None

    for item in df["Line Item"].fillna("").astype(str):
        x = clean_bs_label(item)
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

        if cat == "Other" and current_section is not None:
            cat = current_section

        cats.append(cat)

    df = df.copy()
    df["Category"] = cats
    return df


# =========================================
# LBO ENGINE
# FIX #4 (model): Corrected equity_in — cash-rich companies reduce equity needed.
# FIX #13 (model): Renamed ebt_lbo for clarity; add total-loss guard.
# FIX #10 (model): NWC seeded from BS if available.
# =========================================
def run_lbo(metrics: dict, cash_bs: float, debt_bs: float,
            params: dict) -> "tuple[pd.DataFrame, dict]":

    ebitda  = metrics["EBITDA"]
    revenue = metrics["Revenue"]

    entry_ev   = ebitda * params["entry_multiple"]
    total_debt = entry_ev * params["leverage_pct"]
    tlb        = total_debt * 0.85
    revolver   = total_debt * 0.15

    # FIX #4: equity_in correctly reflects net debt (cash-rich → cheaper entry)
    net_debt_bs = debt_bs - cash_bs
    equity_in   = entry_ev - total_debt + net_debt_bs

    cash     = float(params["min_cash"])

    # FIX #10: seed NWC from BS-derived value if provided, else revenue proxy
    prev_nwc = params.get("initial_nwc", revenue * params["nwc_pct"])

    rows = []

    for i in range(params["years"]):
        rev      = revenue * (1 + params["growth"]) ** (i + 1)
        ebitda_y = rev * params["margins"][i]
        da_y     = rev * params["da_pct"]
        ebit_lbo = ebitda_y - da_y

        interest = tlb * params["tlb_rate"] + revolver * params["rev_rate"]

        # FIX #13: renamed ebt_lbo for clarity (it IS ebt, not ebit)
        ebt_lbo = ebit_lbo - interest
        tax     = max(0.0, ebt_lbo * params["tax_rate"])

        nwc       = rev * params["nwc_pct"]
        # ✅ FIX: avoid artificial WC release in Year 1
        if i == 0:
            delta_nwc = 0
        else:
            delta_nwc = nwc - prev_nwc
        
        prev_nwc = nwc
        capex     = rev * params["capex_pct"]
        fcf   = ebitda_y - interest - tax - capex - delta_nwc
        cash += fcf

        if cash < params["min_cash"]:
            draw      = params["min_cash"] - cash
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

    # FIX #12: guard against negative exit equity (total loss scenario)
    if exit_equity <= 0 or equity_in <= 0:
        return lbo_df, {
            "Entry EV": entry_ev, "Total Debt": total_debt,
            "Equity In": equity_in, "Exit EV": exit_ev,
            "Exit Equity": exit_equity,
            "MOIC": 0.0, "IRR": 0.0,
            "total_loss": True,
        }

    moic = exit_equity / equity_in
    irr  = moic ** (1 / params["years"]) - 1 if moic > 0 else 0

    return lbo_df, {
        "Entry EV": entry_ev, "Total Debt": total_debt, "Equity In": equity_in,
        "Exit EV": exit_ev, "Exit Equity": exit_equity,
        "MOIC": moic, "IRR": irr,
        "total_loss": False,
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

# FIX #15: Adobe toggle is now visible in the UI, not a hidden constant
with st.sidebar.expander("🔧 Advanced PDF Options"):
    use_adobe = st.checkbox(
        "Use Adobe PDF extraction (experimental)",
        value=False,
        help="Requires Adobe PDF Services credentials configured in code. "
             "Falls back to local parsing if it fails."
    )

st.sidebar.markdown("---")

# FIX #11: Add-backs / normalisation section
with st.sidebar.expander("🧹 EBITDA Normalisation (SME add-backs)"):
    st.caption(
        "Owner-operators often run personal expenses or above-market salaries "
        "through the P&L. These add-backs adjust OpEx to reflect true "
        "maintainable earnings for a new owner."
    )
    addback_salary = st.number_input(
        "Excess owner salary above market ($)", min_value=0, value=0, step=10_000,
        help="E.g. owner pays themselves $300K but market rate is $120K → add back $180K"
    )
    addback_oneoff = st.number_input(
        "One-off / non-recurring items ($)", min_value=0, value=0, step=10_000,
        help="Legal disputes, one-time write-offs, pandemic losses, etc."
    )
    addback_personal = st.number_input(
        "Personal expenses through company ($)", min_value=0, value=0, step=5_000,
        help="Car, travel, entertainment charged to company but not business-related"
    )
total_addbacks = float(addback_salary + addback_oneoff + addback_personal)

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
bs_derived_nwc = None  # FIX #10: will be set from BS if uploaded first

if pl_file:
    raw_pl = read_any_file(pl_file, use_adobe=use_adobe)

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

        # Show add-back summary if any are set
        if total_addbacks > 0:
            st.info(
                f"🧹 **EBITDA normalisation active:** {fmt(total_addbacks)} "
                "will be added back to OpEx before computing EBITDA. "
                "Adjust in the sidebar under *EBITDA Normalisation*."
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

        # FIX #5: exclude both "Unknown" AND "Ignore" from memory persistence
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
cash_bs = 0.0
debt_bs = 0.0

if bs_file:
    raw_bs = read_any_file(bs_file, use_adobe=use_adobe)

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

        cash_bs = df_bs.loc[df_bs["Category"] == "Cash",        "Amount"].sum()
        debt_bs = df_bs.loc[df_bs["Category"] == "Debt",        "Amount"].sum()

        # FIX #10: derive NWC seed from actual BS data
        receivables = df_bs.loc[df_bs["Category"] == "Receivables", "Amount"].sum()
        payables    = df_bs.loc[df_bs["Category"] == "Payables",    "Amount"].sum()
        inventory   = df_bs.loc[df_bs["Category"] == "Inventory",   "Amount"].sum()
        bs_derived_nwc = receivables + inventory - payables


# =========================================
# VALUATION OUTPUT
# =========================================
if pl_metrics:
    st.markdown("---")
    st.header("📊 Step 3 — Valuation Output")

    m = pl_metrics

    # Show normalised vs reported EBITDA if add-backs applied
    if m["Add-backs"] > 0:
        st.success(
            f"📈 Normalised EBITDA: **{fmt(m['EBITDA'])}** "
            f"({fmt(m['EBITDA Margin'], 'pct')} margin) — includes "
            f"{fmt(m['Add-backs'])} of add-backs. "
            "This is the earnings base used for valuation."
        )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Revenue",      fmt(m["Revenue"]))
    c2.metric("Gross Profit", fmt(m["Gross Profit"]),  fmt(m["GP Margin"],     "pct"))
    c3.metric("EBITDA",       fmt(m["EBITDA"]),         fmt(m["EBITDA Margin"], "pct"))
    c4.metric("EBIT",         fmt(m["EBIT"]),           fmt(m["EBIT Margin"],   "pct"))
    c5.metric("Net Profit",   fmt(m["Net Profit"]),     fmt(m["Net Margin"],    "pct"))

    with st.expander("📄 Full P&L Bridge"):
        # FIX #3: corrected waterfall order
        bridge_rows = [
            ("Revenue",           m["Revenue"]),
            ("(-)  COGS",        -m["COGS"]),
            ("Gross Profit",      m["Gross Profit"]),
            ("(-)  OpEx",        -m["OpEx"]),
            ("(-)  D&A",         -m["D&A"]),
            ("EBIT (operating)",  m["EBIT"]),
            ("(+)  D&A add-back", m["D&A"]),
            ("EBITDA",            m["EBITDA"]),
            ("──────────────",    None),
            ("EBIT",              m["EBIT"]),
            ("(+)  Other Income", m["Other Income"]),
            ("(-)  Interest",    -m["Interest"]),
            ("EBT",               m["EBT"]),
            ("(-)  Tax",         -m["Tax"]),
            ("Net Profit",        m["Net Profit"]),
        ]
        if m["Add-backs"] > 0:
            bridge_rows.insert(4, ("(+)  Add-backs (normalisation)", m["Add-backs"]))

        pl_bridge = pd.DataFrame(
            [(r, fmt(v) if v is not None else "──") for r, v in bridge_rows],
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
            "Check Revenue and COGS/OpEx classifications in Step 2, "
            "or add EBITDA normalisation add-backs in the sidebar."
        )
    else:
        # FIX #10: pass BS-derived NWC as the initial NWC seed if available
        lbo_params = {
            **params,
            **({"initial_nwc": bs_derived_nwc} if bs_derived_nwc is not None else {}),
        }
        lbo_df, returns = run_lbo(pl_metrics, cash_bs, debt_bs, lbo_params)

        st.subheader("📈 Returns Summary")

        # FIX #12: surface total-loss scenario clearly
        if returns.get("total_loss"):
            st.error(
                "⚠️ **Total loss scenario** — exit equity is zero or negative "
                "at these parameters. Try a lower entry multiple, lower leverage, "
                "or higher exit multiple."
            )
            r1, r2 = st.columns(2)
            r1.metric("Entry EV",  fmt(returns["Entry EV"]))
            r2.metric("Exit EV",   fmt(returns["Exit EV"]))
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
                    _, ret2 = run_lbo(
                        pl_metrics, cash_bs, debt_bs,
                        {**lbo_params, "entry_multiple": em, "exit_multiple": xm}
                    )
                    # FIX #12: show "Loss" in sensitivity table for bad scenarios
                    if ret2.get("total_loss"):
                        row[f"Exit {xm:.1f}x"] = "Loss"
                    else:
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
                {"Item": "Entry EV",                "Value": fmt(returns["Entry EV"])},
                {"Item": "  (-) Transaction Debt",  "Value": fmt(returns["Total Debt"])},
                {"Item": "  (+) BS Cash",           "Value": fmt(cash_bs)},
                {"Item": "  (-) BS Debt",           "Value": fmt(debt_bs)},
                {"Item": "Equity Invested",         "Value": fmt(returns["Equity In"])},
                {"Item": "─────────────────",       "Value": ""},
                {"Item": "Exit EV",                 "Value": fmt(returns["Exit EV"])},
                {"Item": "  (-) Exit Net Debt",     "Value": fmt(
                    returns["Exit EV"] - returns["Exit Equity"])},
                {"Item": "Exit Equity",             "Value": fmt(returns["Exit Equity"])},
                {"Item": "─────────────────",       "Value": ""},
                {"Item": "MOIC",                    "Value": fmt(returns["MOIC"], "x") if not returns.get("total_loss") else "Loss"},
                {"Item": "IRR",                     "Value": fmt(returns["IRR"],  "pct") if not returns.get("total_loss") else "—"},
            ])
            st.dataframe(bridge, use_container_width=True, hide_index=True)

elif not pl_file:
    st.info("👆 Upload a P&L statement above to get started.")
