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
st.title("SME Auto Valuation Tool")

uploaded_file = st.file_uploader("Upload P&L (Excel)", type=["xlsx"])

if uploaded_file:
    df = pd.read_excel(uploaded_file)

    if "Line Item" not in df.columns or "Amount" not in df.columns:
        st.error("Excel must have columns: Line Item, Amount")
        st.stop()

    def classify(line):
        line = str(line).lower()
        if "revenue" in line or "income" in line:
            return "Revenue"
        elif "cost" in line or "cogs" in line:
            return "COGS"
        else:
            return "OpEx"

    df["Category"] = df["Line Item"].apply(classify)
    df["Adjustment"] = "None"

    edited_df = st.data_editor(df, use_container_width=True)

    revenue = edited_df[edited_df["Category"] == "Revenue"]["Amount"].sum()
    cogs = edited_df[edited_df["Category"] == "COGS"]["Amount"].sum()
    opex = edited_df[edited_df["Category"] == "OpEx"]["Amount"].sum()
    addbacks = edited_df[edited_df["Adjustment"] == "Add-back"]["Amount"].sum()

    ebitda = revenue - cogs - abs(opex) + abs(addbacks)
    margin = ebitda / revenue if revenue != 0 else 0

    if margin > 0.25:
        score = 3
    elif margin > 0.15:
        score = 2
    else:
        score = 1

    if revenue > 3000000:
        size_score = 3
    elif revenue > 1000000:
        size_score = 2
    else:
        size_score = 1

    total_score = score + size_score

    low_multiple = 4 + total_score * 0.5
    high_multiple = 6 + total_score * 0.7

    low_val = ebitda * low_multiple
    high_val = ebitda * high_multiple

    st.subheader("Results")
    st.write(f"Revenue: {revenue:,.0f}")
    st.write(f"EBITDA: {ebitda:,.0f}")
    st.write(f"Valuation Range: {low_val:,.0f} – {high_val:,.0f}")
