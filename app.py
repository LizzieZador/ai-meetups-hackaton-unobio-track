"""Streamlit UI — one input form, one interactive report. No auth, no database.

Implements the "Interactive Evidence" Required Deliverable: users can drill from
Recommendation -> Claims (grouped by report section) -> Evidence -> Sources,
instead of reading one flat block of markdown.
"""
import streamlit as st

from pipeline import run_pipeline
from schema import SECTION_TITLES

VERDICTS = {"Invest", "Conditional", "Do Not Invest"}

st.set_page_config(page_title="AI-інвесткомітет для біотеху", layout="wide")
st.title("AI-інвесткомітет для біотеху")
st.caption("Disease/Indication + Mechanism/target -> Investment Underwriting Report")

with st.form("intake"):
    indication = st.text_input("Disease / Indication", placeholder="e.g. non-small cell lung cancer")
    mechanism = st.text_input("Mechanism / target", placeholder="e.g. KRAS G12C inhibitor")
    extra_context = st.text_area(
        "Additional context (optional)",
        placeholder="modality, development stage, preclinical results, biomarkers, route of administration...",
    )
    submitted = st.form_submit_button("Generate report")

if submitted:
    if not indication or not mechanism:
        st.error("Both Indication and Mechanism/target are required.")
    else:
        with st.spinner("Collecting sources and running the committee..."):
            try:
                claims, cost_summary = run_pipeline(indication, mechanism, extra_context)
            except Exception as e:
                st.error(f"Pipeline failed: {e}")
                st.stop()

        by_section: dict[str, list] = {}
        for c in claims:
            by_section.setdefault(c.section, []).append(c)

        recommendation_claims = by_section.get("recommendation", [])
        verdict = next((c.claim for c in recommendation_claims if c.claim in VERDICTS), "No verdict produced")
        st.header(f"Recommendation: {verdict}")
        for c in recommendation_claims:
            st.markdown(f"- **{c.claim}** — {c.reasoning} _(confidence: {c.confidence})_")

        st.divider()
        st.subheader("Claims by section")
        for section, title in SECTION_TITLES.items():
            if section == "recommendation":
                continue
            section_claims = by_section.get(section, [])
            with st.expander(f"{title} — {len(section_claims)} claims"):
                if not section_claims:
                    st.write("_No claims generated for this section._")
                for c in section_claims:
                    badge = "EVIDENCE" if c.type == "evidence" else "INFERENCE"
                    st.markdown(f"**[{badge}] {c.claim}**")
                    if c.type == "evidence":
                        link = f"[{c.source}]({c.url})" if c.url else c.source
                        st.markdown(f"Source: {link} (accessed {c.date_accessed})")
                        if c.exact_snippet:
                            st.caption(c.exact_snippet)
                    else:
                        st.markdown(f"Reasoning: {c.reasoning} _(confidence: {c.confidence})_")
                    st.markdown("---")

        sources = sorted(
            {(c.source, c.url, c.date_accessed) for c in claims if c.type == "evidence" and c.source}
        )
        with st.expander(f"Sources Supporting Major Claims — {len(sources)}"):
            if not sources:
                st.write("_No evidence-backed sources were collected for this run._")
            for source, url, date_accessed in sources:
                link = f"[{source}]({url})" if url else source
                st.markdown(f"- {link} (accessed {date_accessed})")

        st.caption(
            f"Pipeline cost for this run: ~${cost_summary['approx_cost_usd']} "
            f"({cost_summary['calls']} calls, {cost_summary['model']}, "
            f"{cost_summary['input_tokens']} in / {cost_summary['output_tokens']} out tokens)"
        )
