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
st.title("📊 SME Valuation & Deal Analysis Tool")

# ---------- SIDEBAR ASSUMPTIONS ----------
st.sidebar.header("📊 Deal Assumptions")

entry_multiple = st.sidebar.number_input("Entry Multiple", value=5.0)
exit_multiple = st.sidebar.number_input("Exit Multiple", value=6.5)
holding_period = st.sidebar.slider("Holding Period (Years)", 1, 7, 3)

growth_rate = st.sidebar.slider("Revenue Growth (%)", 0, 30, 10) / 100
margin_target = st.sidebar.slider("Target EBITDA Margin (%)", 5, 50, 20) / 100

ebitda_adjustment = st.sidebar.number_input("EBITDA Adjustments", value=0.0)

# ---------- FILE UPLOAD ----------
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

    st.subheader("🧩 P&L Mapping")
    pnl = st.data_editor(pnl)

    revenue = pnl[pnl["Category"] == "Revenue"]["Amount"].sum()
    cogs = pnl[pnl["Category"] == "COGS"]["Amount"].sum()
    opex = pnl[pnl["Category"] == "OpEx"]["Amount"].sum()

    ebitda = revenue - cogs - abs(opex)
    adj_ebitda = ebitda + ebitda_adjustment

    margin = adj_ebitda / revenue if revenue != 0 else 0

    st.subheader("📌 P&L Breakdown")
    st.write(f"Revenue: {revenue:,.0f}")
    st.write(f"COGS: {cogs:,.0f}")
    st.write(f"OpEx: {opex:,.0f}")
    st.write(f"Adjusted EBITDA: {adj_ebitda:,.0f}")
    st.write(f"Margin: {margin:.2%}")

# ---------- PROCESS BALANCE SHEET ----------
net_debt = 0

if bs_file:
    bs = pd.read_excel(bs_file)
    bs = bs.iloc[:, :2]
    bs.columns = ["Line Item", "Amount"]
    bs = bs.dropna(subset=["Line Item"])

    bs["Amount"] = bs["Amount"].apply(clean_amount)
    bs = bs.dropna(subset=["Amount"])

    st.subheader("📊 Balance Sheet")
    st.dataframe(bs)

    cash = bs[bs["Line Item"].str.contains("cash|bank", case=False)]["Amount"].sum()
    debt = bs[bs["Line Item"].str.contains("loan|debt|borrow", case=False)]["Amount"].sum()
    receivables = bs[bs["Line Item"].str.contains("receivable", case=False)]["Amount"].sum()
    payables = bs[bs["Line Item"].str.contains("payable", case=False)]["Amount"].sum()

    net_debt = debt - cash

    st.subheader("📊 Balance Sheet Breakdown")
    st.write(f"Cash: {cash:,.0f}")
    st.write(f"Debt: {debt:,.0f}")
    st.write(f"Net Debt: {net_debt:,.0f}")

# ---------- VALUATION & FORECAST ----------
if pnl_file:

    # Forecast revenue
    forecast_revenue = revenue
    for i in range(holding_period):
        forecast_revenue *= (1 + growth_rate)

    forecast_ebitda = forecast_revenue * margin_target

    # Entry & Exit
    entry_value = adj_ebitda * entry_multiple
    exit_value = forecast_ebitda * exit_multiple

    # Equity value (entry)
    equity_entry = entry_value - net_debt
    equity_exit = exit_value - net_debt

    # Returns
    irr = (equity_exit / equity_entry) ** (1 / holding_period) - 1 if equity_entry > 0 else 0
    moic = equity_exit / equity_entry if equity_entry > 0 else 0

    # ---------- MULTIPLE BREAKDOWN ----------
    st.subheader("📊 Multiples")
    st.write(f"Entry Multiple: {entry_multiple:.1f}x")
    st.write(f"Exit Multiple: {exit_multiple:.1f}x")

    # ---------- FORECAST ----------
    st.subheader("📈 Forecast")
    st.write(f"Forecast Revenue: {forecast_revenue:,.0f}")
    st.write(f"Forecast EBITDA: {forecast_ebitda:,.0f}")

    # ---------- VALUES ----------
    st.subheader("💰 Valuation")

    col1, col2 = st.columns(2)
    col1.metric("Enterprise Value (Entry)", f"{entry_value:,.0f}")
    col2.metric("Enterprise Value (Exit)", f"{exit_value:,.0f}")

    col3, col4 = st.columns(2)
    col3.metric("Equity Value (Entry)", f"{equity_entry:,.0f}")
    col4.metric("Equity Value (Exit)", f"{equity_exit:,.0f}")

    # ---------- RETURNS ----------
    st.subheader("📊 Returns")

    col5, col6 = st.columns(2)
    col5.metric("IRR", f"{irr:.2%}")
    col6.metric("MOIC", f"{moic:.2f}x")

    # ---------- SUMMARY ----------
    st.subheader("📌 Summary")
    st.write(f"Revenue: {revenue:,.0f}")
    st.write(f"Adj EBITDA: {adj_ebitda:,.0f}")
    st.write(f"Margin: {margin:.2%}")
