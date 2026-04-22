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
st.title("📊 SME Auto Valuation Tool")

uploaded_file = st.file_uploader("Upload P&L (Excel)", type=["xlsx"])

if uploaded_file:
    df = pd.read_excel(uploaded_file, header=1)  # <-- IMPORTANT FIX

    # Rename columns properly
    df.columns = ["Line Item", "Amount"]

    # Drop empty rows
    df = df.dropna(how="all")

    # Remove section headers (like "REVENUE", "EXPENSES")
    df = df[df["Amount"].notna()]

    # ---------- CLEAN AMOUNTS ----------
    def clean_amount(x):
        x = str(x)
        x = x.replace(",", "")
        if "(" in x and ")" in x:
            x = "-" + x.replace("(", "").replace(")", "")
        try:
            return float(x)
        except:
            return None

    df["Amount"] = df["Amount"].apply(clean_amount)
    df = df.dropna(subset=["Amount"])

    st.subheader("Cleaned Data")
    st.dataframe(df)

    # ---------- CLASSIFICATION ----------
    def classify(line):
        line = str(line).lower()
        if "revenue" in line or "fees" in line or "income" in line:
            return "Revenue"
        elif "cost" in line or "cogs" in line:
            return "COGS"
        else:
            return "OpEx"

    df["Category"] = df["Line Item"].apply(classify)
    df["Adjustment"] = "None"

    st.subheader("Mapping (Editable)")
    edited_df = st.data_editor(df, use_container_width=True)

    # ---------- CALCULATIONS ----------
    revenue = edited_df[edited_df["Category"] == "Revenue"]["Amount"].sum()
    cogs = edited_df[edited_df["Category"] == "COGS"]["Amount"].sum()
    opex = edited_df[edited_df["Category"] == "OpEx"]["Amount"].sum()
    addbacks = edited_df[edited_df["Adjustment"] == "Add-back"]["Amount"].sum()

    ebitda = revenue - cogs - abs(opex) + abs(addbacks)
    margin = ebitda / revenue if revenue != 0 else 0

    # ---------- SCORING ----------
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

    # ---------- OUTPUT ----------
    st.subheader("📈 Results")

    col1, col2, col3 = st.columns(3)
    col1.metric("Revenue", f"{revenue:,.0f}")
    col2.metric("EBITDA", f"{ebitda:,.0f}")
    col3.metric("Margin", f"{margin:.2%}")

    st.subheader("💰 Valuation Range")
    st.write(f"Low: {low_val:,.0f}")
    st.write(f"High: {high_val:,.0f}")
