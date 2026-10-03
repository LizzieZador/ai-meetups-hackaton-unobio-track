"""Streamlit UI — one input form, one report output. No auth, no database."""
import streamlit as st

from pipeline import run_pipeline

st.set_page_config(page_title="AI-інвесткомітет для біотеху", layout="wide")
st.title("AI-інвесткомітет для біотеху")
st.caption("Disease/Indication + Mechanism/target -> інвестзвіт (Invest / Conditional / Do Not Invest)")

with st.form("intake"):
    indication = st.text_input("Disease / Indication", placeholder="e.g. non-small cell lung cancer")
    mechanism = st.text_input("Mechanism / target", placeholder="e.g. KRAS G12C inhibitor")
    submitted = st.form_submit_button("Generate report")

if submitted:
    if not indication or not mechanism:
        st.error("Both Indication and Mechanism/target are required.")
    else:
        with st.spinner("Collecting sources and running the committee..."):
            try:
                report_md = run_pipeline(indication, mechanism)
            except Exception as e:
                st.error(f"Pipeline failed: {e}")
            else:
                st.markdown(report_md)
