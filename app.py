import streamlit as st
import pandas as pd

# ---------- PASSWORD ----------
def check_password():
    def password_entered():
        if st.session_state["password"] == "valuationrun123":
            st.session_state["password_correct"] = True
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Enter password", type="password", key="password", on_change=password_entered)
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Enter password", type="password", key="password", on_change=password_entered)
        st.error("Incorrect password")
        return False
    else:
        return True

if not check_password():
    st.stop()

# ---------- APP ----------
st.title("📊 SME Valuation Tool (P&L + Balance Sheet)")

pnl_file = st.file_uploader("Upload P&L", type=["xlsx"])
bs_file = st.file_uploader("Upload Balance Sheet", type=["xlsx"])

# ---------- CLEAN FUNCTION ----------
def clean_amount(x):
    x = str(x)
    x = x.replace(",", "")
    if "(" in x and ")" in x:
        x = "-" + x.replace("(", "").replace(")", "")
    try:
        return float(x)
    except:
        return None

# ---------- PROCESS P&L ----------
if pnl_file:
    pnl = pd.read_excel(pnl_file)

    # Take first 2 columns
    pnl = pnl.iloc[:, :2]
    pnl.columns = ["Line Item", "Amount"]

    pnl = pnl.dropna(subset=["Line Item"])

    pnl["Amount"] = pnl["Amount"].apply(clean_amount)
    pnl = pnl.dropna(subset=["Amount"])

    st.subheader("📊 Cleaned P&L")
    st.dataframe(pnl)

    def classify(line):
        line = str(line).lower()
        if "revenue" in line or "income" in line or "fees" in line:
            return "Revenue"
        elif "cost" in line or "cogs" in line:
            return "COGS"
        else:
            return "OpEx"

    pnl["Category"] = pnl["Line Item"].apply(classify)

    st.subheader("🧩 P&L Mapping (Editable)")
    pnl = st.data_editor(pnl)

    revenue = pnl[pnl["Category"] == "Revenue"]["Amount"].sum()
    cogs = pnl[pnl["Category"] == "COGS"]["Amount"].sum()
    opex = pnl[pnl["Category"] == "OpEx"]["Amount"].sum()

    ebitda = revenue - cogs - abs(opex)
    margin = ebitda / revenue if revenue != 0 else 0

    # ---------- CATEGORY BREAKDOWN ----------
    st.subheader("📌 Category Breakdown")
    st.write(f"Revenue: {revenue:,.0f}")
    st.write(f"COGS: {cogs:,.0f}")
    st.write(f"OpEx: {opex:,.0f}")
    st.write(f"EBITDA: {ebitda:,.0f}")
    st.write(f"Margin: {margin:.2%}")

# ---------- PROCESS BALANCE SHEET ----------
if bs_file:
    bs = pd.read_excel(bs_file)

    bs = bs.iloc[:, :2]
    bs.columns = ["Line Item", "Amount"]

    bs = bs.dropna(subset=["Line Item"])

    bs["Amount"] = bs["Amount"].apply(clean_amount)
    bs = bs.dropna(subset=["Amount"])

    st.subheader("📊 Cleaned Balance Sheet")
    st.dataframe(bs)

    # ---------- EXTRACTION ----------
    cash = bs[bs["Line Item"].str.contains("cash|bank", case=False)]["Amount"].sum()
    debt = bs[bs["Line Item"].str.contains("loan|debt|borrow", case=False)]["Amount"].sum()
    receivables = bs[bs["Line Item"].str.contains("receivable", case=False)]["Amount"].sum()
    payables = bs[bs["Line Item"].str.contains("payable", case=False)]["Amount"].sum()

    net_debt = debt - cash

    # ---------- BS OUTPUT ----------
    st.subheader("📊 Balance Sheet Breakdown")
    st.write(f"Cash: {cash:,.0f}")
    st.write(f"Debt: {debt:,.0f}")
    st.write(f"Net Debt: {net_debt:,.0f}")
    st.write(f"Receivables: {receivables:,.0f}")
    st.write(f"Payables: {payables:,.0f}")

# ---------- VALUATION ----------
if pnl_file:
    if margin > 0.25:
        margin_score = 3
    elif margin > 0.15:
        margin_score = 2
    else:
        margin_score = 1

    if revenue > 3000000:
        size_score = 3
    elif revenue > 1000000:
        size_score = 2
    else:
        size_score = 1

    total_score = margin_score + size_score

    low_multiple = 4 + total_score * 0.5
    high_multiple = 6 + total_score * 0.7

    # ---------- MULTIPLE OUTPUT ----------
    st.subheader("📊 Multiple Breakdown")
    st.write(f"Margin Score: {margin_score}")
    st.write(f"Size Score: {size_score}")
    st.write(f"Total Score: {total_score}")
    st.write(f"Multiple Range: {low_multiple:.1f}x – {high_multiple:.1f}x")

    # ---------- ENTERPRISE VALUE ----------
    ev_low = ebitda * low_multiple
    ev_high = ebitda * high_multiple

    st.subheader("📈 Enterprise Value")
    st.write(f"Low: {ev_low:,.0f}")
    st.write(f"High: {ev_high:,.0f}")

    # ---------- EQUITY VALUE ----------
    if bs_file:
        equity_low = ev_low - net_debt
        equity_high = ev_high - net_debt

        st.subheader("💰 Equity Value")
        st.write(f"Low: {equity_low:,.0f}")
        st.write(f"High: {equity_high:,.0f}")

# ---------- SUMMARY ----------
if pnl_file:
    st.subheader("📌 Key Metrics Summary")
    st.write(f"Revenue: {revenue:,.0f}")
    st.write(f"EBITDA: {ebitda:,.0f}")
    st.write(f"Margin: {margin:.2%}")
