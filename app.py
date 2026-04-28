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

BS_SECTION_TRIGGERS = {
    "bank":                "Cash",
    "current assets":      "Receivables",
    "fixed assets":        "Fixed Assets",
    "current liabilities": "Payables",
    "long term liabilit":  "Debt",
    "equity":              "Equity",
}


# =========================================
# AUTO-CALIBRATION ENGINE
# =========================================
def auto_calibrate(metrics: dict, cash_bs: float, debt_bs: float) -> dict:
    """
    Derive recommended deal parameters from the SME's financial profile.

    Logic mirrors how a Singapore/SEA PE analyst would benchmark an SME:
      - Entry multiple based on revenue size + EBITDA margin quality
      - Exit multiple = entry + 1-2x (operational improvement premium)
      - Growth based on margin headroom and revenue base
      - Leverage based on net debt coverage ratio
      - CapEx/NWC based on margin (asset-light vs asset-heavy proxy)
    """
    rev    = metrics.get("Revenue", 0)
    ebitda = metrics.get("EBITDA", 0)
    margin = metrics.get("EBITDA Margin", 0)
    net_debt = debt_bs - cash_bs

    # ── Entry multiple ────────────────────────────────────────────────────────
    # Size tiers (SGD revenue): micro / small / mid
    if rev < 500_000:
        base_entry = 2.5
    elif rev < 2_000_000:
        base_entry = 3.5
    elif rev < 10_000_000:
        base_entry = 5.0
    else:
        base_entry = 6.5

    # Margin quality premium / discount
    if margin >= 0.30:
        margin_adj = +1.0    # premium business
    elif margin >= 0.20:
        margin_adj = +0.5
    elif margin >= 0.10:
        margin_adj = 0.0
    else:
        margin_adj = -0.5    # thin margins → lower multiple

    # Net debt load penalty (if highly leveraged relative to EBITDA)
    leverage_ratio = net_debt / ebitda if ebitda > 0 else 0
    lev_adj = -0.5 if leverage_ratio > 3 else 0

    entry = round(base_entry + margin_adj + lev_adj, 1)
    entry = max(2.5, min(10.0, entry))   # floor / ceiling

    # ── Exit multiple ─────────────────────────────────────────────────────────
    # PE creates value through multiple expansion; typical 1-2x lift
    exit_ = entry + 1.5
    exit_ = max(entry + 0.5, min(12.0, exit_))

    # ── Revenue growth ────────────────────────────────────────────────────────
    # Conservative for micro, moderate for small, aggressive for mid
    if rev < 1_000_000:
        growth = 0.08
    elif rev < 5_000_000:
        growth = 0.12
    else:
        growth = 0.15

    # ── EBITDA margin (exit year target) ─────────────────────────────────────
    # Assume modest improvement from current — PE adds operational value
    target_margin = min(margin + 0.05, 0.45)
    target_margin = max(target_margin, 0.10)

    # ── Leverage ──────────────────────────────────────────────────────────────
    # Debt service coverage: EBITDA / interest should be ≥ 2x
    # Max debt = EBITDA × 3 (conservative for SME — banks are cautious)
    max_debt = min(ebitda * 3, 0.65 * entry * ebitda) if ebitda > 0 else 0
    entry_ev = entry * ebitda
    leverage = min(max_debt / entry_ev, 0.65) if entry_ev > 0 else 0.50
    leverage = max(0.30, round(leverage, 2))

    # ── CapEx / NWC proxies ───────────────────────────────────────────────────
    # Asset-light (high margin) → low capex; asset-heavy → higher capex
    capex = 0.03 if margin >= 0.25 else 0.06
    nwc   = 0.04 if margin >= 0.25 else 0.08   # high-margin = faster collections

    return {
        "entry_multiple": entry,
        "exit_multiple":  round(exit_, 1),
        "growth":         growth,
        "target_margin":  round(target_margin, 3),
        "leverage_pct":   leverage,
        "capex_pct":      capex,
        "nwc_pct":        nwc,
        "rationale": {
            "revenue_tier":    f"${rev/1e6:.2f}M revenue → {base_entry}x base multiple",
            "margin_quality":  f"{margin*100:.1f}% EBITDA margin → {'+' if margin_adj>=0 else ''}{margin_adj:+.1f}x adj",
            "leverage_ratio":  f"{leverage_ratio:.1f}x net debt / EBITDA",
            "suggested_entry": f"{entry:.1f}x",
            "suggested_exit":  f"{round(exit_,1):.1f}x",
        },
    }


def build_scenarios(metrics: dict, cash_bs: float, debt_bs: float,
                    base_params: dict) -> dict:
    """
    Generate Bear / Base / Bull scenario params from the base calibration.
    """
    cal = auto_calibrate(metrics, cash_bs, debt_bs)

    years = base_params.get("years", 5)

    bear = {**base_params,
            "entry_multiple": cal["entry_multiple"] + 0.5,  # paying more
            "exit_multiple":  cal["exit_multiple"]  - 1.0,  # selling for less
            "growth":         max(0.0, cal["growth"] - 0.05),
            "margins":        [max(0.05, cal["target_margin"] - 0.05)] * years,
            "leverage_pct":   min(0.70, cal["leverage_pct"] + 0.10),
            "capex_pct":      cal["capex_pct"] + 0.02,
            "nwc_pct":        cal["nwc_pct"] + 0.02}

    base = {**base_params,
            "entry_multiple": cal["entry_multiple"],
            "exit_multiple":  cal["exit_multiple"],
            "growth":         cal["growth"],
            "margins":        [cal["target_margin"]] * years,
            "leverage_pct":   cal["leverage_pct"],
            "capex_pct":      cal["capex_pct"],
            "nwc_pct":        cal["nwc_pct"]}

    bull = {**base_params,
            "entry_multiple": max(2.5, cal["entry_multiple"] - 0.5),  # buying cheaper
            "exit_multiple":  cal["exit_multiple"] + 1.0,
            "growth":         cal["growth"] + 0.05,
            "margins":        [min(0.50, cal["target_margin"] + 0.05)] * years,
            "leverage_pct":   max(0.25, cal["leverage_pct"] - 0.05),
            "capex_pct":      max(0.01, cal["capex_pct"] - 0.01),
            "nwc_pct":        max(0.01, cal["nwc_pct"]   - 0.02)}

    return {"Bear 🐻": bear, "Base 📊": base, "Bull 🚀": bull}


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
# PDF HELPERS
# =========================================
def _preprocess_image_for_ocr(img):
    img = img.convert("L")
    img = img.filter(ImageFilter.SHARPEN)
    img = img.point(lambda x: 255 if x > 140 else 0)
    return img


_AMOUNT_RE = re.compile(r"(\([\d,]+(?:\.\d+)?\)|-?[\d,]+(?:\.\d+)?)")
_NOTE_RE   = re.compile(r"\b([1-9][0-9]?)\b")


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
                if clean and not re.fullmatch(r"[\d\s.,\-()]+", clean):
                    rows.append([clean, "0"])

    if not rows:
        st.error("OCR produced no usable rows. Check PDF quality.")
        return None
    return pd.DataFrame(rows, columns=["c0", "c1"], dtype=str)


def read_any_file(uploaded_file, use_adobe: bool = False):
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
    # FIX: flush buffer — last row was previously dropped if it ended with 0-amount label
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
    """Returns cleaned DataFrame or None if file cannot be parsed."""
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.fillna("").astype(str)
    df = dedupe_columns(df)
    df.columns = [f"c{i}" for i in range(len(df.columns))]

    # Single-column: try to parse each line as label+amount
    if df.shape[1] == 1:
        def _to_row(text):
            result = _parse_line_to_label_amount(text)
            if result:
                return pd.Series(result)
            return pd.Series([text.strip(), "0"])
        rows = df.iloc[:, 0].astype(str).apply(_to_row)
        df   = pd.DataFrame({"c0": rows[0], "c1": rows[1]})

    # Best amount column
    best_col, best_score = None, -1.0
    for col in df.columns:
        sc = score_amount_column(df[col])
        if sc > best_score:
            best_score, best_col = sc, col

    # FIX: guard — if no numeric column found, return None gracefully
    if best_col is None or best_score <= 0:
        st.error("❌ Could not detect any numeric amount column. "
                 "Check file format — expected columns like 'Item | Amount'.")
        return None

    # Best label column (penalise numeric content)
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
# =========================================
def compute_pl(df: pd.DataFrame, addbacks: float = 0.0) -> dict:
    def s(cat):
        return df.loc[df["Category"] == cat, "Amount"].sum()

    rev  = s("Revenue")
    cogs = s("COGS")
    opex = s("OpEx")           # gross OpEx (before addbacks)
    da   = s("D&A")
    oi   = s("Other Income")
    int_ = s("Interest")
    tax  = s("Tax")

    gp            = rev - cogs
    opex_adj      = opex - addbacks        # normalised OpEx
    ebit          = gp - opex_adj - da     # operating profit
    ebitda        = ebit + da              # standard: EBIT + D&A
    ebt           = ebit + oi - int_
    net           = ebt - tax

    def pct(n, d=rev):
        return n / d if d else 0

    return {
        "Revenue": rev, "COGS": cogs, "Gross Profit": gp, "GP Margin": pct(gp),
        "OpEx (gross)": opex, "Add-backs": addbacks, "OpEx (adj)": opex_adj,
        "D&A": da, "Other Income": oi,
        "EBITDA": ebitda, "EBITDA Margin": pct(ebitda),
        "EBIT": ebit, "EBIT Margin": pct(ebit),
        "Interest": int_, "EBT": ebt, "Tax": tax,
        "Net Profit": net, "Net Margin": pct(net),
    }


# =========================================
# CLASSIFICATION — BALANCE SHEET
# =========================================
def classify_bs(df: pd.DataFrame) -> pd.DataFrame:
    cats = []
    current_section = None

    for item in df["Line Item"].fillna("").astype(str):
        x   = item.lower().strip()
        cat = "Other"

        # Update section context FIRST (before Ignore check)
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
# =========================================
def run_lbo(metrics: dict, cash_bs: float, debt_bs: float,
            params: dict):
    ebitda  = metrics["EBITDA"]
    revenue = metrics["Revenue"]

    entry_ev   = ebitda * params["entry_multiple"]
    total_debt = entry_ev * params["leverage_pct"]
    tlb        = total_debt * 0.85
    revolver   = total_debt * 0.15

    net_debt_bs = debt_bs - cash_bs
    # FIX: floor equity_in at 1 to prevent divide-by-zero on very cash-rich companies
    equity_in = max(1.0, entry_ev - total_debt + net_debt_bs)

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
        delta_nwc = 0 if i == 0 else nwc - prev_nwc   # Y1 NWC delta = 0 (no artificial release)
        prev_nwc  = nwc
        capex     = rev * params["capex_pct"]
        fcf       = ebitda_y - interest - tax - capex - delta_nwc
        cash     += fcf

        if cash < params["min_cash"]:
            draw      = params["min_cash"] - cash
            revolver += draw
            cash     += draw

        excess    = max(0.0, cash - params["min_cash"])
        pay_rev   = min(revolver, excess);  revolver -= pay_rev;  cash -= pay_rev
        excess    = max(0.0, cash - params["min_cash"])
        pay_tlb   = min(tlb, excess);       tlb      -= pay_tlb;  cash -= pay_tlb

        rows.append({
            "Year": i + 1, "Revenue": rev, "EBITDA": ebitda_y,
            "EBITDA Margin": ebitda_y / rev if rev else 0,
            "Interest": interest, "Tax": tax,
            "CapEx": capex, "ΔNWC": delta_nwc, "FCF": fcf,
            "TLB": tlb, "Revolver": revolver, "Cash": cash,
            "Net Debt": tlb + revolver - cash,
        })

    lbo_df      = pd.DataFrame(rows)
    last        = lbo_df.iloc[-1]
    exit_ev     = last["EBITDA"] * params["exit_multiple"]
    exit_equity = exit_ev - last["Net Debt"]

    if exit_equity <= 0:
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
# SESSION STATE INIT
# =========================================
if "calibrated" not in st.session_state:
    st.session_state.calibrated    = False
if "cal_entry" not in st.session_state:
    st.session_state.cal_entry     = 5.0
if "cal_exit" not in st.session_state:
    st.session_state.cal_exit      = 7.0
if "cal_growth" not in st.session_state:
    st.session_state.cal_growth    = 10
if "cal_margin" not in st.session_state:
    st.session_state.cal_margin    = 20
if "cal_leverage" not in st.session_state:
    st.session_state.cal_leverage  = 60
if "cal_capex" not in st.session_state:
    st.session_state.cal_capex     = 5
if "cal_nwc" not in st.session_state:
    st.session_state.cal_nwc       = 5


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

with st.sidebar.expander("🔧 Advanced PDF Options"):
    use_adobe = st.checkbox(
        "Use Adobe PDF extraction (experimental)", value=False,
        help="Requires Adobe PDF Services credentials configured in code."
    )

st.sidebar.markdown("---")

with st.sidebar.expander("🧹 EBITDA Normalisation (SME add-backs)"):
    st.caption(
        "Add back owner salaries above market rate, one-off items, "
        "and personal expenses to arrive at maintainable EBITDA."
    )
    addback_salary   = st.number_input("Excess owner salary ($)", 0, step=10_000)
    addback_oneoff   = st.number_input("One-off / non-recurring ($)", 0, step=10_000)
    addback_personal = st.number_input("Personal expenses ($)", 0, step=5_000)
total_addbacks = float(addback_salary + addback_oneoff + addback_personal)

# ── Auto-calibrate trigger ────────────────────────────────────────────────────
if st.session_state.calibrated:
    st.sidebar.success("✅ Parameters auto-calibrated from financials")
    if st.sidebar.button("🔄 Reset to manual defaults"):
        st.session_state.calibrated   = False
        st.session_state.cal_entry    = 5.0
        st.session_state.cal_exit     = 7.0
        st.session_state.cal_growth   = 10
        st.session_state.cal_margin   = 20
        st.session_state.cal_leverage = 60
        st.session_state.cal_capex    = 5
        st.session_state.cal_nwc      = 5
        st.rerun()

st.sidebar.subheader("Valuation")
entry_multiple = st.sidebar.number_input(
    "Entry EV/EBITDA", 2.0, 20.0,
    value=float(st.session_state.cal_entry), step=0.5
)
exit_multiple = st.sidebar.number_input(
    "Exit EV/EBITDA", 2.0, 20.0,
    value=float(st.session_state.cal_exit), step=0.5
)

st.sidebar.subheader("Holding Period & Growth")
years  = st.sidebar.slider("Holding Period (years)", 1, 7, 5)
growth = st.sidebar.slider(
    "Revenue Growth % p.a.", 0, 40,
    value=int(st.session_state.cal_growth)
) / 100

margin_mode = st.sidebar.radio("EBITDA Margin Input", ["Flat", "Per Year"], horizontal=True)
if margin_mode == "Flat":
    flat_m  = st.sidebar.slider(
        "EBITDA Margin %", 0, 60,
        value=int(st.session_state.cal_margin)
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
    value=int(st.session_state.cal_leverage)
) / 100
tlb_rate = st.sidebar.slider("TLB Interest Rate %", 0, 20, 7) / 100
rev_rate = st.sidebar.slider("Revolver Rate %", 0, 20, 6) / 100

st.sidebar.subheader("Other Assumptions")
tax_rate  = st.sidebar.slider("Tax Rate %", 0, 35, 17) / 100
da_pct    = st.sidebar.slider("D&A % of Revenue", 0, 15, 3) / 100
nwc_pct   = st.sidebar.slider(
    "NWC % of Revenue", 0, 20,
    value=int(st.session_state.cal_nwc)
) / 100
capex_pct = st.sidebar.slider(
    "CapEx % of Revenue", 0, 20,
    value=int(st.session_state.cal_capex)
) / 100
min_cash  = st.sidebar.number_input("Minimum Cash ($)", 0, value=50_000, step=10_000)

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


# =========================================
# PROCESS P&L
# =========================================
pl_metrics     = None
bs_derived_nwc = None
cash_bs = debt_bs = 0.0

if pl_file:
    raw_pl = read_any_file(pl_file, use_adobe=use_adobe)

    if raw_pl is not None:
        df_pl = smart_clean(raw_pl)

        # FIX: smart_clean can return None
        if df_pl is None:
            st.error("P&L could not be parsed. Check file format.")
        else:
            df_pl = classify_pl(df_pl, use_ai=use_ai, api_key=api_key or "")

            st.markdown("---")
            st.header("📋 Step 2 — Review & Correct P&L Classifications")
            st.caption(
                "Every row is editable. Use the Category dropdown to fix "
                "misclassified items. Corrections are saved to memory."
            )

            if total_addbacks > 0:
                st.info(
                    f"🧹 **EBITDA normalisation active:** {fmt(total_addbacks)} "
                    "will be added back before computing EBITDA."
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
    raw_bs = read_any_file(bs_file, use_adobe=use_adobe)

    if raw_bs is not None:
        df_bs = smart_clean(raw_bs)

        if df_bs is None:
            st.error("Balance Sheet could not be parsed. Check file format.")
        else:
            df_bs = classify_bs(df_bs)

            st.markdown("---")
            st.subheader("🏦 Balance Sheet — Review Classifications")
            st.caption(
                "Company-named bank accounts are auto-classified as Cash based on "
                "their position in the statement."
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

            cash_bs     = df_bs.loc[df_bs["Category"] == "Cash",        "Amount"].sum()
            debt_bs     = df_bs.loc[df_bs["Category"] == "Debt",        "Amount"].sum()
            receivables = df_bs.loc[df_bs["Category"] == "Receivables", "Amount"].sum()
            payables    = df_bs.loc[df_bs["Category"] == "Payables",    "Amount"].sum()
            inventory   = df_bs.loc[df_bs["Category"] == "Inventory",   "Amount"].sum()
            bs_derived_nwc = receivables + inventory - payables


# =========================================
# AUTO-CALIBRATE BUTTON
# =========================================
if pl_metrics and pl_metrics.get("EBITDA", 0) > 0:
    cal = auto_calibrate(pl_metrics, cash_bs, debt_bs)

    col_cal, col_info = st.columns([1, 2])
    with col_cal:
        if st.button("🎯 Auto-calibrate deal parameters", type="primary",
                     help="Sets entry/exit multiples, growth, margins, and leverage "
                          "based on this company's financial profile"):
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
        st.caption(
            f"**Calibration logic:** {r['revenue_tier']} | "
            f"Margin adj: {r['margin_quality']} | "
            f"Suggested entry: **{r['suggested_entry']}** / exit: **{r['suggested_exit']}**"
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
    c2.metric("Gross Profit", fmt(m["Gross Profit"]),  fmt(m["GP Margin"],     "pct"))
    c3.metric("EBITDA",       fmt(m["EBITDA"]),         fmt(m["EBITDA Margin"], "pct"))
    c4.metric("EBIT",         fmt(m["EBIT"]),           fmt(m["EBIT Margin"],   "pct"))
    c5.metric("Net Profit",   fmt(m["Net Profit"]),     fmt(m["Net Margin"],    "pct"))

    with st.expander("📄 Full P&L Bridge"):
        bridge_rows = [
            ("Revenue",                   m["Revenue"]),
            ("(-)  COGS",                -m["COGS"]),
            ("Gross Profit",              m["Gross Profit"]),
            ("(-)  OpEx (gross)",        -m["OpEx (gross)"]),
        ]
        if m["Add-backs"] > 0:
            bridge_rows.append(("(+)  Add-backs (normalisation)", m["Add-backs"]))
        bridge_rows += [
            ("(-)  D&A",                 -m["D&A"]),
            ("EBIT (operating profit)",   m["EBIT"]),
            ("(+)  D&A add-back",         m["D&A"]),
            ("EBITDA",                    m["EBITDA"]),
            ("────────────",              None),
            ("EBIT",                      m["EBIT"]),
            ("(+)  Other Income",         m["Other Income"]),
            ("(-)  Interest",            -m["Interest"]),
            ("EBT",                       m["EBT"]),
            ("(-)  Tax",                 -m["Tax"]),
            ("Net Profit",                m["Net Profit"]),
        ]
        pl_bridge = pd.DataFrame(
            [(r, fmt(v) if v is not None else "────") for r, v in bridge_rows],
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
            "Check Revenue and COGS/OpEx classifications in Step 2."
        )
    else:
        lbo_params = {
            **params,
            **({"initial_nwc": bs_derived_nwc} if bs_derived_nwc is not None else {}),
        }
        lbo_df, returns = run_lbo(pl_metrics, cash_bs, debt_bs, lbo_params)

        # ── Scenario Comparison ───────────────────────────────────────────────
        st.subheader("📐 Scenario Analysis — Bear / Base / Bull")
        scenarios = build_scenarios(pl_metrics, cash_bs, debt_bs, lbo_params)

        sc_rows = []
        for sc_name, sc_params in scenarios.items():
            _, sc_ret = run_lbo(pl_metrics, cash_bs, debt_bs, sc_params)
            sc_rows.append({
                "Scenario":    sc_name,
                "Entry":       f"{sc_params['entry_multiple']:.1f}x",
                "Exit":        f"{sc_params['exit_multiple']:.1f}x",
                "Growth":      fmt(sc_params["growth"], "pct"),
                "Margin":      fmt(sc_params["margins"][0], "pct"),
                "Leverage":    fmt(sc_params["leverage_pct"], "pct"),
                "Entry EV":    fmt(sc_ret["Entry EV"]),
                "Equity In":   fmt(sc_ret["Equity In"]),
                "Exit EV":     fmt(sc_ret["Exit EV"]),
                "MOIC":        "Loss" if sc_ret["total_loss"] else f"{sc_ret['MOIC']:.2f}x",
                "IRR":         "—"    if sc_ret["total_loss"] else fmt(sc_ret["IRR"], "pct"),
            })
        st.dataframe(
            pd.DataFrame(sc_rows).set_index("Scenario"),
            use_container_width=True,
        )

        # ── Current (manual) returns ──────────────────────────────────────────
        st.subheader("📈 Current Parameters — Returns")
        if returns.get("total_loss"):
            st.error(
                "⚠️ **Total loss** at current parameters. "
                "Try lower entry multiple, lower leverage, or higher exit multiple. "
                "Or use 🎯 Auto-calibrate above."
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
            r5.metric("IRR",       fmt(returns["IRR"],  "pct"))

            # ── MOIC Sensitivity grid ─────────────────────────────────────────
            st.subheader("🔢 Sensitivity: MOIC")
            rows_sens = []
            for em in [entry_multiple - 1, entry_multiple, entry_multiple + 1]:
                row = {"Entry \\ Exit": f"{em:.1f}x"}
                for xm in [exit_multiple - 1, exit_multiple, exit_multiple + 1]:
                    _, ret2 = run_lbo(pl_metrics, cash_bs, debt_bs,
                                      {**lbo_params, "entry_multiple": em, "exit_multiple": xm})
                    row[f"Exit {xm:.1f}x"] = "Loss" if ret2["total_loss"] else f"{ret2['MOIC']:.2f}x"
                rows_sens.append(row)
            st.dataframe(
                pd.DataFrame(rows_sens).set_index("Entry \\ Exit"),
                use_container_width=True,
            )

        # ── LBO Model table ───────────────────────────────────────────────────
        st.subheader("📋 LBO Model")
        st.dataframe(lbo_df.style.format(FMT_LBO),
                     use_container_width=True, hide_index=True)

        # ── Valuation Bridge ──────────────────────────────────────────────────
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
                {"Item": "MOIC",  "Value": fmt(returns["MOIC"], "x")  if not returns["total_loss"] else "Loss"},
                {"Item": "IRR",   "Value": fmt(returns["IRR"],  "pct") if not returns["total_loss"] else "—"},
            ])
            st.dataframe(bridge, use_container_width=True, hide_index=True)

elif not pl_file:
    st.info("👆 Upload a P&L statement above to get started.")
