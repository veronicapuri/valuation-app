import streamlit as st
import pandas as pd

# ---------------------------
# 🔐 PASSWORD PROTECTION
# ---------------------------
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Enter Password", type="password", on_change=password_entered, key="password")
        return False

    elif not st.session_state["password_correct"]:
        st.text_input("Enter Password", type="password", on_change=password_entered, key="password")
        st.error("Incorrect password")
        return False

    else:
        return True


if not check_password():
    st.stop()

# ---------------------------
# 🎯 PAGE CONFIG
# ---------------------------
st.set_page_config(page_title="SME Valuation Tool", layout="wide")

st.title("📊 SME Valuation & Deal Analysis Tool")

# ---------------------------
# 🧾 SIDEBAR INPUTS
# ---------------------------
st.sidebar.header("Deal Assumptions")

entry_multiple = st.sidebar.number_input("Entry Multiple", value=5.0)
exit_multiple = st.sidebar.number_input("Exit Multiple", value=6.5)
holding_period = st.sidebar.slider("Holding Period (Years)", 1, 7, 3)

growth_rate = st.sidebar.slider("Revenue Growth (%)", 0, 50, 10)
target_margin = st.sidebar.slider("Target EBITDA Margin (%)", 0, 50, 20)
ebitda_adjustments = st.sidebar.number_input("EBITDA Adjustments", value=0.0)

# ---------------------------
# 📂 FILE UPLOAD
# ---------------------------
st.subheader("Upload Files")

pl_file = st.file_uploader("Upload P&L", type=["xlsx"])
bs_file = st.file_uploader("Upload Balance Sheet", type=["xlsx"])

# ---------------------------
# 🧹 CLEANING FUNCTION
# ---------------------------
def clean_data(df):
    df.columns = [c.lower().strip() for c in df.columns]

    line_col, amount_col = None, None

    for col in df.columns:
        if "line" in col or "item" in col or "description" in col:
            line_col = col
        if "amount" in col or "value" in col:
            amount_col = col

    if not line_col or not amount_col:
        st.error("Could not detect columns. Need 'Line Item' and 'Amount'")
        return None

    df = df[[line_col, amount_col]]
    df.columns = ["Line Item", "Amount"]

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")
    df = df.dropna()

    return df

# ---------------------------
# 📊 PROCESS P&L
# ---------------------------
if pl_file:
    pl_df = pd.read_excel(pl_file)
    pl_df = clean_data(pl_df)

    if pl_df is not None:

        st.subheader("Cleaned P&L")
        st.dataframe(pl_df)

        def classify(line):
            line = line.lower()

            if "revenue" in line or "sales" in line:
                return "Revenue"
            elif "cogs" in line or "cost of goods" in line:
                return "COGS"
            else:
                return "OpEx"

        pl_df["Category"] = pl_df["Line Item"].apply(classify)

        revenue = pl_df[pl_df["Category"] == "Revenue"]["Amount"].sum()
        cogs = pl_df[pl_df["Category"] == "COGS"]["Amount"].sum()
        opex = pl_df[pl_df["Category"] == "OpEx"]["Amount"].sum()

        ebitda = revenue - cogs - opex + ebitda_adjustments
        margin = ebitda / revenue if revenue != 0 else 0

        st.subheader("📌 P&L Breakdown")
        st.write(f"Revenue: {revenue:,.0f}")
        st.write(f"COGS: {cogs:,.0f}")
        st.write(f"OpEx: {opex:,.0f}")
        st.write(f"Adjusted EBITDA: {ebitda:,.0f}")
        st.write(f"Margin: {margin:.2%}")

# ---------------------------
# 🏦 PROCESS BALANCE SHEET
# ---------------------------
net_debt = 0

if bs_file:
    bs_df = pd.read_excel(bs_file)
    bs_df = clean_data(bs_df)

    if bs_df is not None:

        st.subheader("Balance Sheet")
        st.dataframe(bs_df)

        def classify_bs(line):
            line = line.lower()

            if "cash" in line:
                return "Cash"
            elif "debt" in line or "loan" in line:
                return "Debt"
            else:
                return "Other"

        bs_df["Category"] = bs_df["Line Item"].apply(classify_bs)

        cash = bs_df[bs_df["Category"] == "Cash"]["Amount"].sum()
        debt = bs_df[bs_df["Category"] == "Debt"]["Amount"].sum()

        net_debt = debt - cash

        st.subheader("📌 Balance Sheet Breakdown")
        st.write(f"Cash: {cash:,.0f}")
        st.write(f"Debt: {debt:,.0f}")
        st.write(f"Net Debt: {net_debt:,.0f}")

# ---------------------------
# 📈 VALUATION + FORECAST
# ---------------------------
if pl_file and pl_df is not None:

    forecast_revenue = revenue * ((1 + growth_rate / 100) ** holding_period)
    forecast_ebitda = forecast_revenue * (target_margin / 100)

    entry_ev = ebitda * entry_multiple
    exit_ev = forecast_ebitda * exit_multiple

    entry_equity = entry_ev - net_debt
    exit_equity = exit_ev - net_debt

    moic = exit_equity / entry_equity if entry_equity != 0 else 0
    irr = (moic ** (1 / holding_period) - 1) if holding_period > 0 else 0

    st.subheader("📊 Valuation")

    col1, col2 = st.columns(2)

    with col1:
        st.metric("Enterprise Value (Entry)", f"{entry_ev:,.0f}")
        st.metric("Equity Value (Entry)", f"{entry_equity:,.0f}")

    with col2:
        st.metric("Enterprise Value (Exit)", f"{exit_ev:,.0f}")
        st.metric("Equity Value (Exit)", f"{exit_equity:,.0f}")

    st.subheader("📈 Returns")

    col1, col2 = st.columns(2)

    with col1:
        st.metric("IRR", f"{irr:.2%}")

    with col2:
        st.metric("MOIC", f"{moic:.2f}x")

    st.subheader("📌 Forecast")

    st.write(f"Forecast Revenue: {forecast_revenue:,.0f}")
    st.write(f"Forecast EBITDA: {forecast_ebitda:,.0f}")

    st.subheader("📌 Summary")

    st.write(f"Revenue: {revenue:,.0f}")
    st.write(f"EBITDA: {ebitda:,.0f}")
    st.write(f"Margin: {margin:.2%}")
